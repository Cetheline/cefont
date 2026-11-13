"""Terminal UI rendering, ANSI colors, tables, waterfall, and character map views."""

import os
import sys
from cefont.core.freetype_backend import FreeTypeUnavailableError, BitmapFormatError
from typing import Any, Dict, List, Optional, Tuple, Union

# ANSI escape codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
GRAY = "\033[90m"

BG_BLACK = "\033[40m"
BG_BLUE = "\033[44m"
BG_GRAY = "\033[100m"


def colorize(text: str, color_code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{color_code}{text}{RESET}"


def badge(text: str, bg_color: str, fg_color: str = WHITE) -> str:
    if not sys.stdout.isatty():
        return f"[{text}]"
    return f"{bg_color}{fg_color}{BOLD} {text} {RESET}"


def status_badge(enabled: bool) -> str:
    if enabled:
        return badge("ENABLED", "\033[42m", WHITE)
    else:
        return badge("DISABLED", "\033[41m", WHITE)


def format_table(headers: List[str], rows: List[List[Any]], align_right: Optional[List[int]] = None) -> str:
    """Format tabular data with borders and automatic column sizing."""
    if not headers and not rows:
        return ""

    num_cols = len(headers) if headers else len(rows[0])
    align_right = align_right or []

    # Calculate max width for each column
    col_widths = [len(str(h)) for h in headers] if headers else [0] * num_cols
    for row in rows:
        for idx in range(min(num_cols, len(row))):
            val = str(row[idx])
            # Strip ANSI when calculating lengths
            raw_len = len(re_strip_ansi(val))
            if raw_len > col_widths[idx]:
                col_widths[idx] = raw_len

    # Add padding
    col_widths = [max(w + 2, 4) for w in col_widths]

    lines = []
    # Top border
    top = "┌" + "┬".join("─" * w for w in col_widths) + "┐"
    lines.append(top)

    # Header row
    if headers:
        header_line = "│"
        for idx, h in enumerate(headers):
            w = col_widths[idx]
            val = f" {h} "
            pad = w - len(h) - 2
            if idx in align_right:
                header_line += " " * pad + colorize(val, BOLD + CYAN) + "│"
            else:
                header_line += colorize(val, BOLD + CYAN) + " " * pad + "│"
        lines.append(header_line)

        # Header divider
        sep = "├" + "┼".join("─" * w for w in col_widths) + "┤"
        lines.append(sep)

    # Data rows
    for r_idx, row in enumerate(rows):
        row_line = "│"
        for idx in range(num_cols):
            w = col_widths[idx]
            val = str(row[idx]) if idx < len(row) else ""
            raw_len = len(re_strip_ansi(val))
            pad = max(0, w - raw_len - 2)
            if idx in align_right:
                row_line += " " * pad + f" {val} " + "│"
            else:
                row_line += f" {val} " + " " * pad + "│"
        lines.append(row_line)

    # Bottom border
    bot = "└" + "┴".join("─" * w for w in col_widths) + "┘"
    lines.append(bot)

    return "\n".join(lines)


def re_strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for accurate string length computation."""
    import re
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def render_waterfall(
    family_or_file: str,
    text: str = "The quick brown fox jumps over the lazy dog",
    sizes: Optional[List[int]] = None,
    filepath: Optional[str] = None,
    findex: int = 0,
    protocol: Optional[str] = "auto"
) -> str:
    """Generate visual waterfall view showing text at various point sizes using FreeType terminal graphics."""
    from cefont.core.renderer import render_font_preview_image
    sizes = sizes or [12, 16, 20, 26, 34, 44]
    output = []
    output.append(colorize(f"── Waterfall Preview: {family_or_file} ──", BOLD + BLUE))

    if protocol == "text" or not filepath or not os.path.isfile(filepath):
        for sz in sizes:
            output.append(f"{colorize(f'{sz:2d} pt', DIM + GRAY)} │ {text}")
        return "\n".join(output)

    for sz in sizes:
        output.append(colorize(f"[{sz:2d} pt]", BOLD + CYAN))
        try:
            rendered = render_font_preview_image(
                filepath=filepath,
                text=text,
                pixel_size=sz,
                face_index=findex,
                protocol=protocol,
                wrap=False
            )
            output.append(rendered)
        except (FreeTypeUnavailableError, BitmapFormatError):
            raise
        except Exception as e:
            output.append(f"{colorize(f'{sz:2d} pt', DIM + GRAY)} │ {text} (render fallback: {e})")

    return "\n".join(output)


def render_charmap_grid(codepoints: List[int], start_row: int = 0x0020, end_row: int = 0x00FF) -> str:
    """
    Render Unicode character map in a 16-column matrix with hex labels.
    """
    lines = []
    # Header: 0 1 2 3 4 5 6 7 8 9 A B C D E F
    col_hdr = "         " + " ".join(f"{x:X}" for x in range(16))
    lines.append(colorize(col_hdr, BOLD + CYAN))
    lines.append(colorize("  " + "─" * 45, GRAY))

    cp_set = set(codepoints)

    # Rows from start_row aligned to 16
    start_base = (start_row // 16) * 16
    end_base = ((end_row // 16) + 1) * 16

    for base in range(start_base, end_base, 16):
        row_str = colorize(f"U+{base:04X}:  ", BOLD + YELLOW)
        row_chars = []
        for offset in range(16):
            cp = base + offset
            if cp in cp_set:
                if 0x20 <= cp <= 0x7E:
                    row_chars.append(chr(cp))
                elif cp > 0x7E and not (0x7F <= cp <= 0x9F):
                    row_chars.append(chr(cp))
                else:
                    row_chars.append(colorize("·", DIM))
            else:
                row_chars.append(colorize("·", GRAY))
        row_str += " ".join(row_chars)
        lines.append(row_str)

    return "\n".join(lines)


def render_comparison_view(
    fonts_info: List[Dict],
    sample_text: Optional[str] = None,
    protocol: Optional[str] = "auto",
    pixel_size: int = 22
) -> str:
    """Render side-by-side / stacked comparison of font faces with real visual font rendering."""
    from cefont.core.renderer import render_font_preview_image
    sample = sample_text or "The quick brown fox jumps over the lazy dog 1234567890"
    lines = []

    lines.append(colorize("═════════════════════════════════════════════════════════════════════", BOLD + BLUE))
    lines.append(colorize("                         FONT COMPARISON MATRIX                      ", BOLD + BLUE))
    lines.append(colorize("═════════════════════════════════════════════════════════════════════", BOLD + BLUE))

    headers = ["Property"] + [f.get("family", "Font") for f in fonts_info]
    properties = [
        ("Style", lambda f: f.get("style", "Regular")),
        ("Filetype", lambda f: f.get("filetype", "TrueType")),
        ("Glyphs", lambda f: str(f.get("n-glyphs") or f.get("n_glyphs") or "—")),
        ("Vendor", lambda f: f.get("vendor", "Unknown")),
        ("License", lambda f: f.get("license-type") or f.get("license_type") or "Unknown"),
        ("Version", lambda f: f.get("version", "1.0")),
        ("Filesize", lambda f: f.get("filesize", "—")),
        ("Location", lambda f: "User" if f.get("filepath", "").startswith(os.path.expanduser("~")) else "System"),
    ]

    table_rows = []
    for prop_name, getter in properties:
        row = [colorize(prop_name, BOLD)]
        for f in fonts_info:
            row.append(getter(f))
        table_rows.append(row)

    lines.append(format_table(headers, table_rows))
    lines.append("")
    lines.append(colorize("Sample Preview (Rendered Outlines):", BOLD + UNDERLINE))
    for f in fonts_info:
        fam = f.get("family", "")
        style = f.get("style", "")
        fp = f.get("filepath", "")
        findex = f.get("findex", 0)
        lines.append(colorize(f"• {fam} ({style}):", BOLD + GREEN))

        if protocol != "text" and fp and os.path.isfile(fp):
            try:
                rendered = render_font_preview_image(
                    filepath=fp,
                    text=sample,
                    pixel_size=pixel_size,
                    face_index=findex,
                    protocol=protocol,
                    wrap=False
                )
                lines.append(rendered)
            except (FreeTypeUnavailableError, BitmapFormatError):
                raise
            except Exception as e:
                lines.append(f"  {sample}")
        else:
            lines.append(f"  {sample}")
        lines.append("")

    return "\n".join(lines)
