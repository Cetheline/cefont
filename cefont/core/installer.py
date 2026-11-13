"""Font installation, removal, and extraction management."""

import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

from cefont.core.config import USER_FONT_DIR, ensure_directories
from cefont.core.db import FontDatabase
from cefont.core.fontconfig import trigger_fontconfig_cache
from cefont.core.freetype_backend import extract_font_metadata
from cefont.core.archives import extract_tar
from cefont.core.filesystem import contained_parts, validate_regular_file, remove_regular_file

SUPPORTED_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2", ".dfont", ".pfa", ".pfb"}
SUPPORTED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}


def sanitize_filename(name: str) -> str:
    """Matches font_manager_to_filename: replace spaces and dashes with underscores."""
    s = name.replace(" ", "_").replace("-", "_")
    return re.sub(r"[^\w\.]", "_", s)


def get_suggested_installation_path(font_filepath: str, face_index: int = 0) -> Optional[str]:
    """
    Compute suggested target path according to Font Manager convention:
    ~/.local/share/fonts/<Vendor>/<Filetype>/<Family>/<SuggestedName>.<ext>
    """
    meta = extract_font_metadata(font_filepath, face_index)
    if not meta:
        return None

    vendor = sanitize_filename(meta.get("vendor") or "Unknown")
    filetype = sanitize_filename(meta.get("filetype") or "TrueType")
    family = sanitize_filename(meta.get("family") or os.path.splitext(os.path.basename(font_filepath))[0])
    style = sanitize_filename(meta.get("style") or "Regular")
    is_variable = meta.get("variable", False)

    ext = os.path.splitext(font_filepath)[1].lstrip(".") or "ttf"

    if is_variable:
        vars_str = sanitize_filename(meta.get("vars") or "")
        filename = f"{family}_{style}_VF_{vars_str}.{ext}"
    else:
        filename = f"{family}_{style}.{ext}"

    # Clean double underscores
    filename = re.sub(r"__+", "_", filename)

    target_dir = os.path.join(USER_FONT_DIR, vendor, filetype, family)
    return os.path.join(target_dir, filename)


class FontInstaller:
    """Manages installing and removing user font files."""

    def __init__(self, db: Optional[FontDatabase] = None):
        ensure_directories()
        self.db = db or FontDatabase()

    def install_font(self, font_path: str) -> Tuple[bool, str, Optional[str]]:
        """Install a single font file into ~/.local/share/fonts/."""
        if not os.path.isfile(font_path):
            return False, f"File does not exist: {font_path}", None

        target = get_suggested_installation_path(font_path)
        if not target:
            # Fallback to direct name under user font dir
            target_dir = os.path.join(USER_FONT_DIR, "Misc")
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, os.path.basename(font_path))

        os.makedirs(os.path.dirname(target), exist_ok=True)

        try:
            shutil.copy2(font_path, target)
            self.db.scan_and_index_directory(os.path.dirname(target))
            trigger_fontconfig_cache()
            return True, f"Installed to {target}", target
        except Exception as e:
            return False, f"Failed to install {font_path}: {str(e)}", None

    def install_archive(self, archive_path: str) -> Tuple[int, List[str]]:
        """Extract archive and install all font files found inside."""
        if not os.path.isfile(archive_path):
            return 0, [f"Archive does not exist: {archive_path}"]

        installed = 0
        errors = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                if archive_path.endswith(".zip"):
                    with zipfile.ZipFile(archive_path, "r") as z:
                        z.extractall(tmp_dir)
                else:
                    with tarfile.open(archive_path, "r:*") as t:
                        extract_tar(t, tmp_dir)
            except Exception as e:
                return 0, [f"Could not extract archive {archive_path}: {str(e)}"]

            for root, _, files in os.walk(tmp_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in SUPPORTED_FONT_EXTENSIONS:
                        fp = os.path.join(root, f)
                        ok, msg, _ = self.install_font(fp)
                        if ok:
                            installed += 1
                        else:
                            errors.append(msg)

        return installed, errors

    def remove_font(self, font_identifier: str) -> Tuple[bool, str]:
        """
        Remove a font by file path or family name from ~/.local/share/fonts/.
        Refuses to delete system-wide fonts (/usr/share/fonts, etc.).
        """
        paths_to_delete = []

        if os.path.isfile(font_identifier):
            norm = os.path.abspath(font_identifier)
            try:
                contained_parts(USER_FONT_DIR, norm)
            except ValueError:
                return False, f"Refusing to delete font outside user font directory: {norm}"
            paths_to_delete.append(norm)
        else:
            # Query by family
            fonts = self.db.get_fonts(font_identifier)
            if not fonts:
                return False, f"Font '{font_identifier}' not found in database."

            for f in fonts:
                fp = os.path.abspath(f["filepath"])
                try:
                    contained_parts(USER_FONT_DIR, fp)
                    paths_to_delete.append(fp)
                except ValueError:
                    continue  # Preserve mixed families' system-wide faces.

        if not paths_to_delete:
            return False, f"No user-installed fonts found for '{font_identifier}' in {USER_FONT_DIR}."

        paths_to_delete = list(dict.fromkeys(paths_to_delete))
        try:
            for path in paths_to_delete:
                validate_regular_file(USER_FONT_DIR, path)
        except (OSError, ValueError) as e:
            return False, f"Refusing unsafe or unavailable font path: {e}"

        deleted = 0
        for p in paths_to_delete:
            try:
                remove_regular_file(USER_FONT_DIR, p)
                deleted += 1
            except Exception as e:
                return False, f"Failed to delete {p}: {str(e)}"

        self.db.delete_fonts(paths_to_delete)
        trigger_fontconfig_cache()
        return True, f"Successfully removed {deleted} font files."
