"""Linux-only Apple login and read-only accessory recovery using emulated CoreADI.

Emulator source is pinned in Dockerfile; Apple's library is fetched by the user
at first use, stored only in /data, and never included in the published image.
"""
import asyncio
import getpass
import hashlib
import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import time
from pathlib import Path

from worker import atomic_write, cipher
from teltonika import Bridge

os.umask(0o077)
logging.disable(logging.CRITICAL)
DATA = Path(os.environ.get('DATA_DIR', '/data'))
UPSTREAM = Path(os.environ.get('MACADI_ROOT', '/opt/os-findmy'))
MACHINE = DATA / 'macadi' / 'machine'
SESSION = DATA / 'mac-session.enc'
KEYS = DATA / 'mac-accessories.enc'
COREADI = DATA / 'macadi' / 'CoreADI.x86_64'
COREADI_SHA256 = '2eafc3c79c7f41a8afbfbeaaf23ee5dab5ad5bda55ec8e54dbc0ddf1d20ce630'


class UserError(RuntimeError):
    pass


def ensure_library():
    if COREADI.exists():
        if hashlib.sha256(COREADI.read_bytes()).hexdigest() != COREADI_SHA256:
            raise UserError('CoreADI-kontrollsumman stämmer inte')
        return
    print('Förbereder Linux-inloggning: hämtar iTunes 12.8.3 direkt från Apple och extraherar CoreADI. '
          'Detta görs en gång och kan ta flera minuter.', flush=True)
    folder = DATA / 'macadi' / 'download'
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / 'tmp'
    temp.mkdir(exist_ok=True)
    if shutil.disk_usage(folder).free < 1024**3:
        raise UserError('Minst 1 GB ledigt i appdata behövs för förberedelsen')
    done = subprocess.run([sys.executable, str(UPSTREAM / 'tools/fetch_mac_coreadi.py'),
                           '--dest', str(folder)], env=dict(os.environ, TMPDIR=str(temp)),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    source = folder / 'coreadi-pkg/rootfs/System/Library/PrivateFrameworks/CoreADI.framework/Versions/A/CoreADI.x86_64'
    if done.returncode != 0 or not source.exists():
        raise UserError('Apple-biblioteket kunde inte hämtas eller extraheras')
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != COREADI_SHA256:
        raise UserError('CoreADI-kontrollsumman stämmer inte')
    atomic_write(COREADI, content)
    # Only disposable download/extraction material, never machine state or keys.
    shutil.rmtree(folder)


def load_modules():
    os.environ['COREADI_PATH'] = str(COREADI)
    os.environ['MACADI_COOLDOWN'] = str(DATA / 'macadi/gsa-429')
    os.environ['MACADI_CA_FILE'] = str(Path(__file__).with_name('apple-root-g3.pem'))
    sys.path.insert(0, str(UPSTREAM / 'tools/machoadi'))
    from findmy.util.tls import apple_trust_context
    apple_trust_context().load_verify_locations(cafile=Path(__file__).with_name('apple-root-g3.pem'))
    import cooldown
    from mac_anisette import MacAnisette
    from provider import MacAnisetteProvider
    from resync import Resyncer
    return cooldown, MacAnisette, MacAnisetteProvider, Resyncer


def snapshot(account):
    # The provider is intentionally non-serializable; save the account separately
    # from its persistent machine directory, inside the existing encrypted store.
    return {'type': 'account', 'ids': {'uid': account._uid, 'devid': account._devid},
            'account': {'username': account._username, 'password': None, 'info': account._account_info},
            'login': {'state': account._login_state.value, 'data': account._login_state_data}, 'anisette': {}}


def save_session(account):
    atomic_write(SESSION, cipher().encrypt(json.dumps(snapshot(account)).encode()))


def install_protocol(account, resyncer, cooldown):
    inner = account._gsa_request
    from findmy.reports import account as account_module
    account_module._GSA_USER_AGENT = account._anisette.akd_user_agent

    async def request(parameters):
        cooldown.guard()
        response = await inner(parameters)
        status = response.get('Status') or {}
        if status.get('hsc') == 429:
            cooldown.note_429()
            raise UserError('Apple begränsar anropen')
        sim = status.get('X-Apple-I-MD-DATA')
        if status.get('hsc') == 434 and sim:
            outcome = await resyncer.answer_sim(sim)
            if not outcome['ok']:
                raise UserError('Apple-maskinen kunde inte synkroniseras')
            account._anisette._window = None
            response = await inner(parameters)  # one protocol resync, no password guessing
            status = response.get('Status') or {}
        # Apple's newer 2FA reply omits au; preserve the normal code verification.
        if parameters.get('o') == 'complete' and status.get('hsc') == 409 and not status.get('au'):
            status['au'] = 'trustedDeviceSecondaryAuth'
        return response

    account._gsa_request = request
    send = account._http.request

    async def transport(method, url, **kwargs):
        cooldown.guard()
        # The library can opt into transport retries for some endpoints. Keep
        # 429 visible immediately so no internal retry defeats the cooldown.
        kwargs['auto_retry'] = False
        response = await send(method, url, **kwargs)
        if response.status_code == 429:
            cooldown.note_429()
            raise UserError('Apple begränsar anropen')
        return response

    account._http.request = transport


async def authenticate(AsyncAppleAccount, LoginState, cooldown, MacAnisette, Provider, Resyncer):
    saved = SESSION.exists() and '--relogin' not in sys.argv
    # Ask before provisioning; never spend the machine's first login window on typing.
    email = password = None
    if not saved:
        email = input('Apple-konto (e-post): ').strip()
        password = getpass.getpass('Apple-lösenord: ')
    if not (MACHINE / 'identity.json').exists():
        MACHINE.mkdir(parents=True, exist_ok=True)
        print('Registrerar en emulerad Mac-identitet för din Linux-container.', flush=True)
        MacAnisette(MACHINE).provision()
    provider = Provider(MACHINE)
    identity = provider.identity_json
    if saved:
        state_info = json.loads(cipher().decrypt(SESSION.read_bytes()))
        if state_info['ids']['devid'] != identity['device_id']:
            raise UserError('Sessionen tillhör en annan maskin')
        account = AsyncAppleAccount(provider, timeout=30, state_info=state_info)
    else:
        account = AsyncAppleAccount(provider, timeout=30, uid=identity['local_user_uuid'], devid=identity['device_id'])
    try:
        resyncer = Resyncer(MACHINE, log=lambda record: None)
        resyncer.on_synced = lambda: setattr(provider, '_window', None)
        install_protocol(account, resyncer, cooldown)
        if saved:
            if account.login_state != LoginState.LOGGED_IN:
                raise UserError('Ny positionsinloggning behövs')
            print('Använder sparad, krypterad Linux-session.', flush=True)
            return account
        try:
            state = await account.login(email, password)
        finally:
            del password
        if state == LoginState.REQUIRE_2FA:
            from findmy import AsyncSmsSecondFactor, AsyncTrustedDeviceSecondFactor
            print('Välj tvåfaktorsmetod: 1 = SMS, 2 = betrodd Apple-enhet.', flush=True)
            choice = input('Metod [1]: ').strip() or '1'
            if choice == '1':
                methods = [m for m in await account.get_2fa_methods() if isinstance(m, AsyncSmsSecondFactor)]
                if not methods:
                    raise UserError('Apple erbjöd ingen SMS-metod')
                for index, method in enumerate(methods):
                    print(f'{index}: SMS {method.phone_number}', flush=True)
                selected = input('SMS-metodens nummer [0]: ').strip() or '0'
                if not selected.isdigit() or int(selected) >= len(methods):
                    raise ValueError('Ogiltig SMS-metod')
                method = methods[int(selected)]
            elif choice == '2':
                method = AsyncTrustedDeviceSecondFactor(account)
            else:
                raise ValueError('Ogiltig tvåfaktorsmetod')
            await method.request()
            code = getpass.getpass('Verifieringskod: ')
            try:
                # A human may spend minutes receiving the code. Renew machine proof
                # before submission, without retrying passwords or verification codes.
                outcome = await resyncer.ensure_fresh()
                if outcome not in ('accepted', 'revived'):
                    raise UserError('Maskinen kunde inte synkroniseras före verifiering')
                provider._window = None
                state = await method.submit(code)
            finally:
                del code
        if state != LoginState.LOGGED_IN:
            raise UserError('Inloggningen blev inte klar')
        save_session(account)
        return account
    except BaseException:
        await account.close()
        raise


async def recover_accessories(account):
    from findmy.keychain.session import AsyncKeychainSession
    from findmy.cloudkit import AsyncBeaconStore
    from tag_keys import for_cloudkit
    if not account._password:
        account._password = getpass.getpass('Apple-lösenord (behövs för att låsa upp föremålsnycklar): ')
    ck_account = for_cloudkit(account)
    session = await AsyncKeychainSession.open(ck_account)
    try:
        options = await session.recovery_options()
        records = list(options.recoverable)
        if not records:
            raise UserError('Ingen återställningsbar enhet finns i iCloud-nyckelringen')
        for index, record in enumerate(records):
            print(f'{index}: {record.describe()}', flush=True)
        selected = input('Välj enheten vars skärmlåskod du känner till [0]: ').strip() or '0'
        if not selected.isdigit() or int(selected) >= len(records):
            raise ValueError('Ogiltig enhet')
        print('Ange koden till just den valda enheten. Ett försök skickas; en felaktig kod försöks inte igen.', flush=True)
        passcode = getpass.getpass('Enhetens skärmlåskod/lösenord: ')
        try:
            peer = await session.recover(records[int(selected)], passcode)
        finally:
            del passcode
        keys = await session.pcs_keys(peer)
        store = AsyncBeaconStore(ck_account)
        try:
            accessories = await store.fetch_accessories(keys)
        finally:
            await store.close()
        # Deliberately no keychain join/enrolment, deletion or terms acceptance.
        atomic_write(KEYS, cipher().encrypt(json.dumps([a.to_json() for a in accessories]).encode()))
        return accessories
    finally:
        await session.close()


async def run():
    from findmy import AsyncAppleAccount, FindMyAccessory, LoginState
    cooldown, MacAnisette, Provider, Resyncer = load_modules()
    cooldown.guard()
    ensure_library()
    account = None
    try:
        account = await authenticate(AsyncAppleAccount, LoginState, cooldown, MacAnisette, Provider, Resyncer)
        if '--export' in sys.argv or not KEYS.exists():
            accessories = await recover_accessories(account)
        else:
            accessories = [FindMyAccessory.from_json(item) for item in json.loads(cipher().decrypt(KEYS.read_bytes()))]
        if not accessories:
            print('Inga exporterbara Hitta-föremål hittades i den dekrypterade nyckelringen.', flush=True)
            return 2
        print(f'Hämtar positioner för {len(accessories)} föremål.', flush=True)
        reports = await account.fetch_location(accessories)
        devices = []
        for accessory in accessories:
            report = reports.get(accessory)
            # A stable fallback is needed for imports without an upstream identifier.
            identity = accessory.identifier or hashlib.sha256(accessory.to_json()['master_key'].encode()).hexdigest()
            device = {'name': accessory.name or 'Hitta-föremål', 'model': accessory.model,
                      'identifier': identity, 'location': None}
            if report is not None:
                device['location'] = {'latitude': report.latitude, 'longitude': report.longitude,
                                      'accuracy': report.horizontal_accuracy, 'timestamp': report.timestamp.isoformat()}
            devices.append(device)
        from datetime import datetime, timezone
        atomic_write(DATA / 'locations.json', json.dumps({'devices': devices, 'updated_at': datetime.now(timezone.utc).isoformat()}).encode())
        atomic_write(KEYS, cipher().encrypt(json.dumps([a.to_json() for a in accessories]).encode()))
        save_session(account)
        try:
            Bridge(DATA).enqueue(devices)
        except Exception:
            print('Kartan sparades, men TCP-kön kunde inte uppdateras. Kontrollera appdata.', flush=True)
        print(f'Klart. {sum(d["location"] is not None for d in devices)} av {len(devices)} föremål har position.', flush=True)
        return 0
    finally:
        if account is not None:
            await account.close()


def main():
    try:
        return asyncio.run(run())
    except (Exception, SystemExit) as error:
        # Never print upstream exception strings: they can include account tokens.
        marker = DATA / 'macadi/gsa-429'
        if marker.exists() and time.time() - marker.stat().st_mtime < 45 * 60:
            print('Apple begränsar anropen (429). Vänta minst 45 minuter innan nästa försök.', flush=True)
        elif isinstance(error, UserError):
            print(str(error), flush=True)
        else:
            print(f'Linux-inloggning eller hämtning misslyckades ({type(error).__name__}). '
                  'Ingen ny lyckad positionshämtning kunde bekräftas. Prova Ny positionsinloggning om sessionen har gått ut.', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
