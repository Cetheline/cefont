"""Validated Alacritty changes with exact original bytes kept in the journal."""
import base64
import datetime
import json
import math
import os
import stat

from cefont.core.terminal_recovery import RecoveryError, atomic_bytes, sync_directory

try:
    import tomllib
except ImportError:  # Older Python still supports the CLI and GSettings adapters.
    tomllib = None


def _value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(_value(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(_value(key) + ' = ' + _value(item) for key, item in value.items()) + '}'
    raise ValueError('Unsupported TOML value; original configuration is unchanged')


def _same(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    return type(left) is type(right) and left == right


def read_config(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, 0o600
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RecoveryError('Configuration must be a regular file')
        return source.read(), stat.S_IMODE(info.st_mode)


def capture_alacritty(path, family, size):
    if not path or not path.endswith('.toml'):
        raise RecoveryError('Only TOML Alacritty configuration is supported; YAML is unchanged')
    if tomllib is None:
        raise RecoveryError('Alacritty configuration validation requires Python 3.11+; no file was changed')
    path = os.path.abspath(path)
    if not os.path.isdir(os.path.dirname(path)):
        raise RecoveryError('Alacritty configuration parent must exist; no directories were created')
    original, mode = read_config(path)
    parsed = tomllib.loads(original.decode('utf-8')) if original is not None else {}
    font = parsed.setdefault('font', {})
    if not isinstance(font, dict):
        raise RecoveryError('Alacritty font configuration is not a table')
    active_size = size if size is not None else font.get('size', 12)
    if isinstance(active_size, bool) or not isinstance(active_size, (int, float)) or not math.isfinite(active_size) or active_size <= 0:
        raise RecoveryError('Font size must be a positive finite number')
    normal = font.setdefault('normal', {})
    if not isinstance(normal, dict):
        raise RecoveryError('Alacritty font.normal is not a table')
    normal['family'] = family
    normal.setdefault('style', 'Regular')
    font['size'] = active_size
    # A canonical TOML representation of parsed data avoids duplicate tables,
    # including dotted/inline/nested tables and arrays of tables. Comments are
    # restored byte-for-byte on revert, but canonicalized if the user keeps it.
    generated = '\n'.join(_value(key) + ' = ' + _value(value) for key, value in parsed.items()) + '\n'
    if not _same(tomllib.loads(generated), parsed):
        raise RecoveryError('Generated TOML failed semantic validation')
    return dict(kind='alacritty', config_path=path, original_exists=original is not None,
                original_bytes=base64.b64encode(original or b'').decode('ascii'),
                applied_bytes=base64.b64encode(generated.encode('utf-8')).decode('ascii'),
                original_mode=mode, active_size=active_size,
                note='Keeping this change keeps canonical TOML formatting without comments; reverting restores the exact original file.')


def apply_alacritty(state):
    current, _ = read_config(state['config_path'])
    original = base64.b64decode(state['original_bytes']) if state['original_exists'] else None
    if current != original:
        raise RecoveryError('Alacritty configuration changed since capture; refusing to overwrite it')
    atomic_bytes(state['config_path'], base64.b64decode(state['applied_bytes']), state['original_mode'])


def restore_alacritty(state):
    current, _ = read_config(state['config_path'])
    original = base64.b64decode(state['original_bytes']) if state['original_exists'] else None
    if current == original:
        # A previous restore may have completed replacement/unlink but failed
        # its directory fsync. Retry that durability barrier before clearing
        # the only recovery record, even when the bytes already match.
        sync_directory(os.path.dirname(state['config_path']))
        return True
    if current != base64.b64decode(state['applied_bytes']):
        raise RecoveryError('Alacritty configuration was edited externally; recovery record retained')
    if original is None:
        os.unlink(state['config_path'])
        sync_directory(os.path.dirname(state['config_path']))
    else:
        atomic_bytes(state['config_path'], original, state['original_mode'])
    return read_config(state['config_path'])[0] == original
