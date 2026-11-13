#!/usr/bin/env python3
"""CeFont installer backend and plain interface. The application TUI is separate.

This file deliberately imports no application modules. No operation installs,
removes, indexes, or changes fonts, user configuration, or user caches.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.parser import BytesParser
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import uuid
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
UPDATE_MESSAGE = 'Update is disabled: no update stream is configured.'
RECEIPT = 'installation.json'


class InstallError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    scope: str
    runtime: Path
    launcher: Path
    administrator: bool

    def __post_init__(self):
        if any(ord(character) < 32 or ord(character) == 127
               for path in (self.runtime, self.launcher) for character in str(path)):
            raise InstallError('Installation paths must not contain control characters.')

    @classmethod
    def select(cls, scope, prefix=None):
        if prefix is not None:
            prefix = Path(os.path.abspath(os.path.expanduser(prefix)))
            return cls(scope, prefix / 'lib/cefont', prefix / 'bin/cefont', False)
        if scope == 'system':
            return cls(scope, Path('/opt/cefont'), Path('/usr/local/bin/cefont'), True)
        home = Path.home()
        data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
        if not data.is_absolute():
            raise InstallError('XDG_DATA_HOME must be an absolute path.')
        return cls(scope, data / 'cefont/runtime', home / '.local/bin/cefont', False)


def safe_text(value):
    """Display subprocess/path text without replaying terminal commands."""
    value = re.sub(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])', '', str(value))
    return ''.join(character for character in value
                   if character in '\n\t' or not unicodedata.category(character).startswith('C'))


def cell_width(value):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
               for c in value)


def wrap_cells(value, width):
    """Wrap paths and wide Unicode names using terminal cells, not codepoints."""
    lines = []
    for paragraph in safe_text(value).expandtabs(4).split('\n'):
        for fragment in textwrap.wrap(paragraph, width) or ['']:
            line = ''
            for character in fragment:
                if line and cell_width(line + character) > width:
                    lines.append(line)
                    line = ''
                line += character
            lines.append(line)
    return lines


class Interface:
    accent = '#74b896'

    def __init__(self, plain=False, no_animation=False, theme='auto'):
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty()
        self.styled = (self.interactive and not plain and 'NO_COLOR' not in os.environ
                       and os.environ.get('TERM') != 'dumb')
        self.gum = None  # Compatibility attribute; menus no longer invoke Gum.
        self.animate = self.styled and not no_animation and sys.stderr.isatty()
        background = os.environ.get('COLORFGBG', '').rsplit(';', 1)[-1]
        self.light = theme == 'light' or (theme == 'auto' and background in ('7', '15'))
        self.accent = '#27613e' if self.light else '#74b896'

    @property
    def width(self):
        return max(12, min(78, shutil.get_terminal_size((80, 24)).columns - 2))

    def ink(self, text, bold=False):
        if not self.styled:
            return safe_text(text)
        return ('\033[1;' if bold else '\033[') + ('38;5;22m' if self.light else '38;5;108m') + safe_text(text) + '\033[0m'

    def panel(self, title, lines):
        width = self.width
        content_width = width - (4 if self.styled else 2)
        content = []
        for line in lines:
            content.extend(wrap_cells(line, max(1, content_width)))
        print()
        if self.styled:
            print(self.ink('╭' + '─' * (width - 2) + '╮'))
            for line in [*wrap_cells(title, content_width), '', *content]:
                # The terminal owns its background and body text colors.
                print(self.ink('│') + ' ' + line + ' ' * (content_width - cell_width(line)) + ' ' + self.ink('│'))
            print(self.ink('╰' + '─' * (width - 2) + '╯'))
        else:
            for line in wrap_cells(title, width):
                print(line)
            print('-' * min(width, len(title)))
            for line in content:
                print('  ' + line)
        sys.stdout.flush()

    def banner(self, prefix=None):
        import tomllib  # main() first reports the Python 3.11 minimum cleanly.
        try:
            version = tomllib.loads(regular_bytes(SOURCE / 'pyproject.toml').decode())['project']['version']
        except (OSError, ValueError, KeyError):
            version = 'unknown'
        print()
        if self.styled and self.width >= 60:
            for line in ('  ▄▀▀ ▄▀▀ █▀▀ ▄▀▄ █▀▄ ▀█▀',
                         '  █   █▀  █▀  █ █ █ █  █ ',
                         '  ▀▄▄ ▀▄▄ █   ▀▄▀ █ █  █ '):
                print(self.ink(line, bold=True))
        else:
            print(self.ink('CeFont', bold=True))
        print('  Installation manager / ' + safe_text(version))
        self.panel('Your installations', [
            'Current user: ' + scope_summary('user', prefix),
            'System-wide: ' + scope_summary('system', prefix),
            '', 'Update: unavailable (no update stream).'])
        if self.interactive:
            print('  Type a number to select. q to cancel.')

    def choose(self, title, options):
        while True:
            print('\n' + self.ink(title, bold=True))
            for index, option in enumerate(options, 1):
                print('  %d. %s' % (index, option))
            try:
                choice = input('Choose a number (q to cancel): ').strip()
            except EOFError:
                raise Cancelled() from None
            if choice.lower() in ('q', 'quit', 'exit'):
                raise Cancelled()
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                return options[int(choice) - 1]
            print('Please choose one of the listed numbers.')

    def review(self, action, layout, method='prebuilt'):
        self.route('Review')
        show_plan(action, layout, self, method)
        # The first/default selection is always the non-mutating choice.
        return self.choose('Apply the reviewed changes?', ['Cancel', 'Back', 'Apply changes'])

    def route(self, current):
        labels = [self.ink(name, bold=True) if name == current and self.styled
                  else '[' + name + ']' if name == current else name
                  for name in ('Action', 'Method', 'Scope', 'Checks', 'Review')]
        # Keep the route readable even in a split terminal pane.
        if self.width < 44:
            print('\n' + self.ink(current, bold=True))
        else:
            print('\n  ' + ' > '.join(labels), flush=True)

    def stage(self, message):
        print('\n  ' + self.ink('>', bold=True) + ' ' + safe_text(message), flush=True)

    @contextmanager
    def activity(self, title):
        """Animate independently of captured child stdout; always restore the line."""
        stopped = threading.Event()
        started = time.monotonic()
        def draw():
            frames = '|/-\\'
            index = 0
            while not stopped.is_set():
                line = '%s %s (%ds)' % (frames[index % len(frames)], title, time.monotonic() - started)
                sys.stderr.write('\r\033[2K  ' + self.ink(line[:self.width - 2]))
                sys.stderr.flush()
                index += 1
                stopped.wait(.1)
        thread = None
        if self.animate:
            thread = threading.Thread(target=draw, name='cefont-install-progress', daemon=True)
            thread.start()
        try:
            yield
        finally:
            stopped.set()
            if thread is not None:
                thread.join()
                sys.stderr.write('\r\033[2K')
                sys.stderr.flush()

    def run(self, arguments, title, cwd=None):
        self.stage(title)
        command = [str(value) for value in arguments]
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        environment.update(PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1',
                           PIP_CONFIG_FILE=os.devnull, PIP_NO_CACHE_DIR='1',
                           PIP_DISABLE_PIP_VERSION_CHECK='1')
        started = time.monotonic()
        try:
            with self.activity(title):
                result = subprocess.run(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as error:
            raise InstallError(title + ' timed out. Check available resources and retry.') from error
        if result.returncode:
            raise InstallError('%s failed (exit %d):\n%s' % (title, result.returncode, safe_text(result.stdout)[-4000:]))
        print('  ' + self.ink('OK') + ' / %.1fs' % (time.monotonic() - started), flush=True)
        return result.stdout

    def checks(self, checks):
        self.panel('Preflight / ' + ('ready' if all(c.status != 'error' for c in checks) else 'needs attention'),
                   [line for check in checks for line in
                    (['[%s] %s: %s' % (check.status.upper(), check.label, check.detail)]
                     + (['      ' + check.hint] if check.hint else []))])

    def complete(self, action, layout):
        if action == 'uninstall':
            self.panel('CeFont removed', ['The owned runtime and command were removed.',
                       'Your fonts, settings, and caches were preserved.'])
            return
        selected = shutil.which('cefont')
        in_path = str(layout.launcher.parent) in os.environ.get('PATH', '').split(os.pathsep)
        lines = ['Command: ' + str(layout.launcher), 'Runtime: ' + str(layout.runtime), '']
        if selected and os.path.abspath(selected) == str(layout.launcher):
            lines += ['PATH: this installation is selected.', 'Try: cefont --help']
        elif in_path:
            lines += ['PATH: another CeFont command may take precedence.',
                      'Try: ' + shlex.quote(str(layout.launcher)) + ' --help']
        else:
            lines += ['PATH: add ' + str(layout.launcher.parent) + ' to your shell PATH.',
                      'Try now: ' + shlex.quote(str(layout.launcher)) + ' --help']
        self.panel('CeFont is ready', lines)

    def failure(self, message):
        self.panel('Operation stopped', [safe_text(message), '',
                   'Read the failure above before retrying. Existing destinations are never overwritten.'])


def check_components(path):
    """Never redirect managed paths through symlinks, even dangling ones."""
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise InstallError('Refusing a symlink in the installation path: ' + str(component))
        if component != path and component.exists() and not component.is_dir():
            raise InstallError('Installation parent is not a directory: ' + str(component))


def validate_install(layout):
    for path in (layout.runtime, layout.launcher):
        check_components(path)
        if os.path.lexists(path):
            raise InstallError('Destination already exists; it will not be overwritten: ' + str(path))


def installation_status(layout):
    """A read-only overview, not a substitute for inventory verification."""
    try:
        check_components(layout.runtime)
        check_components(layout.launcher)
        if not os.path.lexists(layout.runtime):
            return 'command conflict; review required' if os.path.lexists(layout.launcher) else 'not installed'
        if layout.runtime.stat().st_uid != os.geteuid():
            return 'exists; administrator verification required'
        receipt = read_receipt(layout)
        return 'receipt found / %s (not yet verified)' % safe_text(receipt.get('version', 'unknown'))
    except (OSError, InstallError):
        return 'existing destination; review required'


def scope_summary(scope, prefix=None, command=False):
    try:
        layout = Layout.select(scope, prefix)
        return str(layout.launcher) if command else installation_status(layout)
    except (OSError, InstallError) as error:
        # An invalid user layout must not block a valid system installation.
        return 'unavailable: ' + safe_text(error)


@dataclass(frozen=True)
class Check:
    label: str
    status: str
    detail: str
    hint: str = ''


def validate_local_source():
    for name in ('cefont', 'data'):
        path = SOURCE / name
        if not path.is_dir() or path.is_symlink():
            raise InstallError('Build local source is unavailable: this package has no application checkout. '
                               'Use the developer package or choose Prebuilt release.')
    for name in ('pyproject.toml', 'README.md'):
        check_components(SOURCE / name)
        regular_bytes(SOURCE / name)


def bundled_application_wheel():
    folder = SOURCE / 'installer/packages'
    try:
        check_components(folder / 'manifest.json')
        manifest = json.loads(regular_bytes(folder / 'manifest.json'))
        if manifest['schema'] != 1 or not re.fullmatch(r'cefont-[A-Za-z0-9_.+-]+\.whl', manifest['file']):
            raise ValueError('Invalid release wheel filename')
        wheel = folder / manifest['file']
        check_components(wheel)
        if digest(regular_bytes(wheel)) != manifest['sha256']:
            raise InstallError('Prebuilt release wheel checksum mismatch; nothing was installed.')
        content, version = validate_wheel(wheel, manifest['sha256'])
        if version != manifest['version']:
            raise ValueError('Release version does not match wheel metadata')
        return wheel, digest(content)
    except FileNotFoundError as error:
        raise InstallError('Prebuilt release wheel is unavailable. Obtain a complete release package '
                           'or explicitly choose Build local source.') from error
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise InstallError('Invalid prebuilt release manifest or wheel; nothing was installed.') from error


def installation_methods():
    methods = {}
    for name in ('prebuilt', 'source'):
        try:
            if name == 'prebuilt':
                wheel, _ = bundled_application_wheel()
                _, version = validate_wheel(wheel)
                detail = 'Install the bundled release wheel. Local edits are not included.'
            else:
                validate_local_source()
                import tomllib
                version = tomllib.loads(regular_bytes(SOURCE / 'pyproject.toml').decode())['project']['version']
                detail = 'Build this checkout, including your local patches.'
            methods[name] = dict(available=True, version=version, detail=detail)
        except (InstallError, OSError, ValueError, KeyError) as error:
            methods[name] = dict(available=False, detail=safe_text(error))
    return methods


def python_checks(python, build=True, install=True):
    """Inspect the chosen interpreter, without installing or importing CeFont."""
    probe = '''import ctypes, importlib.util, importlib.metadata, json, re, shutil, sys
rows = [["Python", sys.version_info >= (3, 11), sys.version.split()[0]]]
if sys.argv[1] != "uninstall":
    for name in ("venv", "ensurepip"):
        rows.append([name, importlib.util.find_spec(name) is not None, "Required Python module"])
    try:
        ctypes.CDLL("libfreetype.so.6")
        rows.append(["FreeType", True, "Native library available"])
    except OSError:
        rows.append(["FreeType", False, "libfreetype.so.6 is unavailable"])
    for name in ("fc-list", "fc-cache"):
        found = shutil.which(name)
        rows.append([name, bool(found), found or "Fontconfig command not found"])
if sys.argv[1] == "source":
    try:
        version = importlib.metadata.version("setuptools")
        supported = int(re.match(r"[0-9]+", version).group()) >= 61
        rows.append(["setuptools", supported, version])
    except (importlib.metadata.PackageNotFoundError, ValueError, AttributeError):
        rows.append(["setuptools", False, "Version 61 or newer is required to build local source"])
print(json.dumps(rows))
'''
    try:
        result = subprocess.run([python, '-I', '-B', '-c', probe, 'source' if build else 'prebuilt' if install else 'uninstall'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError(safe_text(result.stderr)[-1000:])
        rows = json.loads(result.stdout)
        return [Check(label, 'ok' if ok else 'error', detail,
                      '' if ok else 'Install the missing prerequisite through your package manager, or select --python.')
                for label, ok, detail in rows]
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return [Check('Python prerequisites', 'error', str(error),
                      'Select a working Python 3.11+ interpreter with --python /path/to/python.')]


def preflight(args, layout, ui):
    checks = []
    python = shutil.which(args.python)
    if python is None:
        checks.append(Check('Python', 'error', 'Executable not found: ' + args.python,
                            'Use --python /path/to/python3.11 or newer.'))
    else:
        with ui.activity('Checking prerequisites'):
            checks.extend(python_checks(python, build=args.action == 'install' and args.method == 'source' and not args.prepared_wheel,
                                        install=args.action == 'install'))
    try:
        if args.action == 'install':
            if not args.prepared_wheel:
                if args.method == 'source':
                    validate_local_source()
                else:
                    bundled_application_wheel()
                checks.append(Check('Installation method', 'ok', 'Build local source' if args.method == 'source' else 'Prebuilt release'))
            validate_install(layout)
            checks.append(Check('Destinations', 'ok', 'Runtime and command paths are available.'))
            if not (layout.administrator and os.geteuid() != 0):
                for destination in (layout.runtime, layout.launcher):
                    parent = destination.parent
                    while not parent.exists():
                        parent = parent.parent
                    if not os.access(parent, os.W_OK | os.X_OK, effective_ids=True):
                        raise InstallError('Cannot create an installation beneath ' + str(parent))
                checks.append(Check('Permissions', 'ok', 'Existing destination parents are writable.'))
        elif layout.administrator and os.geteuid() != 0:
            # Full owner/inventory checks must run as the owner, after confirmation.
            checks.append(Check('Ownership', 'pending', 'Administrator verification runs after confirmation.'))
        else:
            validate_uninstall(layout)
            checks.append(Check('Ownership', 'ok', 'Receipt, command, and complete runtime inventory match.'))
    except (InstallError, OSError) as error:
        checks.append(Check('Destinations' if args.action == 'install' else 'Ownership', 'error', str(error),
                            'Review this destination or select another scope. No files were changed by preflight.'))
    if layout.administrator and os.geteuid() != 0:
        available = bool(shutil.which('sudo'))
        checks.append(Check('Administrator access', 'pending' if available else 'error',
                            'sudo authentication follows confirmation.' if available else 'sudo is unavailable.',
                            '' if available else 'Use a user installation or ask your administrator.'))
    ui.checks(checks)
    return all(check.status != 'error' for check in checks)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def regular_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise InstallError('Expected a regular file: ' + str(path))
        return source.read()


def inventory(root):
    """Record every owned entry without following the interpreter's links."""
    entries = {}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            if relative == RECEIPT:
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                entries[relative] = {'kind': 'link', 'target': os.readlink(path)}
            elif stat.S_ISDIR(mode):
                entries[relative] = {'kind': 'directory'}
                visit(path)
            elif stat.S_ISREG(mode):
                entries[relative] = {'kind': 'file', 'sha256': digest(regular_bytes(path))}
            else:
                raise InstallError('Unexpected special file in runtime: ' + str(path))
    visit(root)
    return entries


