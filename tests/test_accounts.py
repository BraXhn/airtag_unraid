import base64
import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

from accounts import Accounts
from storage import cipher

PASSWORD = 'user-password-12345'
ADMIN_PASSWORD = 'admin-password-12345'
CSRF = {'X-Requested-With': 'AirTagMap'}


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('APP_PASSWORD', ADMIN_PASSWORD)
    import app
    result = importlib.reload(app)
    yield result
    result.workspaces.close()


def login(client, username, password=PASSWORD):
    return client.post('/api/login', headers=CSRF, json={'username': username, 'password': password})


def create(module, username):
    user = module.accounts.create(username, PASSWORD)
    return user, module.workspaces.get(user)


def test_admin_create_user_and_login_logout(module):
    admin = module.app.test_client()
    assert admin.get('/').status_code == 401
    assert 'Logga in' in admin.get('/login').text
    assert admin.get('/static/login.js').status_code == 200
    assert login(admin, 'admin', ADMIN_PASSWORD).status_code == 200
    response = admin.post('/api/admin/users', headers=CSRF,
                          json={'username': 'alice', 'password': PASSWORD, 'is_admin': True})
    assert response.status_code == 201 and not response.json['user']['is_admin']
    assert 'password' not in response.text
    listing = admin.get('/api/admin/users').json['users']
    assert listing[0]['username'] == 'alice'
    assert 'password_hash' not in listing[0]
    assert 'Hantera användarkonton' in admin.get('/').text
    alice = module.app.test_client()
    response = login(alice, 'alice')
    assert response.status_code == 200
    cookie = response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert alice.get('/api/me').json['username'] == 'alice'
    assert 'Hantera användarkonton' not in alice.get('/').text
    assert alice.get('/api/admin/users').status_code == 403
    assert alice.post('/api/admin/users', headers=CSRF, json={'username': 'mallory', 'password': PASSWORD}).status_code == 403
    assert alice.post('/api/logout', headers=CSRF, json={}).status_code == 200
    assert alice.get('/api/locations').status_code == 401


def test_all_data_and_control_routes_are_scoped(module, monkeypatch):
    alice, aw = create(module, 'alice')
    bob, bw = create(module, 'bob')
    for user, workspace in ((alice, aw), (bob, bw)):
        values = [{'identifier': user['username'], 'name': user['username'], 'master_key': user['username']+'-SECRET'}]
        (workspace.data/'mac-accessories.enc').write_bytes(cipher(module.accounts.data_password(user)).encrypt(json.dumps(values).encode()))
        (workspace.data/'locations.json').write_text(json.dumps({'devices': [{'identifier': user['username'], 'name': user['username']}], 'updated_at': user['username']}))
        workspace.job.output = user['username']+'-private-dialog'
    (module.DATA/'locations.json').write_text('{"devices":[],"updated_at":"admin-private"}')
    clients = [module.app.test_client(), module.app.test_client()]
    for client, user, other in ((clients[0], alice, bob), (clients[1], bob, alice)):
        assert login(client, user['username']).status_code == 200
        for path in ('/api/state', '/api/locations', '/api/forwarding', '/api/device-selection'):
            response = client.get(path+'?user_id='+other['id'])
            assert response.status_code == 200
            assert other['username'] not in response.text and 'admin-private' not in response.text
            assert 'SECRET' not in response.text and 'master_key' not in response.text
        assert client.post('/api/admin/users/'+other['id'], headers=CSRF, json={'password':'attacker-password'}).status_code == 403
    assert clients[0].post('/api/device-selection', headers=CSRF,
                           json={'all_devices':False,'identifiers':['bob']}).status_code == 400
    assert clients[0].post('/api/device-selection', headers=CSRF,
                           json={'all_devices':False,'identifiers':['alice']}).status_code == 200
    assert bw.device_selection.read()['all_devices'] is True
    assert clients[0].post('/api/auto-refresh', headers=CSRF,
                           json={'enabled':True,'interval_minutes':30}).status_code == 200
    assert bw.auto_refresh.state()['enabled'] is False
    cfg = clients[0].get('/api/forwarding').json['config']
    cfg.update(host='192.0.2.1', port=15027)
    assert clients[0].post('/api/forwarding', headers=CSRF, json=cfg).status_code == 200
    assert bw.bridge.config()['host'] != '192.0.2.1'
    calls=[]
    monkeypatch.setattr(aw.job,'start',lambda argv:calls.append('alice') or True)
    monkeypatch.setattr(bw.job,'start',lambda argv:calls.append('bob') or True)
    assert clients[0].post('/api/start',headers=CSRF,json={'action':'export','user_id':bob['id']}).status_code == 200
    assert calls == ['alice']
    with pytest.raises(InvalidToken):
        cipher(module.accounts.data_password(bob)).decrypt((aw.data/'mac-accessories.enc').read_bytes())


