"""Teltonika TCP bridge. No Apple credentials are used by this module."""
import hashlib
import json
import math
import re
import socket
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def position(location):
    lat, lon = float(location['latitude']), float(location['longitude'])
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Ogiltiga koordinater')
    timestamp = datetime.fromisoformat(location['timestamp'].replace('Z', '+00:00'))
    if timestamp.tzinfo is None:
        raise ValueError('Tidsstämpeln måste innehålla tidszon')
    milliseconds = int(timestamp.timestamp() * 1000)
    if not 0 <= milliseconds < 2**63:
        raise ValueError('Ogiltig tidsstämpel')
    return milliseconds, round(lon * 10**7), round(lat * 10**7)


def encode_packet(location, codec):
    if codec not in ('8', '8E'):
        raise ValueError('Välj Codec 8 eller 8E')
    timestamp, lon, lat = position(location)
    # AirTags do not report satellite counts. Three is a compatibility value
    # for Sibek's minimum-fix filter, not a measured GPS satellite count.
    record = struct.pack('>QBiiHHBH', timestamp, 0, lon, lat, 0, 0, 3, 0)
    # Event ID, total IO, N1/N2/N4/N8; Extended adds NX and uses uint16 fields.
    record += bytes(6 if codec == '8' else 14)
    data = bytes([0x08 if codec == '8' else 0x8E, 1]) + record + b'\x01'
    return bytes(4) + struct.pack('>I', len(data)) + data + struct.pack('>I', crc16(data))


def _with_satellites(packet, count):
    """Rewrite this bridge's single-record packet, including its CRC."""
    data = bytearray(packet[8:-4])
    expected = {8: 33, 142: 41}.get(data[0] if data else None)
    if (len(data) != expected or packet[:4] != bytes(4)
            or int.from_bytes(packet[4:8], 'big') != expected
            or data[1] != 1 or data[-1] != 1
            or int.from_bytes(packet[-4:], 'big') != crc16(data)):
        raise ValueError('Ogiltigt köat Teltonika-paket')
    data[23] = count
    return packet[:8] + bytes(data) + struct.pack('>I', crc16(data))


def recv_exact(sock, count):
    deadline = time.monotonic() + 10
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('TCP timeout')
        sock.settimeout(remaining)
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise ConnectionError('TCP closed')
        result.extend(chunk)
    return bytes(result)


class Rejected(ConnectionError):
    pass


class WrongAck(ConnectionError):
    pass


def send_record(host, port, imei, packet):
    # A separate TCP session per record keeps each IMEI unambiguous.
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(b'\x00\x0f' + imei.encode('ascii'))
        if recv_exact(sock, 1) != b'\x01':
            raise Rejected()
        sock.sendall(packet)
        if recv_exact(sock, 4) != b'\x00\x00\x00\x01':
            raise WrongAck()


