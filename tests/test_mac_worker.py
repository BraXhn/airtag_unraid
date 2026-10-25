import asyncio
import json
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import mac_worker as worker


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'ci-pass-1234')
    monkeypatch.setattr(worker, 'DATA', tmp_path)
    monkeypatch.setattr(worker, 'MACHINE', tmp_path / 'machine')
    monkeypatch.setattr(worker, 'SESSION', tmp_path / 'mac-session.enc')
    monkeypatch.setattr(worker, 'KEYS', tmp_path / 'mac-accessories.enc')
    monkeypatch.setattr(worker, 'COREADI', tmp_path / 'CoreADI.x86_64')
    monkeypatch.setattr(sys, 'argv', ['mac_worker.py'])
    return tmp_path


def test_encrypted_snapshot_omits_password(state):
    account = SimpleNamespace(_uid='uid', _devid='machine', _username='user@example.invalid',
        _password='never-save-this', _account_info={}, _login_state=SimpleNamespace(value=3),
        _login_state_data={'token': 'secret-token'})
    worker.save_session(account)
    encrypted = worker.SESSION.read_bytes()
    assert b'secret-token' not in encrypted and b'user@example.invalid' not in encrypted
    value = json.loads(worker.cipher().decrypt(encrypted))
    assert value['account']['password'] is None
    assert 'never-save-this' not in json.dumps(value)
    assert value['ids']['devid'] == 'machine'
    assert worker.SESSION.stat().st_mode & 0o777 == 0o600


def test_rejects_corrupt_coreadi_without_execution(state):
    worker.COREADI.write_bytes(b'not-the-pinned-library')
    with pytest.raises(worker.UserError, match='kontrollsumman'):
        worker.ensure_library()


@pytest.mark.parametrize('status,expected', [({'hsc':409}, 'trustedDeviceSecondaryAuth'),
                                          ({'hsc':200}, None)])
def test_new_two_factor_response(status, expected, monkeypatch):
    async def gsa(parameters): return {'Status': status}
    async def request(*args, **kwargs): return SimpleNamespace(status_code=200)
    account = SimpleNamespace(_gsa_request=gsa, _http=SimpleNamespace(request=request),
        _anisette=SimpleNamespace(akd_user_agent='consistent-agent'))
    cooldown = SimpleNamespace(guard=lambda: None, note_429=lambda: None)
    monkeypatch.setattr('findmy.reports.account._GSA_USER_AGENT', 'original')
    worker.install_protocol(account, None, cooldown)
    response = asyncio.run(account._gsa_request({'o':'complete'}))
    assert response['Status'].get('au') == expected


def test_429_stops_transport_without_retry(monkeypatch):
    calls=[]
    async def request(*args, **kwargs):
        assert kwargs['auto_retry'] is False
        calls.append('request')
        return SimpleNamespace(status_code=429)
    account = SimpleNamespace(_gsa_request=None, _http=SimpleNamespace(request=request),
        _anisette=SimpleNamespace(akd_user_agent='consistent-agent'))
    cooldown = SimpleNamespace(guard=lambda: None, note_429=lambda: calls.append('cooldown'))
    monkeypatch.setattr('findmy.reports.account._GSA_USER_AGENT', 'original')
    worker.install_protocol(account, None, cooldown)
    with pytest.raises(worker.UserError): asyncio.run(account._http.request('POST','https://example.invalid'))
    assert calls == ['request','cooldown']


def test_read_only_recovery_tries_passcode_once_and_closes(state, monkeypatch):
    from findmy.keychain import session as session_module
    # tag_keys is supplied by the pinned emulator in the image. Only the wrapper
    # is needed here; this fixture must never send an Apple request.
    monkeypatch.setitem(sys.modules, 'tag_keys', SimpleNamespace(for_cloudkit=lambda a:a))
    calls=[]
    class Session:
        @classmethod
        async def open(cls, account): return cls()
        async def recovery_options(self):
            return SimpleNamespace(recoverable=[SimpleNamespace(describe=lambda:'test device')])
        async def recover(self, record, passcode):
            calls.append(passcode)
            raise ValueError('wrong passcode')
        async def close(self): calls.append('close')
    monkeypatch.setattr(session_module,'AsyncKeychainSession',Session)
    monkeypatch.setattr('builtins.input',lambda prompt:'0')
    monkeypatch.setattr(worker.getpass,'getpass',lambda prompt:'test-passcode')
    with pytest.raises(ValueError): asyncio.run(worker.recover_accessories(SimpleNamespace(_password='memory-only')))
    assert calls == ['test-passcode','close']
    assert not worker.KEYS.exists()


