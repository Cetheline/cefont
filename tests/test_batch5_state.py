"""Concurrent configuration tests; every path is private temporary data."""
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cefont.core import actions, collections, comparison, fontconfig, sources


def create_collections(path, prefix, start):
    with patch.object(collections, 'COLLECTIONS_FILE', path), patch.object(collections, 'ensure_directories'):
        start.wait(10)
        for index in range(10):
            collections.create_collection('%s%d' % (prefix, index))


def create_sources(directory, prefix, start):
    base = Path(directory)
    with patch.object(sources, 'SOURCES_FILE', str(base / 'Sources.xml')), \
         patch.object(sources, 'DIRECTORIES_CONF', str(base / 'fc/Directories.conf')), \
         patch.object(fontconfig, 'DIRECTORIES_CONF', str(base / 'fc/Directories.conf')), \
         patch.object(sources, 'ensure_directories'), patch.object(fontconfig, 'ensure_directories'), \
         patch.object(fontconfig, 'trigger_fontconfig_cache'):
        manager = sources.SourceManager(db=Mock())
        start.wait(10)
        for index in range(3):
            manager.add_source(str(base / ('%s%d' % (prefix, index))))


class StateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='cefont-config-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        for module in (collections, actions, comparison, fontconfig):
            context = patch.object(module, 'ensure_directories')
            context.start(); self.addCleanup(context.stop)
        context = patch.object(fontconfig, 'trigger_fontconfig_cache')
        context.start(); self.addCleanup(context.stop)
        for module, setting, filename in ((collections, 'COLLECTIONS_FILE', 'Collections.json'),
                                          (fontconfig, 'ALIASES_CONF', 'Aliases.conf'),
                                          (fontconfig, 'REJECT_CONF', 'Reject.conf'),
                                          (fontconfig, 'DIRECTORIES_CONF', 'Directories.conf'),
                                          (fontconfig, 'RENDERING_CONF', 'Rendering.conf'),
                                          (fontconfig, 'DISPLAY_CONF', 'Display.conf')):
            context = patch.object(module, setting, str(self.base / filename))
            context.start(); self.addCleanup(context.stop)

    def test_stale_collection_snapshot_cannot_overwrite_a_committed_change(self):
        collections.create_collection('Base')
        first, second = collections.load_collections(), collections.load_collections()
        first.append({'name': 'First'})
        second.append({'name': 'Second'})
        collections.save_collections(first)
        with self.assertRaises(RuntimeError):
            collections.save_collections(second)
        self.assertEqual([c['name'] for c in collections.load_collections()], ['Base', 'First'])

    def test_failed_atomic_publication_preserves_original_bytes(self):
        collections.create_collection('Base')
        path = Path(collections.COLLECTIONS_FILE)
        original = path.read_bytes()
        with patch('os.replace', side_effect=OSError('simulated publish failure')):
            with self.assertRaises((RuntimeError, OSError)):
                collections.create_collection('Second')
        self.assertEqual(path.read_bytes(), original)

    def test_corrupt_configuration_is_not_treated_as_empty_for_mutations(self):
        path = Path(collections.COLLECTIONS_FILE)
        path.write_bytes(b'{invalid JSON')
        with self.assertRaises(RuntimeError):
            collections.create_collection('New')
        self.assertEqual(path.read_bytes(), b'{invalid JSON')

    def test_actions_and_comparisons_reject_stale_snapshots(self):
        managers = [(actions.ActionManager(str(self.base / 'Actions.json')), 'load_actions', 'save_actions'),
                    (comparison.ComparisonManager(str(self.base / 'Pinned.json')), 'load_pinned', 'save_pinned')]
        for manager, load, save in managers:
            first, second = getattr(manager, load)(), getattr(manager, load)()
            first.append({'label': 'First'})
            second.append({'label': 'Second'})
            getattr(manager, save)(first)
            with self.subTest(manager=type(manager).__name__), self.assertRaises(RuntimeError):
                getattr(manager, save)(second)
            self.assertEqual(getattr(manager, load)(), [{'label': 'First'}])

    def test_xml_rejects_stale_whole_document_updates(self):
        pairs = [(fontconfig.load_aliases, fontconfig.save_aliases,
                  lambda value: value.update({'A': dict(prefer=['B'])})),
                 (fontconfig.load_rejected_families, fontconfig.save_rejected_families,
                  lambda value: value.add('A')),
                 (fontconfig.load_active_directories, fontconfig.save_active_directories,
                  lambda value: value.append('/fixture')),
                 (fontconfig.load_rendering_properties, fontconfig.save_rendering_properties,
                  lambda value: value.update({'hinting': False})),
                 (fontconfig.load_display_properties, fontconfig.save_display_properties,
                  lambda value: value.update({'dpi': 120}))]
        for load, save, change in pairs:
            first, second = load(), load()
            change(first)
            save(first)
            with self.subTest(loader=load.__name__), self.assertRaises(RuntimeError):
                save(second)

    def test_xml_publication_failure_keeps_valid_original(self):
        fontconfig.set_alias('A', prefer=['B'])
        path = Path(fontconfig.ALIASES_CONF)
        original = path.read_bytes()
        with patch('os.replace', side_effect=OSError('simulated publish failure')):
            with self.assertRaises((RuntimeError, OSError)):
                fontconfig.set_alias('C', prefer=['D'])
        self.assertEqual(path.read_bytes(), original)

    def test_two_processes_preserve_all_successful_collection_updates(self):
        context = multiprocessing.get_context('spawn')
        start = context.Event()
        workers = [context.Process(target=create_collections,
                    args=(collections.COLLECTIONS_FILE, prefix, start)) for prefix in ('A', 'B')]
        for worker in workers:
            worker.start()
        try:
            start.set()
            for worker in workers:
                worker.join(30)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual({c['name'] for c in collections.load_collections()},
                             {'%s%d' % (prefix, index) for prefix in ('A', 'B') for index in range(10)})
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join()

    def test_two_processes_preserve_both_source_configuration_files(self):
        for prefix in ('A', 'B'):
            for index in range(3):
                (self.base / ('%s%d' % (prefix, index))).mkdir()
        context = multiprocessing.get_context('spawn')
        start = context.Event()
        workers = [context.Process(target=create_sources, args=(str(self.base), prefix, start))
                   for prefix in ('A', 'B')]
        for worker in workers:
            worker.start()
        try:
            start.set()
            for worker in workers:
                worker.join(30)
                self.assertEqual(worker.exitcode, 0)
            import xml.etree.ElementTree as ET
            defined = {node.text for node in ET.parse(self.base / 'Sources.xml').findall('.//source')}
            active = {node.text for node in ET.parse(self.base / 'fc/Directories.conf').findall('.//dir')}
            self.assertEqual(defined, active)
            self.assertEqual(len(defined), 6)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join()

    def test_symlink_and_interrupted_writes_preserve_existing_data(self):
        path = Path(collections.COLLECTIONS_FILE)
        outside = self.base / 'outside.json'
        outside.write_bytes(b'[]')
        path.symlink_to(outside)
        with self.assertRaises((RuntimeError, OSError)):
            collections.create_collection('New')
        self.assertEqual(outside.read_bytes(), b'[]')
        path.unlink()
        collections.create_collection('Base')
        original = path.read_bytes()
        path.chmod(0o640)
        with patch('os.fsync', side_effect=OSError('interrupted write')):
            with self.assertRaises((RuntimeError, OSError)):
                collections.create_collection('New')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.base.glob('.cefont-write-*')), [])
        collections.create_collection('New')
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_lock_inode_cannot_be_replaced_as_a_configuration_target(self):
        from cefont.core import state
        path = str(self.base / '.cefont-config.lock')
        with state.configuration_lock(str(self.base / 'fixture.json')):
            inode = Path(path).stat().st_ino
            with self.assertRaises(state.ConfigurationError):
                state.atomic_write(path, b'[]')
            self.assertEqual(Path(path).stat().st_ino, inode)
