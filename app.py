"""Private per-user web workspaces for FindMy tooling."""
import json
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path
from datetime import timedelta

from flask import Flask, abort, g, jsonify, render_template, request, session
from teltonika import Bridge
from scheduler import AutoRefresh
from device_selection import DeviceSelection
from accounts import Accounts

os.umask(0o077)
DATA = Path(os.environ.get('DATA_DIR', '/data'))
DATA.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ.get('APP_PASSWORD', '')
if len(PASSWORD) < 12:
    raise RuntimeError('APP_PASSWORD must contain at least 12 characters')
ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 262144
accounts = Accounts(DATA, PASSWORD)
app.secret_key = accounts.session_key
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
                  SESSION_REFRESH_EACH_REQUEST=False,
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12))


class Job:
    def __init__(self, data=None, password=None):
        self.data = Path(data) if data is not None else DATA
        self.password = password if password is not None else PASSWORD
        self.closed = False
        self.lock = threading.RLock()
        self.proc = None
        self.fd = None
        self.output = ''
        self.secrets = []
        self.code = None
        self.started = 0
        self.started_at = None
        self.finished_at = None
        self.run_id = 0

    def _text(self):
        value = self.output
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '••••')
        return value

    def state(self):
        with self.lock:
            return {'running': self.fd is not None,
                    'output': self._text(), 'exit_code': self.code,
                    'started_at': self.started_at, 'finished_at': self.finished_at, 'run_id': self.run_id}

    def start(self, argv):
        with self.lock:
            if self.closed or self.fd is not None:
                return False
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self.output, self.secrets, self.code = '', [], None
            env = dict(os.environ, PYTHONUNBUFFERED='1', RUST_LOG='off', TERM='dumb',
                       DATA_DIR=str(self.data), APP_PASSWORD=self.password)
            try:
                self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                             cwd=self.data, env=env, start_new_session=True)
            except Exception:
                os.close(master)
                raise
            finally:
                os.close(slave)
            self.fd, self.started = master, time.monotonic()
            self.run_id += 1
            self.started_at, self.finished_at = time.time(), None
            threading.Thread(target=self._read, args=(self.proc, master), daemon=True).start()
            return True

    def _read(self, proc, fd):
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                if time.monotonic() - self.started > 900 and proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                ready, _, _ = select.select([fd], [], [], 0.25)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    with self.lock:
                        self.output = (self.output + decoder.decode(chunk))[-64000:]
                elif proc.poll() is not None:
                    break
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
        finally:
            with self.lock:
                self.code = proc.returncode
                self.finished_at = time.time()
                self.fd = None
                os.close(fd)

    def send(self, value):
        with self.lock:
            if self.fd is None or self.proc.poll() is not None:
                raise ValueError('Ingen aktiv dialog')
            # All answers are treated as secrets; none are echoed or logged.
            if value:
                self.secrets.append(value)
            os.write(self.fd, (value + '\n').encode())

    def stop(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGTERM)


class Workspace:
    def __init__(self, user):
        self.data = accounts.directory(user)
        self.job = Job(self.data, accounts.data_password(user))
        self.bridge = Bridge(self.data)
        self.auto_refresh = AutoRefresh(self.data, self.job,
            [sys.executable, str(ROOT / 'mac_worker.py'), '--non-interactive'])
        self.device_selection = DeviceSelection(self.data, accounts.data_password(user))

    def start(self):
        self.bridge.start()
        self.auto_refresh.start()

    def close(self):
        self.auto_refresh.stop_event.set()
        self.bridge.stop_event.set()
        with self.job.lock:
            self.job.closed = True
            self.job.stop()
        if self.job.proc is not None:
            try:
                self.job.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.job.proc.pid, signal.SIGKILL)
        for thread in (self.auto_refresh.thread, self.bridge.thread):
            if thread:
                thread.join(timeout=2)