def save_receipt(root, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.receipt-', dir=root)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            json.dump(value, target, indent=2, sort_keys=True)
            target.write('\n'); target.flush(); os.fsync(target.fileno())
        os.replace(temporary, root / RECEIPT)
        sync_directory(root)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def launcher_bytes(layout, identifier):
    python = shlex.quote(str(layout.runtime / 'venv/bin/python'))
    return ('#!/bin/sh\n# CeFont managed installation: %s\n'
            'exec %s -I -B -m cefont.main "$@"\n' % (identifier, python)).encode()


@contextmanager
def installation_lock(layout):
    check_components(layout.runtime)
    layout.runtime.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(layout.runtime.parent / '.cefont-install.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InstallError('Installation lock is not a regular file.')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError('Another installer is working on this destination.') from error
        yield
    finally:
        os.close(descriptor)  # Keep the lock inode to avoid replacement races.


def read_receipt(layout):
    check_components(layout.runtime)
    if not layout.runtime.is_dir():
        raise InstallError('No managed installation found at ' + str(layout.runtime))
    if layout.runtime.stat().st_uid != os.geteuid():
        raise InstallError('The runtime is owned by a different user.')
    try:
        value = json.loads(regular_bytes(layout.runtime / RECEIPT))
        if not isinstance(value, dict):
            raise ValueError('Receipt must be an object')
        if (value['schema'] != 1 or value['application'] != 'cefont' or value['scope'] != layout.scope
                or value['runtime'] != str(layout.runtime) or value['launcher'] != str(layout.launcher)
                or str(uuid.UUID(value['id'])) != value['id']
                or value['phase'] != 'ready' or not isinstance(value['files'], dict)):
            raise ValueError('Receipt does not identify this installation')
    except (ValueError, KeyError, OSError, TypeError, AttributeError) as error:
        raise InstallError('Missing, invalid, or mismatched ownership receipt; nothing was removed.') from error
    return value


def validate_uninstall(layout):
    value = read_receipt(layout)
    check_components(layout.launcher)
    if inventory(layout.runtime) != value['files']:
        raise InstallError('Runtime files changed or extra files were added; nothing was removed. Review the runtime manually.')
    if os.path.lexists(layout.launcher):
        if regular_bytes(layout.launcher) != launcher_bytes(layout, value['id']):
            raise InstallError('The command was replaced or modified; nothing was removed.')
    return value


def remove_tree(path):
    if not shutil.rmtree.avoids_symlink_attacks:
        raise InstallError('This platform lacks safe directory removal.')
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        shutil.rmtree(path.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def uninstall(layout, ui):
    # Do not create directories or lock files for an absent/foreign installation.
    validate_uninstall(layout)
    with installation_lock(layout):
        value = validate_uninstall(layout)
        ui.stage('Remove the recorded command and application runtime')
        # The root has been verified against its complete recorded inventory.
        # Deletion is not globally transactional; late filesystem failures are
        # surfaced and may leave an incomplete application for manual review.
        remove_tree(layout.runtime)
        sync_directory(layout.runtime.parent)
        if os.path.lexists(layout.launcher):
            if regular_bytes(layout.launcher) != launcher_bytes(layout, value['id']):
                raise InstallError('The launcher changed during removal; it was preserved.')
            layout.launcher.unlink()
            sync_directory(layout.launcher.parent)
    ui.complete('uninstall', layout)


def validate_wheel(path, expected=None):
    content = regular_bytes(path)
    if expected is not None and digest(content) != expected:
        raise InstallError('The prepared wheel changed; refusing installation.')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        for name in names:
            parts = Path(name).parts
            if name.startswith('/') or '..' in parts or '\\' in name:
                raise InstallError('Unsafe wheel member.')
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise InstallError('Expected one wheel metadata record.')
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata['Name'] != 'cefont' or not metadata['Version']:
            raise InstallError('The prepared wheel is not CeFont.')
        for resource in ('font-manager-vendor.h', 'font-manager-license.h', 'orthographies/BasicLatin'):
            if 'cefont/resources/' + resource not in names:
                raise InstallError('Required packaged resource is missing: ' + resource)
    return content, metadata['Version']


@contextmanager
def prepared_wheel(python, ui):
    validate_local_source()
    # setuptools is an explicit build prerequisite, not fetched from an index.
    ui.run([python, '-I', '-B', '-c',
            'import sys,ensurepip,venv,setuptools.build_meta,ctypes,shutil; '
            'sys.version_info >= (3,11) or sys.exit("Python 3.11+ is required"); '
            'ctypes.CDLL("libfreetype.so.6"); '
            'all(shutil.which(tool) for tool in ("fc-list","fc-cache")) or '
            'sys.exit("Fontconfig is required: install fc-list and fc-cache")'],
           'Check Python, local build tools, FreeType, and Fontconfig')
    with tempfile.TemporaryDirectory(prefix='cefont-build-') as temporary:
        root = Path(temporary)
        source = root / 'source'; source.mkdir()
        for name in ('cefont', 'data'):
            origin = SOURCE / name
            for entry in origin.rglob('*'):
                if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
                    raise InstallError('Source contains a link or special file: ' + str(entry))
            shutil.copytree(origin, source / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('pyproject.toml', 'README.md'):
            (source / name).write_bytes(regular_bytes(SOURCE / name))
        wheel_directory = root / 'wheel'; wheel_directory.mkdir()
        ui.run([python, '-I', '-B', '-c',
                'import sys; from setuptools import build_meta; build_meta.build_wheel(sys.argv[1])',
                str(wheel_directory)], 'Build a local, non-editable CeFont wheel', cwd=source)
        wheels = list(wheel_directory.glob('cefont-*.whl'))
        if len(wheels) != 1:
            raise InstallError('The build did not produce one CeFont wheel.')
        content, _ = validate_wheel(wheels[0])
        # This only runs --help, as the invoking user. It does not index fonts.
        ui.run([python, '-I', '-B', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);from cefont.main import main;'
                'sys.argv=["cefont","--help"];main()', str(wheels[0])], 'Check the packaged command')
        yield wheels[0], digest(content)


def install_wheel(layout, wheel, expected, python, ui, method='prebuilt'):
    if method not in ('prebuilt', 'source'):
        raise InstallError('Unknown installation method.')
    validate_install(layout)
    # Copy verified bytes to a private, stable path before running pip, including
    # during the elevated deployment step. Never build source in that step.
    content, version = validate_wheel(wheel, expected)
    with tempfile.TemporaryDirectory(prefix='cefont-deploy-') as temporary:
        copied = Path(temporary) / wheel.name
        copied.write_bytes(content)
        with installation_lock(layout):
            validate_install(layout)
            layout.runtime.mkdir(mode=0o755)
            identity = (layout.runtime.stat().st_dev, layout.runtime.stat().st_ino)
            identifier = str(uuid.uuid4())
            value = dict(schema=1, application='cefont', id=identifier, scope=layout.scope,
                         runtime=str(layout.runtime), launcher=str(layout.launcher), phase='preparing',
                         version=version, wheel_sha256=digest(content), installation_method=method)
            command_content = launcher_bytes(layout, identifier)
            published = False
            try:
                save_receipt(layout.runtime, value)
                ui.run([python, '-I', '-B', '-m', 'venv', str(layout.runtime / 'venv')], 'Create an isolated Python runtime')
                environment_python = layout.runtime / 'venv/bin/python'
                ui.run([environment_python, '-I', '-B', '-m', 'pip', 'install', '--no-index', '--no-deps',
                        '--no-compile', str(copied)], 'Install the verified local wheel')
                ui.run([environment_python, '-I', '-B', '-m', 'cefont.main', '--help'], 'Verify the installed command')
                value.update(phase='ready', files=inventory(layout.runtime),
                             installed_at=datetime.now(timezone.utc).isoformat())
                save_receipt(layout.runtime, value)
                check_components(layout.launcher)
                layout.launcher.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_command = tempfile.mkstemp(prefix='.cefont-command-', dir=layout.launcher.parent)
                try:
                    with os.fdopen(descriptor, 'wb') as target:
                        target.write(command_content); target.flush()
                        os.fchmod(target.fileno(), 0o755); os.fsync(target.fileno())
                    # Exclusive publication also refuses dangling links and races.
                    os.link(temporary_command, layout.launcher)
                    published = True
                    sync_directory(layout.launcher.parent)
                finally:
                    os.unlink(temporary_command)
            except BaseException:
                if published and os.path.lexists(layout.launcher) and regular_bytes(layout.launcher) == command_content:
                    layout.launcher.unlink()
                if (layout.runtime.is_dir() and not layout.runtime.is_symlink() and
                        (layout.runtime.stat().st_dev, layout.runtime.stat().st_ino) == identity):
                    remove_tree(layout.runtime)
                raise
    ui.complete('install', layout)


@contextmanager
def selected_wheel(args, python, ui):
    if args.method == 'source':
        with prepared_wheel(python, ui) as result:
            yield result
    else:
        ui.stage('Verify the bundled prebuilt release wheel')
        yield bundled_application_wheel()


def execute(args, layout, ui):
    python = shutil.which(args.python)
    if python is None:
        raise InstallError('Python executable not found: ' + args.python)
    python = os.path.abspath(python)
    if layout.administrator and os.geteuid() != 0:
        sudo = shutil.which('sudo')
        if not sudo:
            raise InstallError('System installation needs administrator access, but sudo is unavailable.')
        base = [sudo, *(['-n'] if getattr(args, 'ui_json', None) else []), '--', python, '-I', '-B', str(Path(__file__).resolve()), args.action,
                '--system', '--yes', '--plain', '--python', python, '--method', args.method]
        if getattr(args, 'ui_json', None):
            base += ['--ui-json', 'execute']
        if args.action == 'install':
            validate_install(layout)
            with selected_wheel(args, python, ui) as (wheel, checksum):
                with elevated_wait(args, ui):
                    result = subprocess.run([*base, '--prepared-wheel', str(wheel), '--wheel-sha256', checksum], check=False)
        else:
            with elevated_wait(args, ui):
                result = subprocess.run(base, check=False)
        if result.returncode:
            raise InstallError('The administrator operation failed or was cancelled (exit %s).' % result.returncode)
        if getattr(args, 'ui_json', None):
            ui.complete(args.action, layout)
        return
    if args.prepared_wheel:
        if args.action != 'install' or not layout.administrator or os.geteuid() != 0 or not args.wheel_sha256:
            raise InstallError('Prepared wheels are reserved for elevated system deployment.')
        install_wheel(layout, Path(args.prepared_wheel), args.wheel_sha256, python, ui, args.method)
    elif args.action == 'install':
        validate_install(layout)
        with selected_wheel(args, python, ui) as (wheel, checksum):
            install_wheel(layout, wheel, checksum, python, ui, args.method)
    else:
        uninstall(layout, ui)


@contextmanager
def elevated_wait(args, ui):
    # The unprivileged UI cannot safely terminate an already elevated child.
    # On UI shutdown, retain the pipe and wait for that child to finish instead
    # of killing sudo and potentially abandoning a privileged transaction.
    if not getattr(args, 'ui_json', None):
        yield
        return
    def wait_for_administrator(signum, frame):
        ui.stage('Stop requested; waiting for the administrator operation to finish safely')
    previous = signal.signal(signal.SIGTERM, wait_for_administrator)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def show_plan(action, layout, ui=None, method='prebuilt'):
    ui = ui or Interface(plain=True)
    ui.panel('Review / %s for %s' % (action, 'this user' if layout.scope == 'user' else 'all users'), [
        *(['Method: ' + ('Prebuilt release (bundled wheel)' if method == 'prebuilt' else 'Build local source (includes checkout edits)')] if action == 'install' else []),
        'Runtime: ' + str(layout.runtime), 'Command: ' + str(layout.launcher),
        'Access: ' + ('administrator privileges' if layout.administrator else 'current filesystem permissions'),
        '', 'Fonts, personal configuration, and caches are preserved.',
        'Existing destinations will not be overwritten.' if action == 'install' else
        'Only an unchanged installation owned by this installer can be removed.'])


class JsonInterface(Interface):
    """JSON-lines boundary for the Go UI. No terminal input or escape codes."""
    def __init__(self):
        super().__init__(plain=True)

    def emit(self, kind, **values):
        print(json.dumps(dict(type=kind, **values), ensure_ascii=True), flush=True)

    def stage(self, message):
        self.emit('stage', message=safe_text(message))

    def checks(self, checks):
        self.emit('checks', checks=[asdict(check) for check in checks])

    def complete(self, action, layout):
        selected = shutil.which('cefont')
        status = 'Run the full command path, or add its directory to PATH.'
        if selected and os.path.abspath(selected) == str(layout.launcher):
            status = 'PATH selects this installation.'
        elif selected:
            status = 'Another CeFont command currently takes precedence on PATH.'
        self.emit('complete', action=action, runtime=str(layout.runtime), command=str(layout.launcher),
                  path_status=status if action == 'install' else '')

    def failure(self, message):
        self.emit('error', message=safe_text(message))

    def run(self, arguments, title, cwd=None):
        self.stage(title)
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        environment.update(PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1', PIP_CONFIG_FILE=os.devnull,
                           PIP_NO_CACHE_DIR='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        try:
            result = subprocess.run([str(value) for value in arguments], cwd=cwd, env=environment,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors='replace', timeout=180, check=False)
        except subprocess.TimeoutExpired as error:
            raise InstallError(title + ' timed out.') from error
        if result.returncode:
            raise InstallError('%s failed (exit %d):\n%s' % (title, result.returncode, safe_text(result.stdout)[-4000:]))
        self.emit('stage_done', message=title)
        return result.stdout


def json_main(args):
    ui = JsonInterface()
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.ui_json == 'overview':
            import tomllib
            version = tomllib.loads(regular_bytes(SOURCE / 'pyproject.toml').decode())['project']['version']
            layouts = {}
            for scope in ('user', 'system'):
                try:
                    layout = Layout.select(scope, args.prefix)
                    layouts[scope] = dict(runtime=str(layout.runtime), command=str(layout.launcher),
                                          administrator=layout.administrator and os.geteuid() != 0,
                                          status=installation_status(layout))
                except (OSError, InstallError) as error:
                    layouts[scope] = dict(status='unavailable', error=safe_text(error))
            ui.emit('overview', version=version, layouts=layouts, methods=installation_methods())
            return 0
        if not args.action or not args.scope:
            raise InstallError('The UI backend requires an explicit action and scope.')
        layout = Layout.select(args.scope, args.prefix)
        if args.ui_json == 'execute' and not args.yes:
            raise InstallError('Execution requires explicit --yes after review.')
        if args.dry_run:
            ui.emit('result', ok=True, dry_run=True)
            return 0
        if not preflight(args, layout, ui):
            raise InstallError('Preflight failed. Resolve the reported checks before retrying.')
        if args.ui_json == 'execute':
            execute(args, layout, ui)
        ui.emit('result', ok=True)
        return 0
    except KeyboardInterrupt:
        ui.emit('result', ok=False, cancelled=True, message='Operation interrupted; review any recovery details before retrying.')
        return 130
    except (InstallError, OSError, ValueError, zipfile.BadZipFile) as error:
        ui.failure(error)
        ui.emit('result', ok=False, message=safe_text(error))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


def wants_fullscreen(args):
    return (not args.plain and not args.yes and not args.dry_run and not args.prepared_wheel
            and sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM') != 'dumb'
            and 'NO_COLOR' not in os.environ)


def ui_source_fingerprint(source):
    inputs = sorted([*[p for p in source.glob('*.go') if not p.name.endswith('_test.go')],
                     source / 'go.mod', source / 'go.sum'])
    if not (source / 'main.go').is_file() or not all(path.is_file() for path in inputs):
        raise InstallError('Full-screen UI source is incomplete.')
    token = hashlib.sha256()
    for path in inputs:
        token.update(path.name.encode()); token.update(regular_bytes(path))
    return token.hexdigest()


def bundled_ui(source):
    architectures = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
    architecture = architectures.get(os.uname().machine.lower())
    folder = SOURCE / 'installer/bin'
    manifest = folder / 'manifest.json'
    if sys.platform != 'linux' or not architecture or not os.path.lexists(manifest):
        return None
    check_components(manifest)
    try:
        value = json.loads(regular_bytes(manifest))
        if value['schema'] != 1 or not isinstance(value['binaries'], dict):
            raise ValueError('Invalid release manifest')
        if (source / 'main.go').is_file() and value['source_sha256'] != ui_source_fingerprint(source):
            print('Bundled UI is older than its source; using the developer-build fallback.', file=sys.stderr)
            return None
        entry = value['binaries'].get('linux-' + architecture)
        if entry is None:
            return None
        expected_name = 'cefont-setup-linux-' + architecture
        if entry['file'] != expected_name:
            raise ValueError('Unexpected binary path')
        binary = folder / expected_name
        check_components(binary)
        if digest(regular_bytes(binary)) != entry['sha256']:
            raise InstallError('Bundled UI checksum mismatch; rebuild or obtain an intact release.')
        if binary.stat().st_mode & 0o022 or not os.access(binary, os.X_OK):
            raise InstallError('Bundled UI permissions are unsafe or the file is not executable.')
        return binary
    except (ValueError, KeyError, TypeError) as error:
        raise InstallError('Invalid bundled UI manifest; no executable was started.') from error


def launch_fullscreen(args):
    """Prefer a verified release binary; Go is only a developer fallback."""
    source = SOURCE / 'installer/tui'
    binary = bundled_ui(source)
    if binary is not None:
        return exec_fullscreen(binary, args)
    try:
        fingerprint = ui_source_fingerprint(source)
    except InstallError:
        print('Full-screen UI source is unavailable; using the text installer.', file=sys.stderr)
        return None
    token = hashlib.sha256(fingerprint.encode())
    token.update((sys.platform + os.uname().machine).encode())
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'cefont-installer/ui'
    if not cache.is_absolute():
        raise InstallError('XDG_CACHE_HOME must be absolute for the UI build cache.')
    check_components(cache)
    # Never run a cached helper from a directory another account can replace,
    # particularly when the installer was started as root.
    for directory in (cache, *cache.parents):
        if directory.exists():
            metadata = directory.stat()
            if metadata.st_uid not in (0, os.geteuid()) or (metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX):
                raise InstallError('UI cache has an untrusted owner or writable parent. Use --plain: ' + str(directory))
    binary = cache / ('setup-' + token.hexdigest()[:24])
    check_components(binary)
    if binary.exists():
        metadata = binary.stat()
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise InstallError('UI helper ownership or permissions are unsafe. Use --plain.')
    if not binary.is_file():
        go = shutil.which('go')
        if not go:
            print('Go 1.26.7+ is needed to build the full-screen UI; using the text installer. Try --plain.', file=sys.stderr)
            return None
        print('Building the full-screen setup UI (first launch may download pinned Go modules)...', flush=True)
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.build-', dir=cache) as temporary:
            built = Path(temporary) / 'setup'
            environment = dict(os.environ, CGO_ENABLED='0')
            result = subprocess.run([go, 'build', '-mod=readonly', '-trimpath', '-o', str(built), '.'],
                                    cwd=source, env=environment, check=False)
            if result.returncode:
                print('UI build failed. Run ./install.sh --plain for text setup.', file=sys.stderr)
                return result.returncode
            os.replace(built, binary)
    return exec_fullscreen(binary, args)


def exec_fullscreen(binary, args):
    arguments = [str(binary), '--backend', sys.executable, '--script', str(Path(__file__).resolve()),
                 '--python', args.python, '--theme', args.theme, '--method', args.method]
    for name in ('prefix', 'action', 'scope'):
        if getattr(args, name):
            arguments += ['--' + name, str(getattr(args, name))]
    if args.no_animation:
        arguments += ['--no-animation']
    # Replace the launcher so the UI owns terminal signals and can finish
    # backend cleanup before restoring the alternate screen.
    os.execv(str(binary), arguments)
    return 0  # execv returns only in a test double.


def parser():
    command = argparse.ArgumentParser(prog='./install.sh',
                                      description='Install or remove CeFont for your account or the system using a bundled release or local source.',
                                      epilog='Updates are disabled. Run without arguments for interactive setup.')
    command.add_argument('action', nargs='?', choices=('install', 'uninstall', 'update'))
    scope = command.add_mutually_exclusive_group()
    scope.add_argument('--user', action='store_const', const='user', dest='scope', help='Manage the current account installation.')
    scope.add_argument('--system', action='store_const', const='system', dest='scope', help='Manage the system installation; requests sudo when needed.')
    command.add_argument('--prefix', help='Custom prefix: PREFIX/lib/cefont and PREFIX/bin/cefont (no automatic sudo).')
    command.add_argument('--yes', action='store_true', help='Apply an explicitly selected action and scope without prompting.')
    command.add_argument('--dry-run', action='store_true', help='Show the plan without creating files or building packages.')
    command.add_argument('--plain', action='store_true', help='Use the Python text interface instead of the full-screen setup.')
    command.add_argument('--no-animation', action='store_true', help='Show static progress without an animated spinner.')
    command.add_argument('--theme', choices=('auto', 'light', 'dark'), default='auto',
                         help='Accent contrast for the terminal background (auto uses COLORFGBG when available).')
    command.add_argument('--python', default=sys.executable, help='Python 3.11+ used to build and create the isolated runtime.')
    command.add_argument('--method', choices=('prebuilt', 'source'), default='prebuilt',
                         help='Install the bundled prebuilt release (default) or build the local checkout.')
    command.add_argument('--ui-json', choices=('overview', 'check', 'execute'), help=argparse.SUPPRESS)
    command.add_argument('--prepared-wheel', help=argparse.SUPPRESS)
    command.add_argument('--wheel-sha256', help=argparse.SUPPRESS)
    return command


def main(argv=None):
    if sys.version_info < (3, 11):
        print('CeFont installer requires Python 3.11 or newer.', file=sys.stderr)
        return 1
    command = parser()
    args = command.parse_args(argv)
    supplied = list(argv) if argv is not None else sys.argv[1:]
    method_explicit = any(item == '--method' or item.startswith('--method=') for item in supplied)
    if args.action == 'update':
        print(UPDATE_MESSAGE, file=sys.stderr)
        return 2
    if args.ui_json:
        return json_main(args)
    if wants_fullscreen(args):
        try:
            result = launch_fullscreen(args)
        except (InstallError, OSError) as error:
            print('CeFont setup: ' + safe_text(error), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        if result is not None:
            return result
    ui = Interface(args.plain, no_animation=args.no_animation, theme=args.theme)
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        if ui.interactive:
            ui.banner(args.prefix)
        if not ui.interactive and (not args.action or not args.scope):
            command.error('Choose install/uninstall and --user/--system explicitly when not using a terminal.')
        step = ('action' if not args.action else 'method' if args.action == 'install' and ui.interactive
                and not args.yes and not method_explicit and not args.dry_run else 'scope' if not args.scope else 'check')
        while True:
            if step == 'action':
                ui.route('Action')
                choice = ui.choose('What would you like to do?', ['Install CeFont', 'Uninstall CeFont', 'Exit'])
                if choice == 'Exit':
                    return 0
                args.action = 'install' if choice == 'Install CeFont' else 'uninstall'
                step = 'method' if args.action == 'install' and not args.yes else 'scope'
            if step == 'method':
                ui.route('Method')
                methods = installation_methods()
                ui.panel('How would you like to install?', [
                    'Prebuilt release (recommended): ' + methods['prebuilt']['detail'],
                    'Build local source: ' + methods['source']['detail']])
                choice = ui.choose('Choose an installation method.',
                                   ['Prebuilt release (recommended)', 'Build local source', 'Back', 'Cancel'])
                if choice == 'Cancel':
                    raise Cancelled()
                if choice == 'Back':
                    step = 'action'
                    continue
                args.method = 'prebuilt' if choice == 'Prebuilt release (recommended)' else 'source'
                step = 'scope'
            if step == 'scope':
                ui.route('Scope')
                ui.panel('Choose where CeFont lives', [
                    'Current user: just your account; no sudo required.',
                    '  Command: ' + scope_summary('user', args.prefix, command=True),
                    'System-wide: shared installation; administrator access may be required.',
                    '  Command: ' + scope_summary('system', args.prefix, command=True)])
                choice = ui.choose('Choose the installation scope.', ['Current user', 'System-wide', 'Back', 'Cancel'])
                if choice == 'Cancel':
                    raise Cancelled()
                if choice == 'Back':
                    step = 'method' if args.action == 'install' else 'action'
                    continue
                args.scope = 'user' if choice == 'Current user' else 'system'
                step = 'check'
            layout = Layout.select(args.scope, args.prefix)
            if args.dry_run:
                show_plan(args.action, layout, ui, args.method)
                print('\nDry run: no files changed.')
                return 0
            if not ui.interactive and not args.yes:
                raise InstallError('Noninteractive changes require --yes after reviewing --dry-run.')
            ui.route('Checks')
            if not preflight(args, layout, ui):
                if not ui.interactive or args.yes:
                    raise InstallError('Preflight failed. Resolve the reported checks before retrying.')
                choice = ui.choose('Resolve the checks before continuing.', ['Cancel', 'Back', 'Retry'])
                if choice == 'Cancel':
                    raise Cancelled()
                step = 'scope' if choice == 'Back' else 'check'
                continue
            if args.yes:
                show_plan(args.action, layout, ui, args.method)
                break
            choice = ui.review(args.action, layout, args.method)
            if choice == 'Back':
                step = 'scope'
                continue
            if choice != 'Apply changes':
                raise Cancelled()
            break
        execute(args, layout, ui)
        return 0
    except Cancelled:
        print('\nCancelled. No changes were requested.')
        return 0
    except (InstallError, OSError, zipfile.BadZipFile) as error:
        ui.failure(error)
        print('CeFont installer: %s' % safe_text(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nCeFont installer interrupted.', file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == '__main__':
    raise SystemExit(main())