def test_reset_password_revokes_session_and_preserves_keys(module):
    user, workspace = create(module, 'alice')
    key = module.accounts.data_password(user)
    encrypted = cipher(key).encrypt(b'private Apple session')
    (workspace.data/'mac-session.enc').write_bytes(encrypted)
    alice = module.app.test_client()
    assert login(alice, 'alice').status_code == 200
    module.accounts.update(user['id'], {'password':'replacement-password'})
    assert alice.get('/api/locations').status_code == 401
    assert login(alice, 'alice').status_code == 401
    assert login(alice, 'alice','replacement-password').status_code == 200
    assert module.accounts.data_password(module.accounts.get(user['id'])) == key
    assert cipher(key).decrypt((workspace.data/'mac-session.enc').read_bytes()) == b'private Apple session'
    restored = Accounts(module.DATA, ADMIN_PASSWORD)
    assert restored.data_password(restored.get(user['id'])) == key
    assert restored.authenticate('alice','replacement-password')
    assert not restored.authenticate('alice',PASSWORD)
    assert PASSWORD.encode() not in restored.path.read_bytes()


def test_pause_revokes_access_stops_jobs_and_resume_keeps_data(module):
    user, workspace = create(module, 'alice')
    alice = module.app.test_client(); admin = module.app.test_client()
    login(alice,'alice');login(admin,'admin',ADMIN_PASSWORD)
    (workspace.data/'locations.json').write_text('{"devices":[],"updated_at":"keep-me"}')
    assert workspace.job.start([sys.executable,'-c','import time; time.sleep(30)'])
    assert admin.post('/api/admin/users/'+user['id'],headers=CSRF,json={'enabled':False}).status_code == 200
    assert workspace.job.proc.poll() is not None and workspace.job.closed
    assert workspace.auto_refresh.stop_event.is_set() and workspace.bridge.stop_event.is_set()
    assert alice.get('/api/state').status_code == 401
    assert login(alice,'alice').status_code == 401
    assert user['id'] not in module.workspaces.items
    with pytest.raises(PermissionError):
        module.workspaces.get(user)  # A stale request must not restart a paused account.
    assert admin.post('/api/admin/users/'+user['id'],headers=CSRF,json={'enabled':True}).status_code == 200
    assert alice.get('/api/state').status_code == 401  # Old cookies stay revoked.
    assert login(alice,'alice').status_code == 200
    assert alice.get('/api/locations').json['updated_at'] == 'keep-me'
    assert not module.workspaces.get(module.accounts.get(user['id'])).job.closed


def test_job_child_uses_own_directory_and_key_without_global_environment_change(module):
    alice, workspace = create(module, 'alice')
    before = dict(os.environ)
    code = "import os,pathlib; pathlib.Path('probe.json').write_text(__import__('json').dumps({'data':os.environ['DATA_DIR'],'key':os.environ['APP_PASSWORD']}))"
    assert workspace.job.start([sys.executable,'-c',code])
    deadline = time.monotonic()+3
    while workspace.job.state()['running'] and time.monotonic()<deadline:time.sleep(.01)
    assert workspace.job.state()['exit_code'] == 0
    value = json.loads((workspace.data/'probe.json').read_text())
    assert value == {'data':str(workspace.data),'key':module.accounts.data_password(alice)}
    assert not (module.DATA/'probe.json').exists()
    assert dict(os.environ) == before


