"""Installer command tests; every writable destination is disposable."""
import json
import hashlib
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from scripts import install_cefont as setup

ROOT = Path(__file__).resolve().parents[1]


class InstallerCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cefont-installer-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.prefix = self.base / 'destination with spaces'
        self.env = os.environ.copy()
        self.env.update(HOME=str(self.base / 'home'), XDG_DATA_HOME=str(self.base / 'data'),
                        XDG_CONFIG_HOME=str(self.base / 'config'), XDG_CACHE_HOME=str(self.base / 'cache'),
                        CEFONT_INSTALL_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE='1',
                        PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        self.env.pop('PYTHONPATH', None)

    def run_installer(self, *arguments, input=None):
        return subprocess.run(['sh', str(ROOT / 'install.sh'), *arguments], input=input,
                              text=True, capture_output=True, env=self.env, cwd=self.base,
                              timeout=180)

    def test_update_is_disabled_and_does_not_create_destinations(self):
        result = self.run_installer('update', '--user', '--prefix', str(self.prefix), '--yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('disabled', result.stderr.lower())
        self.assertFalse(self.prefix.exists())

    def test_control_characters_in_destination_are_rejected_before_review(self):
        destination = str(self.base / 'unreviewable\x1b[2Jtarget')
        result = self.run_installer('install', '--user', '--prefix', destination, '--yes', '--plain')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('control characters', result.stderr)
        self.assertNotIn('\x1b', result.stdout + result.stderr)
        self.assertFalse(Path(destination).exists())

    def test_dry_run_reports_user_and_system_paths_without_writes(self):
        for scope, expected in (('--user', str(self.base / 'data/cefont/runtime')),
                                ('--system', '/opt/cefont')):
            with self.subTest(scope=scope):
                result = self.run_installer('install', scope, '--dry-run', '--plain')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_noninteractive_mutation_requires_explicit_confirmation(self):
        result = self.run_installer('install', '--user', '--prefix', str(self.prefix), '--plain')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--yes', result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_isolated_install_runs_outside_checkout_and_uninstalls_only_owned_files(self):
        sentinel = self.base / 'data/fonts/personal.ttf'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b'personal fixture')
        result = self.run_installer('install', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        launcher = self.prefix / 'bin/cefont'
        runtime = self.prefix / 'lib/cefont'
        receipt = json.loads((runtime / 'installation.json').read_text())
        self.assertEqual(receipt['scope'], 'user')
        self.assertEqual(receipt['phase'], 'ready')
        output = subprocess.run([str(launcher), '--help'], text=True, capture_output=True,
                                env=self.env, cwd=self.base, timeout=30)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn('CeFont', output.stdout)
        original_launcher = launcher.read_bytes()
        original_receipt = (runtime / 'installation.json').read_bytes()
        for mutation in ('extra file', 'changed launcher', 'wrong scope'):
            with self.subTest(mutation=mutation):
                if mutation == 'extra file':
                    (runtime / 'keep.txt').write_bytes(b'personal addition')
                elif mutation == 'changed launcher':
                    launcher.write_bytes(b'other application')
                else:
                    altered = dict(receipt, scope='system')
                    (runtime / 'installation.json').write_text(json.dumps(altered))
                failed = self.run_installer('uninstall', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
                self.assertNotEqual(failed.returncode, 0, failed.stdout)
                self.assertTrue(runtime.exists())
                self.assertTrue(launcher.exists())
                if mutation == 'extra file':
                    self.assertEqual((runtime / 'keep.txt').read_bytes(), b'personal addition')
                    (runtime / 'keep.txt').unlink()
                launcher.write_bytes(original_launcher)
                (runtime / 'installation.json').write_bytes(original_receipt)
        repeated = self.run_installer('install', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(launcher.read_bytes(), original_launcher)
        result = self.run_installer('uninstall', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(launcher.exists())
        self.assertFalse(runtime.exists())
        self.assertEqual(sentinel.read_bytes(), b'personal fixture')

    def test_system_scope_uses_disposable_prefix_and_loads_packaged_resources(self):
        result = self.run_installer('install', '--system', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        runtime = self.prefix / 'lib/cefont'
        self.assertEqual(json.loads((runtime / 'installation.json').read_text())['scope'], 'system')
        output = subprocess.run([str(runtime / 'venv/bin/python'), '-I', '-B', '-c',
                                  'from cefont.core.data import load_orthographies;print(len(load_orthographies()))'],
                                 cwd=self.base, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(output.stdout.strip(), '127')
        removed = self.run_installer('uninstall', '--system', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse(runtime.exists())
        self.assertFalse((self.prefix / 'bin/cefont').exists())

    def test_uninstall_rejects_foreign_receipt_under_optimized_python(self):
        runtime = self.prefix / 'lib/cefont'
        runtime.mkdir(parents=True)
        value = dict(schema=1, application='some-other-app', scope='user', runtime=str(runtime),
                     launcher=str(self.prefix / 'bin/cefont'), phase='ready', files={},
                     id='00000000-0000-4000-8000-000000000001')
        (runtime / 'installation.json').write_text(json.dumps(value))
        result = subprocess.run([sys.executable, '-I', '-B', '-O', str(ROOT / 'scripts/install_cefont.py'),
                                 'uninstall', '--user', '--prefix', str(self.prefix), '--yes', '--plain'],
                                text=True, capture_output=True, env=self.env, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(runtime.exists())

    def test_existing_destinations_and_symlink_parents_are_never_overwritten(self):
        target = self.base / 'outside'
        target.mkdir()
        sentinel = target / 'keep'
        sentinel.write_bytes(b'keep')
        self.prefix.mkdir()
        (self.prefix / 'lib').symlink_to(target, target_is_directory=True)
        result = self.run_installer('install', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertEqual(list(target.iterdir()), [sentinel])
        (self.prefix / 'lib').unlink()
        (self.prefix / 'bin').mkdir()
        launcher = self.prefix / 'bin/cefont'
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    launcher.symlink_to(self.base / 'missing')
                else:
                    launcher.write_bytes(b'another program')
                before = os.readlink(launcher) if dangling else launcher.read_bytes()
                result = self.run_installer('install', '--user', '--prefix', str(self.prefix), '--yes', '--plain')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(os.readlink(launcher) if dangling else launcher.read_bytes(), before)
                launcher.unlink()
        self.assertFalse((self.prefix / 'lib/cefont').exists())

    def test_foreign_directory_without_receipt_is_not_removed(self):
        runtime = self.prefix / 'lib/cefont'
        runtime.mkdir(parents=True)
        sentinel = runtime / 'keep'
        sentinel.write_bytes(b'other data')
        result = self.run_installer('uninstall', '--system', '--prefix', str(self.prefix), '--yes', '--plain')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_bytes(), b'other data')

    def test_plain_menu_and_cancelled_review_do_not_install(self):
        master, slave = pty.openpty()
        process = subprocess.Popen(['sh', str(ROOT / 'install.sh'), '--plain', '--prefix', str(self.prefix)],
                                   stdin=slave, stdout=slave, stderr=slave, env=self.env, cwd=self.base)
        os.close(slave)
        output = bytearray()
        prompts = 0
        try:
            deadline = time.monotonic() + 20
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try: output.extend(os.read(master, 65536))
                    except OSError: break
                count = output.count(b'Choose a number')
                if count > prompts:
                    os.write(master, b'1\n')
                    prompts += 1
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0, output.decode(errors='replace'))
        finally:
            if process.poll() is None:
                process.terminate(); process.wait()
            os.close(master)
        self.assertEqual(prompts, 4)
        self.assertFalse(self.prefix.exists())


class InstallerTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cefont-install-transaction-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.layout = setup.Layout.select('user', str(self.base / 'target'))
        self.wheel = self.base / 'cefont-0.0.0-py3-none-any.whl'
        with zipfile.ZipFile(self.wheel, 'w') as archive:
            archive.writestr('cefont-0.0.0.dist-info/METADATA', 'Name: cefont\nVersion: 0.0.0\n')
            for resource in ('font-manager-vendor.h', 'font-manager-license.h', 'orthographies/BasicLatin'):
                archive.writestr('cefont/resources/' + resource, 'fixture')
        self.checksum = hashlib.sha256(self.wheel.read_bytes()).hexdigest()

    def test_failed_or_interrupted_deployment_rolls_back_its_owned_runtime(self):
        for error in (setup.InstallError('simulated pip failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                ui = Mock()
                ui.run.side_effect = error
                with self.assertRaises(type(error)):
                    setup.install_wheel(self.layout, self.wheel, self.checksum, sys.executable, ui)
                self.assertFalse(self.layout.runtime.exists())
                self.assertFalse(self.layout.launcher.exists())

    def test_changed_wheel_is_rejected_before_any_destination_is_created(self):
        self.wheel.write_bytes(b'changed')
        with self.assertRaisesRegex(setup.InstallError, 'changed'):
            setup.install_wheel(self.layout, self.wheel, self.checksum, sys.executable, Mock())
        self.assertFalse(self.layout.runtime.parent.exists())

    def test_failure_output_does_not_replay_terminal_control_sequences(self):
        ui = setup.Interface(plain=True)
        failed = subprocess.CompletedProcess([], 1, '\x1b[31mfailed\x1b[0m\x1b[2J')
        with patch.object(setup.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(setup.InstallError, 'failed') as error:
                ui.run([sys.executable, '-c', 'pass'], 'Fixture step')
        self.assertNotIn('\x1b', str(error.exception))

    def test_racing_launcher_is_preserved_without_publishing_the_runtime(self):
        self.layout.launcher.parent.mkdir(parents=True)
        def race(*args, **kwargs):
            self.layout.launcher.write_bytes(b'another program appeared')
        ui = Mock()
        ui.run.side_effect = race
        with self.assertRaises(FileExistsError):
            setup.install_wheel(self.layout, self.wheel, self.checksum, sys.executable, ui)
        self.assertFalse(self.layout.runtime.exists())
        self.assertEqual(self.layout.launcher.read_bytes(), b'another program appeared')

    def test_system_dispatch_requests_only_the_prepared_deployment(self):
        import contextlib
        args = setup.parser().parse_args(['install', '--system', '--yes', '--method', 'source'])
        layout = setup.Layout.select('system')
        @contextlib.contextmanager
        def prepared(*args):
            yield self.wheel, self.checksum
        with patch.object(setup, 'validate_install'), patch.object(setup, 'prepared_wheel', prepared), \
             patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.shutil, 'which', side_effect=lambda name: '/usr/bin/sudo' if name == 'sudo' else sys.executable), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            setup.execute(args, layout, Mock())
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['/usr/bin/sudo', '--'])
        self.assertIn('--prepared-wheel', command)
        self.assertIn(self.checksum, command)
        self.assertIn('--system', command)

    def test_system_uninstall_dispatch_does_not_build_or_run_as_user(self):
        args = setup.parser().parse_args(['uninstall', '--system', '--yes'])
        with patch.object(setup, 'prepared_wheel') as build, \
             patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.shutil, 'which', side_effect=lambda name: '/usr/bin/sudo' if name == 'sudo' else sys.executable), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            setup.execute(args, setup.Layout.select('system'), Mock())
        build.assert_not_called()
        self.assertIn('uninstall', run.call_args.args[0])
        self.assertNotIn('--prepared-wheel', run.call_args.args[0])

    def test_installation_lock_rejects_a_competing_writer(self):
        with setup.installation_lock(self.layout):
            with self.assertRaisesRegex(setup.InstallError, 'Another installer'):
                with setup.installation_lock(self.layout):
                    self.fail('Lock contention must not enter the mutation block')

    def test_private_prepared_wheel_flags_cannot_install_in_a_user_scope(self):
        args = setup.parser().parse_args(['install', '--user', '--prefix', str(self.base / 'target'),
                                         '--prepared-wheel', str(self.wheel), '--wheel-sha256', self.checksum, '--yes'])
        with self.assertRaisesRegex(setup.InstallError, 'reserved'):
            setup.execute(args, self.layout, Mock())
        self.assertFalse(self.layout.runtime.parent.exists())


if __name__ == '__main__':
    unittest.main()
