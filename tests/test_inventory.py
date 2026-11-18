import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from findmy.cloudkit import accessories_from_records
from findmy.cloudkit.beacons import DecryptedRecord, DecryptedRecords
from findmy.cloudkit.client import RecordSyncPage, SYNC_STATUS_COMPLETE
from findmy.cloudkit.constants import RecordType
from findmy.cloudkit.proto import cloudkit_pb2 as ck

from inventory import Inventory, InventoryError
from storage import cipher


def beacon(number):
    return DecryptedRecord(name=f'tag-{number}', record_type=RecordType.MASTER_BEACON,
        values={'privateKey': (number+1).to_bytes(32,'big'), 'sharedSecret': bytes(range(32)),
                'sharedSecret2': bytes(range(32,64)), 'pairingDate':datetime(2026,1,1,tzinfo=timezone.utc),
                'model':'AirTag1,1'})


def tag(number):
    return accessories_from_records([beacon(number)])[0]


def change(number):
    return ck.RecordChange(record=ck.Record(etag=f'e-{number}',
        record_identifier=ck.RecordIdentifier(value=ck.Identifier(name=f'tag-{number}')),
        type=ck.NameWrapper(name=RecordType.MASTER_BEACON)))


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD','inventory-test-password')
    return Inventory(tmp_path)


class Store:
    def __init__(self,pages):
        self.pages,self.calls=pages,[]
        self._client=self
    async def record_sync(self,zone,**kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls)-1]
    async def zone_record_defaults(self,keys):return []


def decoder(monkeypatch, skipped=None):
    def decrypt(records,keys,**kwargs):
        return DecryptedRecords([beacon(int(r.name.split('-')[1])) for r in records],skipped=skipped)
    monkeypatch.setattr('findmy.cloudkit.decrypt_records',decrypt)


def test_import_more_than_twenty_across_pages_and_add_new_tags(inventory,monkeypatch):
    decoder(monkeypatch)
    old=[tag(i).to_json() for i in range(20)]
    old[0].update(alignment_date='2026-11-17T12:00:00+00:00',alignment_index=12345)
    inventory.accessories.write_bytes(cipher().encrypt(json.dumps(old).encode()))
    first=RecordSyncPage([change(i) for i in range(20)],b'next-page',None)
    second=RecordSyncPage([change(i) for i in range(20,31)],b'end',SYNC_STATUS_COMPLETE)
    store=Store([first,second]);key=ec.generate_private_key(ec.SECP224R1())
    result=asyncio.run(inventory.refresh(store,[key]))
    assert len(result)==31
    assert {a.identifier for a in result}=={f'tag-{i}' for i in range(31)}
    assert store.calls[0]['continuation_token'] is None
    assert store.calls[1]['continuation_token']==b'next-page'
    assert inventory.state()['pages']==2 and inventory.state()['added']==11
    assert inventory.state()['imported']==31 and not inventory.state()['partial']
    assert result[0].to_json()['alignment_index']==12345
    assert inventory.load_keys()[0].private_numbers()==key.private_numbers()
    assert inventory.keys.stat().st_mode & 0o777 == 0o600
    assert b'PRIVATE KEY' not in inventory.keys.read_bytes()
    assert inventory.due() and not inventory.due(automatic=True)
    # A later run starts from the full zone again and discovers another item.
    store=Store([RecordSyncPage([change(i) for i in range(32)],None,SYNC_STATUS_COMPLETE)])
    assert len(asyncio.run(inventory.refresh(store)))==32
    assert inventory.state()['added']==1


@pytest.mark.parametrize('tokens',[(None,), (b'same',b'same'),(b'a',b'b',b'a')])
def test_incomplete_or_cyclic_pages_keep_previous_catalogue(inventory,tokens):
    original=cipher().encrypt(json.dumps([tag(0).to_json()]).encode())
    inventory.accessories.write_bytes(original)
    pages=[RecordSyncPage([change(i)],token,None) for i,token in enumerate(tokens)]
    with pytest.raises(InventoryError,match='ofullständig'):
        asyncio.run(inventory.refresh(Store(pages),[ec.generate_private_key(ec.SECP224R1())]))
    assert inventory.accessories.read_bytes()==original
    assert inventory.state()['failed'] is True
    assert not inventory.keys.exists()


def test_page_limit_is_error_not_partial_success(inventory):
    pages=[RecordSyncPage([],str(i).encode(),None) for i in range(100)]
    with pytest.raises(InventoryError,match='100 sidor'):
        asyncio.run(inventory.refresh(Store(pages),[]))
    assert not inventory.accessories.exists() and inventory.state()['failed']


def test_partial_decryption_reports_counts_and_preserves_existing_tags(inventory,monkeypatch,capsys):
    original=[tag(0).to_json(),tag(1).to_json()]
    inventory.accessories.write_bytes(cipher().encrypt(json.dumps(original).encode()))
    def decrypt(*args,**kwargs):
        return DecryptedRecords([beacon(2)],skipped={'no key held':2,'OTHER-PRIVATE-RECORD-TYPE':3})
    monkeypatch.setattr('findmy.cloudkit.decrypt_records',decrypt)
    result=asyncio.run(inventory.refresh(Store([RecordSyncPage([change(2)],None,SYNC_STATUS_COMPLETE)]),
                                       [ec.generate_private_key(ec.SECP224R1())]))
    state=inventory.state()
    assert len(result)==3 and state['imported']==1 and state['retained']==2
    assert state['unreadable_records']==2 and state['partial']
    assert 'OTHER-PRIVATE' not in json.dumps(state) and 'master_key' not in json.dumps(state)
    assert 'OTHER-PRIVATE' not in capsys.readouterr().out


def test_network_failure_keeps_previous_keys_and_success_timestamp(inventory):
    inventory.save_keys([ec.generate_private_key(ec.SECP224R1())])
    keys=inventory.keys.read_bytes()
    inventory.status.write_text('{"checked_at":1000,"imported":25}')
    class Broken(Store):
        async def record_sync(self,*args,**kwargs):raise TimeoutError('private-token-do-not-log')
    with pytest.raises(TimeoutError):asyncio.run(inventory.refresh(Broken([])))
    assert inventory.keys.read_bytes()==keys
    assert inventory.state()['checked_at']==1000 and inventory.state()['failed']
    assert 'private-token' not in inventory.status.read_text()


def test_autorefresh_rate_is_hourly_and_legacy_install_needs_preparation(inventory,monkeypatch):
    assert not inventory.due() and not inventory.state()['refresh_available']
    inventory.save_keys([ec.generate_private_key(ec.SECP224R1())])
    inventory.status.write_text('{"checked_at":1000}')
    monkeypatch.setattr('inventory.time.time',lambda:4599)
    assert not inventory.due(automatic=True) and inventory.due()
    monkeypatch.setattr('inventory.time.time',lambda:4600)
    assert inventory.due(automatic=True)
