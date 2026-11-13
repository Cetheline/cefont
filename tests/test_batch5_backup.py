"""Backup restore only writes disposable destinations."""
from pathlib import Path
import tempfile
import zipfile
import unittest
from unittest.mock import Mock, patch

from cefont.core import backup


class ImportPathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='cefont-myfonts-backup-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.source = self.base / 'restore'
        self.source.mkdir()
        for setting, subdir in (('USER_FONT_DIR', 'dest-fonts'), ('PACKAGE_CONFIG_DIR', 'settings'),
                                ('FONTCONFIG_CONF_DIR', 'fc')):
            target = self.base / subdir
            target.mkdir()
            context = patch.object(backup, setting, str(target))
            context.start()
            self.addCleanup(context.stop)
        for name in ('ensure_directories', 'trigger_fontconfig_cache'):
            context = patch.object(backup, name)
            context.start()
            self.addCleanup(context.stop)

    def test_import_classifies_relative_components_not_ancestor_names(self):
        files = {'fonts/Family/sample.ttf': b'fixture', 'other/note.txt': b'ignore',
                 'fonts-old/another.ttf': b'ignore', 'fontconfig-backup/extra.conf': b'ignore',
                 'fonts/Family/Actions.json': b'font payload', 'Actions.json': b'[]',
                 'fontconfig/conf.d/sample.conf': b'<fontconfig/>'}
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        database = Mock()
        ok, message = backup.import_backup(str(self.source), db=database)
        self.assertTrue(ok, message)
        actual = {p.relative_to(self.base / 'dest-fonts').as_posix(): p.read_bytes()
                  for p in (self.base / 'dest-fonts').rglob('*') if p.is_file()}
        self.assertEqual(actual, {'Family/sample.ttf': b'fixture', 'Family/Actions.json': b'font payload'})
        self.assertEqual((self.base / 'settings/Actions.json').read_bytes(), b'[]')
        self.assertEqual({p.name for p in (self.base / 'fc').glob('*.conf')}, {'sample.conf'})
        database.sync.assert_called_once_with(force=True)

    def test_zip_import_preserves_the_same_relative_layout(self):
        archive = self.base / 'fonts-backup.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('fonts/Family/sample.ttf', b'fixture')
            output.writestr('fonts-other/no.ttf', b'ignore')
        ok, message = backup.import_backup(str(archive), db=Mock())
        self.assertTrue(ok, message)
        self.assertEqual((self.base / 'dest-fonts/Family/sample.ttf').read_bytes(), b'fixture')
        self.assertEqual(len(list((self.base / 'dest-fonts').rglob('*.ttf'))), 1)

    def test_link_rejection_happens_before_any_payload_is_copied(self):
        (self.source / 'Actions.json').write_bytes(b'[]')
        fonts = self.source / 'fonts'
        fonts.mkdir()
        (fonts / 'linked.ttf').symlink_to(self.base / 'outside')
        database = Mock()
        ok, _ = backup.import_backup(str(self.source), db=database)
        self.assertFalse(ok)
        self.assertEqual(list((self.base / 'settings').iterdir()), [])
        self.assertEqual(list((self.base / 'dest-fonts').iterdir()), [])
        database.sync.assert_not_called()
