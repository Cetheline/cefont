"""Backup and restore configuration and user font files."""

import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

from cefont.core.config import (
    ACTIONS_FILE,
    COLLECTIONS_FILE,
    COMPARISONS_FILE,
    FONTCONFIG_CONF_DIR,
    PACKAGE_CONFIG_DIR,
    PINNED_COMPARISONS_FILE,
    SOURCES_FILE,
    USER_FONT_DIR,
    ensure_directories,
)
from cefont.core.archives import extract_tar
from cefont.core.db import FontDatabase
from cefont.core.fontconfig import trigger_fontconfig_cache
from cefont.core.filesystem import validate_regular_file
from cefont.core.state import atomic_write, configuration_lock


def export_backup(
    destination_path: str,
    include_fonts: bool = True,
    include_settings: bool = True,
    include_collections: bool = True,
    include_sources: bool = True,
    include_actions: bool = True,
) -> Tuple[bool, str]:
    """
    Export Font Manager configuration (and optionally user fonts) to a directory or archive (.tar.gz / .zip).
    """
    # No overwrite/merge: even empty directories and dangling links are refused.
    # Staging is private and never reuses a caller-owned `<destination>.staging`.
    dest = os.path.abspath(destination_path)
    parent = os.path.dirname(dest)
    is_archive = dest.endswith((".tar.gz", ".tgz", ".zip"))
    try:
        if os.path.lexists(dest):
            raise ValueError("Backup destination already exists")
        resolved_dest = os.path.realpath(dest)
        for source in (USER_FONT_DIR, PACKAGE_CONFIG_DIR, FONTCONFIG_CONF_DIR):
            resolved_source = os.path.realpath(source)
            shared = os.path.commonpath([resolved_dest, resolved_source])
            if shared in (resolved_dest, resolved_source):
                raise ValueError("Backup destination overlaps a managed source directory")
        if not os.path.isdir(parent):
            raise ValueError("Backup destination parent must already exist")

        with tempfile.TemporaryDirectory(prefix=".cefont-backup-", dir=parent) as work:
            staging_dir = os.path.join(work, "payload")
            os.mkdir(staging_dir)
            items = []
            if include_actions:
                items.append(ACTIONS_FILE)
            if include_sources:
                items.append(SOURCES_FILE)
            if include_collections:
                items.extend([COLLECTIONS_FILE, PINNED_COMPARISONS_FILE, COMPARISONS_FILE])
            for source in items:
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(staging_dir, os.path.basename(source)))
            if include_settings and os.path.isdir(FONTCONFIG_CONF_DIR):
                fc_dest = os.path.join(staging_dir, "fontconfig", "conf.d")
                os.makedirs(fc_dest)
                for name in os.listdir(FONTCONFIG_CONF_DIR):
                    source = os.path.join(FONTCONFIG_CONF_DIR, name)
                    if name.endswith(".conf") and os.path.isfile(source):
                        shutil.copy2(source, os.path.join(fc_dest, name))
            if include_fonts and os.path.isdir(USER_FONT_DIR):
                shutil.copytree(USER_FONT_DIR, os.path.join(staging_dir, "fonts"))

            if is_archive:
                packed = os.path.join(work, "archive")
                if dest.endswith(".zip"):
                    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
                        for root, _, files in os.walk(staging_dir):
                            for name in files:
                                full = os.path.join(root, name)
                                archive.write(full, os.path.relpath(full, staging_dir))
                else:
                    with tarfile.open(packed, "w:gz") as archive:
                        archive.add(staging_dir, arcname="")
                # Exclusive publication: a destination appearing meanwhile wins.
                os.link(packed, dest)
            else:
                # Reserve a new directory exclusively, then replace that empty
                # reservation with the completed payload on the same filesystem.
                os.mkdir(dest)
                try:
                    os.rename(staging_dir, dest)
                except Exception:
                    os.rmdir(dest)  # Only our empty reservation, never a tree.
                    raise
        return True, f"Backup successfully exported to: {dest}"
    except Exception as e:
        return False, f"Export failed: {str(e)}"


def import_backup(source_path: str, db: Optional[FontDatabase] = None) -> Tuple[bool, str]:
    """
    Import Font Manager configuration from a directory or archive (.tar.gz / .zip).
    """
    src = os.path.abspath(source_path)
    if not os.path.exists(src):
        return False, f"Source path does not exist: {src}"

    is_archive = src.endswith(".tar.gz") or src.endswith(".tgz") or src.endswith(".zip")
    staging_dir = src
    temp_extract = None

    if is_archive:
        try:
            temp_extract = tempfile.mkdtemp(prefix="cefont-import-")
            if src.endswith(".zip"):
                with zipfile.ZipFile(src, "r") as z:
                    z.extractall(temp_extract)
            else:
                with tarfile.open(src, "r:gz") as tar:
                    extract_tar(tar, temp_extract)
            staging_dir = temp_extract
        except Exception as e:
            if temp_extract and os.path.isdir(temp_extract):
                shutil.rmtree(temp_extract)
            return False, f"Failed to extract archive: {str(e)}"

    try:
        if not os.path.isdir(staging_dir) or os.path.islink(staging_dir):
            raise ValueError('Backup source must be a regular directory')
        copies = []
        configs = {'Collections.json', 'Comparisons.json', 'PinnedComparisons.json', 'Sources.xml', 'Actions.json'}
        def walk_error(error):
            raise error
        # Classify only paths relative to the payload root. Preflight all links
        # and destinations before making any managed-directory changes.
        for root, dirs, files in os.walk(staging_dir, onerror=walk_error):
            if any(os.path.islink(os.path.join(root, name)) for name in dirs):
                raise ValueError('Backup contains a symlink directory')
            for name in files:
                full_src = os.path.join(root, name)
                validate_regular_file(staging_dir, full_src)
                parts = Path(os.path.relpath(full_src, staging_dir)).parts
                if parts[0] == 'fonts' and len(parts) > 1:
                    managed, relative = USER_FONT_DIR, parts[1:]
                elif len(parts) == 1 and name in configs:
                    managed, relative = PACKAGE_CONFIG_DIR, parts
                elif len(parts) == 3 and parts[:2] == ('fontconfig', 'conf.d') and name.endswith('.conf'):
                    managed, relative = FONTCONFIG_CONF_DIR, (name,)
                else:
                    continue
                target = os.path.join(managed, *relative)
                current = managed
                for component in ('',) + tuple(relative):
                    current = os.path.join(current, component) if component else current
                    if os.path.islink(current):
                        raise ValueError('Backup destination contains a symlink: ' + current)
                if os.path.lexists(target) and not os.path.isfile(target):
                    raise ValueError('Backup destination is not a regular file: ' + target)
                copies.append((full_src, target))
        ensure_directories()
        with configuration_lock(*[target for _, target in copies
                                   if os.path.commonpath([USER_FONT_DIR, target]) != USER_FONT_DIR]):
            for full_src, target in copies:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.commonpath([USER_FONT_DIR, target]) == USER_FONT_DIR:
                    shutil.copy2(full_src, target)
                else:
                    with open(full_src, 'rb') as source:
                        atomic_write(target, source.read())

        # Re-index database and refresh cache
        trigger_fontconfig_cache()
        database = db or FontDatabase()
        database.sync(force=True)

        if temp_extract and os.path.isdir(temp_extract):
            shutil.rmtree(temp_extract)

        return True, "Backup successfully imported and database re-synchronized."
    except Exception as e:
        if temp_extract and os.path.isdir(temp_extract):
            shutil.rmtree(temp_extract)
        return False, f"Import failed: {str(e)}"
