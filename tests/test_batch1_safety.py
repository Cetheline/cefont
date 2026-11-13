"""Batch 1 behavior checks: dummy data only, isolated paths and mocked services."""
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from cefont.core import backup, collections, fontconfig, google_fonts, installer


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cefont-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.fonts = self.base / 'managed'
        self.fonts.mkdir()
        self.db = Mock()
        for module in (installer, google_fonts, backup):
            self.start_patch(module, 'ensure_directories')
            self.start_patch(module, 'trigger_fontconfig_cache')
        self.start_patch(installer, 'USER_FONT_DIR', str(self.fonts))
        self.start_patch(backup, 'USER_FONT_DIR', str(self.fonts))
        self.start_patch(google_fonts, 'GOOGLE_FONTS_DIR', str(self.fonts))
        for name in ('ACTIONS_FILE', 'COLLECTIONS_FILE', 'COMPARISONS_FILE',
                     'PINNED_COMPARISONS_FILE', 'SOURCES_FILE', 'FONTCONFIG_CONF_DIR',
                     'PACKAGE_CONFIG_DIR'):
            self.start_patch(backup, name, str(self.base / name))
        real_temporary_directory = tempfile.TemporaryDirectory
        self.start_patch(installer.tempfile, 'TemporaryDirectory',
                         side_effect=lambda **kw: real_temporary_directory(**dict({'dir': str(self.base)}, **kw)))
        real_mkdtemp = tempfile.mkdtemp
        def sandbox_mkdtemp(suffix=None, prefix=None, dir=None):
            return real_mkdtemp(suffix=suffix, prefix=prefix, dir=dir or str(self.base))
        self.start_patch(backup.tempfile, 'mkdtemp', side_effect=sandbox_mkdtemp)
        self.install = installer.FontInstaller(db=self.db)
        self.google = google_fonts.GoogleFontsManager(db=self.db)

    def start_patch(self, obj, name, *args, **kwargs):
        p = patch.object(obj, name, *args, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def dummy(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'disposable fixture, not a font')
        return path

    def test_google_rejects_absolute_and_traversal_without_mutation(self):
        for family in ('../victim', str(self.base / 'victim'), '.', ''):
            with self.subTest(family=family):
                victim = self.base / 'victim'
                victim.mkdir(exist_ok=True)
                sentinel = self.dummy(victim / 'keep')
                with patch.object(google_fonts.os, 'remove') as remove, \
                     patch.object(google_fonts.os, 'rmdir') as rmdir:
                    ok, _ = self.google.uninstall_family(family)
                self.assertFalse(ok)
                remove.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(sentinel.exists())
        self.db.purge_directory.assert_not_called()

    def test_google_rejects_family_symlink(self):
        outside = self.base / 'outside'
        outside.mkdir()
        sentinel = self.dummy(outside / 'keep')
        (self.fonts / 'Alias').symlink_to(outside, target_is_directory=True)
        ok, _ = self.google.uninstall_family('Alias')
        self.assertFalse(ok)
        self.assertTrue(sentinel.exists())
        self.db.purge_directory.assert_not_called()

    def test_google_valid_family_does_not_follow_internal_symlink(self):
        family = self.fonts / 'Real Family'
        self.dummy(family / 'nested' / 'dummy.txt')
        outside = self.dummy(self.base / 'outside' / 'keep')
        (family / 'link').symlink_to(outside.parent, target_is_directory=True)
        ok, message = self.google.uninstall_family('Real Family')
        self.assertTrue(ok, message)
        self.assertFalse(family.exists())
        self.assertTrue(outside.exists())
        self.db.purge_directory.assert_called_once_with(str(family))

    def test_remove_rejects_sibling_prefix(self):
        target = self.dummy(self.base / 'managed-other' / 'dummy.txt')
        with patch.object(installer.os, 'remove') as remove:
            ok, _ = self.install.remove_font(str(target))
        self.assertFalse(ok)
        remove.assert_not_called()
        self.assertTrue(target.exists())
        self.db.delete_fonts.assert_not_called()

    def test_remove_rejects_symlink_parent_and_leaf(self):
        target = self.dummy(self.base / 'outside' / 'keep')
        (self.fonts / 'alias').symlink_to(target.parent, target_is_directory=True)
        (self.fonts / 'leaf').symlink_to(target)
        for path in (self.fonts / 'alias' / 'keep', self.fonts / 'leaf'):
            with self.subTest(path=path), patch.object(installer.os, 'remove') as remove:
                ok, _ = self.install.remove_font(str(path))
                self.assertFalse(ok)
                remove.assert_not_called()
        self.assertTrue(target.exists())
        self.db.delete_fonts.assert_not_called()

    def test_remove_valid_file_prunes_only_managed_parents(self):
        target = self.dummy(self.fonts / 'Family' / 'dummy.txt')
        self.assertTrue(self.install.remove_font(str(target))[0])
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())
        self.assertTrue(self.fonts.exists())

    def test_remove_does_not_follow_parent_swapped_after_preflight(self):
        target = self.dummy(self.fonts / 'Family' / 'dummy.txt')
        sentinel = self.dummy(self.base / 'outside' / 'dummy.txt')
        real_validate = installer.validate_regular_file
        def swap_after_validation(root, path):
            real_validate(root, path)
            target.parent.rename(self.fonts / 'Moved')
            target.parent.symlink_to(sentinel.parent, target_is_directory=True)
        with patch.object(installer, 'validate_regular_file', side_effect=swap_after_validation):
            ok, _ = self.install.remove_font(str(target))
        self.assertFalse(ok)
        self.assertTrue(sentinel.exists())
        self.assertTrue((self.fonts / 'Moved' / 'dummy.txt').exists())
        self.db.delete_fonts.assert_not_called()

    def test_remove_family_validates_all_candidates_before_deleting(self):
        good = self.dummy(self.fonts / 'Good' / 'dummy.txt')
        outside = self.dummy(self.base / 'outside' / 'keep')
        (self.fonts / 'Alias').symlink_to(outside.parent, target_is_directory=True)
        self.db.get_fonts.return_value = [dict(filepath=str(good)),
                                         dict(filepath=str(self.fonts / 'Alias' / 'keep'))]
        with patch.object(installer.os, 'remove') as remove:
            ok, _ = self.install.remove_font('Family')
        self.assertFalse(ok)
        remove.assert_not_called()
        self.assertTrue(good.exists())
        self.db.delete_fonts.assert_not_called()

    def test_export_rejects_existing_destinations_and_overlapping_sources(self):
        for dest in (self.base / 'out', self.base / 'out.zip', self.fonts,
                     self.fonts / 'nested' / 'new.zip'):
            with self.subTest(dest=dest):
                if dest == self.base / 'out':
                    self.dummy(dest / 'fonts' / 'keep')
                elif dest == self.base / 'out.zip':
                    self.dummy(dest)
                with patch.object(backup.shutil, 'rmtree') as remove:
                    ok, _ = backup.export_backup(str(dest))
                self.assertFalse(ok)
                remove.assert_not_called()
        self.assertTrue((self.base / 'out' / 'fonts' / 'keep').exists())

    def test_export_rejects_dangling_symlink(self):
        dest = self.base / 'link.zip'
        dest.symlink_to(self.base / 'absent')
        self.assertFalse(backup.export_backup(str(dest))[0])
        self.assertTrue(dest.is_symlink())

    def test_export_preserves_preexisting_staging_and_valid_output(self):
        self.dummy(self.fonts / 'Family' / 'dummy.txt')
        for suffix in ('', '.zip', '.tar.gz'):
            with self.subTest(suffix=suffix):
                dest = self.base / ('result' + suffix)
                sentinel = self.dummy(Path(str(dest) + '.staging') / 'keep')
                ok, message = backup.export_backup(str(dest))
                self.assertTrue(ok, message)
                self.assertTrue(sentinel.exists())
                self.assertTrue(dest.exists())
                if not suffix:
                    self.assertEqual((dest / 'fonts' / 'Family' / 'dummy.txt').read_bytes(),
                                     b'disposable fixture, not a font')
                elif suffix == '.zip':
                    with zipfile.ZipFile(str(dest)) as archive:
                        self.assertEqual(archive.read('fonts/Family/dummy.txt'),
                                         b'disposable fixture, not a font')
                else:
                    with tarfile.open(str(dest)) as archive:
                        with archive.extractfile('fonts/Family/dummy.txt') as source:
                            self.assertEqual(source.read(), b'disposable fixture, not a font')

    def test_export_does_not_overwrite_destination_created_during_staging(self):
        self.dummy(self.fonts / 'dummy.txt')
        real_copytree = backup.shutil.copytree
        for suffix in ('', '.zip'):
            dest = self.base / ('late' + suffix)
            def concurrent_output(source, target):
                result = real_copytree(source, target)
                if suffix:
                    self.dummy(dest)
                else:
                    self.dummy(dest / 'keep')
                return result
            with self.subTest(suffix=suffix), \
                 patch.object(backup.shutil, 'copytree', side_effect=concurrent_output):
                ok, _ = backup.export_backup(str(dest))
            self.assertFalse(ok)
            sentinel = dest if suffix else dest / 'keep'
            self.assertEqual(sentinel.read_bytes(), b'disposable fixture, not a font')

    def test_export_publish_failure_removes_only_its_reservation(self):
        dest = self.base / 'failed-directory'
        with patch.object(backup.os, 'rename', side_effect=OSError('injected')):
            ok, _ = backup.export_backup(str(dest))
        self.assertFalse(ok)
        self.assertFalse(dest.exists())
        self.assertEqual([x.name for x in self.base.iterdir()], ['managed'])

    def test_google_delete_failure_does_not_purge_database(self):
        self.dummy(self.fonts / 'Family' / 'dummy.txt')
        with patch('cefont.core.filesystem.os.unlink', side_effect=PermissionError('injected')):
            ok, _ = self.google.uninstall_family('Family')
        self.assertFalse(ok)
        self.db.purge_directory.assert_not_called()
        self.assertTrue((self.fonts / 'Family' / 'dummy.txt').exists())

    def test_family_removal_keeps_system_faces_and_deduplicates_user_faces(self):
        user = self.dummy(self.fonts / 'Family' / 'dummy.txt')
        system = self.dummy(self.base / 'system' / 'keep')
        self.db.get_fonts.return_value = [dict(filepath=str(user)), dict(filepath=str(user)),
                                         dict(filepath=str(system))]
        ok, message = self.install.remove_font('Family')
        self.assertTrue(ok, message)
        self.assertTrue(system.exists())
        self.db.delete_fonts.assert_called_once_with([str(user)])

    def test_export_copy_failure_leaves_no_destination(self):
        self.dummy(self.fonts / 'dummy.txt')
        dest = self.base / 'failed.zip'
        with patch.object(backup.shutil, 'copytree', side_effect=OSError('injected')):
            ok, _ = backup.export_backup(str(dest))
        self.assertFalse(ok)
        self.assertFalse(dest.exists())
        self.assertEqual(sorted(x.name for x in self.base.iterdir()), ['managed'])

    def test_empty_collection_activation_is_noop(self):
        for col in ({'families': [], 'children': []},
                    {'families': [], 'children': [{'families': [], 'children': []}]}):
            with patch.object(collections, 'get_collection', return_value=col), \
                 patch.object(fontconfig, 'load_rejected_families', return_value={'Other'}), \
                 patch.object(fontconfig, 'save_rejected_families') as save:
                self.assertEqual(collections.enable_collection('Empty'), (True, 0))
                save.assert_not_called()

    def test_populated_collection_enables_only_members(self):
        with patch.object(collections, 'get_collection', return_value={'families': ['A']}), \
             patch.object(fontconfig, 'load_rejected_families', return_value={'A', 'Other'}), \
             patch.object(fontconfig, 'save_rejected_families') as save:
            self.assertEqual(collections.enable_collection('One'), (True, 1))
            save.assert_called_once_with({'Other'})

    def make_tar(self, name, members):
        path = self.base / name
        with tarfile.open(str(path), 'w:gz') as archive:
            for member, payload in members:
                archive.addfile(member, io.BytesIO(payload) if payload else None)
        return path

    def regular(self, name, payload=b'fixture'):
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        return member, payload

    def test_tar_rejects_unsafe_members_before_any_install_or_restore(self):
        # Force old permissive semantics on 3.14, without using an older interpreter.
        # Even the absolute escape target is disposable, inside this test's sandbox.
        unsafe = []
        for name in ('../escape', str(self.base / 'escape'), 'dir/../../escape'):
            unsafe.append(self.regular(name))
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            member = tarfile.TarInfo('bad')
            member.type = kind
            member.linkname = str(self.base / 'escape')
            unsafe.append((member, b''))
        for index, bad in enumerate(unsafe):
            archive = self.make_tar('unsafe%d.tar.gz' % index,
                                    [self.regular('valid.ttf'), bad])
            with self.subTest(index=index), \
                 patch.object(tarfile.TarFile, 'extraction_filter',
                              staticmethod(getattr(tarfile, 'fully_trusted_filter', lambda member, path: member)), create=True), \
                 patch.object(self.install, 'install_font', return_value=(True, '', '')) as install, \
                 patch.object(backup.shutil, 'copy2') as copy:
                count, errors = self.install.install_archive(str(archive))
                ok, _ = backup.import_backup(str(archive), db=self.db)
                self.assertEqual(count, 0)
                self.assertTrue(errors)
                self.assertFalse(ok)
                install.assert_not_called()
                copy.assert_not_called()
            self.db.sync.assert_not_called()

    def test_tar_accepts_nested_regular_files_and_root_directory(self):
        root = tarfile.TarInfo('')
        root.type = tarfile.DIRTYPE
        archive = self.make_tar('valid.tar.gz', [(root, b''), self.regular('Family/dummy.ttf')])
        with patch.object(self.install, 'install_font', return_value=(True, '', '')) as install:
            count, errors = self.install.install_archive(str(archive))
        self.assertEqual((count, errors), (1, []))
        install.assert_called_once()

    def test_tar_rejects_duplicate_and_conflicting_members(self):
        for index, members in enumerate((
                [self.regular('same.ttf'), self.regular('same.ttf')],
                [self.regular('parent'), self.regular('parent/child.ttf')])):
            archive = self.make_tar('conflict%d.tar.gz' % index, members)
            with self.subTest(index=index), \
                 patch.object(self.install, 'install_font') as install:
                count, errors = self.install.install_archive(str(archive))
                self.assertEqual(count, 0)
                self.assertTrue(errors)
                install.assert_not_called()

    def test_valid_tar_backup_import_retains_config_and_font_layout(self):
        config = self.base / 'config-output'
        config.mkdir()
        self.start_patch(backup, 'PACKAGE_CONFIG_DIR', str(config))
        archive = self.make_tar('backup.tar.gz', [self.regular('Collections.json', b'[]'),
                                                self.regular('fonts/Family/dummy.txt')])
        ok, message = backup.import_backup(str(archive), db=self.db)
        self.assertTrue(ok, message)
        self.assertEqual((config / 'Collections.json').read_bytes(), b'[]')
        self.assertEqual((self.fonts / 'Family' / 'dummy.txt').read_bytes(), b'fixture')
        self.db.sync.assert_called_once_with(force=True)

    def test_tar_link_is_rejected_with_current_default_too(self):
        link = tarfile.TarInfo('link')
        link.type = tarfile.SYMTYPE
        link.linkname = 'somewhere'
        archive = self.make_tar('link.tar.gz', [(link, b'')])
        with patch.object(self.install, 'install_font') as install:
            count, errors = self.install.install_archive(str(archive))
        self.assertEqual(count, 0)
        self.assertTrue(errors)
        install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
