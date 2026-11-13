"""CLI outcomes with service boundaries mocked, including all mutations."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from cefont import main
from cefont.core import actions
from cefont.core import google_fonts


class CommandOutcomeTests(unittest.TestCase):
    def invoke(self, arguments):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(main.sys, 'argv', ['cefont'] + arguments), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try:
                main.main()
            except SystemExit as exit:
                return exit.code, output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()

    def test_mixed_install_and_remove_batches_fail_but_attempt_all_items(self):
        for arguments, method, results in ((['install', 'good.ttf', 'bad.ttf'], 'install_font',
                                           [(True, 'installed', '/fixture'), (False, 'injected error', None)]),
                                          (['--install', 'good.ttf', 'bad.ttf'], 'install_font',
                                           [(True, 'installed', '/fixture'), (False, 'injected error', None)]),
                                          (['remove', 'good', 'bad'], 'remove_font',
                                           [(True, 'removed'), (False, 'injected error')])):
            manager = Mock()
            getattr(manager, method).side_effect = results
            with self.subTest(arguments=arguments), patch.object(main, 'FontInstaller', return_value=manager):
                status, output, error = self.invoke(arguments)
            self.assertEqual(status, 1)
            self.assertIn('injected error', error)
            self.assertEqual(getattr(manager, method).call_count, 2)

    def test_failed_backup_and_google_operations_exit_nonzero(self):
        cases = [(['export', '/fixture'], 'export_backup', (False, 'injected error')),
                 (['import', '/fixture'], 'import_backup', (False, 'injected error'))]
        for arguments, target, result in cases:
            with self.subTest(arguments=arguments), patch.object(main, target, return_value=result):
                status, _, error = self.invoke(arguments)
            self.assertEqual(status, 1)
            self.assertIn('injected error', error)
        manager = Mock()
        for sub, method, result in [('install', 'install_family', (False, 'injected error', 1)),
                                    ('uninstall', 'uninstall_family', (False, 'injected error')),
                                    ('update', 'update_catalog', (False, 'injected error', 0))]:
            getattr(manager, method).return_value = result
            with self.subTest(sub=sub), patch.object(main, 'GoogleFontsManager', return_value=manager):
                status, _, error = self.invoke(['google', sub] + ([] if sub == 'update' else ['Fixture']))
            self.assertEqual(status, 1)
            self.assertIn('injected error', error)

    def test_failed_action_exits_nonzero_and_wait_checks_child_status(self):
        manager = Mock()
        manager.execute_action.return_value = (False, 'injected error')
        with patch.object(main, 'ActionManager', return_value=manager):
            status, _, error = self.invoke(['actions', 'run', 'Fixture', '--wait'])
        self.assertEqual(status, 1)
        self.assertIn('injected error', error)
        with patch.object(actions, 'ensure_directories'), patch.object(actions.ActionManager, 'load_actions',
                return_value=[dict(action_name='Fixture', executable='false', arguments='')]), \
             patch.object(actions.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7, '', 'failed')):
            ok, message = actions.ActionManager('/not-read').execute_action('Fixture', background=False)
        self.assertFalse(ok)
        self.assertIn('7', message)

    def test_successful_mutations_still_exit_zero(self):
        manager = Mock()
        manager.install_font.return_value = (True, 'installed', '/fixture')
        with patch.object(main, 'FontInstaller', return_value=manager):
            self.assertEqual(self.invoke(['install', 'fixture.ttf'])[0], 0)
        with patch.object(main, 'export_backup', return_value=(True, 'exported')):
            self.assertEqual(self.invoke(['export', '/fixture'])[0], 0)

    def test_empty_and_partial_archive_installations_are_failures(self):
        for result in ((0, []), (1, ['injected archive error'])):
            manager = Mock()
            manager.install_archive.return_value = result
            with self.subTest(result=result), patch.object(main, 'FontInstaller', return_value=manager):
                self.assertEqual(self.invoke(['install', 'fixture.zip'])[0], 1)

    def test_other_failed_mutations_exit_nonzero(self):
        cases = [('CollectionManager', 'create_collection', False, ['collections', 'create', 'Fixture']),
                 ('CollectionManager', 'delete_collection', False, ['collections', 'delete', 'Fixture']),
                 ('CollectionManager', 'set_collection_active', False, ['collections', 'enable', 'Fixture']),
                 ('SourceManager', 'add_source', (False, 'injected error', 0), ['sources', 'add', '/fixture']),
                 ('ActionManager', 'remove_action', False, ['actions', 'remove', 'Fixture']),
                 ('ComparisonManager', 'remove_comparison', False, ['compare', 'pinned-delete', 'Fixture'])]
        for factory, method, result, arguments in cases:
            manager = Mock()
            getattr(manager, method).return_value = result
            with self.subTest(arguments=arguments), patch.object(main, factory, return_value=manager):
                self.assertEqual(self.invoke(arguments)[0], 1)

    def test_partial_google_download_is_reported_as_failure(self):
        info = dict(family='Fixture', files={'regular': 'https://example.invalid/ok',
                                           'bold': 'https://example.invalid/fail'})
        with tempfile.TemporaryDirectory(prefix='cefont-google-') as tmp, \
             patch.object(google_fonts, 'ensure_directories'), \
             patch.object(google_fonts, 'trigger_fontconfig_cache'), \
             patch.object(google_fonts, 'GOOGLE_FONTS_DIR', tmp), \
             patch.object(google_fonts.GoogleFontsManager, 'get_family_info', return_value=info), \
             patch.object(google_fonts.urllib.request, 'urlopen', side_effect=[io.BytesIO(b'fixture'), OSError('injected')]):
            manager = google_fonts.GoogleFontsManager(db=Mock())
            ok, message, count = manager.install_family('Fixture')
        self.assertFalse(ok)
        self.assertEqual(count, 1)
        self.assertIn('1', message)
