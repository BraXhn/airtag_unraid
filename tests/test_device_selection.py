import json
from types import SimpleNamespace

import pytest

from device_selection import DeviceSelection, accessory_id
from storage import cipher


@pytest.fixture
def selection(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD','ci-pass-1234')
    values=[{'identifier':'one','name':'Nycklar','model':'AirTag','master_key':'private-key-one','skn':'secret-one'},
            {'identifier':'two','name':'Väska','model':'AirTag','master_key':'private-key-two','skn':'secret-two'}]
    (tmp_path/'mac-accessories.enc').write_bytes(cipher().encrypt(json.dumps(values).encode()))
    return DeviceSelection(tmp_path),values,tmp_path


def test_catalogue_contains_only_safe_fields(selection):
    store,values,path=selection
    state=store.state()
    assert all(device['selected'] for device in state['devices'])
    assert set(state['devices'][0])=={'identifier','name','model','selected'}
    result=json.dumps(state)
    for secret in ('private-key-one','private-key-two','secret-one','secret-two','master_key','skn'):
        assert secret not in result


def test_selection_persists_and_excludes_new_devices(selection):
    store,values,path=selection
    store.save({'all_devices':False,'identifiers':['two']})
    tags=[SimpleNamespace(to_json=lambda value=value:value) for value in values]
    assert store.filter(tags)==[tags[1]]
    restored=DeviceSelection(path)
    assert restored.read()['identifiers']==['two']
    values.append({'identifier':'three','name':'New','master_key':'private-key-three'})
    (path/'mac-accessories.enc').write_bytes(cipher().encrypt(json.dumps(values).encode()))
    assert next(d for d in restored.state()['devices'] if d['identifier']=='three')['selected'] is False
    store.save({'all_devices':True,'identifiers':[]})
    assert all(d['selected'] for d in store.state()['devices'])


def test_empty_selection_and_invalid_ids(selection):
    store,values,path=selection
    store.save({'all_devices':False,'identifiers':[]})
    assert store.filter([SimpleNamespace(to_json=lambda:values[0])])==[]
    for value in ({'all_devices':False,'identifiers':['unknown']},
                  {'all_devices':True,'identifiers':'one'}, {'all_devices':'yes','identifiers':[]}):
        with pytest.raises(ValueError): store.save(value)


def test_fallback_identifier_is_stable_when_alignment_changes():
    first={'master_key':'private-but-stable','alignment_index':10,'name':'old'}
    assert accessory_id(first)==accessory_id(dict(first,alignment_index=500,name='renamed'))
