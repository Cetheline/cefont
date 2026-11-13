"""Font Sources management (Sources.xml and 09-Directories.conf)."""

import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from cefont.core.config import (
    SOURCES_FILE,
    DIRECTORIES_CONF,
    ensure_directories,
)
from cefont.core.db import FontDatabase
from cefont.core.state import atomic_write, read_xml, serialized, snapshot
from cefont.core.fontconfig import (
    _format_xml_and_save,
    load_active_directories,
    save_active_directories,
)


class SourceManager:
    """Manages custom user font directories (Sources)."""

    def __init__(self, db: Optional[FontDatabase] = None):
        ensure_directories()
        self.db = db or FontDatabase()

    def load_sources(self) -> List[Dict[str, any]]:
        """
        Load all defined sources from Sources.xml.
        Returns a list of dicts: [{'path': str, 'active': bool, 'exists': bool, 'count': int}]
        """
        all_paths = self._read_sources_xml()
        active_dirs = set(self._normalize_path(p) for p in load_active_directories())

        result = []
        for path in all_paths:
            norm = self._normalize_path(path)
            exists = os.path.isdir(norm)
            active = norm in active_dirs
            count = 0
            if exists:
                count = self.db.count_fonts_in_directory(norm)
            result.append({
                "path": norm,
                "active": active,
                "exists": exists,
                "count": count,
            })
        return result

    def _normalize_path(self, path: str) -> str:
        return os.path.abspath(os.path.expanduser(path))

    def _read_sources_xml(self) -> List[str]:
        root, revision = read_xml(SOURCES_FILE)
        paths = [node.text.strip() for node in root.findall('.//source') if node.text]
        return snapshot(paths, SOURCES_FILE, revision)

    def _save_sources_xml(self, paths: List[str]):
        if not paths:
            atomic_write(SOURCES_FILE, None, paths)
            return

        root = ET.Element("fontconfig")
        for p in paths:
            elem = ET.SubElement(root, "source")
            elem.text = p

        _format_xml_and_save(root, SOURCES_FILE, paths)

    @serialized(lambda self, *args, **kwargs: [SOURCES_FILE, DIRECTORIES_CONF])
    def add_source(self, path: str, activate: bool = True) -> Tuple[bool, str, int]:
        """
        Add a directory source.
        Returns (success: bool, message: str, fonts_added: int)
        """
        norm = self._normalize_path(path)
        if not os.path.isdir(norm):
            return False, f"Directory does not exist: {norm}", 0

        sources = self._read_sources_xml()
        active = set(self._normalize_path(p) for p in load_active_directories())
        norm_sources = [self._normalize_path(s) for s in sources]
        if norm not in norm_sources:
            sources.append(norm)
            self._save_sources_xml(sources)

        if activate:
            active.add(norm)
            save_active_directories(list(active))

        # Index fonts into database
        count = self.db.scan_and_index_directory(norm)
        return True, f"Added source: {norm}", count

    @serialized(lambda self, *args, **kwargs: [SOURCES_FILE, DIRECTORIES_CONF])
    def remove_source(self, path: str, purge_db: bool = True) -> Tuple[bool, str]:
        """
        Remove a directory source and optionally purge its fonts from database.
        """
        norm = self._normalize_path(path)
        sources = [self._normalize_path(s) for s in self._read_sources_xml()]
        active = [self._normalize_path(p) for p in load_active_directories() if self._normalize_path(p) != norm]
        if norm not in sources:
            return False, f"Source not found: {norm}"

        sources = [s for s in sources if s != norm]
        self._save_sources_xml(sources)

        save_active_directories(active)

        if purge_db:
            self.db.purge_directory(norm)

        return True, f"Removed source: {norm}"

    @serialized(lambda self, *args, **kwargs: [SOURCES_FILE, DIRECTORIES_CONF])
    def enable_source(self, path: str) -> Tuple[bool, str]:
        norm = self._normalize_path(path)
        sources = [self._normalize_path(s) for s in self._read_sources_xml()]
        if norm not in sources:
            return False, f"Source not found: {norm}"

        active = set(self._normalize_path(p) for p in load_active_directories())
        active.add(norm)
        save_active_directories(list(active))

        # Ensure directory is scanned
        self.db.scan_and_index_directory(norm)
        return True, f"Enabled source: {norm}"

    @serialized(lambda self, *args, **kwargs: [SOURCES_FILE, DIRECTORIES_CONF])
    def disable_source(self, path: str) -> Tuple[bool, str]:
        norm = self._normalize_path(path)
        active = [self._normalize_path(p) for p in load_active_directories() if self._normalize_path(p) != norm]
        save_active_directories(active)
        return True, f"Disabled source: {norm}"
