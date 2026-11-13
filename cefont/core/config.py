"""Configuration paths and settings for CeFont."""

import os
import shutil
from pathlib import Path
from cefont.core.state import atomic_write, configuration_lock

# Base XDG paths
XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
XDG_CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))

# CeFont specific directories
PACKAGE_NAME = "cefont"
USER_FONT_DIR = os.path.join(XDG_DATA_HOME, "fonts")
PACKAGE_CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, PACKAGE_NAME)
PACKAGE_CACHE_DIR = os.path.join(XDG_CACHE_HOME, PACKAGE_NAME)
FONTCONFIG_CONF_DIR = os.path.join(XDG_CONFIG_HOME, "fontconfig", "conf.d")

# System font directories
SYSTEM_FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/var/lib/flatpak/exports/share/fonts",
]

# Database and configuration files
DATABASE_FILE = os.path.join(PACKAGE_CACHE_DIR, "cefont.sqlite")
LOCALE_FILE = os.path.join(PACKAGE_CACHE_DIR, "locale")
SOURCES_FILE = os.path.join(PACKAGE_CONFIG_DIR, "Sources.xml")
COLLECTIONS_FILE = os.path.join(PACKAGE_CONFIG_DIR, "Collections.json")
PINNED_COMPARISONS_FILE = os.path.join(PACKAGE_CONFIG_DIR, "PinnedComparisons.json")
COMPARISONS_FILE = os.path.join(PACKAGE_CONFIG_DIR, "Comparisons.json")
ACTIONS_FILE = os.path.join(PACKAGE_CONFIG_DIR, "Actions.json")

# Fontconfig generated files
REJECT_CONF = os.path.join(FONTCONFIG_CONF_DIR, "78-Reject.conf")
DIRECTORIES_CONF = os.path.join(FONTCONFIG_CONF_DIR, "09-Directories.conf")
ALIASES_CONF = os.path.join(FONTCONFIG_CONF_DIR, "39-Aliases.conf")
RENDERING_CONF = os.path.join(FONTCONFIG_CONF_DIR, "19-DefaultProperties.conf")
DISPLAY_CONF = os.path.join(FONTCONFIG_CONF_DIR, "19-DisplayProperties.conf")

# Google Fonts storage
GOOGLE_FONTS_DIR = os.path.join(USER_FONT_DIR, "Google Fonts")
GOOGLE_FONTS_API_KEY_B64 = "QUl6YVN5QTlpUmZqMFlYc184RGhJR1Q1YzNGRDBWNmtSQWV5cFA4"

# Application version & branding
VERSION = "1.0.0"
APP_NAME = "CeFont"
AUTHORS = ["Yosatsu"]


def ensure_directories():
    """Ensure all required configuration and cache directories exist."""
    for directory in [
        USER_FONT_DIR,
        PACKAGE_CONFIG_DIR,
        PACKAGE_CACHE_DIR,
        FONTCONFIG_CONF_DIR,
        GOOGLE_FONTS_DIR,
    ]:
        Path(directory).mkdir(parents=True, exist_ok=True)

    # Initial migration/compatibility: if cefont.sqlite does not exist yet,
    # but font-manager.sqlite exists in cache, copy it to avoid cold rescanning
    if not os.path.isfile(DATABASE_FILE):
        legacy_db = os.path.join(XDG_CACHE_HOME, "font-manager", "font-manager.sqlite")
        if os.path.isfile(legacy_db):
            try:
                shutil.copy2(legacy_db, DATABASE_FILE)
            except Exception:
                pass

    # Copy legacy Collections.json if present and cefont does not have one
    legacy_coll = os.path.join(XDG_CONFIG_HOME, "font-manager", "Collections.json")
    if os.path.isfile(legacy_coll):
        with configuration_lock(COLLECTIONS_FILE):
            if not os.path.lexists(COLLECTIONS_FILE):
                with open(legacy_coll, 'rb') as source:
                    atomic_write(COLLECTIONS_FILE, source.read())
