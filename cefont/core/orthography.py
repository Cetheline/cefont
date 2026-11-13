"""Orthography analysis and language coverage."""

import os
from typing import Dict, List, Optional, Set, Tuple

from cefont.core.data import load_orthographies
from cefont.core.db import get_detailed_font_info, get_fonts_by_family
from cefont.core.freetype_backend import get_all_codepoints


def list_available_orthographies() -> List[Dict]:
    """List all 128 language and script orthographies with metadata."""
    orthographies = load_orthographies()
    result = []
    for name, data in sorted(orthographies.items(), key=lambda x: x[0]):
        result.append({
            "name": name,
            "native": data.get("native") or "",
            "key": data.get("key"),
            "sample": data.get("sample") or "",
            "total_codepoints": len(data.get("codepoints", set())),
            "pangram_count": len(data.get("pangrams", [])),
        })
    return result


def analyze_font_orthographies(
    family_or_file: str,
    index: int = 0,
    filter_lang: Optional[str] = None,
    min_coverage: float = 0.0
) -> Dict:
    """
    Analyze language coverage for a given font file or family.
    Returns:
    {
        'font': str,
        'filepath': str,
        'findex': int,
        'total_font_glyphs': int,
        'results': [
            {
                'name': str,
                'native': str,
                'coverage': float (0-100),
                'present_count': int,
                'required_count': int,
                'missing_chars': [str],
                'sample': str,
                'pangram': str,
                'fully_supported': bool
            }
        ]
    }
    """
    filepath = family_or_file
    findex = index

    if not os.path.isfile(family_or_file):
        fonts = get_fonts_by_family(family_or_file)
        if not fonts:
            info = get_detailed_font_info(family_or_file)
            if not info:
                raise ValueError(f"Font family or file not found: {family_or_file}")
            filepath = info["filepath"]
            findex = info.get("findex", 0)
        else:
            filepath = fonts[0]["filepath"]
            findex = fonts[0].get("findex", 0)

    font_codepoints = get_all_codepoints(filepath, findex)
    orthographies = load_orthographies()

    results = []
    for name, data in orthographies.items():
        if filter_lang and filter_lang.lower() not in name.lower() and (not data.get("native") or filter_lang.lower() not in data["native"].lower()):
            continue

        req_cps = data.get("codepoints", set())
        if not req_cps:
            continue

        key = data.get("key")
        if key is not None and key != 0 and key not in font_codepoints:
            key_present = False
        else:
            key_present = True

        present = req_cps.intersection(font_codepoints)
        missing = req_cps - font_codepoints

        coverage = (len(present) / len(req_cps)) * 100.0 if req_cps else 0.0
        if coverage < min_coverage:
            continue

        missing_chars = [chr(cp) for cp in sorted(missing)[:50] if 0x20 <= cp <= 0x10FFFF]
        pangram = data["pangrams"][0] if data.get("pangrams") else (data.get("sample") or "")

        results.append({
            "name": name,
            "native": data.get("native") or "",
            "coverage": round(coverage, 1),
            "present_count": len(present),
            "required_count": len(req_cps),
            "missing_chars": missing_chars,
            "sample": data.get("sample") or "",
            "pangram": pangram,
            "fully_supported": (len(missing) == 0 and key_present),
        })

    # Sort results: fully supported first, then by coverage descending, then alphabetical
    results.sort(key=lambda x: (not x["fully_supported"], -x["coverage"], x["name"]))

    return {
        "font": os.path.basename(filepath),
        "filepath": filepath,
        "findex": findex,
        "total_font_glyphs": len(font_codepoints),
        "results": results,
    }
