"""Refresh the per-user accessory catalogue, with complete paging and safe counts."""
import base64
import json
import time
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from device_selection import accessory_id
from storage import atomic_write, cipher


class InventoryError(RuntimeError):
    pass


class Inventory:
    def __init__(self, data):
        self.data = Path(data)
        self.keys = self.data / 'inventory-zone-keys.enc'
        self.status = self.data / 'inventory-status.json'
        self.accessories = self.data / 'mac-accessories.enc'

    def state(self):
        value = json.loads(self.status.read_text()) if self.status.exists() else {}
        return {**value, 'refresh_available': self.keys.exists()}

    def due(self, automatic=False):
        return self.keys.exists() and (not automatic or time.time() - self.state().get('checked_at', 0) >= 3600)

    def load_keys(self):
        values = json.loads(cipher().decrypt(self.keys.read_bytes()))
        keys = [serialization.load_der_private_key(base64.b64decode(value), password=None) for value in values]
        if not keys or any(not isinstance(key, ec.EllipticCurvePrivateKey) for key in keys):
            raise InventoryError('Föremålslistans nycklar behöver förnyas. Välj Anslut Apple-konto.')
        return keys

    def save_keys(self, keys):
        values = [base64.b64encode(key.private_bytes(serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode() for key in keys]
        atomic_write(self.keys, cipher().encrypt(json.dumps(values).encode()))

    async def refresh(self, store, zone_keys=None):
        try:
            return await self._refresh(store, zone_keys)
        except BaseException:
            # Keep the last successful catalogue and its timestamp. No upstream
            # exception text, record IDs or key material belongs in the public status.
            value = self.state()
            value.update(last_attempt=time.time(), failed=True)
            atomic_write(self.status, json.dumps(value).encode())
            raise

    async def _refresh(self, store, zone_keys):
        from findmy import FindMyAccessory
        from findmy.cloudkit import (BEACON_STORE_ZONE, accessories_from_records,
            can_be_located, decrypt_records, group_records, records_from_changes)

        keys = zone_keys if zone_keys is not None else self.load_keys()
        changes, token, seen = [], None, set()
        # The upstream iterator only logs missing/repeated cursors and returns a
        # truncated result. Require an explicit complete page before saving anything.
        for page_number in range(1, 101):
            page = await store._client.record_sync(BEACON_STORE_ZONE,
                continuation_token=token, max_changes=200)
            changes.extend(page.changes)
            print(f'Föremålslista: sida {page_number}, totalt {len(changes)} CloudKit-poster.', flush=True)
            if page.complete:
                break
            next_token = page.continuation_token
            if not next_token or next_token in seen:
                raise InventoryError('Apple gav en ofullständig föremålslista utan fungerande fortsättning. Den sparade listan behålls.')
            seen.add(next_token)
            token = next_token
        else:
            raise InventoryError('Föremålslistan blev inte färdig efter 100 sidor. Den sparade listan behålls.')

        records = records_from_changes(changes, BEACON_STORE_ZONE)
        decrypted = decrypt_records(records, keys, default_master_keys=await store.zone_record_defaults(keys))
        groups = group_records(decrypted)
        locatable = sum(can_be_located(group.beacon) for group in groups)
        fresh = accessories_from_records(decrypted)
        old = json.loads(cipher().decrypt(self.accessories.read_bytes())) if self.accessories.exists() else []
        previous = {accessory_id(value): value for value in old}
        merged = dict(previous)
        for accessory in fresh:
            value = accessory.to_json()
            identity = accessory_id(value)
            prior = previous.get(identity, {})
            # CloudKit may carry an older alignment than our completed location
            # fetches. Preserve that work when the underlying master key is unchanged.
            if prior.get('master_key') == value.get('master_key') and prior.get('alignment_date'):
                before = datetime.fromisoformat(prior['alignment_date'])
                current = datetime.fromisoformat(value['alignment_date']) if value.get('alignment_date') else None
                if current is None or before > current:
                    value.update(alignment_date=prior['alignment_date'], alignment_index=prior['alignment_index'])
            merged[identity] = value
        values = list(merged.values())
        result = [FindMyAccessory.from_json(value) for value in values]
        skipped = getattr(decrypted, 'skipped', {})
        unreadable = skipped.get('no key held', 0) + skipped.get('would not decrypt', 0)
        fresh_ids = {accessory_id(accessory.to_json()) for accessory in fresh}
        status = {'checked_at': time.time(), 'failed': False, 'pages': page_number,
                  'cloudkit_records': len(records), 'imported': len(fresh),
                  'available': len(values), 'added': len(fresh_ids - previous.keys()),
                  'retained': len(previous.keys() - fresh_ids),
                  'unreadable_records': unreadable, 'invalid_accessories': locatable - len(fresh),
                  'without_private_key': len(groups) - locatable,
                  'partial': bool(unreadable or locatable != len(fresh))}
        # Cache only the accessory zone's keys, never broad iCloud Keychain service
        # keys. This allows discovering new items without another passcode prompt.
        self.save_keys(keys)
        atomic_write(self.accessories, cipher().encrypt(json.dumps(values).encode()))
        atomic_write(self.status, json.dumps(status).encode())
        print(f'Föremålslista klar: {len(fresh)} importerade, {status["added"]} nya, '
              f'{len(values)} tillgängliga. {unreadable} poster kunde inte dekrypteras; '
              f'{status["invalid_accessories"]} föremål kunde inte konverteras.', flush=True)
        if status['partial']:
            print('Listan kan vara ofullständig. Tidigare importerade föremål behålls. '
                  'Välj Anslut Apple-konto för att förnya föremålsnycklarna.', flush=True)
        if status['retained']:
            print(f'{status["retained"]} tidigare föremål saknades i importen och behölls i listan.', flush=True)
        return result
