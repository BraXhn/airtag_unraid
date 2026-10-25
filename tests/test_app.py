import base64
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('APP_PASSWORD', 'test-password-012345678901234567890')
    import app
    module = importlib.reload(app)
    yield module
    module.job.stop()
    module.bridge.stop_event.set()
    if module.bridge.thread:
        module.bridge.thread.join(timeout=2)


def auth():
    token = base64.b64encode(b'admin:test-password-012345678901234567890').decode()
    return {'Authorization': 'Basic ' + token, 'X-Requested-With': 'AirTagMap'}


def test_protected_routes_and_csrf(module):
    client = module.app.test_client()
    for path in ('/', '/api/state', '/api/locations', '/static/app.js'):
        assert client.get(path).status_code == 401
    assert client.get('/healthz').status_code == 200
    assert client.get('/', headers=auth()).status_code == 200
    headers = auth()
    del headers['X-Requested-With']
    assert client.post('/api/stop', headers=headers).status_code == 403
    assert client.post('/api/stop', headers={**auth(), 'Origin': 'https://evil.test'}).status_code == 403
    assert client.post('/api/start', headers=auth(), json={'action': 'bash'}).status_code == 400
    assert client.post('/api/input', headers=auth(), json={'value': 'x\nmalicious'}).status_code == 400


def test_data_headers_and_no_invented_positions(module):
    client = module.app.test_client()
    response = client.get('/api/locations', headers=auth())
    assert response.json == {'devices': [], 'updated_at': None}
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'
    data = {'devices': [{'name': '<script>alert(1)</script>', 'location': None}], 'updated_at': '2026-10-25'}
    (module.DATA / 'locations.json').write_text(json.dumps(data))
    assert client.get('/api/locations', headers=auth()).json == data


def test_forwarding_settings_require_auth_and_validate(module):
    client = module.app.test_client()
    assert client.get('/api/forwarding').status_code == 401
    assert client.post('/api/forwarding', json={}).status_code == 401
    cfg = client.get('/api/forwarding', headers=auth()).json['config']
    assert cfg['enabled'] is False
    assert client.post('/api/forwarding', headers=auth(), json={'enabled': True}).status_code == 400
    cfg.update(host='192.0.2.10', port=12345, codec='8')
    # Remain disabled: this test never contacts an external destination.
    assert client.post('/api/forwarding', headers=auth(), json=cfg).status_code == 200
    saved = client.get('/api/forwarding', headers=auth()).json['config']
    assert saved['host'] == '192.0.2.10' and saved['codec'] == '8'
    no_csrf = {'Authorization': auth()['Authorization']}
    assert client.post('/api/forwarding', headers=no_csrf, json=cfg).status_code == 403


def test_pty_input_is_hidden_and_single_job(module):
    code = "import getpass; value=getpass.getpass('Password: '); print('received '+str(len(value)))"
    assert module.job.start([sys.executable, '-c', code])
    assert not module.job.start([sys.executable, '-c', 'pass'])
    deadline = time.monotonic() + 4
    while 'Password:' not in module.job.state()['output'] and time.monotonic() < deadline:
        time.sleep(.02)
    module.job.send('a-super-private-password')
    while module.job.state()['running'] and time.monotonic() < deadline:
        time.sleep(.02)
    state = module.job.state()
    assert state['exit_code'] == 0
    assert 'received ' + str(len('a-super-private-password')) in state['output']
    assert 'a-super-private-password' not in state['output']


def test_worker_encryption_and_location_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'test-password-012345678901234567890')
    import worker
    monkeypatch.setattr(worker, 'DATA', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['worker.py'])
    keys = tmp_path / 'export' / 'keys'
    keys.mkdir(parents=True)
    (keys / 'tag.json').write_text('{}')
    state = {'account': {'password': 'sensitive-apple-password'}}
    (tmp_path / 'session.enc').write_bytes(worker.cipher().encrypt(json.dumps(state).encode()))

    class Accessory:
        name, model, identifier = 'Nycklar', 'AirTag', 'id-1'
        @classmethod
        def from_json(cls, path): return cls()
        def to_json(self): return {'type': 'accessory'}

    class Report:
        latitude, longitude, horizontal_accuracy = 59.3, 18.1, 25
        timestamp = worker.datetime(2026, 10, 25, tzinfo=worker.timezone.utc)

    class Account:
        @classmethod
        def from_json(cls, value, **kwargs):
            assert value == state
            return cls()
        def fetch_location(self, accessories): return {accessories[0]: Report()}
        def to_json(self): return state
        async def close(self): pass

    monkeypatch.setattr(worker, 'FindMyAccessory', Accessory)
    monkeypatch.setattr(worker, 'AppleAccount', Account)
    assert worker.main() == 0
    result = json.loads((tmp_path / 'locations.json').read_text())
    assert result['devices'][0]['location']['latitude'] == 59.3
    assert result['devices'][0]['name'] == 'Nycklar'
    assert b'sensitive-apple-password' not in (tmp_path / 'session.enc').read_bytes()
    assert (tmp_path / 'session.enc').stat().st_mode & 0o777 == 0o600


def test_failed_fetch_preserves_old_map(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'test-password-012345678901234567890')
    import worker
    monkeypatch.setattr(worker, 'DATA', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['worker.py'])
    keys = tmp_path / 'export' / 'keys'
    keys.mkdir(parents=True)
    (keys / 'tag.json').write_text('{}')
    (tmp_path / 'locations.json').write_text('{"devices": [], "updated_at": "previous"}')
    (tmp_path / 'session.enc').write_bytes(b'invalid-session')
    class Accessory:
        @classmethod
        def from_json(cls, path): return cls()
    monkeypatch.setattr(worker, 'FindMyAccessory', Accessory)
    assert worker.main() == 1
    assert json.loads((tmp_path / 'locations.json').read_text())['updated_at'] == 'previous'
