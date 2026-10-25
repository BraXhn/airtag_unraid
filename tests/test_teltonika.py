import socket
import struct
import threading
import time
from datetime import datetime, timezone

import pytest

from teltonika import Bridge, crc16, encode_packet, recv_exact, virtual_imei


LOCATION = {'latitude': -33.865143, 'longitude': 151.2099,
            'timestamp': '2026-10-25T08:30:00+00:00'}
DEVICES = [{'identifier': 'tag-1', 'name': 'Nycklar', 'location': LOCATION}]


@pytest.mark.parametrize('codec,codec_id,size,io_size', [('8', 8, 33, 6), ('8E', 142, 41, 14)])
def test_packet_fields(codec, codec_id, size, io_size):
    packet = encode_packet(LOCATION, codec)
    assert packet[:4] == bytes(4)
    assert struct.unpack('>I', packet[4:8])[0] == size
    data = packet[8:-4]
    assert data[:2] == bytes([codec_id, 1]) and data[-1] == 1
    assert struct.unpack('>Q', data[2:10])[0] == int(datetime(2026, 10, 25, 8, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert data[10] == 0  # priority
    assert struct.unpack('>iiHHBH', data[11:26]) == (1512099000, -338651430, 0, 0, 3, 0)
    assert data[26:-1] == bytes(io_size)
    assert int.from_bytes(packet[-4:], 'big') == crc16(data)
    # Independently published Teltonika sample verifies CRC algorithm/init.
    sample = bytes.fromhex('08010000016B40D8EA30010000000000000000000000000000000105021503010101425E0F01F10000601A014E000000000000000001')
    assert crc16(sample) == 0xC7CF


@pytest.mark.parametrize('bad', [dict(LOCATION, latitude=91), dict(LOCATION, longitude=float('nan')),
                                dict(LOCATION, timestamp='2026-10-25T00:00:00')])
def test_invalid_positions(bad):
    with pytest.raises(ValueError):
        encode_packet(bad, '8E')


def enable(bridge, **changes):
    cfg = bridge.config()
    cfg.update(enabled=True, host='127.0.0.1', port=5027, codec='8E')
    cfg.update(changes)
    bridge.save_config(cfg)


@pytest.mark.parametrize('codec', ['8', '8E'])
def test_legacy_queue_uses_three_satellites_without_requeue(tmp_path, monkeypatch, codec):
    import teltonika
    import hashlib
    import json
    bridge = Bridge(tmp_path)
    enable(bridge, codec=codec)
    packet = encode_packet(LOCATION, codec)
    # Construct the packet and fingerprint used before the compatibility update.
    data = bytearray(packet[8:-4])
    data[23] = 0
    legacy = packet[:8] + data + struct.pack('>I', crc16(data))
    imei = virtual_imei('tag-1')
    fingerprint = hashlib.sha256(json.dumps(['127.0.0.1', 5027, imei, 'tag-1']).encode() + legacy).hexdigest()
    with bridge.db() as db:
        db.execute('INSERT INTO queue(fingerprint,host,port,imei,packet) VALUES(?,?,?,?,?)',
                   (fingerprint, '127.0.0.1', 5027, imei, legacy))
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 1
    sent = []
    monkeypatch.setattr(teltonika, 'send_record', lambda *args: sent.append(args))
    assert bridge.dispatch_one()
    assert sent == [('127.0.0.1', 5027, imei, packet)]
    bridge = Bridge(tmp_path)
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 0
    assert bridge.status()['sent'] == 1


def test_config_and_stable_ids(tmp_path):
    bridge = Bridge(tmp_path)
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 0
    cfg = bridge.config()
    imei = cfg['tags'][0]['imei']
    assert imei == virtual_imei('tag-1') and len(imei) == 15 and imei.isascii() and imei.isdigit()
    assert Bridge(tmp_path).config()['tags'] == cfg['tags']
    enable(bridge)
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 1
    cfg = bridge.config()
    cfg['tags'][0]['imei'] = '123'
    with pytest.raises(ValueError): bridge.save_config(cfg)
    assert bridge.config()['tags'][0]['imei'] == imei
    bridge.enqueue([{'identifier': 'tag-2', 'name': 'Väska'}])
    cfg = bridge.config()
    cfg['tags'][1]['imei'] = cfg['tags'][0]['imei']
    with pytest.raises(ValueError): bridge.save_config(cfg)


def test_queue_dedup_pause_retry_restart_and_endpoint_snapshot(tmp_path, monkeypatch):
    import teltonika
    bridge = Bridge(tmp_path)
    enable(bridge)
    bridge.enqueue(DEVICES)
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 1
    attempts = []
    def fail(*args):
        attempts.append(args)
        raise TimeoutError()
    monkeypatch.setattr(teltonika, 'send_record', fail)
    assert bridge.dispatch_one()
    assert bridge.status()['pending'] == 1 and bridge.status()['error']
    assert not bridge.dispatch_one()  # backoff
    enable(bridge, enabled=False, host='other.example')
    assert not bridge.dispatch_one()
    with bridge.db() as db: db.execute('UPDATE queue SET next_attempt=0')
    bridge = Bridge(tmp_path)
    enable(bridge, host='other.example')
    monkeypatch.setattr(teltonika, 'send_record', lambda *args: attempts.append(args))
    assert bridge.dispatch_one()
    assert attempts[-1][0] == '127.0.0.1'  # never silently redirect pending telemetry
    assert bridge.status()['sent'] == 1 and not bridge.status()['error']
    enable(bridge)
    bridge.enqueue(DEVICES)
    assert bridge.status()['pending'] == 0  # acknowledged fingerprint survives restart
    bridge.enqueue([dict(DEVICES[0], location=dict(LOCATION, timestamp='2026-10-25T08:31:00Z'))])
    assert bridge.status()['pending'] == 1


@pytest.mark.parametrize('codec,reply,expected_sent', [('8', b'\0\0\0\1', 1), ('8E', b'\0\0\0\1', 1),
                                                    ('8E', b'\0\0\0\0', 0), ('8', b'', 0)])
def test_real_tcp_receiver_fragmented_ack(tmp_path, codec, reply, expected_sent):
    received, errors = [], []
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        server.settimeout(3)
        def receiver():
            try:
                conn, _ = server.accept()
                with conn:
                    size = int.from_bytes(recv_exact(conn, 2), 'big')
                    received.append(recv_exact(conn, size).decode())
                    conn.sendall(b'\x01')
                    header = recv_exact(conn, 8)
                    received.append(header + recv_exact(conn, int.from_bytes(header[4:], 'big') + 4))
                    for byte in reply:
                        conn.sendall(bytes([byte]))
                        time.sleep(.005)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=receiver)
        thread.start()
        bridge = Bridge(tmp_path)
        enable(bridge, port=server.getsockname()[1], codec=codec)
        bridge.enqueue(DEVICES)
        assert bridge.dispatch_one()
        thread.join(timeout=4)
    assert not errors and not thread.is_alive()
    assert received == [virtual_imei('tag-1'), encode_packet(LOCATION, codec)]
    assert bridge.status()['sent'] == expected_sent
    assert bridge.status()['pending'] == 1 - expected_sent


def test_rejected_imei_is_not_delivered(tmp_path, monkeypatch):
    from teltonika import Rejected
    import teltonika
    bridge = Bridge(tmp_path)
    enable(bridge)
    bridge.enqueue(DEVICES)
    def reject(*args): raise Rejected()
    monkeypatch.setattr(teltonika, 'send_record', reject)
    bridge.dispatch_one()
    assert bridge.status()['sent'] == 0
    assert bridge.status()['error'] == 'Servern avvisade ID:t'


def test_unselected_cached_location_is_not_queued(tmp_path):
    bridge=Bridge(tmp_path)
    enable(bridge)
    bridge.enqueue([dict(DEVICES[0],selected=False)])
    assert bridge.status()['pending']==0
    bridge.enqueue([dict(DEVICES[0],selected=True)])
    assert bridge.status()['pending']==1
