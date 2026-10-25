"""Apply the small, fail-closed transport patch to the pinned emulator source."""
from pathlib import Path
import sys


def patch(root):
    path = Path(root) / 'tools/machoadi/gsa.py'
    text = path.read_text()
    old = ('        self._ctx = ssl.create_default_context()\n'
           '        self._ctx.check_hostname = False\n'
           '        self._ctx.verify_mode = ssl.CERT_NONE')
    new = ('        from findmy.util.tls import apple_trust_context\n'
           '        self._ctx = apple_trust_context()\n'
           '        import os\n'
           '        self._ctx.load_verify_locations(cafile=os.environ["MACADI_CA_FILE"])')
    if text.count(old) != 1:
        raise RuntimeError('Pinned emulator TLS code changed; review before building')
    path.write_text(text.replace(old, new))
    path = Path(root) / 'tools/machoadi/cooldown.py'
    text = path.read_text()
    old = 'MARKER = Path("/tmp/macadi/gsa-429")'
    if text.count(old) != 1:
        raise RuntimeError('Pinned cooldown code changed; review before building')
    path.write_text(text.replace(old, 'import os\nMARKER = Path(os.environ.get("MACADI_COOLDOWN", "/data/macadi/gsa-429"))'))


if __name__ == '__main__':
    patch(sys.argv[1])
