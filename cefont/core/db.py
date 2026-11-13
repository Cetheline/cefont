"""Database layer for Font Manager SQLite database."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
from typing import Callable, Dict, List, Optional, Tuple

from cefont.core.config import (
    DATABASE_FILE,
    USER_FONT_DIR,
    ensure_directories,
)
from cefont.core.database_schema import FontDatabaseError, TABLES, initialize
from cefont.core.data import load_orthographies
from cefont.core.fontconfig import load_active_directories
from cefont.core.freetype_backend import extract_font_metadata, get_all_codepoints

CREATE_FONTS_TABLE = """
CREATE TABLE IF NOT EXISTS Fonts (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INTEGER,
    family TEXT,
    style TEXT,
    spacing INTEGER,
    slant INTEGER,
    weight INTEGER,
    width INTEGER,
    description TEXT
);
"""

CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS Metadata (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INTEGER,
    family TEXT,
    style TEXT,
    owner INTEGER,
    psname TEXT,
    filetype TEXT,
    'n-glyphs' INTEGER,
    copyright TEXT,
    version TEXT,
    description TEXT,
    'license-data' TEXT,
    'license-url' TEXT,
    vendor TEXT,
    designer TEXT,
    'designer-url' TEXT,
    'license-type' TEXT,
    fsType INTEGER,
    filesize TEXT,
    checksum TEXT
);
"""

CREATE_PANOSE_TABLE = """
CREATE TABLE IF NOT EXISTS Panose (
    uid INTEGER PRIMARY KEY,
    P0 INTEGER, P1 INTEGER, P2 INTEGER, P3 INTEGER, P4 INTEGER,
    P5 INTEGER, P6 INTEGER, P7 INTEGER, P8 INTEGER, P9 INTEGER,
    filepath TEXT,
    findex INTEGER
);
"""

CREATE_ORTHOGRAPHY_TABLE = """
CREATE TABLE IF NOT EXISTS Orthography (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INT,
    support TEXT,
    sample TEXT
);
"""

CREATE_INDICES = """
CREATE INDEX IF NOT EXISTS font_match_idx ON Fonts (filepath, findex, family, description);
CREATE INDEX IF NOT EXISTS info_match_idx ON Metadata (filepath, findex, owner, filetype, vendor, 'license-type');
CREATE INDEX IF NOT EXISTS panose_match_idx ON Panose (filepath, findex, P0);
"""


def get_db_connection() -> sqlite3.Connection:
    try:
        ensure_directories()
        con = sqlite3.connect(DATABASE_FILE, timeout=30)
    except (OSError, sqlite3.Error) as error:
        raise FontDatabaseError('Cannot open font cache: %s' % error) from error
    try:
        con.row_factory = sqlite3.Row
        init_db(con)
        return con
    except BaseException as error:
        con.close()
        if isinstance(error, Exception) and not isinstance(error, FontDatabaseError):
            raise FontDatabaseError('Font-cache initialization failed: %s' % error) from error
        raise


def init_db(con: sqlite3.Connection):
    initialize(con, (CREATE_FONTS_TABLE, CREATE_METADATA_TABLE, CREATE_PANOSE_TABLE,
                     CREATE_ORTHOGRAPHY_TABLE), CREATE_INDICES)


class FontDiscoveryError(FontDatabaseError):
    """Discovery was incomplete; its result must not replace the inventory."""


CACHE_REVISION = 3  # Corrected OS/2, orthography and vendor parsing refresh on sync.
FONT_EXTENSIONS = ('.ttf','.otf','.ttc','.otc','.woff','.woff2','.dfont','.pfa','.pfb')
ALL_CACHE_TABLES = TABLES + ('CacheState',)


