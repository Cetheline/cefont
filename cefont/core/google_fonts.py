"""Google Fonts catalog, search, download, and installation management."""

import base64
import json
import os
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from cefont.core.config import (
    GOOGLE_FONTS_DIR,
    PACKAGE_CACHE_DIR,
    USER_FONT_DIR,
    ensure_directories,
)
from cefont.core.db import FontDatabase
from cefont.core.freetype_backend import require_freetype
from cefont.core.fontconfig import trigger_fontconfig_cache
from cefont.core.filesystem import remove_directory

SORT_OPTIONS = ["alpha", "date", "popularity", "style", "trending"]
CACHE_TTL_SECONDS = 2 * 24 * 3600  # 2 days, matching upstream have_valid_cache


def _get_api_key() -> str:
    """Retrieve Google Fonts API key."""
    return base64.b64decode(b"QUl6YVN5QTlpUmZqMFlYc184RGhJR1Q1YzNGRDBWNmtSQWV5cFA4").decode("utf-8")


class GoogleFontsManager:
    """Interface to Google Fonts catalog and font installation."""

    def __init__(self, db: Optional[FontDatabase] = None):
        ensure_directories()
        self.db = db or FontDatabase()
        self.api_key = _get_api_key()

    def get_cache_file(self, sort: str = "alpha") -> str:
        return os.path.join(PACKAGE_CACHE_DIR, f"gfc-{sort}.json")

    def is_cache_valid(self, sort: str = "alpha") -> bool:
        cache_path = self.get_cache_file(sort)
        if not os.path.isfile(cache_path):
            return False
        try:
            mtime = os.path.getmtime(cache_path)
            return (time.time() - mtime) < CACHE_TTL_SECONDS
        except Exception:
            return False

    def update_catalog(self, sort: str = "alpha", force: bool = False) -> Tuple[bool, str, int]:
        """Fetch and cache Google Fonts catalog from Google Web Fonts API."""
        if sort not in SORT_OPTIONS:
            sort = "alpha"

        cache_path = self.get_cache_file(sort)
        if not force and self.is_cache_valid(sort):
            count = self.get_catalog_count(sort)
            return True, f"Using cached catalog (valid for 2 days)", count

        url = f"https://www.googleapis.com/webfonts/v1/webfonts?key={self.api_key}&sort={sort}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FontManagerCLI/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                with open(cache_path, "wb") as f:
                    f.write(data)
                jdata = json.loads(data.decode("utf-8"))
                items = jdata.get("items", [])
                return True, f"Successfully downloaded catalog ({len(items)} families)", len(items)
        except Exception as e:
            if os.path.isfile(cache_path):
                count = self.get_catalog_count(sort)
                return False, f"Network error ({e}), falling back to existing cache", count
            return False, f"Failed to download catalog: {str(e)}", 0

    def get_catalog_count(self, sort: str = "alpha") -> int:
        items = self.load_catalog(sort)
        return len(items)

    def load_catalog(self, sort: str = "alpha") -> List[Dict]:
        """Load catalog from cache file, fetching if missing."""
        if sort not in SORT_OPTIONS:
            sort = "alpha"

        cache_path = self.get_cache_file(sort)
        if not os.path.isfile(cache_path):
            self.update_catalog(sort)

        if not os.path.isfile(cache_path):
            return []

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("items", [])
        except Exception:
            return []

    def search_catalog(
        self,
        query: str = "",
        category: Optional[str] = None,
        subset: Optional[str] = None,
        sort: str = "alpha",
    ) -> List[Dict]:
        """Search Google Fonts catalog by name query, category, or subset."""
        items = self.load_catalog(sort)
        q = query.lower().strip()
        cat = category.lower().strip() if category else None
        sub = subset.lower().strip() if subset else None

        results = []
        for item in items:
            fam = item.get("family", "")
            if q and q not in fam.lower():
                continue
            if cat and item.get("category", "").lower() != cat:
                continue
            if sub and sub not in [s.lower() for s in item.get("subsets", [])]:
                continue

            # Check installation status
            installed_dir = os.path.join(GOOGLE_FONTS_DIR, fam)
            is_installed = os.path.isdir(installed_dir) and any(os.scandir(installed_dir))

            results.append({
                "family": fam,
                "category": item.get("category", ""),
                "variants": item.get("variants", []),
                "subsets": item.get("subsets", []),
                "version": item.get("version", ""),
                "last_modified": item.get("lastModified", ""),
                "installed": is_installed,
                "files": item.get("files", {})
            })

        return results

    def get_family_info(self, family: str) -> Optional[Dict]:
        """Get full details for a Google Font family."""
        items = self.load_catalog("alpha")
        fam_lower = family.lower().strip()
        for item in items:
            if item.get("family", "").lower() == fam_lower:
                installed_dir = os.path.join(GOOGLE_FONTS_DIR, item.get("family", ""))
                is_installed = os.path.isdir(installed_dir) and any(os.scandir(installed_dir))
                return {
                    "family": item.get("family", ""),
                    "category": item.get("category", ""),
                    "variants": item.get("variants", []),
                    "subsets": item.get("subsets", []),
                    "version": item.get("version", ""),
                    "last_modified": item.get("lastModified", ""),
                    "installed": is_installed,
                    "files": item.get("files", {})
                }
        return None

    def install_family(self, family: str) -> Tuple[bool, str, int]:
        """
        Download and install all font files for a Google Font family.
        Installs to ~/.local/share/fonts/Google Fonts/<Family>/<Family>-<Variant>.ttf.
        """
        require_freetype()
        info = self.get_family_info(family)
        if not info:
            return False, f"Font family '{family}' not found in Google Fonts catalog.", 0

        target_dir = os.path.join(GOOGLE_FONTS_DIR, info["family"])
        os.makedirs(target_dir, exist_ok=True)

        files = info.get("files", {})
        if not files:
            return False, f"No downloadable font files found for '{family}'.", 0

        downloaded = 0
        errors = []
        for variant, url in files.items():
            ext = ".ttf"
            if url.endswith(".otf"):
                ext = ".otf"
            filename = f"{info['family']}-{variant}{ext}"
            dest_path = os.path.join(target_dir, filename)

            # Ensure HTTPS
            if url.startswith("http://"):
                url = "https://" + url[7:]

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FontManagerCLI/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    with open(dest_path, "wb") as f:
                        f.write(resp.read())
                downloaded += 1
            except Exception as e:
                errors.append('%s: %s' % (variant, e))

        if downloaded > 0:
            # Reindex directory
            self.db.scan_and_index_directory(target_dir)
            trigger_fontconfig_cache()
            if errors:
                return False, 'Installed %d variants, but %d failed: %s' % (downloaded, len(errors), '; '.join(errors)), downloaded
            return True, f"Successfully installed {downloaded} variants to {target_dir}", downloaded
        else:
            return False, f"Failed to download variants for '{family}'.", 0

    def uninstall_family(self, family: str) -> Tuple[bool, str]:
        """Uninstall a Google Font family and remove files from ~/.local/share/fonts/Google Fonts/."""
        if (not family or family in (".", "..") or os.path.isabs(family)
                or '/' in family or '\\' in family or '\x00' in family):
            return False, "Family must be a single directory name."
        fam_dir = os.path.join(GOOGLE_FONTS_DIR, family)
        if os.path.islink(fam_dir) or not os.path.isdir(fam_dir):
            return False, f"Google Font '{family}' is not installed."

        try:
            remove_directory(GOOGLE_FONTS_DIR, fam_dir)
            self.db.purge_directory(fam_dir)
            trigger_fontconfig_cache()
            return True, f"Uninstalled '{family}' from {fam_dir}"
        except Exception as e:
            return False, f"Failed to uninstall '{family}': {str(e)}"
