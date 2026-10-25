"""Interactive FindMy client, isolated from the HTTP server."""
import asyncio
import base64
import getpass
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from findmy import AppleAccount, FindMyAccessory, LocalAnisetteProvider, LoginState
from findmy import SmsSecondFactorMethod
from teltonika import Bridge

os.umask(0o077)
logging.disable(logging.CRITICAL)
DATA = Path(os.environ.get('DATA_DIR', '/data'))


def atomic_write(path, content):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(content)
    tmp.chmod(0o600)
    tmp.replace(path)


def cipher():
    # A high-entropy APP_PASSWORD is required; the Compose setup generates one.
    key = hashlib.sha256(('airtag-session-v1:' + os.environ['APP_PASSWORD']).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def main():
    paths = sorted((DATA / 'export' / 'keys').glob('*.json'))
    if not paths:
        print('Inga AirTag-nycklar hittades. Kör steg 1 först.', flush=True)
        return 2
    session = DATA / 'session.enc'
    account = None
    try:
        accessories = [FindMyAccessory.from_json(path) for path in paths]
        if session.exists() and '--relogin' not in sys.argv:
            account = AppleAccount.from_json(json.loads(cipher().decrypt(session.read_bytes())),
                                            anisette_libs_path=DATA / 'ani_libs.bin')
            print('Använder sparad, krypterad positionssession.', flush=True)
        else:
            account = AppleAccount(LocalAnisetteProvider(libs_path=DATA / 'ani_libs.bin'))
            email = input('Apple-konto (e-post): ')
            password = getpass.getpass('Apple-lösenord: ')
            state = account.login(email, password)
            del password
            if state == LoginState.REQUIRE_2FA:
                methods = account.get_2fa_methods()
                for i, method in enumerate(methods):
                    label = ('SMS ' + method.phone_number if isinstance(method, SmsSecondFactorMethod)
                             else 'Betrodd Apple-enhet')
                    print(f'{i}: {label}', flush=True)
                selected = int(input('Välj metodens nummer: '))
                if not 0 <= selected < len(methods):
                    raise ValueError('Ogiltig metod')
                method = methods[selected]
                method.request()
                method.submit(getpass.getpass('Verifieringskod: '))
            if account.login_state != LoginState.LOGGED_IN:
                print('Inloggningen blev inte klar.', flush=True)
                return 3
            atomic_write(session, cipher().encrypt(json.dumps(account.to_json()).encode()))

        print('Hämtar senast rapporterade positioner. Första sökningen kan ta flera minuter.', flush=True)
        reports = account.fetch_location(accessories)
        devices = []
        for accessory, path in zip(accessories, paths):
            report = reports.get(accessory)
            device = {'name': accessory.name or path.stem, 'model': accessory.model,
                      'identifier': accessory.identifier, 'location': None}
            if report is not None:
                device['location'] = {'latitude': report.latitude, 'longitude': report.longitude,
                                      'accuracy': report.horizontal_accuracy,
                                      'timestamp': report.timestamp.isoformat()}
            devices.append(device)
            atomic_write(path, json.dumps(accessory.to_json()).encode())
        atomic_write(session, cipher().encrypt(json.dumps(account.to_json()).encode()))
        result = {'devices': devices, 'updated_at': datetime.now(timezone.utc).isoformat()}
        atomic_write(DATA / 'locations.json', json.dumps(result).encode())
        try:
            Bridge(DATA).enqueue(devices)
        except Exception:
            print('Kartan sparades, men TCP-kön kunde inte uppdateras. Kontrollera lagringsutrymmet.', flush=True)
        print(f'Klart. {sum(d["location"] is not None for d in devices)} av {len(devices)} föremål har position.', flush=True)
        return 0
    except Exception as error:
        # Do not emit exception values: upstream errors can contain tokens or account data.
        print(f'Åtgärden misslyckades ({type(error).__name__}). Ingen ny karta sparades. '
              'Kontrollera nätverk och verifiering; prova sedan Ny positionsinloggning vid sessionsfel.', flush=True)
        return 1
    finally:
        if account is not None:
            loop = getattr(account, '_loop', None)
            if loop is None:
                asyncio.run(account.close())
            else:
                loop.run_until_complete(account.close())


if __name__ == '__main__':
    sys.exit(main())
