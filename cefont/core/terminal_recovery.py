"""Durable, exclusively owned terminal recovery records (POSIX)."""
import fcntl
import json
import os
import stat
import tempfile


class RecoveryError(RuntimeError):
    """Persistent state could not be safely changed or recovered."""


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path, content, mode=0o600):
    parent = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix='.cefont-', dir=parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RecoveryStore:
    """One global lock conservatively serializes all persistent terminal changes.

    Never unlink the lock inode. Every writer/remover/recovery path holds it.
    A unique owner token additionally prevents stale contexts clearing new data.
    """
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.descriptor = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        descriptor = os.open(self.path + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise RecoveryError('Another terminal change or recovery session owns the recovery lock')
        self.descriptor = descriptor
        return self

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def read(self):
        if self.descriptor is None:
            raise RecoveryError('Recovery lock is not held')
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise RecoveryError('Recovery record is not an owned regular file')
            record = json.load(source)
        if not isinstance(record, dict):
            raise RecoveryError('Invalid recovery record; preserved for manual recovery')
        return record

    def write(self, record):
        previous = self.read()
        owner = record.get('owner')
        if not isinstance(owner, str) or not owner:
            raise RecoveryError('A recovery owner token is required')
        if previous is not None and previous.get('owner') != owner:
            raise RecoveryError('Cannot overwrite another recovery record')
        atomic_bytes(self.path, json.dumps(record, ensure_ascii=True).encode('utf-8'))

    def clear(self, owner):
        previous = self.read()
        if previous is None:
            return
        if not owner or previous.get('owner') != owner:
            raise RecoveryError('Cannot clear another session recovery record')
        os.unlink(self.path)
        sync_directory(os.path.dirname(self.path))
