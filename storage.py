"""Shared encrypted storage, with no Apple client or process side effects."""
import base64
import hashlib
import os

from cryptography.fernet import Fernet


def atomic_write(path, content):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(content)
    tmp.chmod(0o600)
    tmp.replace(path)


def cipher(password=None):
    key = hashlib.sha256(('airtag-session-v1:' + (password if password is not None else os.environ['APP_PASSWORD'])).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))
