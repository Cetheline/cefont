"""Font comparison and pinned comparisons management."""

import datetime
import json
import os
from typing import Dict, List, Optional, Tuple

from cefont.core.state import read_json, write_json, serialized
from cefont.core.config import (
    COMPARISONS_FILE,
    PINNED_COMPARISONS_FILE,
    ensure_directories,
)
from cefont.core.db import get_detailed_font_info, get_fonts_by_family


class ComparisonManager:
    """Manages saved font comparisons in PinnedComparisons.json."""

    def __init__(self, filepath: Optional[str] = None):
        ensure_directories()
        self.filepath = filepath or PINNED_COMPARISONS_FILE
        if filepath is None and not os.path.exists(self.filepath) and os.path.isfile(COMPARISONS_FILE):
            self.filepath = COMPARISONS_FILE

    def load_pinned(self) -> List[Dict]:
        """Load pinned comparisons from JSON file."""
        return read_json(self.filepath)

    def save_pinned(self, comparisons: List[Dict]):
        """Save pinned comparisons list to JSON file."""
        write_json(self.filepath, comparisons)

    @serialized(lambda self, *args, **kwargs: [self.filepath])
    def add_comparison(self, label: str, font_identifiers: List[str]) -> Dict:
        """
        Create and save a new pinned comparison.
        font_identifiers can be family names or filepaths.
        """
        items = []
        for fid in font_identifiers:
            info = None
            if os.path.isfile(fid):
                info = get_detailed_font_info(fid)
            else:
                fonts = get_fonts_by_family(fid)
                if fonts:
                    info = get_detailed_font_info(fonts[0]["filepath"], fonts[0].get("findex", 0))

            if info:
                items.append({
                    "filepath": info["filepath"],
                    "findex": info.get("findex", 0),
                    "family": info.get("family", ""),
                    "style": info.get("style", "Regular"),
                    "description": info.get("description", f"{info.get('family')} {info.get('style')}")
                })
            else:
                items.append({
                    "family": fid,
                    "description": fid
                })

        new_entry = {
            "label": label,
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": items,
        }

        pinned = self.load_pinned()
        pinned.append(new_entry)
        self.save_pinned(pinned)
        return new_entry

    @serialized(lambda self, *args, **kwargs: [self.filepath])
    def remove_comparison(self, index_or_label: str) -> bool:
        pinned = self.load_pinned()
        idx_to_remove = None

        if index_or_label.isdigit():
            idx = int(index_or_label)
            if 0 <= idx < len(pinned):
                idx_to_remove = idx

        if idx_to_remove is None:
            for i, p in enumerate(pinned):
                if p.get("label", "").lower() == index_or_label.lower():
                    idx_to_remove = i
                    break

        if idx_to_remove is not None:
            pinned.pop(idx_to_remove)
            self.save_pinned(pinned)
            return True
        return False
