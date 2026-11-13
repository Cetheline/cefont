"""Isolated SQLite and synthetic font tests; never touch real font/config paths."""
import hashlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cefont.core import db, fontconfig


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='cefont-db-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.directory = self.base / 'fonts'
        self.directory.mkdir()
        self.database = self.base / 'inventory.sqlite'
        self.extracts = []
        self.face_counts = {}
        for obj, key, value in ((db, 'DATABASE_FILE', str(self.database)),
                                (db, 'USER_FONT_DIR', str(self.directory))):
            context = patch.object(obj, key, value)
            context.start();self.addCleanup(context.stop)
        for name, kwargs in (
            ('ensure_directories', {}), ('load_active_directories', dict(return_value=[])),
            ('load_orthographies', dict(return_value={'ASCII':dict(codepoints={65},pangrams=['A'])})),
            ('extract_font_metadata', dict(side_effect=self.metadata)),
            ('get_all_codepoints', dict(return_value={65})),
        ):
            context=patch.object(db,name,**kwargs);context.start();self.addCleanup(context.stop)

    def fixture(self, name='sample.ttf', content=b'font version one', faces=1):
        path=self.directory/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if faces>1:content=b'ttcf'+struct.pack('>II',0x00010000,faces)+b'\x00'*4*faces+content
        path.write_bytes(content)
        self.face_counts[str(path)]=faces
        return path

    def metadata(self, path, index=0):
        if index>=self.face_counts.get(path,1):return None
        self.extracts.append((path,index))
        return dict(filepath=path,findex=index,family='Fixture',style='Face%d'%index,
                    owner=0,psname='Fixture',filetype='TrueType Collection' if self.face_counts.get(path,1)>1 else 'TrueType',
                    **{'n-glyphs':1,'copyright':'','version':'1','description':'Fixture',
                       'license-data':'','license-url':'','vendor':'Test','designer':'','designer-url':'',
                       'license-type':'Unknown','fsType':0,'filesize':str(Path(path).stat().st_size),
                       'checksum':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'panose':[1]*10,
                       'num_faces':self.face_counts.get(path,1)})

    def inventory(self, path, indexes=(0,)):
        return [dict(filepath=str(path),findex=index,family='Fixture',style='Face%d'%index,
                     spacing=100,slant=0,weight=80,width=100,description='Fixture') for index in indexes]

    def sync(self, fonts, **kwargs):
        with patch.object(db,'scan_available_fonts',return_value=fonts):return db.update_database(**kwargs)

    def snapshot(self):
        with closing(sqlite3.connect(str(self.database))) as con, con:
            rows = {table: con.execute('SELECT * FROM '+table+' ORDER BY uid').fetchall()
                    for table in ('Fonts', 'Metadata', 'Panose', 'Orthography')}
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='CacheState'").fetchone():
                rows['CacheState'] = con.execute('SELECT * FROM CacheState ORDER BY filepath, findex').fetchall()
            return rows

    def test_discovery_failure_preserves_inventory_even_with_force(self):
        path=self.fixture();self.sync(self.inventory(path));before=self.snapshot()
        for failure in (FileNotFoundError('fc-list'), subprocess.CalledProcessError(1,['fc-list']), RuntimeError('unexpected')):
            with self.subTest(failure=type(failure).__name__),patch.object(db.subprocess,'run',side_effect=failure):
                with self.assertRaises(RuntimeError):db.update_database(force=True)
            self.assertEqual(self.snapshot(),before)

    def test_nonzero_discovery_status_without_exception_is_failure(self):
        with patch.object(db.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','failed')):
            with self.assertRaises(RuntimeError):db.scan_available_fonts()

    def test_malformed_partial_discovery_is_not_accepted(self):
        path=self.fixture()
        output='%s|0|Fixture|Regular|80|0|100|100\nMALFORMED\n'%path
        with patch.object(db.subprocess,'run',return_value=subprocess.CompletedProcess([],0,output,'')):
            with self.assertRaises(RuntimeError):db.scan_available_fonts()

    def test_malformed_custom_source_configuration_preserves_inventory(self):
        path = self.fixture()
        self.sync(self.inventory(path))
        before = self.snapshot()
        config = self.base / '09-Directories.conf'
        config.write_text('<fontconfig><dir>unterminated', encoding='utf-8')
        empty = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(db.subprocess, 'run', return_value=empty), \
             patch.object(db, 'load_active_directories', fontconfig.load_active_directories), \
             patch.object(fontconfig, 'DIRECTORIES_CONF', str(config)):
            with self.assertRaises(db.FontDiscoveryError):
                db.update_database()
        self.assertEqual(self.snapshot(), before)

    def test_successful_empty_inventory_prunes_all_related_cache(self):
        path=self.fixture();self.sync(self.inventory(path));path.unlink()
        self.assertEqual(self.sync([]),(0,0))
        self.assertTrue(all(not rows for rows in self.snapshot().values()))

    def test_interrupted_large_rebuild_is_atomic(self):
        path=self.fixture();self.sync(self.inventory(path));before=self.snapshot()
        fonts=self.inventory(path,range(202));self.face_counts[str(path)]=202
        def interrupt(current,total,message):
            if current==201:raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.sync(fonts,force=True,progress_callback=interrupt)
        self.assertEqual(self.snapshot(),before)

    def test_repeated_directory_scan_has_stable_rows_and_joins(self):
        path=self.fixture()
        self.assertEqual(db.scan_and_index_directory(str(self.directory)),1)
        before=self.snapshot();self.extracts.clear()
        self.assertEqual(db.scan_and_index_directory(str(self.directory)),1)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(db.get_fonts_by_family('Fixture')),1)
        self.assertEqual(self.extracts,[])

    def test_collection_faces_are_independent_and_force_refreshes_every_face(self):
        path=self.fixture('collection.otc',faces=3);fonts=self.inventory(path,range(3))
        self.assertEqual(self.sync(fonts),(3,3))
        self.extracts.clear();self.assertEqual(self.sync(fonts),(3,0));self.assertEqual(self.extracts,[])
        self.assertEqual(self.sync(fonts,force=True),(3,3))
        self.assertEqual(self.extracts,[(str(path),0),(str(path),1),(str(path),2)])
        self.assertEqual(len(db.get_fonts_by_family('Fixture')),3)

    def test_directory_scan_enumerates_collection_faces(self):
        self.fixture('collection.ttc',faces=3)
        self.assertEqual(db.scan_and_index_directory(str(self.directory)),3)
        self.assertEqual(db.scan_and_index_directory(str(self.directory)),3)
        self.assertTrue(all(len(rows)==3 for rows in self.snapshot().values()))

    def test_directory_scan_retains_named_instances_of_an_unchanged_file(self):
        path = self.fixture('variable.ttf')
        named_instance = 1 << 16
        self.face_counts[str(path)] = named_instance + 1
        self.sync(self.inventory(path, (0, named_instance)))
        self.extracts.clear()
        self.assertEqual(db.scan_and_index_directory(str(self.directory)), 2)
        self.assertEqual({row['findex'] for row in db.get_fonts_by_family('Fixture')}, {0, named_instance})
        self.assertEqual(self.extracts, [])
        path.write_bytes(b'font without a named instance')
        self.face_counts[str(path)] = 1
        self.assertEqual(db.scan_and_index_directory(str(self.directory)), 1)
        self.assertTrue(all(len(rows) == 1 for rows in self.snapshot().values()))

    def test_changed_contents_refresh_only_affected_file(self):
        first=self.fixture();second=self.fixture('other.ttf')
        fonts=self.inventory(first)+self.inventory(second)
        self.sync(fonts);self.extracts.clear()
        old=first.stat();first.write_bytes(b'font version two')
        os.utime(str(first),ns=(old.st_atime_ns,old.st_mtime_ns))
        self.assertEqual(self.sync(fonts),(2,1))
        self.assertEqual(self.extracts,[(str(first),0)])
        row=db.get_detailed_font_info(str(first))
        self.assertEqual(row['checksum'],hashlib.sha256(first.read_bytes()).hexdigest())

    def test_missing_related_cache_is_repaired(self):
        path=self.fixture();fonts=self.inventory(path);self.sync(fonts)
        with closing(sqlite3.connect(str(self.database))) as con, con:con.execute('DELETE FROM Panose')
        self.assertEqual(self.sync(fonts),(1,1))
        self.assertEqual(len(self.snapshot()['Panose']),1)

    def test_removed_faces_and_files_prune_related_tables(self):
        first=self.fixture('collection.ttc',faces=3);second=self.fixture('other.ttf')
        self.sync(self.inventory(first,range(3))+self.inventory(second))
        second.unlink();self.face_counts[str(first)]=1
        self.sync(self.inventory(first))
        for table,rows in self.snapshot().items():self.assertEqual(len(rows),1,table)

    def test_metadata_failure_rolls_back_all_tables(self):
        path=self.fixture();fonts=self.inventory(path);self.sync(fonts);before=self.snapshot()
        path.write_bytes(b'changed')
        with patch.object(db,'extract_font_metadata',side_effect=RuntimeError('broken font')):
            with self.assertRaises(RuntimeError):self.sync(fonts,force=True)
        self.assertEqual(self.snapshot(),before)

    def test_exact_file_purge_does_not_delete_suffix(self):
        path=self.fixture();suffix=self.fixture('sample.ttf.backup.ttf')
        self.sync(self.inventory(path)+self.inventory(suffix))
        db.delete_fonts_by_paths([str(path)])
        for rows in self.snapshot().values():self.assertEqual(len(rows),1)
        self.assertEqual(db.get_fonts_by_family('Fixture')[0]['filepath'],str(suffix))

    def test_directory_purge_uses_literal_component_boundaries(self):
        for name in ("a_%'", "axb'", "a_%'-sibling"):
            self.fixture(name+'/sample.ttf')
        paths=sorted(self.directory.rglob('*.ttf'))
        self.sync([font for path in paths for font in self.inventory(path)])
        target=self.directory/"a_%'"
        self.assertEqual(db.count_fonts_in_directory(str(target)),1)
        db.purge_directory(str(target))
        self.assertEqual(len(db.get_fonts_by_family('Fixture')),2)
        self.assertEqual(db.count_fonts_in_directory(str(target)),0)

    def legacy(self):
        with closing(sqlite3.connect(str(self.database))) as con, con:
            for statement in (db.CREATE_FONTS_TABLE,db.CREATE_METADATA_TABLE,db.CREATE_PANOSE_TABLE,db.CREATE_ORTHOGRAPHY_TABLE):con.execute(statement)
            for index in (0,1):
                con.execute('INSERT INTO Fonts(filepath,findex,family,style) VALUES (?,0,?,?)',('/legacy/font.ttf','Legacy','Complete' if index==0 else None))
            con.execute('INSERT INTO Fonts(filepath,findex,family) VALUES (?,1,?)',('/legacy/font.ttf','Other face'))
            con.execute('CREATE TABLE UserNotes(note TEXT)');con.execute("INSERT INTO UserNotes VALUES ('keep me')")

    def test_legacy_dedup_preserves_faces_best_rows_and_backup(self):
        self.legacy();con=db.get_db_connection();con.close()
        faces=db.get_fonts_by_family('Legacy')
        self.assertEqual(len(faces),1);self.assertEqual(faces[0]['style'],'Complete')
        with closing(sqlite3.connect(str(self.database))) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM Fonts').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT note FROM UserNotes').fetchone()[0],'keep me')
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        backups=list(self.base.glob('*.pre-migration-*.sqlite'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(str(backups[0]))) as con:self.assertEqual(con.execute('SELECT count(*) FROM Fonts').fetchone()[0],3)
        con=db.get_db_connection();con.close()
        self.assertEqual(list(self.base.glob('*.pre-migration-*.sqlite')),backups)

    def test_interrupted_migration_rolls_back_and_preserves_recoverable_copy(self):
        from cefont.core import database_schema
        self.legacy()
        with patch.object(database_schema,'_identity_indexes',side_effect=RuntimeError('migration interrupted')):
            with self.assertRaises(RuntimeError):db.get_db_connection()
        with closing(sqlite3.connect(str(self.database))) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM Fonts').fetchone()[0],3)
            self.assertFalse(con.execute("SELECT 1 FROM sqlite_master WHERE name='CeFontSchema'").fetchone())
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(len(list(self.base.glob('*.pre-migration-*.sqlite'))),1)
        con=db.get_db_connection();con.close()
        self.assertEqual(len(db.get_fonts_by_family('Legacy')),1)

    def test_legacy_related_rows_are_deduplicated_without_merging_faces(self):
        self.legacy()
        path = '/legacy/font.ttf'
        with closing(sqlite3.connect(str(self.database))) as con, con:
            con.executemany('INSERT INTO Metadata(filepath,findex,vendor) VALUES (?,?,?)',
                            [(path, 0, 'Old'), (path, 0, 'New'), (path, 1, 'Other')])
            con.executemany('INSERT INTO Panose(filepath,findex,P0,P1) VALUES (?,?,?,?)',
                            [(path, 0, 1, 2), (path, 0, None, 9), (path, 1, 5, 6)])
            con.executemany('INSERT INTO Orthography(filepath,findex,support,sample) VALUES (?,?,?,?)',
                            [(path, 0, '["ASCII"]', 'A'), (path, 0, None, 'B'), (path, 1, '[]', '')])
        con = db.get_db_connection()
        with closing(con):
            self.assertEqual([tuple(row) for row in con.execute('SELECT findex,vendor FROM Metadata ORDER BY findex')],
                             [(0, 'New'), (1, 'Other')])
            self.assertEqual([tuple(row) for row in con.execute('SELECT findex,P0,P1 FROM Panose ORDER BY findex')],
                             [(0, 1, 2), (1, 5, 6)])
            self.assertEqual([tuple(row) for row in con.execute('SELECT findex,support FROM Orthography ORDER BY findex')],
                             [(0, '["ASCII"]'), (1, '[]')])
        self.assertEqual(len(db.get_fonts_by_family('Legacy')), 1)

    def test_metadata_failure_during_directory_index_preserves_snapshot(self):
        first=self.fixture();db.scan_and_index_directory(str(self.directory));before=self.snapshot()
        second=self.fixture('z-broken.ttf')
        actual=self.metadata
        def fail(path,index=0):
            if path==str(second):raise RuntimeError('invalid metadata')
            return actual(path,index)
        with patch.object(db,'extract_font_metadata',side_effect=fail):
            with self.assertRaises(RuntimeError):db.scan_and_index_directory(str(self.directory))
        self.assertEqual(self.snapshot(),before)

    def test_removed_directory_files_prune_only_that_directory(self):
        first=self.fixture('first/a.ttf');second=self.fixture('second/b.ttf')
        self.sync(self.inventory(first)+self.inventory(second));first.unlink()
        self.assertEqual(db.scan_and_index_directory(str(first.parent)),0)
        self.assertEqual(db.count_fonts_in_directory(str(second.parent)),1)
        self.assertEqual(len(db.get_fonts_by_family('Fixture')),1)

    def test_file_changed_during_rebuild_causes_rollback(self):
        path=self.fixture();fonts=self.inventory(path);self.sync(fonts);before=self.snapshot()
        def change(*args):path.write_bytes(b'changed during scan')
        with self.assertRaises(RuntimeError):self.sync(fonts,progress_callback=change)
        self.assertEqual(self.snapshot(),before)

    def test_existing_partial_metadata_rows_are_repaired_atomically(self):
        path=self.fixture();fonts=self.inventory(path);self.sync(fonts)
        with closing(sqlite3.connect(str(self.database))) as con,con:
            con.execute('DELETE FROM Metadata')
        info=db.get_detailed_font_info(str(path))
        self.assertEqual(info['checksum'],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(all(len(rows)==1 for rows in self.snapshot().values()))

    def test_legacy_metadata_without_revision_is_refreshed(self):
        path=self.fixture();fonts=self.inventory(path);self.sync(fonts)
        with closing(sqlite3.connect(str(self.database))) as con,con:
            con.execute('DELETE FROM CacheState')
            con.execute("UPDATE Metadata SET vendor='Stale vendor'")
        self.extracts.clear();self.assertEqual(self.sync(fonts),(1,1))
        self.assertEqual(db.get_detailed_font_info(str(path))['vendor'],'Test')
        self.assertEqual(self.extracts,[(str(path),0)])

    def test_cached_support_also_requires_the_numeric_orthography_key(self):
        path = self.fixture()
        with patch.object(db, 'load_orthographies', return_value={
                'Fixture': dict(key=66, codepoints={65}, pangrams=['A'])}):
            self.sync(self.inventory(path))
        self.assertEqual(db.get_detailed_font_info(str(path))['supported_orthographies'], '[]')

    def test_sql_unique_constraints_prevent_duplicate_valid_identities(self):
        path=self.fixture();self.sync(self.inventory(path))
        with closing(sqlite3.connect(str(self.database))) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('INSERT INTO Fonts(filepath,findex) VALUES (?,0)',(str(path),))

    def test_valid_fontconfig_range_properties_and_empty_discovery(self):
        path=self.fixture()
        output='%s|0|Fixture|Regular|[0 210]|0|100|100\n'%path
        with patch.object(db.subprocess,'run',return_value=subprocess.CompletedProcess([],0,output,'')):
            fonts=db.scan_available_fonts()
            self.assertEqual(len(fonts),1)
            self.assertEqual(fonts[0]['weight'],80)
        path.unlink()
        with patch.object(db.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            self.assertEqual(db.scan_available_fonts(),[])

    def test_directory_walk_failure_is_not_partial_discovery(self):
        def denied(*args,**kwargs):
            kwargs['onerror'](PermissionError('unreadable source'))
            return iter(())
        with patch.object(db.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')), \
             patch.object(db.os,'walk',side_effect=denied):
            with self.assertRaises(RuntimeError):db.scan_available_fonts()

    def test_discovery_timeout_is_explicit(self):
        with patch.object(db.subprocess,'run',side_effect=subprocess.TimeoutExpired(['fc-list'],30)):
            with self.assertRaises(RuntimeError):db.scan_available_fonts()

    def test_update_cli_failure_exits_nonzero_for_both_command_forms(self):
        import contextlib,io
        from cefont import main
        for arguments in (['cefont','update'],['cefont','--update']):
            output=io.StringIO()
            with self.subTest(arguments=arguments),patch.object(main.sys,'argv',arguments), \
                 patch.object(main,'sync_database',side_effect=db.FontDiscoveryError('injected discovery failure')), \
                 contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as error:main.main()
            self.assertEqual(error.exception.code,1)
            self.assertIn('discovery failure',output.getvalue())