def test_fetch_maps_apple_reports_and_enqueues_without_new_login(state, monkeypatch):
    from findmy import FindMyAccessory
    import findmy
    class Accessory:
        name, model, identifier = 'Nycklar', 'AirTag', 'same-stable-id'
        def to_json(self): return {'identifier':self.identifier}
    accessory=Accessory()
    class Account:
        async def fetch_location(self, item):
            assert item is accessory
            return SimpleNamespace(latitude=59.3,longitude=18.1,horizontal_accuracy=20,
                timestamp=datetime(2026,10,25,tzinfo=timezone.utc))
        async def close(self): self.closed=True
    account=Account()
    worker.KEYS.write_bytes(worker.cipher().encrypt(b'[{}]'))
    async def authenticate(*args): return account
    monkeypatch.setattr(worker,'authenticate',authenticate)
    monkeypatch.setattr(worker,'load_modules',lambda:(SimpleNamespace(guard=lambda:None),None,None,None))
    monkeypatch.setattr(worker,'ensure_library',lambda:None)
    monkeypatch.setattr(worker,'save_session',lambda account:None)
    monkeypatch.setattr(FindMyAccessory,'from_json',lambda item:accessory)
    assert asyncio.run(worker.run())==0
    assert account.closed
    data=json.loads((state/'locations.json').read_text())
    assert data['devices'][0]['identifier']=='same-stable-id'
    assert data['devices'][0]['location']['latitude']==59.3
    from teltonika import Bridge
    assert Bridge(state).config()['tags'][0]['identifier']=='same-stable-id'


def test_tls_patch_fails_closed_and_keeps_certificate_checks(tmp_path):
    import patch_macadi
    source=tmp_path/'tools/machoadi'
    source.mkdir(parents=True)
    (source/'gsa.py').write_text('class GSA:\n    def __init__(self):\n'
        '        self._ctx = ssl.create_default_context()\n'
        '        self._ctx.check_hostname = False\n'
        '        self._ctx.verify_mode = ssl.CERT_NONE\n')
    (source/'cooldown.py').write_text('MARKER = Path("/tmp/macadi/gsa-429")\n')
    patch_macadi.patch(tmp_path)
    text=(source/'gsa.py').read_text()
    assert 'CERT_NONE' not in text and 'check_hostname = False' not in text
    assert 'apple_trust_context()' in text
    assert 'MACADI_CA_FILE' in text
    with pytest.raises(RuntimeError): patch_macadi.patch(tmp_path)
    context=ssl.create_default_context()
    context.load_verify_locations(cafile=Path(worker.__file__).with_name('apple-root-g3.pem'))
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED


def test_progress_preserves_completed_and_previous_positions_on_network_error(state, monkeypatch):
    class Tag:
        model='AirTag'
        def __init__(self, identity): self.identifier=self.name=identity
        def to_json(self): return {'identifier':self.identifier}
    tags=[Tag('one'),Tag('two'),Tag('three')]
    previous={'latitude':58.0,'longitude':17.0,'accuracy':50,'timestamp':'2026-10-24T00:00:00Z'}
    (state/'locations.json').write_text(json.dumps({'devices':[{'identifier':'two','location':previous}]}))
    enqueued=[]
    class Queue:
        def __init__(self, data): pass
        def enqueue(self, devices):
            enqueued.append(json.loads(json.dumps(devices)))
    monkeypatch.setattr(worker,'Bridge',Queue)
    monkeypatch.setattr(worker,'save_session',lambda account:None)
    class Account:
        async def fetch_location(self, tag):
            if tag.identifier=='two': raise TimeoutError()
            if tag.identifier=='three':
                interim=json.loads((state/'locations.json').read_text())
                assert interim['completed']==2
                assert interim['devices'][0]['location']['latitude']==59.3
                assert interim['devices'][1]['location']==previous
                assert any(d[0]['fetch_status']=='ok' for d in enqueued)
            return SimpleNamespace(latitude=59.3,longitude=18.1,horizontal_accuracy=10,
                                   timestamp=datetime(2026,10,25,tzinfo=timezone.utc))
    assert asyncio.run(worker.fetch_positions(Account(),tags))==1
    final=json.loads((state/'locations.json').read_text())
    assert final['completed']==3 and final['failures']==1 and final['fetching'] is False
    assert final['devices'][1]['fetch_status']=='error'
    assert final['devices'][1]['location']==previous
    assert final['devices'][2]['fetch_status']=='ok'