class Workspaces:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}
        self.running = False

    def get(self, user):
        with self.lock:
            current = accounts.get(user['id'])
            if not current or not current['enabled']:
                raise PermissionError('Kontot är pausat')
            if user['id'] not in self.items:
                self.items[user['id']] = Workspace(user)
            result = self.items[user['id']]
            if self.running:
                result.start()
            return result

    def start(self):
        with self.lock:
            self.running = True
            for user in [accounts.get('admin'), *accounts.list()]:
                if user['enabled']:
                    self.get(user)

    def pause(self, uid):
        with self.lock:
            item = self.items.pop(uid, None)
            if item:
                item.close()

    def close(self):
        with self.lock:
            self.running = False
            for item in self.items.values():
                item.close()
            self.items.clear()


workspaces = Workspaces()
admin_workspace = workspaces.get(accounts.get('admin'))
# Retain these aliases for existing integrations/tests; requests always use g.workspace.
job, bridge = admin_workspace.job, admin_workspace.bridge
auto_refresh, device_selection = admin_workspace.auto_refresh, admin_workspace.device_selection


def check_csrf():
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('X-Requested-With') != 'AirTagMap':
            abort(403)
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            abort(403)


def public_user(user):
    return {key: user[key] for key in ('id', 'username', 'is_admin', 'enabled')}


@app.before_request
def protect():
    if request.path == '/healthz' or request.path in ('/login', '/static/login.js', '/static/app.css'):
        return None
    if request.path == '/api/login':
        check_csrf()
        return None
    user = accounts.get(session.get('user_id')) if session.get('user_id') else None
    if not user or not user['enabled'] or user['version'] != session.get('version'):
        user = None
    # Existing Basic-auth clients continue working. A browser logout explicitly
    # blocks cached Basic credentials until the next successful form login.
    if not user and not session.get('user_id') and not session.get('basic_blocked') and request.authorization:
        auth = request.authorization
        user, limited = accounts.login(auth.username, auth.password, request.remote_addr)
        if limited:
            return {'error': 'För många inloggningsförsök. Vänta fem minuter.'}, 429
    if user is None:
        if request.path == '/':
            return render_template('login.html'), 401
        return {'error': 'Logga in för att fortsätta'}, 401
    check_csrf()
    g.user = user
    try:
        g.workspace = workspaces.get(user)
    except PermissionError:
        return {'error': 'Kontot är pausat'}, 401
    g.workspace.bridge.start()


@app.get('/login')
def login_page():
    return render_template('login.html')


@app.post('/api/login')
def login():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        return {'error': 'Ange användarnamn och lösenord'}, 400
    user, limited = accounts.login(value.get('username'), value.get('password'), request.remote_addr)
    if limited:
        return {'error': 'För många inloggningsförsök. Vänta fem minuter.'}, 429
    if user is None:
        return {'error': 'Fel användarnamn eller lösenord, eller pausat konto'}, 401
    session.clear()
    session.permanent = True
    session.update(user_id=user['id'], version=user['version'])
    return {'user': public_user(user)}


@app.post('/api/logout')
def logout():
    session.clear()
    session['basic_blocked'] = True
    return {'ok': True}


@app.get('/api/me')
def me():
    return public_user(g.user)


@app.get('/api/admin/users')
def list_users():
    if not g.user['is_admin']:
        abort(403)
    return {'users': accounts.list()}


@app.post('/api/admin/users')
def create_user():
    if not g.user['is_admin']:
        abort(403)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        return {'error': 'Ange användarnamn och lösenord'}, 400
    try:
        user = accounts.create(value.get('username'), value.get('password'))
        if workspaces.running:
            workspaces.get(user)
    except ValueError as error:
        return {'error': str(error)}, 400
    return {'user': public_user(user)}, 201


@app.post('/api/admin/users/<uid>')
def update_user(uid):
    if not g.user['is_admin']:
        abort(403)
    try:
        with workspaces.lock:
            user = accounts.update(uid, request.get_json(silent=True))
            if not user['enabled']:
                workspaces.pause(user['id'])
            elif workspaces.running:
                workspaces.get(user)
    except ValueError as error:
        return {'error': str(error)}, 400
    return {'user': public_user(user)}


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # OSM requires a Referer for browser tile requests. Send only the origin,
    # never the page path, query, Basic credentials or any Apple account data.
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://tile.openstreetmap.org; "
        "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
    return response


