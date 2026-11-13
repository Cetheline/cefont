"""Locked, versioned configuration snapshots and atomic publication (POSIX)."""
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import json
import os
import stat
import tempfile
import threading
import xml.etree.ElementTree as ET


class ConfigurationError(RuntimeError):
    pass


class ConfigurationConflict(ConfigurationError):
    """A caller tried to publish a snapshot superseded by another writer."""


class _List(list):
    pass


class _Dict(dict):
    pass


class _Set(set):
    pass


_locks = {}
_locks_guard = threading.Lock()
_held = threading.local()
_UNSPECIFIED = object()


@contextmanager
def configuration_lock(*paths):
    """Serialize cooperating writers by directory; never unlink lock inodes."""
    names = sorted({os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(p))),
                                 '.cefont-config.lock') for p in paths})
    if not names:
        yield
        return
    name, *remaining = names
    key = (os.getpid(), name)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, 'paths', None)
        if held is None:
            held = _held.paths = set()
        if key in held:
            with configuration_lock(*remaining):
                yield
            return
        os.makedirs(os.path.dirname(name), exist_ok=True)
        descriptor = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ConfigurationError('Configuration lock is not a regular file: ' + name)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            held.add(key)
            try:
                with configuration_lock(*remaining):
                    yield
            finally:
                held.remove(key)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def serialized(paths):
    """Hold the entire read/modify/write operation, including nested saves."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with configuration_lock(*paths(*args, **kwargs)):
                return function(*args, **kwargs)
        return call
    return decorate


def _read(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ConfigurationError('Configuration must be a regular file: ' + path)
        return source.read()


def _revision(raw):
    return hashlib.sha256(raw).digest() if raw is not None else None


def snapshot(value, path, revision):
    tracked = {list: _List, dict: _Dict, set: _Set}[type(value)](value)
    tracked.path = os.path.abspath(path)
    tracked.revision = revision
    return tracked


def read_json(path):
    with configuration_lock(path):
        raw = _read(path)
        try:
            value = json.loads(raw.decode('utf-8')) if raw is not None else []
            if not isinstance(value, list):
                raise ValueError('Expected a list')
        except (UnicodeError, ValueError) as error:
            raise ConfigurationError('Cannot read configuration %s: %s' % (path, error)) from error
        return snapshot(value, path, _revision(raw))


def read_xml(path):
    with configuration_lock(path):
        raw = _read(path)
        try:
            root = ET.fromstring(raw) if raw is not None else ET.Element('fontconfig')
            if root.tag != 'fontconfig':
                raise ValueError('Expected a fontconfig root')
        except (ET.ParseError, ValueError) as error:
            raise ConfigurationError('Cannot read configuration %s: %s' % (path, error)) from error
        return root, _revision(raw)


def atomic_write(path, content, original=None):
    """Replace a regular file atomically, checking tracked snapshots for conflict.

    Plain values intentionally replace the latest whole document under a lock.
    Values obtained from a loader carry a version and reject stale replacement.
    A post-replace fsync failure is reported, but publication may have happened.
    """
    if os.path.basename(path) == '.cefont-config.lock':
        raise ConfigurationError('The configuration lock cannot be used as a data file')
    with configuration_lock(path):
        expected = getattr(original, 'revision', _UNSPECIFIED)
        if expected is not _UNSPECIFIED and original.path != os.path.abspath(path):
            raise ConfigurationConflict('Snapshot belongs to a different configuration file')
        current = _read(path)
        if expected is not _UNSPECIFIED and expected != _revision(current):
            raise ConfigurationConflict('Configuration changed; reload before saving: ' + path)
        parent = os.path.dirname(os.path.abspath(path))
        if content is None:
            if current is not None:
                os.unlink(path)
                directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            if expected is not _UNSPECIFIED:
                original.revision = None
            return
        mode = stat.S_IMODE(os.stat(path).st_mode) if current is not None else 0o600
        descriptor, temporary = tempfile.mkstemp(prefix='.cefont-write-', dir=parent)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                os.fchmod(target.fileno(), mode)
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            # Refresh the snapshot so the same caller can continue sequentially.
            if expected is not _UNSPECIFIED:
                original.revision = _revision(content)
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8'), value)
