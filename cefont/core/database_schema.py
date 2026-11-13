"""Transactional, recoverable upgrades for the font cache schema."""
from contextlib import closing
import os
import sqlite3
import tempfile


class FontDatabaseError(RuntimeError):
    """A font-cache operation failed without publishing a partial result."""


TABLES = ('Fonts', 'Metadata', 'Panose', 'Orthography')
SCHEMA_VERSION = 1


def _backup(con):
    path = next((row[2] for row in con.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not path:
        return None
    parent = os.path.dirname(path)
    descriptor, backup = tempfile.mkstemp(prefix=os.path.basename(path) + '.pre-migration-',
                                          suffix='.sqlite', dir=parent)
    os.close(descriptor)
    try:
        # Read through a separate connection: backing up this connection while
        # it holds a write transaction would wait for its own transaction.
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        with open(backup, 'rb') as saved:
            os.fsync(saved.fileno())
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return backup
    except BaseException:
        os.unlink(backup)  # Only the uniquely created, incomplete backup.
        raise


def _deduplicate(con, table):
    cur = con.execute('SELECT * FROM "'+table+'" ORDER BY uid')
    columns = [column[0] for column in cur.description]
    keep = {}
    discarded = []
    for values in cur.fetchall():
        row = dict(zip(columns, values))
        if row['filepath'] is None or row['findex'] is None:
            continue  # Preserve unidentified legacy rows, not merge them together.
        key = (row['filepath'], row['findex'])
        rank = (sum(value is not None and value != '' for name,value in row.items()
                    if name not in ('uid','filepath','findex')), row['uid'])
        previous = keep.get(key)
        if previous is None:
            keep[key] = (rank, row['uid'])
        elif rank > previous[0]:
            discarded.append(previous[1])
            keep[key] = (rank, row['uid'])
        else:
            discarded.append(row['uid'])
    con.executemany('DELETE FROM "'+table+'" WHERE uid=?', [(uid,) for uid in discarded])


def _identity_indexes(con):
    for table in TABLES:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS "cefont_'+table.lower()+'_identity" '
                    'ON "'+table+'" (filepath COLLATE BINARY, findex)')


def _migration_state(con):
    names = {row[0] for row in con.execute('SELECT name FROM sqlite_master')}
    version = None
    if 'CeFontSchema' in names:
        row = con.execute('SELECT version FROM CeFontSchema WHERE singleton=1').fetchone()
        version = row[0] if row else None
        if version is not None and version != SCHEMA_VERSION:
            raise FontDatabaseError('Unsupported font-cache schema version: %s' % version)
    required = set(TABLES) | {'CacheState', 'CeFontSchema'}
    required.update('cefont_' + name.lower() + '_identity' for name in TABLES)
    current = version == SCHEMA_VERSION and required.issubset(names)
    return current, bool(set(TABLES).intersection(names))


def initialize(con, definitions, indexes):
    if _migration_state(con)[0]:
        return
    if con.in_transaction:
        raise FontDatabaseError('Schema initialization requires an idle connection')
    con.execute('BEGIN IMMEDIATE')
    try:
        # Recheck after obtaining the writer lock, in case another process migrated.
        current, legacy = _migration_state(con)
        if current:
            con.commit()
            return
        backup = _backup(con) if legacy else None
        for definition in definitions:
            con.execute(definition)
        for table in TABLES:
            _deduplicate(con, table)
        _identity_indexes(con)
        for statement in indexes.split(';'):
            if statement.strip():
                con.execute(statement)
        con.execute('''CREATE TABLE IF NOT EXISTS CacheState (
            filepath TEXT NOT NULL, findex INTEGER NOT NULL, checksum TEXT NOT NULL,
            revision INTEGER NOT NULL, PRIMARY KEY(filepath,findex))''')
        con.execute('''CREATE TABLE IF NOT EXISTS CeFontSchema (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, backup_path TEXT)''')
        con.execute('INSERT OR REPLACE INTO CeFontSchema VALUES (1,?,?)', (SCHEMA_VERSION,backup))
        con.commit()
    except BaseException:
        con.rollback()
        raise
