"""Persistent per-accessory preferences and a strictly public metadata view."""
import hashlib
import json
from pathlib import Path

from storage import atomic_write, cipher


def accessory_id(value):
    return value.get('identifier') or hashlib.sha256(value['master_key'].encode()).hexdigest()


class DeviceSelection:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / 'device-selection.json'

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'all_devices': True, 'identifiers': []}

    def catalogue(self):
        path = self.data / 'mac-accessories.enc'
        if not path.exists():
            return []
        values = json.loads(cipher().decrypt(path.read_bytes()))
        # Never return the accessory mappings: they contain private keys.
        return [{'identifier': accessory_id(value), 'name': value.get('name') or 'Hitta-föremål',
                 'model': value.get('model')} for value in values]

    def state(self):
        config = self.read()
        devices = self.catalogue()
        for device in devices:
            device['selected'] = config['all_devices'] or device['identifier'] in config['identifiers']
        return {**config, 'devices': devices}

    def save(self, value):
        if not isinstance(value, dict) or type(value.get('all_devices')) is not bool:
            raise ValueError('Ange om alla föremål ska hämtas')
        identifiers = value.get('identifiers')
        if not isinstance(identifiers, list) or len(identifiers) > 1000 or any(not isinstance(i, str) for i in identifiers):
            raise ValueError('Ogiltigt föremålsurval')
        known = {device['identifier'] for device in self.catalogue()}
        if not set(identifiers) <= known:
            raise ValueError('Okänt föremål. Ladda om listan och försök igen.')
        config = {'all_devices': value['all_devices'],
                  'identifiers': [] if value['all_devices'] else sorted(set(identifiers))}
        atomic_write(self.path, json.dumps(config).encode())

    def filter(self, accessories):
        config = self.read()
        return [a for a in accessories if config['all_devices'] or accessory_id(a.to_json()) in config['identifiers']]