def _file_token(path):
    value = os.stat(path)
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def _fingerprint(path):
    before = _file_token(path)
    digest = hashlib.sha256()
    with open(path,'rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            digest.update(chunk)
    if _file_token(path) != before:
        raise FontDatabaseError('Font changed while hashing: '+path)
    return digest.hexdigest(),before


def _face_indexes(path):
    with open(path,'rb') as source:
        header = source.read(12)
    if header[:4] != b'ttcf':
        return range(1)
    if len(header) != 12:
        raise FontDiscoveryError('Truncated font collection: '+path)
    count = int.from_bytes(header[8:12],'big')
    if count < 1 or count > max(0,(os.path.getsize(path)-12)//4):
        raise FontDiscoveryError('Invalid collection face count: '+path)
    return range(count)


def _directory_face_indexes(con, path, digest):
    base_faces = _face_indexes(path)
    yield from base_faces
    # FreeType uses bits 16–30 for named instances. A directory scan cannot
    # discover new ones, but should keep known ones for unchanged files.
    instance_bit = 1 << 16
    known = con.execute(
        'SELECT findex FROM CacheState WHERE filepath=? AND checksum=? AND findex>=? ORDER BY findex',
        (path, digest, instance_bit),
    ).fetchall()
    for (index,) in known:
        if index & (instance_bit - 1) in base_faces:
            yield index


def _walk_fonts(directory):
    def failure(error):
        raise FontDiscoveryError('Cannot enumerate font source: %s' % error)
    for root,_,files in os.walk(directory,onerror=failure):
        for name in sorted(files):
            if name.lower().endswith(FONT_EXTENSIONS):
                path = os.path.abspath(os.path.join(root,name))
                if not os.path.isfile(path) or not os.access(path,os.R_OK):
                    raise FontDiscoveryError('Font is missing or unreadable: '+path)
                yield path


def _font_row(path,index,meta):
    family = meta['family'] or os.path.splitext(os.path.basename(path))[0]
    style = meta['style'] or 'Regular'
    return dict(filepath=path,findex=index,family=family,style=style,spacing=0,
                slant=0,weight=80,width=100,description=('%s %s'%(family,style)).strip())


def scan_available_fonts() -> List[Dict]:
    """Return a complete inventory (including a valid empty list), or raise.

    Strict policy: malformed output, inaccessible reported fonts, unreadable
    configured sources, traversal errors and unparseable fallback fonts abort
    discovery. No partial fallback result may replace a previous inventory.
    Missing user-font storage is a legitimate empty source. Named variable
    instances reported by Fontconfig keep their encoded indexes; filesystem
    fallback enumerates base collection faces only.
    """
    try:
        command = ['fc-list','-f','%{file}|%{index}|%{family}|%{style}|%{weight}|%{slant}|%{width}|%{spacing}\n']
        result = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,check=True,timeout=30)
        if result.returncode:
            raise FontDiscoveryError('fc-list failed: '+(result.stderr or str(result.returncode)))
        fonts = {}
        for line in result.stdout.splitlines():
            if not line.strip():continue
            parts = line.split('|')
            if len(parts) != 8 or not parts[0] or not parts[1].isdigit() or not parts[2].strip():
                raise FontDiscoveryError('Malformed fc-list record; inventory not replaced')
            path = os.path.abspath(parts[0])
            if not os.path.isfile(path) or not os.access(path,os.R_OK):
                raise FontDiscoveryError('Fontconfig reported an unreadable/missing font: '+path)
            index = int(parts[1])
            family,style = parts[2].split(',')[0].strip(),parts[3].split(',')[0].strip() or 'Regular'
            values=[]
            for value,default in zip(parts[4:],(80,0,100,0)):
                if value == '':values.append(default)
                elif value.isdigit():values.append(int(value))
                elif re.fullmatch(r'\[\d+\s+\d+\]',value):values.append(default)
                else:raise FontDiscoveryError('Malformed numeric Fontconfig property')
            fonts[(path,index)] = dict(filepath=path,findex=index,family=family,style=style,
                                       weight=values[0],slant=values[1],width=values[2],spacing=values[3],
                                       description=('%s %s'%(family,style)).strip())
        active = load_active_directories(strict=True)
        for directory in dict.fromkeys([USER_FONT_DIR] + active):
            try:
                mode = os.stat(directory).st_mode
            except FileNotFoundError:
                if directory not in active:
                    continue
                raise FontDiscoveryError('Configured font source is unavailable: ' + directory) from None
            if not stat.S_ISDIR(mode):
                raise FontDiscoveryError('Font source is not a directory: ' + directory)
            for path in _walk_fonts(directory):
                for index in _face_indexes(path):
                    if (path,index) in fonts:continue
                    token = _file_token(path)
                    meta = extract_font_metadata(path,index)
                    if _file_token(path) != token:
                        raise FontDiscoveryError('Fallback font changed during discovery: '+path)
                    if meta is None:raise FontDiscoveryError('Cannot read fallback font face: %s [%d]'%(path,index))
                    row = _font_row(path,index,meta)
                    row['_metadata'] = meta
                    row['_metadata_token'] = token
                    fonts[(path,index)] = row
        return list(fonts.values())
    except FontDiscoveryError:
        raise
    except Exception as error:
        raise FontDiscoveryError('Font discovery failed; previous inventory preserved: %s'%error) from error


def _upsert(con,table,values):
    columns = [name for name in values if name not in ('filepath','findex')]
    where = 'filepath = ? COLLATE BINARY AND findex = ?'
    key = (values['filepath'],values['findex'])
    assignment = ','.join('"'+name+'"=?' for name in columns)
    cursor = con.execute('UPDATE "'+table+'" SET '+assignment+' WHERE '+where,
                         [values[name] for name in columns]+list(key))
    if not cursor.rowcount:
        names = list(values)
        con.execute('INSERT INTO "'+table+'" ('+','.join('"'+name+'"' for name in names)+') '
                    'VALUES ('+','.join('?' for _ in names)+')',[values[name] for name in names])


def _ensure_cache(con,path,index,fingerprint,orthographies,force=False,preloaded=None):
    digest,_ = fingerprint
    meta = con.execute('SELECT * FROM Metadata WHERE filepath=? COLLATE BINARY AND findex=?',(path,index)).fetchone()
    panose = con.execute('SELECT * FROM Panose WHERE filepath=? COLLATE BINARY AND findex=?',(path,index)).fetchone()
    orth = con.execute('SELECT support FROM Orthography WHERE filepath=? COLLATE BINARY AND findex=?',(path,index)).fetchone()
    state = con.execute('SELECT checksum,revision FROM CacheState WHERE filepath=? AND findex=?',(path,index)).fetchone()
    valid_orth = False
    if orth:
        try:
            support = json.loads(orth[0]);valid_orth=isinstance(support,list) and all(isinstance(name,str) for name in support)
        except (ValueError,TypeError):pass
    if (not force and meta and meta['checksum']==digest and meta['filetype']
            and panose and all(panose['P%d'%i] is not None for i in range(10)) and valid_orth
            and state and state[0]==digest and state[1]==CACHE_REVISION):
        return dict(meta),False
    meta = preloaded if preloaded is not None else extract_font_metadata(path,index)
    if meta is None:
        raise FontDatabaseError('Cannot extract metadata for %s [%d]'%(path,index))
    if not meta.get('filetype'):
        raise FontDatabaseError('Missing font format metadata: '+path)
    if meta['checksum'] != digest:
        raise FontDatabaseError('Font changed during metadata extraction: '+path)
    keys = ('family','style','owner','psname','filetype','n-glyphs','copyright','version','description',
            'license-data','license-url','vendor','designer','designer-url','license-type','fsType','filesize','checksum')
    values = {name:meta[name] for name in keys}
    values.update(filepath=path,findex=index)
    _upsert(con,'Metadata',values)
    if len(meta['panose']) != 10:
        raise FontDatabaseError('Invalid Panose metadata: '+path)
    _upsert(con,'Panose',dict(filepath=path,findex=index,**{'P%d'%i:value for i,value in enumerate(meta['panose'])}))
    codepoints = get_all_codepoints(path,index)
    supported = [name for name,data in orthographies.items()
                 if data['codepoints'] and data['codepoints'].issubset(codepoints)
                 and (not data.get('key') or data['key'] in codepoints)]
    sample = next((orthographies[name]['pangrams'][0] for name in supported if orthographies[name]['pangrams']), '')
    _upsert(con,'Orthography',dict(filepath=path,findex=index,support=json.dumps(supported),sample=sample))
    _upsert(con,'CacheState',dict(filepath=path,findex=index,checksum=digest,revision=CACHE_REVISION))
    return meta,True


def _stage(con):
    con.execute('CREATE TEMP TABLE CurrentFonts(filepath TEXT, findex INTEGER, PRIMARY KEY(filepath,findex))')


def _prune(con,directory=None):
    for table in ALL_CACHE_TABLES:
        query = 'DELETE FROM "'+table+'" WHERE NOT EXISTS (SELECT 1 FROM CurrentFonts c WHERE c.filepath="'+table+'".filepath COLLATE BINARY AND c.findex="'+table+'".findex)'
        params=[]
        if directory is not None:
            prefix = _directory_prefix(directory)
            query += ' AND substr(filepath,1,?) = ? COLLATE BINARY'
            params=[len(prefix),prefix]
        con.execute(query,params)


def _check_tokens(fingerprints):
    for path,(_,token) in fingerprints.items():
        if _file_token(path) != token:
            raise FontDatabaseError('Font changed during indexing: '+path)


def update_database(force: bool = False, progress_callback: Optional[Callable[[int,int,str],None]] = None) -> Tuple[int,int]:
    """Publish a complete inventory and cache as one transaction, or roll back."""
    fonts = scan_available_fonts()  # Do not even migrate a cache on discovery failure.
    con = get_db_connection()
    try:
        con.execute('BEGIN IMMEDIATE')
        _stage(con)
        orthographies = load_orthographies()
        fingerprints={};refreshed=0
        for number,font in enumerate(fonts,1):
            path,index=os.path.abspath(font['filepath']),font['findex']
            if not isinstance(index,int) or index<0 or '\x00' in path:
                raise FontDatabaseError('Invalid file/face identity')
            if con.execute('SELECT 1 FROM CurrentFonts WHERE filepath=? AND findex=?',(path,index)).fetchone():
                raise FontDatabaseError('Duplicate discovered file/face identity')
            fingerprints.setdefault(path,None)
            if fingerprints[path] is None:fingerprints[path]=_fingerprint(path)
            preloaded = font.get('_metadata') if font.get('_metadata_token') == fingerprints[path][1] else None
            _,changed=_ensure_cache(con,path,index,fingerprints[path],orthographies,force,preloaded)
            refreshed+=int(changed)
            values={key:font[key] for key in ('family','style','spacing','slant','weight','width','description')}
            values.update(filepath=path,findex=index)
            _upsert(con,'Fonts',values)
            con.execute('INSERT INTO CurrentFonts VALUES (?,?)',(path,index))
            if progress_callback:progress_callback(number,len(fonts),'%s (%s)'%(font['family'],font['style']))
        _prune(con)
        _check_tokens(fingerprints)
        con.commit()
        return len(fonts),refreshed
    except BaseException as error:
        con.rollback()
        if isinstance(error,Exception) and not isinstance(error,FontDatabaseError):
            raise FontDatabaseError('Font synchronization failed; prior inventory preserved: %s'%error) from error
        raise
    finally:con.close()


sync_database = update_database


def get_all_families() -> List[str]:
    """Return all unique family names sorted naturally."""
    con = get_db_connection()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT family FROM Fonts ORDER BY family COLLATE NOCASE")
    families = [row[0] for row in cur.fetchall()]
    con.close()
    return families


def get_fonts_by_family(family: str) -> List[Dict]:
    """Return all variations/faces for a font family."""
    con = get_db_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT f.uid, f.filepath, f.findex, f.family, f.style, f.spacing, f.slant, f.weight, f.width, f.description,
               m.vendor, m.filetype, m.'license-type', m.'n-glyphs', m.copyright, m.version, m.designer
        FROM Fonts f
        LEFT JOIN Metadata m ON f.filepath = m.filepath AND f.findex = m.findex
        WHERE f.family = ?
        ORDER BY f.weight, f.slant, f.style
        """,
        (family,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_detailed_font_info(family_or_file: str, index: int = 0) -> Optional[Dict]:
    """Retrieve full metadata, Panose, and Orthography for a font family or specific file."""
    con = get_db_connection()
    cur = con.cursor()

    if os.path.isfile(family_or_file):
        cur.execute(
            """
            SELECT f.*, m.owner, m.psname, m.filetype, m.'n-glyphs' as n_glyphs, m.copyright,
                   m.version, m.description as meta_description, m.'license-data' as license_data,
                   m.'license-url' as license_url, m.vendor, m.designer, m.'designer-url' as designer_url,
                   m.'license-type' as license_type, m.fsType, m.filesize, m.checksum,
                   p.P0, p.P1, p.P2, p.P3, p.P4, p.P5, p.P6, p.P7, p.P8, p.P9,
                   o.support as supported_orthographies, o.sample as sample_text
            FROM Fonts f
            LEFT JOIN Metadata m ON f.filepath = m.filepath AND f.findex = m.findex
            LEFT JOIN Panose p ON f.filepath = p.filepath AND f.findex = p.findex
            LEFT JOIN Orthography o ON f.filepath = o.filepath AND f.findex = o.findex
            WHERE f.filepath = ? AND f.findex = ?
            """,
            (family_or_file, index)
        )
    else:
        cur.execute(
            """
            SELECT f.*, m.owner, m.psname, m.filetype, m.'n-glyphs' as n_glyphs, m.copyright,
                   m.version, m.description as meta_description, m.'license-data' as license_data,
                   m.'license-url' as license_url, m.vendor, m.designer, m.'designer-url' as designer_url,
                   m.'license-type' as license_type, m.fsType, m.filesize, m.checksum,
                   p.P0, p.P1, p.P2, p.P3, p.P4, p.P5, p.P6, p.P7, p.P8, p.P9,
                   o.support as supported_orthographies, o.sample as sample_text
            FROM Fonts f
            LEFT JOIN Metadata m ON f.filepath = m.filepath AND f.findex = m.findex
            LEFT JOIN Panose p ON f.filepath = p.filepath AND f.findex = p.findex
            LEFT JOIN Orthography o ON f.filepath = o.filepath AND f.findex = o.findex
            WHERE f.family = ?
            ORDER BY f.weight, f.slant
            LIMIT 1
            """,
            (family_or_file,)
        )

    row = cur.fetchone()

    if not row:
        con.close()
        if os.path.isfile(family_or_file):
            return extract_font_metadata(family_or_file, index)
        return None

    row_dict = dict(row)
    filepath = row_dict.get("filepath")
    findex = row_dict.get("findex", 0)

    if not row_dict.get("filetype") and filepath and os.path.isfile(filepath):
        try:
            con.execute('BEGIN IMMEDIATE')
            fingerprint = _fingerprint(filepath)
            _ensure_cache(con, filepath, findex, fingerprint, load_orthographies())
            _check_tokens({filepath: fingerprint})
            con.commit()
        except BaseException as error:
            con.rollback()
            con.close()
            if isinstance(error, Exception) and not isinstance(error, FontDatabaseError):
                raise FontDatabaseError('Metadata repair failed: %s' % error) from error
            raise
        con.close()
        return get_detailed_font_info(family_or_file, index)

    con.close()
    return row_dict


def delete_fonts_by_paths(paths: List[str]):
    """Delete exactly the supplied literal paths, including all face indexes."""
    if not paths:return
    con=get_db_connection()
    try:
        con.execute('BEGIN IMMEDIATE')
        for table in ALL_CACHE_TABLES:
            con.executemany('DELETE FROM "'+table+'" WHERE filepath = ? COLLATE BINARY',[(path,) for path in paths])
        con.commit()
    except BaseException:
        con.rollback();raise
    finally:con.close()


def _directory_prefix(path):
    return os.path.abspath(path).rstrip(os.sep)+os.sep


def purge_directory(directory_path: str):
    """Purge literal descendants, never sibling-prefix or SQL-wildcard matches."""
    prefix=_directory_prefix(directory_path)
    con=get_db_connection()
    try:
        con.execute('BEGIN IMMEDIATE')
        for table in ALL_CACHE_TABLES:
            con.execute('DELETE FROM "'+table+'" WHERE substr(filepath,1,?) = ? COLLATE BINARY',(len(prefix),prefix))
        con.commit()
    except BaseException:
        con.rollback();raise
    finally:con.close()


def count_fonts_in_directory(directory_path: str) -> int:
    prefix=_directory_prefix(directory_path)
    con=get_db_connection()
    try:
        return con.execute('SELECT count(*) FROM Fonts WHERE substr(filepath,1,?) = ? COLLATE BINARY',(len(prefix),prefix)).fetchone()[0]
    finally:con.close()


def scan_and_index_directory(directory_path: str, progress_callback: Optional[Callable[[int,int,str],None]] = None) -> int:
    """Synchronize a readable directory atomically; missing directories are a no-op."""
    directory_path=os.path.abspath(directory_path)
    if not os.path.isdir(directory_path):return 0
    con=get_db_connection()
    try:
        con.execute('BEGIN IMMEDIATE');_stage(con)
        paths=list(_walk_fonts(directory_path));fingerprints={};count=0
        orthographies=load_orthographies()
        for number,path in enumerate(paths,1):
            fingerprints[path]=_fingerprint(path)
            for index in _directory_face_indexes(con, path, fingerprints[path][0]):
                meta,_=_ensure_cache(con,path,index,fingerprints[path],orthographies)
                values=_font_row(path,index,meta)
                old=con.execute('SELECT spacing,slant,weight,width FROM Fonts WHERE filepath=? COLLATE BINARY AND findex=?',(path,index)).fetchone()
                if old:
                    values.update(zip(('spacing','slant','weight','width'),old))
                _upsert(con,'Fonts',values)
                con.execute('INSERT INTO CurrentFonts VALUES (?,?)',(path,index));count+=1
            if progress_callback:progress_callback(number,len(paths),os.path.basename(path))
        _prune(con,directory_path);_check_tokens(fingerprints)
        con.commit();return count
    except BaseException as error:
        con.rollback()
        if isinstance(error,Exception) and not isinstance(error,FontDatabaseError):
            raise FontDatabaseError('Directory indexing failed; prior data preserved: %s'%error) from error
        raise
    finally:con.close()


class FontDatabase:
    """Object-oriented database proxy for Font Manager database."""

    def __init__(self):
        self.con = get_db_connection()

    def get_families(self) -> List[str]:
        return get_all_families()

    def get_fonts(self, family: str) -> List[Dict]:
        return get_fonts_by_family(family)

    def get_info(self, family_or_path: str, index: int = 0) -> Optional[Dict]:
        return get_detailed_font_info(family_or_path, index)

    def sync(self, force: bool = False, callback: Optional[Callable] = None) -> Tuple[int, int]:
        return sync_database(force=force, progress_callback=callback)

    def count_fonts_in_directory(self, path: str) -> int:
        return count_fonts_in_directory(path)

    def scan_and_index_directory(self, path: str, callback: Optional[Callable] = None) -> int:
        return scan_and_index_directory(path, progress_callback=callback)

    def purge_directory(self, path: str):
        purge_directory(path)

    def delete_fonts(self, paths: List[str]):
        delete_fonts_by_paths(paths)

