"""Installation-method regressions; no real font/configuration destinations."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from scripts import install_cefont as setup


class MethodTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cefont-method-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.packages = self.base / 'installer/packages'
        self.packages.mkdir(parents=True)
        self.wheel = self.packages / 'cefont-1.0.0-py3-none-any.whl'
        with zipfile.ZipFile(self.wheel, 'w') as archive:
            archive.writestr('cefont-1.0.0.dist-info/METADATA', 'Name: cefont\nVersion: 1.0.0\n')
            for resource in ('font-manager-vendor.h', 'font-manager-license.h', 'orthographies/BasicLatin'):
                archive.writestr('cefont/resources/' + resource, 'fixture')
        self.manifest = dict(schema=1, file=self.wheel.name, version='1.0.0',
                             sha256=hashlib.sha256(self.wheel.read_bytes()).hexdigest())
        (self.packages / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_default_method_is_prebuilt(self):
        self.assertEqual(setup.parser().parse_args(['install', '--user']).method, 'prebuilt')

    def test_local_source_edits_do_not_invalidate_prebuilt_wheel(self):
        (self.base / 'cefont').mkdir()
        (self.base / 'cefont/__init__.py').write_text('LOCAL_PATCH = True')
        with patch.object(setup, 'SOURCE', self.base):
            wheel, checksum = setup.bundled_application_wheel()
        self.assertEqual(wheel, self.wheel)
        self.assertEqual(checksum, self.manifest['sha256'])

    def test_corrupt_prebuilt_is_refused_before_destination_changes(self):
        self.wheel.write_bytes(b'corrupt')
        args = setup.parser().parse_args(['install', '--user', '--yes', '--prefix', str(self.base / 'target')])
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup, 'prepared_wheel') as build:
            with self.assertRaisesRegex(setup.InstallError, 'checksum'):
                setup.execute(args, setup.Layout.select('user', self.base / 'target'), MagicMock())
        build.assert_not_called()
        self.assertFalse((self.base / 'target').exists())

    def test_missing_source_is_reported_without_switching_methods(self):
        args = setup.parser().parse_args(['install', '--user', '--method', 'source', '--prefix', str(self.base / 'target')])
        ui = MagicMock()
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup, 'python_checks', return_value=[]):
            self.assertFalse(setup.preflight(args, setup.Layout.select('user', self.base / 'target'), ui))
        checks = ui.checks.call_args.args[0]
        self.assertTrue(any(c.status == 'error' and 'source' in c.detail.lower() for c in checks))
        self.assertEqual(args.method, 'source')
        self.assertFalse((self.base / 'target').exists())

    def test_prebuilt_skips_source_and_setuptools_checks(self):
        args = setup.parser().parse_args(['install', '--user', '--prefix', str(self.base / 'target')])
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup, 'python_checks', return_value=[]) as checks, \
             patch.object(setup, 'validate_local_source', side_effect=AssertionError('Must not inspect source')):
            self.assertTrue(setup.preflight(args, setup.Layout.select('user', self.base / 'target'), MagicMock()))
        self.assertFalse(checks.call_args.kwargs['build'])

    def test_manifest_cannot_redirect_wheel_outside_package_folder(self):
        self.manifest['file'] = '../../foreign.whl'
        (self.packages / 'manifest.json').write_text(json.dumps(self.manifest))
        with patch.object(setup, 'SOURCE', self.base):
            with self.assertRaises(setup.InstallError):
                setup.bundled_application_wheel()

    def test_system_prebuilt_dispatches_verified_wheel_without_building(self):
        args = setup.parser().parse_args(['install', '--system', '--yes'])
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup, 'validate_install'), \
             patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.shutil, 'which', side_effect=lambda name: '/fixture/sudo' if name == 'sudo' else sys.executable), \
             patch.object(setup, 'prepared_wheel') as build, \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            setup.execute(args, setup.Layout.select('system'), MagicMock())
        build.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--method') + 1], 'prebuilt')
        self.assertEqual(command[command.index('--wheel-sha256') + 1], self.manifest['sha256'])


class MethodIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cefont-method-integration-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = self.base / 'checkout'
        self.checkout.mkdir()
        for name in ('cefont', 'data', 'installer/packages'):
            shutil.copytree(setup.SOURCE / name, self.checkout / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('README.md', 'pyproject.toml', 'scripts/install_cefont.py'):
            target = self.checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(setup.SOURCE / name, target)
        self.backend = self.checkout / 'scripts/install_cefont.py'
        self.prefix = self.base / 'managed'
        self.env = dict(os.environ, HOME=str(self.base / 'home'), XDG_DATA_HOME=str(self.base / 'personal-data'),
                        XDG_CONFIG_HOME=str(self.base / 'config'), XDG_CACHE_HOME=str(self.base / 'cache'))
        self.env.pop('PYTHONPATH', None)
        self.sentinel = self.base / 'personal-data/fonts/personal-fixture.ttf'
        self.sentinel.parent.mkdir(parents=True)
        self.sentinel.write_bytes(b'preserve personal fixture')

    def run_installer(self, action, *extra):
        return subprocess.run([sys.executable, '-I', '-B', str(self.backend), action, '--user', '--prefix',
                               str(self.prefix), '--yes', '--plain', *extra], cwd=self.base, env=self.env,
                              text=True, capture_output=True, timeout=180)

    def require_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sentinel.read_bytes(), b'preserve personal fixture')

    def test_prebuilt_ignores_local_patch_and_source_installs_it(self):
        module = self.checkout / 'cefont/__init__.py'
        module.write_text(module.read_text() + '\nLOCAL_PATCH_MARKER = "patched locally"\n')
        baseline = json.loads((self.checkout / 'installer/packages/manifest.json').read_text())
        checksums = {}
        for method in ('prebuilt', 'source'):
            extra = [] if method == 'prebuilt' else ['--method', 'source']
            self.require_success(self.run_installer('install', *extra))
            receipt = json.loads((self.prefix / 'lib/cefont/installation.json').read_text())
            self.assertEqual(receipt['installation_method'], method)
            checksums[method] = receipt['wheel_sha256']
            command = [str(self.prefix / 'lib/cefont/venv/bin/python'), '-I', '-B', '-c',
                       'import cefont;print(getattr(cefont,"LOCAL_PATCH_MARKER","release"))']
            output = subprocess.run(command, cwd=self.base, env=self.env, text=True, capture_output=True, timeout=30)
            self.require_success(output)
            self.assertEqual(output.stdout.strip(), 'release' if method == 'prebuilt' else 'patched locally')
            self.require_success(self.run_installer('uninstall'))
        self.assertEqual(checksums['prebuilt'], baseline['sha256'])
        self.assertNotEqual(checksums['source'], checksums['prebuilt'])

    def test_prebuilt_without_setuptools_and_source_failure_without_fallback(self):
        bootstrap = self.base / 'runtime-only-python'
        subprocess.run([sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(bootstrap)], check=True, timeout=60)
        python = bootstrap / 'bin/python'
        absent = subprocess.run([str(python), '-I', '-B', '-c',
                                  'import importlib.util;assert importlib.util.find_spec("setuptools") is None'],
                                 capture_output=True, text=True, timeout=30)
        self.require_success(absent)
        self.require_success(self.run_installer('install', '--python', str(python)))
        self.require_success(self.run_installer('uninstall'))
        failed = self.run_installer('install', '--method', 'source', '--python', str(python))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('setuptools', failed.stdout)
        self.assertFalse((self.prefix / 'lib/cefont').exists())
        self.assertFalse((self.prefix / 'bin/cefont').exists())


if __name__ == '__main__':
    unittest.main()