@app.get('/healthz')
def health():
    return {'ok': True}


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/state')
def state():
    result = g.workspace.job.state()
    result['has_keys'] = (g.workspace.data / 'mac-accessories.enc').exists() or any((g.workspace.data / 'export' / 'keys').glob('*.json'))
    result['has_session'] = (g.workspace.data / 'mac-session.enc').exists() or (g.workspace.data / 'session.enc').exists()
    return result


@app.post('/api/start')
def start():
    action = (request.get_json(silent=True) or {}).get('action')
    if action in ('export', 'locations', 'relogin') and os.environ.get('FINDMY_BACKEND', 'macadi') == 'macadi':
        argv = [sys.executable, str(ROOT / 'mac_worker.py')]
        if action == 'export':
            argv.append('--export')
        elif action == 'relogin':
            argv.append('--relogin')
    elif action == 'export':
        folder = g.workspace.data / 'export'
        folder.mkdir(exist_ok=True)
        profile = folder / 'device-profile.toml'
        if not profile.exists():
            profile.write_bytes((ROOT / 'device-profile.template.toml').read_bytes())
            profile.chmod(0o600)
        argv = ['/usr/local/bin/export-findmy', '--device-profile', str(profile),
                '--output-dir', str(folder / 'keys')]
        if os.environ.get('ANISETTE_URL'):
            argv += ['--anisette-url', os.environ['ANISETTE_URL']]
    elif action in ('locations', 'relogin'):
        argv = [sys.executable, str(ROOT / 'worker.py')]
        if action == 'relogin':
            argv.append('--relogin')
    else:
        return {'error': 'Okänd åtgärd'}, 400
    try:
        if not g.workspace.job.start(argv):
            return {'error': 'En åtgärd körs redan'}, 409
    except OSError:
        return {'error': 'Verktyget kunde inte startas. Kontrollera Docker-bygget.'}, 503
    return {'ok': True}


@app.post('/api/input')
def answer():
    value = (request.get_json(silent=True) or {}).get('value')
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
        return {'error': 'Ogiltigt svar'}, 400
    try:
        g.workspace.job.send(value)
    except (ValueError, OSError):
        return {'error': 'Dialogen är inte aktiv'}, 409
    return {'ok': True}


@app.post('/api/stop')
def stop():
    g.workspace.auto_refresh.defer()
    g.workspace.job.stop()
    return {'ok': True}


@app.get('/api/locations')
def locations():
    path = g.workspace.data / 'locations.json'
    if not path.exists():
        return {'devices': [], 'updated_at': None}
    return jsonify(json.loads(path.read_text()))


@app.get('/api/forwarding')
def forwarding():
    g.workspace.bridge.reconcile()
    return {'config': g.workspace.bridge.config(), 'status': g.workspace.bridge.status()}


@app.post('/api/forwarding')
def save_forwarding():
    try:
        g.workspace.bridge.save_config(request.get_json(silent=True))
        g.workspace.bridge.reconcile()
    except ValueError as error:
        return {'error': str(error)}, 400
    return {'ok': True}


@app.get('/api/auto-refresh')
def refresh_state():
    return g.workspace.auto_refresh.state()


@app.post('/api/auto-refresh')
def save_refresh():
    try:
        g.workspace.auto_refresh.save(request.get_json(silent=True))
    except ValueError as error:
        return {'error': str(error)}, 400
    return {'ok': True}


@app.get('/api/device-selection')
def selection_state():
    try:
        return g.workspace.device_selection.state()
    except Exception:
        return {'all_devices': True, 'identifiers': [], 'devices': [],
                'error': 'Föremålslistan kunde inte läsas. Anslut Apple-kontot i appen igen.'}


@app.post('/api/device-selection')
def save_selection():
    try:
        g.workspace.device_selection.save(request.get_json(silent=True))
        g.workspace.auto_refresh.devices_changed()
    except ValueError as error:
        return {'error': str(error)}, 400
    except Exception:
        return {'error': 'Föremålslistan kunde inte läsas. Anslut Apple-kontot i appen igen.'}, 400
    return {'ok': True}
