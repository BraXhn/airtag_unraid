"""Single-user, LAN/SSH-only web wrapper for FindMy tooling."""
import hashlib
import hmac
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

from flask import Flask, Response, abort, jsonify, render_template, request

os.umask(0o077)
DATA = Path(os.environ.get('DATA_DIR', '/data'))
DATA.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ.get('APP_PASSWORD', '')
if len(PASSWORD) < 24:
    raise RuntimeError('APP_PASSWORD must contain at least 24 characters')
ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 8192


class Job:
    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.fd = None
        self.output = ''
        self.secrets = []
        self.code = None
        self.started = 0

    def _text(self):
        value = self.output
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '••••')
        return value

    def state(self):
        with self.lock:
            return {'running': self.fd is not None,
                    'output': self._text(), 'exit_code': self.code}

    def start(self, argv):
        with self.lock:
            if self.fd is not None:
                return False
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self.output, self.secrets, self.code = '', [], None
            env = dict(os.environ, PYTHONUNBUFFERED='1', RUST_LOG='off', TERM='dumb')
            try:
                self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                             cwd=DATA, env=env, start_new_session=True)
            except Exception:
                os.close(master)
                raise
            finally:
                os.close(slave)
            self.fd, self.started = master, time.monotonic()
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


job = Job()


@app.before_request
def protect():
    if request.path == '/healthz':
        return None
    auth = request.authorization
    valid = auth and hmac.compare_digest(auth.username or '', 'admin') and hmac.compare_digest(
        hashlib.sha256((auth.password or '').encode()).digest(),
        hashlib.sha256(PASSWORD.encode()).digest())
    if not valid:
        return Response('Logga in med admin och ditt applösenord.', 401,
                        {'WWW-Authenticate': 'Basic realm="AirTag-karta", charset="UTF-8"'})
    if request.method != 'GET':
        # Custom header blocks cross-origin form posts; CORS is never enabled.
        if request.headers.get('X-Requested-With') != 'AirTagMap':
            abort(403)
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            abort(403)


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
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
    result = job.state()
    result['has_keys'] = any((DATA / 'export' / 'keys').glob('*.json'))
    result['has_session'] = (DATA / 'session.enc').exists()
    return result


@app.post('/api/start')
def start():
    action = (request.get_json(silent=True) or {}).get('action')
    if action == 'export':
        folder = DATA / 'export'
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
        if not job.start(argv):
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
        job.send(value)
    except (ValueError, OSError):
        return {'error': 'Dialogen är inte aktiv'}, 409
    return {'ok': True}


@app.post('/api/stop')
def stop():
    job.stop()
    return {'ok': True}


@app.get('/api/locations')
def locations():
    path = DATA / 'locations.json'
    if not path.exists():
        return {'devices': [], 'updated_at': None}
    return jsonify(json.loads(path.read_text()))
