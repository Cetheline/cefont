"""Structured setup protocol; all writable paths belong to temporary fixtures."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_cefont as setup


class InstallerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cefont-bridge-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = dict(os.environ, HOME=str(self.base / 'home'), XDG_DATA_HOME=str(self.base / 'data'),
                        XDG_CONFIG_HOME=str(self.base / 'config'), XDG_CACHE_HOME=str(self.base / 'cache'))
        # These cases exercise the developer-build fallback independently of releases.
        bundled = patch.object(setup, 'bundled_ui', return_value=None)
        bundled.start()
        self.addCleanup(bundled.stop)

    def call(self, *args):
        result = subprocess.run([sys.executable, '-I', '-B', str(setup.SOURCE / 'scripts/install_cefont.py'),
                                 '--ui-json', *args], env=self.env, capture_output=True, text=True, timeout=180)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        return result, events

    def test_overview_is_read_only_and_has_explicit_scope_layouts(self):
        result, events = self.call('overview')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['type'], 'overview')
        self.assertEqual(events[0]['layouts']['user']['status'], 'not installed')
        self.assertEqual(list(self.base.iterdir()), [])

    def test_execute_requires_explicit_confirmation(self):
        result, events = self.call('execute', 'install', '--user', '--prefix', str(self.base / 'target'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(events[-1]['ok'])
        self.assertIn('--yes', events[-1]['message'])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_dry_run_never_builds_or_creates_destinations(self):
        result, events = self.call('execute', 'install', '--user', '--yes', '--dry-run', '--prefix', str(self.base / 'target'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(events[-1]['dry_run'])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_failed_check_preserves_foreign_files(self):
        target = self.base / 'target'
        runtime = target / 'lib/cefont'
        runtime.mkdir(parents=True)
        (runtime / 'personal').write_text('keep')
        result, events = self.call('check', 'install', '--user', '--prefix', str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(e['type'] == 'checks' for e in events))
        self.assertEqual((runtime / 'personal').read_text(), 'keep')
        self.assertEqual(list(runtime.iterdir()), [runtime / 'personal'])

    def test_json_failure_does_not_replay_terminal_controls(self):
        ui = setup.JsonInterface()
        output = io.StringIO()
        with redirect_stdout(output):
            ui.failure('\x1b]52;c;payload\x07bad\x00')
        event = json.loads(output.getvalue())
        self.assertEqual(event['message'], 'bad')

    def test_missing_go_falls_back_without_creating_cache(self):
        args = setup.parser().parse_args([])
        with patch.dict(os.environ, self.env), patch.object(setup.shutil, 'which', return_value=None):
            self.assertIsNone(setup.launch_fullscreen(args))
        self.assertFalse((self.base / 'cache').exists())

    def test_compiled_ui_is_reused_without_go(self):
        args = setup.parser().parse_args(['--prefix', str(self.base / 'target')])
        def run(arguments, **kwargs):
            if arguments[1] == 'build':
                output = Path(arguments[arguments.index('-o') + 1])
                output.write_bytes(b'fixture executable')
                output.chmod(0o755)
            return subprocess.CompletedProcess(arguments, 0)
        with patch.dict(os.environ, self.env), patch.object(setup.shutil, 'which', return_value='/fixture/go'), \
             patch.object(setup.subprocess, 'run', side_effect=run) as calls, \
             patch.object(setup.os, 'execv') as launch:
            self.assertEqual(setup.launch_fullscreen(args), 0)
            self.assertEqual(calls.call_count, 1)
            launch.assert_called_once()
        with patch.dict(os.environ, self.env), patch.object(setup.shutil, 'which', return_value=None) as lookup, \
             patch.object(setup.os, 'execv') as launch:
            self.assertEqual(setup.launch_fullscreen(args), 0)
            lookup.assert_not_called()
            launch.assert_called_once()
        self.assertFalse((self.base / 'target').exists())

    def test_writable_cache_parent_is_rejected_before_compilation(self):
        self.base.chmod(0o777)
        try:
            with patch.dict(os.environ, self.env), patch.object(setup.subprocess, 'run') as run:
                with self.assertRaisesRegex(setup.InstallError, 'untrusted'):
                    setup.launch_fullscreen(setup.parser().parse_args([]))
            run.assert_not_called()
            self.assertFalse((self.base / 'cache').exists())
        finally:
            self.base.chmod(0o700)

    def test_elevated_wait_defers_stop_until_child_finishes(self):
        args = setup.parser().parse_args(['install', '--system', '--ui-json', 'execute', '--yes'])
        ui = setup.JsonInterface()
        previous = signal.getsignal(signal.SIGTERM)
        output = io.StringIO()
        with redirect_stdout(output), setup.elevated_wait(args, ui):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertIn('administrator operation', output.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_structured_install_and_uninstall_outside_checkout(self):
        prefix = self.base / 'isolated target'
        sentinel = self.base / 'data/fonts/personal.ttf'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b'personal fixture')
        result, events = self.call('execute', 'install', '--user', '--yes', '--prefix', str(prefix))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(events[-1]['ok'])
        self.assertTrue(any(e['type'] == 'stage_done' for e in events))
        self.assertTrue(any(e['type'] == 'complete' for e in events))
        result, events = self.call('execute', 'uninstall', '--user', '--yes', '--prefix', str(prefix))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(events[-1]['ok'])
        self.assertFalse((prefix / 'lib/cefont').exists())
        self.assertFalse((prefix / 'bin/cefont').exists())
        self.assertEqual(sentinel.read_bytes(), b'personal fixture')


class BundledUITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cefont-bundled-ui-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'installer/tui'
        self.source.mkdir(parents=True)
        for name in ('main.go', 'go.mod', 'go.sum'):
            (self.source / name).write_text('fixture')
        self.folder = self.base / 'installer/bin'
        self.folder.mkdir()
        self.binary = self.folder / 'cefont-setup-linux-amd64'
        self.binary.write_bytes(b'fixture executable')
        self.binary.chmod(0o755)
        self.manifest = dict(schema=1, source_sha256=setup.ui_source_fingerprint(self.source), binaries={
            'linux-amd64': dict(file=self.binary.name, sha256=hashlib.sha256(self.binary.read_bytes()).hexdigest())})
        self.save_manifest()

    def save_manifest(self):
        (self.folder / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_bundled_binary_runs_without_go_or_cache_creation(self):
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup.sys, 'platform', 'linux'), \
             patch.object(setup.os, 'uname', return_value=type('Host', (), {'machine': 'x86_64'})()), \
             patch.object(setup.shutil, 'which', side_effect=AssertionError('Go must not be searched')), \
             patch.object(setup.os, 'execv') as launch, \
             patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.base / 'absent-cache')}):
            self.assertEqual(setup.launch_fullscreen(setup.parser().parse_args([])), 0)
        self.assertEqual(launch.call_args.args[0], str(self.binary))
        self.assertFalse((self.base / 'absent-cache').exists())

    def test_modified_binary_is_refused(self):
        self.binary.write_bytes(b'modified')
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup.os, 'uname',
                return_value=type('Host', (), {'machine': 'x86_64'})()):
            with self.assertRaisesRegex(setup.InstallError, 'checksum'):
                setup.bundled_ui(self.source)

    def test_stale_binary_is_not_silently_used(self):
        (self.source / 'main.go').write_text('updated source')
        with patch.object(setup, 'SOURCE', self.base):
            self.assertIsNone(setup.bundled_ui(self.source))

    def test_manifest_cannot_redirect_execution_outside_bundle(self):
        self.manifest['binaries']['linux-amd64']['file'] = '../../foreign'
        self.save_manifest()
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup.os, 'uname',
                return_value=type('Host', (), {'machine': 'x86_64'})()):
            with self.assertRaises(setup.InstallError):
                setup.bundled_ui(self.source)

    def test_unsupported_architecture_falls_back_without_execution(self):
        with patch.object(setup, 'SOURCE', self.base), patch.object(setup.os, 'uname',
                return_value=type('Host', (), {'machine': 'unknown'})()):
            self.assertIsNone(setup.bundled_ui(self.source))


if __name__ == '__main__':
    unittest.main()
