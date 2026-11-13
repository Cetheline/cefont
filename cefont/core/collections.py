"""Collections manager for user font collections stored in Collections.json."""

import json
import os
import time
from typing import Dict, List, Optional, Set, Tuple

from cefont.core.state import ConfigurationError, read_json, write_json, serialized
from cefont.core.config import COLLECTIONS_FILE, ensure_directories
from cefont.core.fontconfig import disable_families, enable_families, load_rejected_families


def load_collections() -> List[Dict]:
    """Load all collections from Collections.json."""
    return read_json(COLLECTIONS_FILE)


def save_collections(collections: List[Dict]):
    """Save collections list to Collections.json."""
    ensure_directories()
    write_json(COLLECTIONS_FILE, collections)


def _find_collection(collections: List[Dict], name: str) -> Optional[Dict]:
    """Find a collection recursively by name."""
    for col in collections:
        if col.get("name", "").lower() == name.lower():
            return col
        children = col.get("children", [])
        found = _find_collection(children, name)
        if found:
            return found
    return None


def get_collection(name: str) -> Optional[Dict]:
    """Retrieve collection by name."""
    collections = load_collections()
    return _find_collection(collections, name)


@serialized(lambda *args, **kwargs: [COLLECTIONS_FILE])
def create_collection(name: str, comment: Optional[str] = None, parent: Optional[str] = None) -> bool:
    """Create a new collection."""
    collections = load_collections()
    if _find_collection(collections, name):
        return False

    now_str = time.strftime("%c")
    new_col = {
        "name": name,
        "comment": comment or f"Created : {now_str}",
        "active": True,
        "families": [],
        "children": [],
    }

    if parent:
        parent_col = _find_collection(collections, parent)
        if not parent_col:
            return False
        if "children" not in parent_col:
            parent_col["children"] = []
        parent_col["children"].append(new_col)
    else:
        collections.append(new_col)

    save_collections(collections)
    return True


@serialized(lambda *args, **kwargs: [COLLECTIONS_FILE])
def delete_collection(name: str) -> bool:
    """Delete a collection by name recursively."""
    collections = load_collections()

    def remove_rec(col_list: List[Dict]) -> bool:
        for idx, col in enumerate(col_list):
            if col.get("name", "").lower() == name.lower():
                col_list.pop(idx)
                return True
            if remove_rec(col.get("children", [])):
                return True
        return False

    success = remove_rec(collections)
    if success:
        save_collections(collections)
    return success


@serialized(lambda *args, **kwargs: [COLLECTIONS_FILE])
def rename_collection(old_name: str, new_name: str) -> bool:
    """Rename an existing collection."""
    collections = load_collections()
    col = _find_collection(collections, old_name)
    if not col:
        return False
    if _find_collection(collections, new_name) and old_name.lower() != new_name.lower():
        return False

    col["name"] = new_name
    save_collections(collections)
    return True


@serialized(lambda *args, **kwargs: [COLLECTIONS_FILE])
def add_families_to_collection(name: str, families: List[str]) -> Tuple[bool, int]:
    """Add font families to a collection."""
    collections = load_collections()
    col = _find_collection(collections, name)
    if not col:
        return False, 0

    current_fams = set(col.get("families", []))
    before_len = len(current_fams)
    for f in families:
        current_fams.add(f.strip())

    col["families"] = sorted(current_fams, key=str.lower)
    save_collections(collections)
    added = len(current_fams) - before_len
    return True, added


@serialized(lambda *args, **kwargs: [COLLECTIONS_FILE])
def remove_families_from_collection(name: str, families: List[str]) -> Tuple[bool, int]:
    """Remove font families from a collection."""
    collections = load_collections()
    col = _find_collection(collections, name)
    if not col:
        return False, 0

    current_fams = set(col.get("families", []))
    before_len = len(current_fams)
    for f in families:
        current_fams.discard(f.strip())

    col["families"] = sorted(current_fams, key=str.lower)
    save_collections(collections)
    removed = before_len - len(current_fams)
    return True, removed


def get_all_families_in_collection(col: Dict) -> Set[str]:
    """Get all families in a collection including its children."""
    families = set(col.get("families", []))
    for child in col.get("children", []):
        families.update(get_all_families_in_collection(child))
    return families


def enable_collection(name: str) -> Tuple[bool, int]:
    """Enable all font families in a collection (removes from reject list)."""
    col = get_collection(name)
    if not col:
        return False, 0

    fams = list(get_all_families_in_collection(col))
    if not fams:
        return True, 0
    removed, _ = enable_families(fams)
    return True, removed


def disable_collection(name: str) -> Tuple[bool, int]:
    """Disable all font families in a collection (adds to reject list)."""
    col = get_collection(name)
    if not col:
        return False, 0

    fams = list(get_all_families_in_collection(col))
    added, _ = disable_families(fams)
    return True, added


def get_collection_state(col: Dict) -> str:
    """Determine if a collection is Active, Disabled, or Partially Disabled."""
    rejected = load_rejected_families()
    fams = get_all_families_in_collection(col)
    if not fams:
        return "Empty"
    if fams.issubset(rejected):
        return "Disabled"
    if fams.intersection(rejected):
        return "Partially Disabled"
    return "Enabled"


class CollectionManager:
    """Object-oriented manager for font collections."""

    def load_collections(self) -> List[Dict]:
        return load_collections()

    def save_collections(self, collections: List[Dict]):
        save_collections(collections)

    def get_collection(self, name: str) -> Optional[Dict]:
        return get_collection(name)

    def create_collection(self, name: str, description: str = "", parent: Optional[str] = None) -> bool:
        return create_collection(name, comment=description, parent=parent)

    def delete_collection(self, name: str) -> bool:
        return delete_collection(name)

    def rename_collection(self, old_name: str, new_name: str) -> bool:
        return rename_collection(old_name, new_name)

    def add_families(self, name: str, families: List[str]) -> int:
        ok, added = add_families_to_collection(name, families)
        if not ok:
            raise ConfigurationError("Collection not found: " + name)
        return added

    def remove_families(self, name: str, families: List[str]) -> int:
        ok, removed = remove_families_from_collection(name, families)
        if not ok:
            raise ConfigurationError("Collection not found: " + name)
        return removed

    def set_collection_active(self, name: str, active: bool) -> bool:
        if active:
            ok, _ = enable_collection(name)
        else:
            ok, _ = disable_collection(name)
        return ok

