#!/usr/bin/env python3
"""Build the application wheel, static setup UI, and standard/developer releases.

Run as a developer: python3 -B scripts/build_installer_ui.py
Customers only need to extract the matching archive and run ./install.sh.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

from install_cefont import (SOURCE, Interface, bundled_application_wheel, check_components,
                            prepared_wheel, regular_bytes, ui_source_fingerprint, validate_wheel)

TARGETS = ('linux-amd64', 'linux-arm64')
OUTPUT = SOURCE / 'installer/bin'


def write_atomic(path, content, mode=0o644):
    check_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cefont-build-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(content)
            os.fchmod(target.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def json_objects(text):
    decoder = json.JSONDecoder()
    while text.strip():
        value, offset = decoder.raw_decode(text.lstrip())
        yield value
        text = text.lstrip()[offset:]


def collect_licenses(go):
    result = subprocess.run([go, 'list', '-deps', '-json', '.'], cwd=SOURCE / 'installer/tui',
                            check=True, capture_output=True, text=True)
    files = {}
    index = ['CeFont setup uses the following Go modules. Their license texts follow in this directory.', '']
    seen = set()
    for package in json_objects(result.stdout):
        module = package.get('Module')
        if not module or module.get('Main') or module['Path'] in seen:
            continue
        seen.add(module['Path'])
        location = Path(module['Dir'])
        notices = sorted(p for p in location.iterdir() if p.is_file() and
                         p.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'PATENTS')))
        if not notices:
            raise RuntimeError('No license text found for ' + module['Path'])
        key = re.sub(r'[^a-zA-Z0-9_.-]', '_', module['Path'] + '@' + module['Version'])
        index.append(module['Path'] + ' ' + module['Version'] + ' / ' + key)
        for notice in notices:
            files[Path(key) / notice.name] = notice.read_bytes()
    goroot = Path(subprocess.run([go, 'env', 'GOROOT'], check=True, capture_output=True, text=True).stdout.strip())
    go_license = next((path for path in (goroot / 'LICENSE', Path('/usr/share/licenses/go/LICENSE')) if path.is_file()), None)
    if go_license is None:
        raise RuntimeError('The Go toolchain license text is missing; include it before shipping binaries.')
    files[Path('Go/LICENSE')] = go_license.read_bytes()
    for name in ('LICENSE', 'PATENTS'):
        if (goroot / name).is_file():
            files[Path('Go') / name] = (goroot / name).read_bytes()
    files[Path('README.txt')] = ('\n'.join(index) + '\n').encode()
    return files


def build():
    go = shutil.which('go')
    if not go:
        raise RuntimeError('Building releases requires Go; customers running the finished archives do not.')
    source = SOURCE / 'installer/tui'
    fingerprint = ui_source_fingerprint(source)
    manifest = dict(schema=1, source_sha256=fingerprint, binaries={},
                    go_version=subprocess.run([go, 'version'], capture_output=True, text=True, check=True).stdout.strip())
    with tempfile.TemporaryDirectory(prefix='cefont-ui-release-') as temporary:
        stage = Path(temporary)
        for target in TARGETS:
            filename = 'cefont-setup-' + target
            environment = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH=target.split('-')[1],
                               GOAMD64='v1', GOARM64='v8.0')
            subprocess.run([go, 'build', '-mod=readonly', '-trimpath', '-buildvcs=false',
                            '-ldflags=-s -w -buildid=', '-o', str(stage / filename), '.'],
                           cwd=source, env=environment, check=True)
            content = (stage / filename).read_bytes()
            manifest['binaries'][target] = dict(file=filename, sha256=hashlib.sha256(content).hexdigest(), size=len(content))
            print('Built', target, len(content), 'bytes', flush=True)
        licenses = collect_licenses(go)
        if ui_source_fingerprint(source) != fingerprint:
            raise RuntimeError('UI source changed during the build; rebuild before shipping.')
        for item in manifest['binaries'].values():
            write_atomic(OUTPUT / item['file'], (stage / item['file']).read_bytes(), 0o755)
        for name, content in licenses.items():
            write_atomic(OUTPUT / 'licenses' / name, content)
        write_atomic(OUTPUT / 'manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
        sums = ''.join(item['sha256'] + '  ' + item['file'] + '\n' for item in manifest['binaries'].values())
        write_atomic(OUTPUT / 'SHA256SUMS', sums.encode())
    return manifest


def application_source_hash():
    paths = [SOURCE / 'pyproject.toml', SOURCE / 'README.md']
    for name in ('cefont', 'data'):
        paths.extend(p for p in (SOURCE / name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    result = hashlib.sha256()
    for path in sorted(paths):
        result.update(str(path.relative_to(SOURCE)).encode())
        result.update(regular_bytes(path))
    return result.hexdigest()


def build_application_wheel():
    fingerprint = application_source_hash()
    with prepared_wheel(sys.executable, Interface(plain=True)) as (wheel, checksum):
        content, version = validate_wheel(wheel, checksum)
        if application_source_hash() != fingerprint:
            raise RuntimeError('Application source changed during wheel build; rebuild before shipping.')
        folder = SOURCE / 'installer/packages'
        write_atomic(folder / wheel.name, content)
        manifest = dict(schema=1, file=wheel.name, version=version, sha256=checksum, source_sha256=fingerprint)
        write_atomic(folder / 'manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
        print('Built prebuilt application wheel:', wheel.name, flush=True)


def source_files(profile):
    paths = [SOURCE / name for name in ('install.sh', 'README.md', 'pyproject.toml',
             'scripts/install_cefont.py', 'docs/INSTALLER.md')]
    wheel, _ = bundled_application_wheel()
    paths += [wheel, SOURCE / 'installer/packages/manifest.json']
    directories = ['installer/licenses', 'installer/bin/licenses']
    if profile == 'developer':
        paths.append(SOURCE / 'scripts/build_installer_ui.py')
        directories += ['cefont', 'data', 'installer/tui', 'tests']
    for directory in directories:
        for path in (SOURCE / directory).rglob('*'):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise RuntimeError('Release source contains a symlink: ' + str(path))
            if path.is_file():
                paths.append(path)
            elif not path.is_dir():
                raise RuntimeError('Release source contains a special file: ' + str(path))
    return sorted(paths)


def package(manifest):
    if manifest['source_sha256'] != ui_source_fingerprint(SOURCE / 'installer/tui'):
        raise RuntimeError('The binaries are stale; rebuild before packaging.')
    version = tomllib.loads(regular_bytes(SOURCE / 'pyproject.toml').decode())['project']['version']
    if not re.fullmatch(r'[a-zA-Z0-9.+_-]+', version):
        raise RuntimeError('Unsafe package version')
    bundled_application_wheel()
    application = json.loads(regular_bytes(SOURCE / 'installer/packages/manifest.json'))
    if application['version'] != version or application['source_sha256'] != application_source_hash():
        raise RuntimeError('Application source or release metadata changed; rebuild the release wheel before packaging matching source.')
    if not (SOURCE / 'installer/licenses/GPL-3.0.txt').is_file():
        raise RuntimeError('Missing project license text')
    destination = SOURCE / 'dist'
    check_components(destination)
    destination.mkdir(exist_ok=True)
    for target, profile in [(target, profile) for target in TARGETS for profile in ('standard', 'developer')]:
        paths = source_files(profile)
        item = manifest['binaries'][target]
        binary = OUTPUT / item['file']
        if hashlib.sha256(regular_bytes(binary)).hexdigest() != item['sha256']:
            raise RuntimeError('Release binary checksum mismatch: ' + target)
        suffix = '-developer' if profile == 'developer' else ''
        archive = destination / ('cefont-' + version + '-' + target + suffix + '.tar.gz')
        check_components(archive)
        fd, temporary = tempfile.mkstemp(prefix='.cefont-release-', dir=destination)
        os.close(fd)
        try:
            with tarfile.open(temporary, 'w:gz') as tar:
                for path in [*paths, binary]:
                    name = str(Path('cefont-' + version) / path.relative_to(SOURCE))
                    info = tar.gettarinfo(str(path), arcname=name)
                    if not info.isfile():
                        raise RuntimeError('Unexpected release entry: ' + str(path))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    info.mtime = 0
                    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    with path.open('rb') as source:
                        tar.addfile(info, source)
                local_manifest = dict(manifest, binaries={target: item})
                generated = {
                    'manifest.json': (json.dumps(local_manifest, indent=2, sort_keys=True) + '\n').encode(),
                    'SHA256SUMS': (item['sha256'] + '  ' + item['file'] + '\n').encode(),
                }
                source_notice = ('CeFont ' + version + '\n\nCorresponding application and installer UI source: '
                                 'cefont-' + version + '-' + target + '-developer.tar.gz\n'
                                 'Original upstream: https://github.com/FontManager/font-manager\n'
                                 'Default installation uses the bundled wheel; the developer package also supports building local patches.\n')
                notice = source_notice.encode()
                info = tarfile.TarInfo('cefont-' + version + '/SOURCE.txt')
                info.size, info.mode = len(notice), 0o644
                tar.addfile(info, io.BytesIO(notice))
                for filename, content in generated.items():
                    info = tarfile.TarInfo(str(Path('cefont-' + version) / 'installer/bin' / filename))
                    info.size, info.mode, info.mtime = len(content), 0o644, 0
                    tar.addfile(info, io.BytesIO(content))
            os.replace(temporary, archive)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        write_atomic(archive.with_suffix(archive.suffix + '.sha256'), (checksum + '  ' + archive.name + '\n').encode())
        print('Packaged', archive, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--binaries-only', action='store_true')
    mode.add_argument('--package-only', action='store_true')
    mode.add_argument('--wheel-only', action='store_true', help='Rebuild only the bundled application wheel; Go is not needed.')
    args = parser.parse_args()
    if not args.package_only and not args.binaries_only:
        build_application_wheel()
    if args.wheel_only:
        return
    manifest = json.loads(regular_bytes(OUTPUT / 'manifest.json')) if args.package_only else build()
    if not args.binaries_only:
        package(manifest)


if __name__ == '__main__':
    main()
