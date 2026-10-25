"""Local accounts. Only server-generated IDs select a private workspace."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


class Accounts:
    def __init__(self, data, admin_password):
        self.data, self.admin_password = Path(data), admin_password
        key_path = self.data / 'accounts.key'
        self.path = self.data / 'accounts.sqlite3'
        if self.path.exists() and not key_path.exists():
            raise RuntimeError('accounts.key saknas. Återställ filen från backup innan appen startas.')
        try:
            with key_path.open('xb') as output:
                output.write(secrets.token_bytes(32))
            key_path.chmod(0o600)
        except FileExistsError:
            pass
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise RuntimeError('accounts.key måste innehålla 32 byte. Återställ filen från backup.')
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)''')
        self.path.chmod(0o600)
        self.dummy_hash = generate_password_hash(secrets.token_urlsafe(24))
        self.lock = threading.Lock()
        self.failures = {}

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @property
    def session_key(self):
        return hmac.digest(self.key, b'web-session', 'sha256')

    def data_password(self, user):
        if user['id'] == 'admin':
            return self.admin_password  # Preserve existing encrypted Apple data.
        return hmac.new(self.key, ('user-data:' + user['id']).encode(), 'sha256').hexdigest()

    def directory(self, user):
        if user['id'] == 'admin':
            return self.data
        # IDs originate in our database, never in query/body/path parameters.
        if not re.fullmatch(r'[0-9a-f]{32}', user['id']):
            raise ValueError('Ogiltigt konto-ID')
        path = self.data / 'users' / user['id']
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def get(self, uid):
        if uid == 'admin':
            return {'id': 'admin', 'username': 'admin', 'is_admin': True, 'enabled': True,
                    'version': hmac.new(self.key, self.admin_password.encode(), 'sha256').hexdigest()}
        with self.db() as db:
            row = db.execute('SELECT id,username,enabled,version,created_at FROM users WHERE id=?', (uid,)).fetchone()
        return {**dict(row), 'is_admin': False, 'enabled': bool(row['enabled'])} if row else None

    def list(self):
        with self.db() as db:
            rows = db.execute('SELECT id,username,enabled,version,created_at FROM users ORDER BY username').fetchall()
        return [{**dict(row), 'enabled': bool(row['enabled']), 'is_admin': False} for row in rows]

    @staticmethod
    def password(value):
        if not isinstance(value, str) or not 12 <= len(value) <= 256:
            raise ValueError('Lösenordet måste innehålla 12–256 tecken')
        return value

    def create(self, username, password):
        if not isinstance(username, str):
            raise ValueError('Ange ett användarnamn')
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,39}', username) or username == 'admin':
            raise ValueError('Användarnamnet måste ha 3–40 tecken (a–z, 0–9, punkt, bindestreck eller understreck), börja med en bokstav och får inte vara admin')
        hashed = generate_password_hash(self.password(password))
        uid = secrets.token_hex(16)
        try:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT count(*) FROM users').fetchone()[0] >= 100:
                    raise ValueError('Högst 100 användarkonton stöds')
                db.execute('INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)',
                           (uid, username, hashed, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError('Användarnamnet finns redan') from None
        return self.get(uid)

    def update(self, uid, value):
        if uid == 'admin' or not self.get(uid):
            raise ValueError('Kontot finns inte eller kan inte ändras här')
        if not isinstance(value, dict) or not value or set(value) - {'password', 'enabled'}:
            raise ValueError('Ange lösenord eller kontostatus')
        if 'enabled' in value and type(value['enabled']) is not bool:
            raise ValueError('Kontostatus måste vara sant eller falskt')
        hashed = generate_password_hash(self.password(value['password'])) if 'password' in value else None
        with self.db() as db:
            if hashed:
                db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?', (hashed, uid))
            if 'enabled' in value:
                db.execute('UPDATE users SET enabled=?,version=version+1 WHERE id=?', (int(value['enabled']), uid))
        return self.get(uid)

    def authenticate(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 40 or len(password) > 256:
            return None
        username = username.strip().lower()
        if username == 'admin':
            valid = hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                        hashlib.sha256(self.admin_password.encode()).digest())
            return self.get('admin') if valid else None
        with self.db() as db:
            row = db.execute('SELECT id,password_hash,enabled,version FROM users WHERE username=?', (username,)).fetchone()
        valid = check_password_hash(row['password_hash'] if row else self.dummy_hash, password)
        user = self.get(row['id']) if valid and row and row['enabled'] else None
        # A reset/pause racing the slow password hash must revoke this attempt too.
        return user if user and user['enabled'] and user['version'] == row['version'] else None

    def login(self, username, password, address):
        """Bounded, per source/name retry limiter; never store supplied passwords."""
        now = time.monotonic()
        bucket = (address, str(username)[:40].lower())
        with self.lock:
            self.failures = {k: v for k, v in self.failures.items() if now - v[0] < 300}
            attempts = self.failures.get(bucket, (now, 0))
            if attempts[1] >= 8 or len(self.failures) >= 2048:
                return None, True
            self.failures[bucket] = (attempts[0], attempts[1] + 1)
        user = self.authenticate(username, password)
        if user:
            with self.lock:
                self.failures.pop(bucket, None)
        return user, False