def virtual_imei(identifier, salt=0):
    # Private ID, not a registered cellular IMEI. Include a Luhn check digit.
    value = '9' + str(int(hashlib.sha256(f'{identifier}:{salt}'.encode()).hexdigest(), 16) % 10**13).zfill(13)
    total = sum((int(c) * 2 // 10 + int(c) * 2 % 10) if i % 2 else int(c)
                for i, c in enumerate(value))
    return value + str((-total) % 10)


class Bridge:
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'teltonika.sqlite3'
        self.start_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tags (
                    identifier TEXT PRIMARY KEY, name TEXT NOT NULL, imei TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                    host TEXT NOT NULL, port INTEGER NOT NULL, imei TEXT NOT NULL,
                    packet BLOB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, sent_at REAL, error TEXT);
                CREATE INDEX IF NOT EXISTS queue_pending ON queue(next_attempt, id) WHERE sent_at IS NULL;
                CREATE INDEX IF NOT EXISTS queue_device ON queue(host, port, imei, id) WHERE sent_at IS NULL;
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)',
                       (json.dumps({'enabled': False, 'host': '', 'port': 5027, 'codec': '8E'}),))
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _tags(self, db, devices):
        for device in devices:
            identifier = device.get('identifier')
            if not isinstance(identifier, str) or not identifier:
                continue
            row = db.execute('SELECT imei FROM tags WHERE identifier=?', (identifier,)).fetchone()
            if row is None:
                salt = 0
                while db.execute('SELECT 1 FROM tags WHERE imei=?', (virtual_imei(identifier, salt),)).fetchone():
                    salt += 1
                db.execute('INSERT INTO tags VALUES (?, ?, ?)',
                           (identifier, str(device.get('name') or identifier), virtual_imei(identifier, salt)))
            else:
                db.execute('UPDATE tags SET name=? WHERE identifier=?',
                           (str(device.get('name') or identifier), identifier))

    def config(self):
        with self.db() as db:
            value = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
            value['tags'] = [dict(row) for row in db.execute('SELECT * FROM tags ORDER BY name, identifier')]
            return value

    def save_config(self, value):
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError('Aktiverad måste vara sant eller falskt')
        host, port, codec = value.get('host'), value.get('port'), value.get('codec')
        if not isinstance(host, str) or len(host) > 253 or (host and not re.fullmatch(r'[A-Za-z0-9_.:\-]+', host)):
            raise ValueError('Ange ett värdnamn eller IP utan http:// eller port')
        if value['enabled'] and not host:
            raise ValueError('Ange serveradress')
        if type(port) is not int or not 1 <= port <= 65535 or codec not in ('8', '8E'):
            raise ValueError('Ogiltig TCP-port eller codec')
        tags = value.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 1000:
            raise ValueError('Ogiltig föremålslista')
        mapping = {}
        for tag in tags:
            if not isinstance(tag, dict) or not isinstance(tag.get('identifier'), str) or not isinstance(tag.get('imei'), str) or not re.fullmatch(r'[0-9]{15}', tag['imei']):
                raise ValueError('Varje ID måste bestå av exakt 15 siffror')
            if tag['identifier'] in mapping:
                raise ValueError('Dubblerat föremål')
            mapping[tag['identifier']] = tag['imei']
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {r['identifier']: r for r in db.execute('SELECT * FROM tags')}
            if any(identifier not in existing for identifier in mapping):
                raise ValueError('Okänt föremål; ladda om inställningarna')
            final = {key: mapping.get(key, row['imei']) for key, row in existing.items()}
            if len(set(final.values())) != len(final):
                raise ValueError('Varje föremål måste ha ett unikt 15-siffrigt ID')
            # Permit swapping IDs while preserving the UNIQUE constraint.
            for key in mapping:
                db.execute('UPDATE tags SET imei=? WHERE identifier=?', ('pending:' + key, key))
            for key, imei in mapping.items():
                db.execute('UPDATE tags SET imei=? WHERE identifier=?', (imei, key))
            settings = {key: value[key] for key in ('enabled', 'host', 'port', 'codec')}
            db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(settings),))

    def enqueue(self, devices):
        with self.db() as db:
            # Serialize configuration, ID allocation and inserts across processes.
            db.execute('BEGIN IMMEDIATE')
            self._tags(db, devices)
            cfg = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
            if not cfg['enabled']:
                return
            for device in devices:
                if device.get('selected') is False:
                    continue
                location = device.get('location')
                tag = db.execute('SELECT imei FROM tags WHERE identifier=?', (device.get('identifier'),)).fetchone()
                if not location or tag is None:
                    continue
                try:
                    packet = encode_packet(location, cfg['codec'])
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                imei = tag['imei']
                fingerprint = hashlib.sha256(json.dumps([cfg['host'], cfg['port'], imei,
                    device['identifier']]).encode() + _with_satellites(packet, 0)).hexdigest()
                db.execute('INSERT OR IGNORE INTO queue (fingerprint,host,port,imei,packet) VALUES (?,?,?,?,?)',
                           (fingerprint, cfg['host'], cfg['port'], imei, packet))

    def reconcile(self):
        path = self.data / 'locations.json'
        if path.exists():
            self.enqueue(json.loads(path.read_text()).get('devices', []))

    def status(self):
        with self.db() as db:
            counts = db.execute('SELECT count(*) AS total, count(sent_at) AS sent FROM queue').fetchone()
            failure = db.execute('SELECT error FROM queue WHERE sent_at IS NULL AND error IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
            last = db.execute('SELECT max(sent_at) FROM queue').fetchone()[0]
            return {'pending': counts['total'] - counts['sent'], 'sent': counts['sent'],
                    'last_sent': last, 'error': failure['error'] if failure else None}

    def dispatch_one(self):
        if not self.config()['enabled']:
            return False
        with self.db() as db:
            # Retain order within a device/endpoint, allow other devices to proceed.
            row = db.execute('''SELECT * FROM queue q WHERE sent_at IS NULL AND next_attempt<=?
                AND NOT EXISTS (SELECT 1 FROM queue older WHERE older.sent_at IS NULL
                AND older.host=q.host AND older.port=q.port AND older.imei=q.imei AND older.id<q.id)
                ORDER BY id LIMIT 1''', (time.time(),)).fetchone()
        if row is None:
            return False
        try:
            # Upgrade pending packets too; keep their endpoint, ID and timestamp.
            # Fingerprints retain the original zero-satellite representation so
            # an update does not requeue already acknowledged reports.
            send_record(row['host'], row['port'], row['imei'], _with_satellites(row['packet'], 3))
        except (OSError, Rejected, WrongAck) as error:
            message = ('Servern avvisade ID:t' if isinstance(error, Rejected) else
                       'Servern kvitterade inte en post' if isinstance(error, WrongAck) else
                       'TCP-anslutning eller kvittens misslyckades')
            delay = min(300, 5 * 2**min(row['attempts'], 6))
            with self.db() as db:
                db.execute('UPDATE queue SET attempts=attempts+1,next_attempt=?,error=? WHERE id=?',
                           (time.time() + delay, message, row['id']))
        else:
            with self.db() as db:
                db.execute('UPDATE queue SET sent_at=?,error=NULL WHERE id=?', (time.time(), row['id']))
        return True

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.reconcile()
                for _ in range(50):
                    if self.stop_event.is_set() or not self.dispatch_one():
                        break
            except (OSError, sqlite3.Error, ValueError):
                # Retry local IO failures too. Never log location payloads.
                pass
            self.stop_event.wait(5)

    def start(self):
        # Gunicorn calls this after loading the worker; never run multiple workers.
        with self.start_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
