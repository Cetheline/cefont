"""Terminal safety tests: all settings calls mocked, all files disposable."""
import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cefont.core import terminal_tester as tt
from cefont.core import terminal_config


class Settings:
    def __init__(self):
        self.values = {'use-system-font': 'true', 'custom-font': "'Original 11'", 'font': "'Original 11'", 'font-scale': '1.0'}
        self.fail_new = False
        self.fail_restore = False
        self.calls = []
    def __call__(self, args, check=False):
        self.calls.append(args)
        if args[0] == 'get':
            out = "'11111111-1111-1111-1111-111111111111'" if args[-1] == 'default' else self.values[args[-1]]
            return subprocess.CompletedProcess(args, 0, out + '\n', '')
        key, value = args[-2:]
        if self.fail_new and key in ('font', 'custom-font') and 'New' in value:
            raise RuntimeError('injected apply failure')
        if self.fail_restore and ('Original' in value or (key == 'use-system-font' and value == 'true')):
            raise RuntimeError('injected restore failure')
        if key in ('font', 'custom-font'):
            try:value = repr(ast.literal_eval(value))
            except (SyntaxError, ValueError):value = repr(value)
        self.values[key] = value
        return subprocess.CompletedProcess(args, 0, '', '')


class TerminalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='cefont-terminal-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.settings = Settings()
        for obj, name, kwargs in (
            (tt, 'RECOVERY_FILE', dict(new=str(self.base / 'recovery.json'))),
            (tt, 'PACKAGE_CACHE_DIR', dict(new=str(self.base))),
            (tt, 'detect_terminal_emulator', dict(return_value=('kgx', {}))),
            (tt, 'run_gsettings', dict(side_effect=self.settings)),
        ):
            mock = patch.object(obj, name, **kwargs)
            mock.start(); self.addCleanup(mock.stop)

    def test_journal_failure_never_applies_font(self):
        before = self.settings.values.copy()
        with patch.object(tt, 'write_recovery_journal', side_effect=OSError('disk full')):
            try:
                with tt.TerminalFontContext('New', 13):pass
            except (OSError, RuntimeError):pass
        self.assertEqual(self.settings.values, before)
        self.assertFalse(any(c[0] == 'set' for c in self.settings.calls))

    def test_partial_application_rolls_back(self):
        self.settings.fail_new = True
        before = self.settings.values.copy()
        with tt.TerminalFontContext('New', 13):pass
        self.assertEqual(self.settings.values, before)

    def test_failed_restore_keeps_record_and_reports_failure(self):
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        self.settings.fail_restore = True
        try:
            with self.assertRaises(RuntimeError):ctx.cleanup()
            self.assertTrue(Path(tt.RECOVERY_FILE).exists())
        finally:
            self.settings.fail_restore = False
            ctx.cleanup()

    def test_kitty_switching_is_disabled_even_when_command_reports_success(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode), \
                 patch.object(tt.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode)), \
                 patch.object(tt.os.path, 'expanduser', return_value=str(self.base / 'kitty.conf')), \
                 patch.object(tt, 'get_process_ancestry', return_value=[]):
                ok, state = tt.apply_terminal_font('kitty', 'New', 13)
                self.assertFalse(ok)
                self.assertIn('error', state)
                self.assertFalse((self.base / 'kitty.conf').exists())

    @unittest.skipUnless(terminal_config.tomllib is not None, 'Alacritty mutation requires a TOML parser')
    def test_alacritty_parent_table_remains_valid(self):
        import tomllib
        config = self.base / 'alacritty.toml'
        original = b'[font]\nsize = 11\n[window]\nopacity = 0.8\n'
        config.write_bytes(original)
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)):
            with tt.TerminalFontContext('New', 13):
                parsed = tomllib.loads(config.read_text())
                self.assertEqual(parsed['font']['normal']['family'], 'New')
                self.assertEqual(parsed['window']['opacity'], 0.8)
        self.assertEqual(config.read_bytes(), original)

    def test_concurrent_session_cannot_replace_record(self):
        first = tt.TerminalFontContext('New', 13).__enter__()
        record = Path(tt.RECOVERY_FILE).read_bytes()
        try:
            try:
                with tt.TerminalFontContext('Other', 14):pass
            except RuntimeError:pass
            self.assertEqual(Path(tt.RECOVERY_FILE).read_bytes(), record)
            self.assertIn('New', self.settings.values['custom-font'])
        finally:first.cleanup()

    def test_journal_open_replace_and_sync_failures_precede_mutation(self):
        from cefont.core import terminal_recovery as recovery
        for operation in ('mkstemp', 'replace', 'fsync'):
            before = self.settings.values.copy()
            self.settings.calls.clear()
            target = recovery.tempfile if operation == 'mkstemp' else recovery.os
            with self.subTest(operation=operation), patch.object(target, operation, side_effect=OSError('injected')):
                with self.assertRaises((OSError, RuntimeError)):
                    tt.TerminalFontContext('New', 13).__enter__()
            self.assertEqual(self.settings.values, before)
            self.assertFalse(any(call[0] == 'set' for call in self.settings.calls))
            if Path(tt.RECOVERY_FILE).exists():tt.recover_orphaned_state()

    def test_journal_is_durable_before_first_setting_write(self):
        from cefont.core import terminal_recovery as recovery
        events = []
        real_sync, real_replace = recovery.os.fsync, recovery.os.replace
        def sync(descriptor):
            events.append('sync'); return real_sync(descriptor)
        def replace(source, target):
            events.append('replace'); return real_replace(source, target)
        def settings(args, check=False):
            if args[0] == 'set':
                self.assertTrue(Path(tt.RECOVERY_FILE).exists())
                self.assertEqual(events[:3], ['sync', 'replace', 'sync'])
            return self.settings(args, check)
        with patch.object(recovery.os, 'fsync', side_effect=sync), \
             patch.object(recovery.os, 'replace', side_effect=replace), \
             patch.object(tt, 'run_gsettings', side_effect=settings):
            with tt.TerminalFontContext('New', 13):pass

    def test_interrupted_initialization_rolls_back(self):
        before = self.settings.values.copy()
        def interrupt(args, check=False):
            if args[0] == 'set' and 'New' in args[-1]:raise KeyboardInterrupt()
            return self.settings(args, check)
        with patch.object(tt, 'run_gsettings', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                tt.TerminalFontContext('New', 13).__enter__()
        self.assertEqual(self.settings.values, before)
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    def test_successful_cleanup_is_idempotent(self):
        before = self.settings.values.copy()
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        ctx.cleanup()
        calls = len(self.settings.calls)
        ctx.cleanup()
        self.assertEqual(len(self.settings.calls), calls)
        self.assertEqual(self.settings.values, before)
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    def test_clear_failure_preserves_retry_record(self):
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        with patch.object(tt, 'clear_recovery_journal', side_effect=OSError('injected')):
            with self.assertRaises(RuntimeError):ctx.cleanup()
        self.assertTrue(Path(tt.RECOVERY_FILE).exists())
        self.assertIn('Recovered', tt.recover_orphaned_state())
        self.assertIsNone(tt.recover_orphaned_state())

    def test_orphan_failure_then_success_keeps_record_until_verified(self):
        before = self.settings.values.copy()
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        ctx._release()  # Simulate process exit after applying, without cleanup.
        self.settings.fail_restore = True
        with self.assertRaises(RuntimeError):tt.recover_orphaned_state()
        self.assertTrue(Path(tt.RECOVERY_FILE).exists())
        self.settings.fail_restore = False
        self.assertIn('Recovered', tt.recover_orphaned_state())
        self.assertEqual(self.settings.values, before)
        self.assertIsNone(tt.recover_orphaned_state())

    def test_corrupt_and_legacy_records_are_not_deleted(self):
        for content in (b'not JSON', b'{"original_state":{},"terminal_type":"kitty"}'):
            Path(tt.RECOVERY_FILE).write_bytes(content)
            with self.subTest(content=content), self.assertRaises(RuntimeError):
                tt.recover_orphaned_state()
            self.assertEqual(Path(tt.RECOVERY_FILE).read_bytes(), content)
        self.assertFalse(any(call[0] == 'set' for call in self.settings.calls))

    def test_active_session_blocks_manual_recovery(self):
        with tt.TerminalFontContext('New', 13):
            record = Path(tt.RECOVERY_FILE).read_bytes()
            with self.assertRaises(RuntimeError):tt.recover_orphaned_state()
            self.assertEqual(Path(tt.RECOVERY_FILE).read_bytes(), record)

    def test_owner_mismatch_cannot_clear_or_overwrite(self):
        from cefont.core.terminal_recovery import RecoveryStore
        store = RecoveryStore(tt.RECOVERY_FILE).acquire()
        try:
            tt.write_recovery_journal(dict(owner='first', version=2), store=store)
            before = Path(tt.RECOVERY_FILE).read_bytes()
            with self.assertRaises(RuntimeError):tt.clear_recovery_journal('other', store=store)
            with self.assertRaises(RuntimeError):tt.write_recovery_journal(dict(owner='other'), store=store)
            self.assertEqual(Path(tt.RECOVERY_FILE).read_bytes(), before)
            tt.clear_recovery_journal('first', store=store)
            tt.clear_recovery_journal('first', store=store)
        finally:store.close()

    def test_second_process_cannot_acquire_live_lock(self):
        import sys
        with tt.TerminalFontContext('New', 13):
            code = ('import sys;from cefont.core.terminal_recovery import RecoveryStore,RecoveryError;'
                    '\ntry: RecoveryStore(sys.argv[1]).acquire()'
                    '\nexcept RecoveryError: print("busy")')
            result = subprocess.run([sys.executable, '-B', '-c', code, tt.RECOVERY_FILE],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'busy')

    def test_kept_record_after_clear_failure_does_not_restore(self):
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        with patch.object(tt, 'clear_recovery_journal', side_effect=OSError('injected')):
            with self.assertRaises(RuntimeError):ctx.keep()
        self.assertEqual(json.loads(Path(tt.RECOVERY_FILE).read_text())['intent'], 'keep')
        self.assertIn('Finalized', tt.recover_orphaned_state())
        self.assertIn('New', self.settings.values['custom-font'])
        self.assertIsNone(tt.recover_orphaned_state())

    def test_gnome_and_kgx_partial_failure_rollback(self):
        for terminal in ('kgx', 'gnome-terminal'):
            self.settings.fail_new = True
            before = self.settings.values.copy()
            with self.subTest(terminal=terminal), \
                 patch.object(tt, 'detect_terminal_emulator', return_value=(terminal, {})):
                with tt.TerminalFontContext('New', 13) as ctx:
                    self.assertFalse(ctx.is_active)
                    self.assertIn('restored', ctx.fallback_error)
            self.assertEqual(self.settings.values, before)
            self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    def test_partial_apply_and_rollback_failure_stay_recoverable(self):
        self.settings.fail_new = self.settings.fail_restore = True
        with self.assertRaises(RuntimeError):tt.TerminalFontContext('New', 13).__enter__()
        record = json.loads(Path(tt.RECOVERY_FILE).read_text())
        self.assertEqual(record['original_state']['original_values']['use-system-font'], 'true')
        self.settings.fail_new = self.settings.fail_restore = False
        tt.recover_orphaned_state()
        self.assertEqual(self.settings.values['use-system-font'], 'true')

    def test_nonzero_return_code_is_not_success(self):
        before = self.settings.values.copy()
        def command(args, check=False):
            if args[0] == 'set' and 'New' in args[-1]:
                return subprocess.CompletedProcess(args, 1, '', 'injected return code')
            return self.settings(args, check)
        with patch.object(tt, 'run_gsettings', side_effect=command):
            with tt.TerminalFontContext('New', 13) as ctx:
                self.assertFalse(ctx.is_active)
        self.assertEqual(self.settings.values, before)

    def test_gsettings_external_edit_blocks_restore_without_overwrite(self):
        ctx = tt.TerminalFontContext('New', 13).__enter__()
        self.settings.values['custom-font'] = "'External 15'"
        with self.assertRaises(RuntimeError):ctx.cleanup()
        self.assertEqual(self.settings.values['custom-font'], "'External 15'")
        self.assertTrue(Path(tt.RECOVERY_FILE).exists())
        self.settings.values['custom-font'] = "'Original 11'"
        tt.recover_orphaned_state()

    @unittest.skipUnless(terminal_config.tomllib is not None, 'Alacritty mutation requires a TOML parser')
    def test_alacritty_nested_dotted_and_inline_configs_roundtrip(self):
        import tomllib
        cases = [
            b'# original comment\n[font]\nsize=11.5\n[font.normal]\nfamily="Old"\nstyle="Bold"\n',
            b'font = {size=11, normal={family="Old"}}\nwindow.opacity=0.9\n',
            b'font.normal.family="Old"\n[[keyboard.bindings]]\nkey="K"\nchars="x"\n',
            b'metric=nan\nwhen=2026-11-11T12:00:00Z\n',
        ]
        config = self.base / 'alacritty.toml'
        for original in cases:
            config.write_bytes(original)
            config.chmod(0o640)
            with self.subTest(original=original), \
                 patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
                 patch.object(tt, '_find_alacritty_config', return_value=str(config)):
                with tt.TerminalFontContext('New "Font" \\ Family', 13):
                    parsed = tomllib.loads(config.read_text())
                    self.assertEqual(parsed['font']['normal']['family'], 'New "Font" \\ Family')
                self.assertEqual(config.read_bytes(), original)
                self.assertEqual(config.stat().st_mode & 0o777, 0o640)

    def test_alacritty_invalid_or_unsupported_input_is_unchanged(self):
        from cefont.core import terminal_config
        for suffix, original in (('.toml', b'[font]\n[font]\n'), ('.yml', b'font: {size: 11}')):
            config = self.base / ('alacritty' + suffix)
            config.write_bytes(original)
            with self.subTest(suffix=suffix), \
                 patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
                 patch.object(tt, '_find_alacritty_config', return_value=str(config)):
                with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
                self.assertEqual(config.read_bytes(), original)
                self.assertFalse(Path(tt.RECOVERY_FILE).exists())
        config = self.base / 'valid.toml'
        config.write_bytes(b'[font]\nsize=11\n')
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)), \
             patch.object(terminal_config, '_value', return_value='INVALID OUTPUT'):
            with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
        self.assertEqual(config.read_bytes(), b'[font]\nsize=11\n')

    def test_alacritty_no_parser_is_safe_fallback(self):
        from cefont.core import terminal_config
        config = self.base / 'valid.toml'
        config.write_bytes(b'[font]\nsize=11\n')
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)), \
             patch.object(terminal_config, 'tomllib', None):
            with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
        self.assertEqual(config.read_bytes(), b'[font]\nsize=11\n')

    @unittest.skipUnless(terminal_config.tomllib is not None, 'Alacritty mutation requires a TOML parser')
    def test_alacritty_external_edit_is_preserved_on_failed_restore(self):
        config = self.base / 'alacritty.toml'
        original = b'[font]\nsize=11\n'
        config.write_bytes(original)
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)):
            ctx = tt.TerminalFontContext('New', 13).__enter__()
            config.write_bytes(b'# external\n[font]\nsize=15\n')
            with self.assertRaises(RuntimeError):ctx.cleanup()
            self.assertEqual(config.read_bytes(), b'# external\n[font]\nsize=15\n')
            self.assertTrue(Path(tt.RECOVERY_FILE).exists())
            config.write_bytes(original)
            tt.recover_orphaned_state()

    def test_public_apply_restore_and_permanent_commit(self):
        before = self.settings.values.copy()
        ok, state = tt.apply_terminal_font('kgx', 'New', 13)
        self.assertTrue(ok)
        self.assertTrue(tt.restore_terminal_font('kgx', state))
        self.assertEqual(self.settings.values, before)
        self.assertTrue(tt.set_terminal_font_permanently('New', 13)[0])
        self.assertIn('New', self.settings.values['custom-font'])
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    def test_unknown_restore_and_xterm_switch_do_not_claim_success(self):
        self.assertFalse(tt.restore_terminal_font('unknown', {}))
        self.assertFalse(tt._restore_kitty({'method': 'remote_control'}))
        with patch.object(tt.sys.stdout, 'write') as output:
            ok, _ = tt.apply_terminal_font('xterm', 'New', 13)
            self.assertFalse(ok)
            output.assert_not_called()

    def test_success_reported_without_actual_application_is_rejected(self):
        before = self.settings.values.copy()
        def ignored(args, check=False):
            if args[0] == 'set':return subprocess.CompletedProcess(args, 0, '', '')
            return self.settings(args, check)
        with patch.object(tt, 'run_gsettings', side_effect=ignored):
            with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
        self.assertEqual(self.settings.values, before)

    def test_signal_setup_failure_does_not_apply_settings(self):
        before = self.settings.values.copy()
        with patch.object(tt.signal, 'signal', side_effect=ValueError('not main thread')):
            with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
        self.assertEqual(self.settings.values, before)
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    @unittest.skipUnless(terminal_config.tomllib is not None, 'Alacritty mutation requires a TOML parser')
    def test_alacritty_post_replace_failure_is_rolled_back(self):
        from cefont.core import terminal_recovery as recovery
        config = self.base / 'alacritty.toml'
        original = b'[font]\nsize=11\n'
        config.write_bytes(original)
        actual = recovery.sync_directory
        failed = []
        def sync(path):
            if Path(path) == self.base and config.read_bytes() != original and not failed:
                failed.append(True);raise OSError('injected post-replace failure')
            return actual(path)
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)), \
             patch.object(recovery, 'sync_directory', side_effect=sync):
            with tt.TerminalFontContext('New', 13) as ctx:self.assertFalse(ctx.is_active)
        self.assertTrue(failed)
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())

    def test_cli_recovery_error_exits_nonzero(self):
        import contextlib
        import io
        from cefont import main
        output = io.StringIO()
        with patch.object(main.sys, 'argv', ['cefont', 'test', '--recover']), \
             patch.object(main, 'recover_orphaned_state', side_effect=tt.RecoveryError('restore failed')), \
             contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as result:main.main()
        self.assertEqual(result.exception.code, 1)
        self.assertIn('restore failed', output.getvalue())

    def test_cli_mock_never_claims_kept_or_restored(self):
        import contextlib
        import io
        from cefont import main
        for action in ('keep', 'revert'):
            output = io.StringIO()
            with patch.object(main, 'get_detailed_font_info', return_value={'family':'New'}), \
                 patch.object(main, 'detect_terminal_emulator', return_value=('kitty', {})), \
                 patch.object(tt, 'detect_terminal_emulator', return_value=('kitty', {})), \
                 patch.object(main, 'run_interactive_test_session', return_value=action), \
                 contextlib.redirect_stdout(output):
                main.cmd_test(main.build_parser().parse_args(['test', 'New']))
            self.assertNotIn('✔ Kept', output.getvalue())
            self.assertNotIn('✔ Restored', output.getvalue())
            self.assertIn('Preview only', output.getvalue())

    @unittest.skipUnless(terminal_config.tomllib is not None, 'Alacritty mutation requires a TOML parser')
    def test_alacritty_retry_syncs_restored_directory_before_clearing_record(self):
        from cefont.core import terminal_config
        config = self.base / 'config' / 'alacritty.toml'
        config.parent.mkdir()
        original = b'[font]\nsize=11\n'
        config.write_bytes(original)
        with patch.object(tt, 'detect_terminal_emulator', return_value=('alacritty', {})), \
             patch.object(tt, '_find_alacritty_config', return_value=str(config)):
            ctx = tt.TerminalFontContext('New', 13).__enter__()
        # Simulate a completed original-file replacement whose directory fsync
        # did not complete. Matching bytes alone must not clear the journal.
        config.write_bytes(original)
        with patch.object(terminal_config, 'sync_directory', side_effect=OSError('injected')):
            with self.assertRaises(RuntimeError):ctx.cleanup()
            self.assertTrue(Path(tt.RECOVERY_FILE).exists())
        self.assertIn('Recovered', tt.recover_orphaned_state())
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(Path(tt.RECOVERY_FILE).exists())
