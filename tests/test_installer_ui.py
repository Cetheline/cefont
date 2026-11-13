"""Read-only UI checks and disposable installer navigation fixtures."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts import install_cefont as setup


class InstallerUITests(unittest.TestCase):
    def test_preflight_refuses_existing_target_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            runtime = prefix / 'lib/cefont'
            runtime.mkdir(parents=True)
            marker = runtime / 'personal'
            marker.write_text('preserve')
            ui = MagicMock(interactive=False)
            with patch.object(setup, 'Interface', return_value=ui), \
                 patch.object(setup, 'python_checks', return_value=[]), \
                 patch.object(setup, 'execute') as execute:
                result = setup.main(['install', '--user', '--prefix', directory, '--yes'])
            self.assertEqual(result, 1)
            execute.assert_not_called()
            ui.confirm.assert_not_called()
            self.assertEqual(marker.read_text(), 'preserve')

    def test_back_from_review_can_change_scope_without_execution(self):
        ui = Mock(interactive=True)
        ui.choose.side_effect = ['Install CeFont', 'Prebuilt release (recommended)', 'Current user', 'System-wide']
        ui.review.side_effect = ['Back', 'Cancel']
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(setup, 'Interface', return_value=ui), \
             patch.object(setup, 'preflight', return_value=True), \
             patch.object(setup, 'execute') as execute:
            self.assertEqual(setup.main(['--prefix', directory]), 0)
        self.assertEqual([call.args[1].scope for call in ui.review.call_args_list], ['user', 'system'])
        execute.assert_not_called()

    def test_dry_run_skips_dependency_checks_and_mutations(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(setup, 'preflight') as preflight, \
             patch.object(setup, 'execute') as execute:
            result = setup.main(['install', '--user', '--prefix', directory + '/absent', '--dry-run'])
            self.assertEqual(result, 0)
            self.assertEqual(list(Path(directory).iterdir()), [])
        preflight.assert_not_called()
        execute.assert_not_called()

    def test_status_does_not_follow_symlink_or_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            outside = prefix / 'outside'
            outside.mkdir()
            (prefix / 'lib').symlink_to(outside, target_is_directory=True)
            result = setup.installation_status(setup.Layout.select('user', prefix))
            self.assertIn('review', result.lower())
            self.assertEqual(list(outside.iterdir()), [])

    def test_plain_narrow_panel_wraps_without_terminal_controls(self):
        with patch.dict(os.environ, {'NO_COLOR': '1'}), \
             patch.object(setup.shutil, 'get_terminal_size', return_value=os.terminal_size((36, 24))):
            ui = setup.Interface(plain=True)
            output = io.StringIO()
            with redirect_stdout(output):
                ui.panel('Install location', ['Runtime: /a/very/long/path/' + 'x' * 70])
            self.assertNotIn('\x1b', output.getvalue())
            self.assertTrue(all(len(line) <= 36 for line in output.getvalue().splitlines()))

    def test_failed_work_sanitizes_all_control_characters(self):
        ui = setup.Interface(plain=True)
        failed = subprocess.CompletedProcess([], 1, '\x1b]52;c;secret\x07bad\x08\r\x00\x1b[2J')
        with patch.object(setup.subprocess, 'run', return_value=failed):
            with self.assertRaises(setup.InstallError) as caught:
                ui.run([sys.executable, '-c', 'pass'], 'Build fixture')
        self.assertIn('bad', str(caught.exception))
        self.assertFalse(any(c in str(caught.exception) for c in ('\x1b', '\x07', '\x08', '\r', '\x00')))

    def test_progress_is_visible_while_child_output_is_captured(self):
        ui = setup.Interface(plain=True)
        ui.styled = True
        ui.animate = True
        ui.interactive = True
        stream = io.StringIO()
        observed = []
        def child(*args, **kwargs):
            time.sleep(.2)
            observed.append(stream.getvalue())
            return subprocess.CompletedProcess([], 0, 'captured build output')
        with patch.object(setup.sys, 'stderr', stream), \
             patch.object(setup.subprocess, 'run', side_effect=child):
            self.assertEqual(ui.run(['fixture'], 'Build fixture'), 'captured build output')
        self.assertIn('Build fixture', observed[0])
        finished = stream.getvalue()
        time.sleep(.15)
        self.assertEqual(stream.getvalue(), finished, 'spinner thread must stop')

    def test_unicode_paths_fit_styled_panel_cells(self):
        with patch.object(setup.shutil, 'get_terminal_size', return_value=os.terminal_size((32, 24))):
            ui = setup.Interface(plain=True)
            ui.styled = True
            output = io.StringIO()
            with redirect_stdout(output):
                ui.panel('Installation', ['/tmp/' + '字体' * 20])
            lines = setup.safe_text(output.getvalue()).splitlines()
            self.assertTrue(all(setup.cell_width(line) <= 32 for line in lines))
            borders = [line for line in lines if line.startswith('│')]
            self.assertTrue(all(setup.cell_width(line) == ui.width for line in borders))

    def test_no_color_and_no_animation_disable_motion(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=True), \
             patch.object(setup.sys.stdout, 'isatty', return_value=True), \
             patch.dict(os.environ, {'NO_COLOR': '1', 'TERM': 'xterm-256color'}):
            ui = setup.Interface()
            self.assertFalse(ui.styled)
            self.assertFalse(ui.animate)
            self.assertIsNone(ui.gum)
        with patch.dict(os.environ, {}, clear=True):
            ui = setup.Interface(no_animation=True)
            self.assertFalse(ui.animate)

    def test_light_theme_uses_dark_sage_for_contrast(self):
        ui = setup.Interface(theme='light')
        ui.styled = True
        self.assertEqual(ui.accent, '#27613e')
        self.assertIn('38;5;22m', ui.ink('Selected'))

    def test_system_uninstall_defers_privileged_inventory_without_sudo(self):
        args = setup.parser().parse_args(['uninstall', '--system'])
        ui = MagicMock()
        with patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup, 'python_checks', return_value=[]), \
             patch.object(setup, 'validate_uninstall') as validate, \
             patch.object(setup.subprocess, 'run') as run:
            self.assertTrue(setup.preflight(args, setup.Layout.select('system'), ui))
        validate.assert_not_called()
        run.assert_not_called()
        checks = ui.checks.call_args.args[0]
        self.assertTrue(any(check.label == 'Ownership' and check.status == 'pending' for check in checks))

    def test_timed_out_progress_thread_stops(self):
        ui = setup.Interface(plain=True)
        ui.styled = ui.animate = True
        stream = io.StringIO()
        with patch.object(setup.sys, 'stderr', stream), \
             patch.object(setup.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 180)):
            with self.assertRaisesRegex(setup.InstallError, 'timed out'):
                ui.run(['fixture'], 'Timeout fixture')
        finished = stream.getvalue()
        time.sleep(.15)
        self.assertEqual(stream.getvalue(), finished)

    def test_multiline_diagnostics_keep_the_panel_aligned(self):
        ui = setup.Interface(plain=True)
        ui.styled = True
        output = io.StringIO()
        with redirect_stdout(output):
            ui.panel('Build failed', ['Compiler failed\nSecond diagnostic line'])
        lines = setup.safe_text(output.getvalue()).splitlines()
        self.assertTrue(all(line.endswith('│') for line in lines if 'diagnostic' in line or 'Compiler' in line))

    def test_text_review_defaults_to_cancel_without_gum(self):
        ui = setup.Interface(plain=True)
        with patch('builtins.input', return_value='1'), patch.object(setup.subprocess, 'run') as run:
            self.assertEqual(ui.choose('Apply the reviewed changes?', ['Cancel', 'Back', 'Apply changes']), 'Cancel')
        run.assert_not_called()

    def test_invalid_user_layout_does_not_block_system_overview(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'invalid-relative-directory'}):
            self.assertIn('unavailable', setup.scope_summary('user'))
            self.assertEqual(setup.scope_summary('system', command=True), '/usr/local/bin/cefont')


if __name__ == '__main__':
    unittest.main()