def test_dialog_input_and_stop_cannot_control_another_account(module):
    alice, aw=create(module,'alice');bob,bw=create(module,'bob')
    ca,cb=module.app.test_client(),module.app.test_client()
    login(ca,'alice');login(cb,'bob')
    code="import getpass; value=getpass.getpass('First: '); print('length='+str(len(value)),flush=True); getpass.getpass('Second: ')"
    assert aw.job.start([sys.executable,'-c',code])
    assert bw.job.start([sys.executable,'-c',code])
    deadline=time.monotonic()+3
    while ('First:' not in aw.job.state()['output'] or 'First:' not in bw.job.state()['output']) and time.monotonic()<deadline:time.sleep(.01)
    assert ca.post('/api/input',headers=CSRF,json={'value':'alice-secret','user_id':bob['id']}).status_code == 200
    while 'length=' not in aw.job.state()['output'] and time.monotonic()<deadline:time.sleep(.01)
    assert 'length=12' in aw.job.state()['output'] and 'alice-secret' not in aw.job.state()['output']
    assert 'length=' not in bw.job.state()['output']
    assert ca.post('/api/stop',headers=CSRF,json={'user_id':bob['id']}).status_code == 200
    aw.job.proc.wait(timeout=3)
    assert bw.job.proc.poll() is None
    assert cb.get('/api/state').json['running'] is True


def test_background_workers_resume_only_enabled_users(module, monkeypatch):
    alice, workspace = create(module,'alice')
    bob = module.accounts.create('bob',PASSWORD)
    module.accounts.update(bob['id'],{'enabled':False})
    # Reconstruct the manager to exercise the same startup path as Gunicorn.
    module.workspaces.close()
    manager = module.Workspaces()
    started=[]
    monkeypatch.setattr(module.Workspace,'start',lambda self:started.append(self.data))
    manager.start()
    assert set(manager.items) == {'admin',alice['id']}
    assert module.DATA in started and module.accounts.directory(alice) in started
    assert not (module.DATA/'users'/bob['id']).exists()
    manager.close()


def test_login_csrf_rate_limit_and_admin_reserved(module):
    client = module.app.test_client()
    assert client.post('/api/login',json={'username':'admin','password':ADMIN_PASSWORD}).status_code == 403
    assert client.post('/api/login',headers={**CSRF,'Origin':'https://evil.test'},json={}).status_code == 403
    for _ in range(8):assert login(client,'admin','wrong-password').status_code == 401
    assert login(client,'admin',ADMIN_PASSWORD).status_code == 429
    for name in ('admin','../bob','a','/etc/passwd'):
        with pytest.raises(ValueError):module.accounts.create(name,PASSWORD)
    for password in ('short',123,None):
        with pytest.raises(ValueError):module.accounts.create('alice',password)
    user=module.accounts.create('alice',PASSWORD)
    with pytest.raises(ValueError):module.accounts.create('ALICE',PASSWORD)
    with pytest.raises(ValueError):module.accounts.update('admin',{'enabled':False})
    with pytest.raises(ValueError):module.accounts.update(user['id'],{'is_admin':True})
    with pytest.raises(ValueError):module.accounts.directory({'id':'../../escape'})


def test_logout_blocks_cached_basic_auth(module):
    client=module.app.test_client()
    headers={**CSRF,'Authorization':'Basic '+base64.b64encode(('admin:'+ADMIN_PASSWORD).encode()).decode()}
    assert client.get('/api/me',headers=headers).status_code == 200
    assert client.post('/api/logout',headers=headers,json={}).status_code == 200
    assert client.get('/api/me',headers=headers).status_code == 401


def test_existing_admin_data_remains_in_original_directory(module):
    assert module.admin_workspace.data == module.DATA
    assert module.accounts.data_password(module.accounts.get('admin')) == ADMIN_PASSWORD
    assert module.app.config['PERMANENT_SESSION_LIFETIME'].total_seconds() == 12*3600
    assert module.app.config['SESSION_REFRESH_EACH_REQUEST'] is False


def test_missing_installation_key_fails_closed(tmp_path):
    store=Accounts(tmp_path,ADMIN_PASSWORD)
    store.create('alice',PASSWORD)
    key=tmp_path/'accounts.key'
    key.unlink()
    with pytest.raises(RuntimeError,match='accounts.key saknas'):
        Accounts(tmp_path,ADMIN_PASSWORD)
    assert not key.exists()


def test_secure_cookie_option(module):
    module.app.config['SESSION_COOKIE_SECURE']=True
    response=login(module.app.test_client(),'admin',ADMIN_PASSWORD)
    assert 'Secure;' in response.headers['Set-Cookie']