def test_fatal_fetch_keeps_already_received_report(state, monkeypatch):
    class Tag:
        model='AirTag'
        def __init__(self, identity): self.identifier=self.name=identity
        def to_json(self): return {'identifier':self.identifier}
    tags=[Tag('one'),Tag('two')]
    monkeypatch.setattr(worker,'save_session',lambda account:None)
    class Account:
        async def fetch_location(self, tag):
            if tag.identifier=='two': raise worker.UserError('Apple-sessionen avslutades')
            return SimpleNamespace(latitude=59.3,longitude=18.1,horizontal_accuracy=10,
                                   timestamp=datetime(2026,10,25,tzinfo=timezone.utc))
    with pytest.raises(worker.UserError): asyncio.run(worker.fetch_positions(Account(),tags))
    result=json.loads((state/'locations.json').read_text())
    assert result['completed']==1 and result['fetching'] is False
    assert result['devices'][0]['location']['latitude']==59.3
    assert worker.KEYS.exists()


def test_unattended_mode_never_prompts_or_provisions_when_not_ready(state, monkeypatch):
    monkeypatch.setattr(sys,'argv',['mac_worker.py','--non-interactive'])
    def forbidden(*args): raise AssertionError('Unattended mode must not ask for credentials')
    monkeypatch.setattr('builtins.input',forbidden)
    monkeypatch.setattr(worker.getpass,'getpass',forbidden)
    monkeypatch.setattr(worker,'ensure_library',forbidden)
    assert worker.main()==10
    status=json.loads((state/'session-status.json').read_text())
    assert status['login_required'] is True


def test_unattended_recovery_refuses_passcode_prompt(state, monkeypatch):
    monkeypatch.setattr(sys,'argv',['mac_worker.py','--non-interactive'])
    with pytest.raises(worker.LoginRequired): asyncio.run(worker.recover_accessories(None))


def test_only_selected_device_is_queried_and_other_keys_and_locations_are_preserved(state, monkeypatch):
    class Tag:
        model='AirTag'
        def __init__(self, identity): self.identifier=self.name=identity
        def to_json(self): return {'identifier':self.identifier,'master_key':'private-'+self.identifier}
    tags=[Tag('one'),Tag('two')]
    previous={'latitude':58.0,'longitude':17.0,'accuracy':50,'timestamp':'2026-10-24T00:00:00Z'}
    (state/'locations.json').write_text(json.dumps({'devices':[{'identifier':'one','location':previous}]}))
    monkeypatch.setattr(worker,'save_session',lambda account:None)
    called=[]
    class Account:
        async def fetch_location(self, tag):
            called.append(tag.identifier)
            return None
    assert asyncio.run(worker.fetch_positions(Account(),[tags[1]],all_accessories=tags))==0
    assert called==['two']
    devices=json.loads((state/'locations.json').read_text())['devices']
    unselected=next(d for d in devices if d['identifier']=='one')
    assert unselected['location']==previous and unselected['selected'] is False
    assert len(json.loads(worker.cipher().decrypt(worker.KEYS.read_bytes())))==2


def test_export_returns_device_list_before_any_position_query(state, monkeypatch):
    monkeypatch.setattr(sys,'argv',['mac_worker.py','--export'])
    class Account:
        async def close(self): pass
        async def fetch_location(self,*args): raise AssertionError('export must not fetch positions')
    account=Account()
    async def auth(*args): return account
    async def recover(*args): return [SimpleNamespace(identifier='one')]
    monkeypatch.setattr(worker,'authenticate',auth)
    monkeypatch.setattr(worker,'recover_accessories',recover)
    monkeypatch.setattr(worker,'load_modules',lambda:(SimpleNamespace(guard=lambda:None),None,None,None))
    monkeypatch.setattr(worker,'ensure_library',lambda:None)
    monkeypatch.setattr(worker,'save_session',lambda account:None)
    assert asyncio.run(worker.run())==0
    assert not (state/'locations.json').exists()
