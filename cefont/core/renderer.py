"""Terminal Graphics Protocol & FreeType Vector Text Renderer for CeFont.

Supports:
1. Kitty Graphics Protocol (f=100 PNG)
2. iTerm2 Inline Images Protocol (File=inline=1 PNG)
3. DEC Sixel Graphics Protocol (16-shade grayscale)
4. Unicode Half-Block (▀ / ▄) 24-bit TrueColor Universal Fallback
5. Classic ASCII Matrix Fallback

Renders 100% accurate, anti-aliased font vector outlines directly inside
the terminal without requiring an X11/Wayland display server or GUI.
"""

import base64
import ctypes
import os
import shutil
import struct
import sys
import zlib
from typing import Dict, List, Optional, Set, Tuple

from cefont.core.freetype_backend import (
    FT_Bitmap,
    FT_GlyphSlotRec,
    FT_FaceRec,
    bitmap_to_grayscale,
    glyph_slot,
    FT_LOAD_RENDER,
    FT_RENDER_MODE_NORMAL,
    _ft,
    get_ft_context,
    FreeTypeUnavailableError,
    BitmapFormatError,
)

# =========================================================================
# 1. Text & Glyph Bitmap Renderer (FreeType 2)
# =========================================================================

def render_text_bitmap(
    filepath: str,
    text: str,
    pixel_size: int = 24,
    face_index: int = 0,
    fg_color: Tuple[int, int, int] = (255, 255, 255),
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    padding: int = 2,
    max_width: Optional[int] = None,
    wrap: bool = False,
) -> Tuple[int, int, bytearray]:
    """
    Render a string of text (single or multiline) using FreeType into an RGB canvas.
    Returns: (canvas_width, canvas_height, rgb_bytes)
    """
    if _ft is None:
        raise FreeTypeUnavailableError('FreeType is unavailable; install the system libfreetype.so.6 library.')
    ctx = get_ft_context()
    face_ptr = ctypes.c_void_p()
    err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), face_index, ctypes.byref(face_ptr))
    if err != 0 or not face_ptr.value:
        raise RuntimeError(f"FreeType failed to load font: {filepath}")

    try:
        _ft.FT_Set_Pixel_Sizes(face_ptr, 0, pixel_size)
        line_height = int(pixel_size * 1.35)

        # Prepare text lines (handle explicit newlines and optional word wrap)
        input_lines = text.split("\n")
        lines = []

        if wrap and max_width:
            limit = max_width - (padding * 2)
            for raw_line in input_lines:
                words = raw_line.split(" ")
                curr_line = []
                curr_w = 0
                for word in words:
                    # Measure word width in pixels
                    word_w = 0
                    for ch in (word + " "):
                        err = _ft.FT_Load_Char(face_ptr, ord(ch), FT_LOAD_RENDER)
                        if err == 0:
                            slot = glyph_slot(face_ptr)
                            adv = slot.advance.x >> 6
                            word_w += adv if adv > 0 else (slot.bitmap.width + 1)
                        else:
                            word_w += (pixel_size // 2)

                    if curr_w + word_w > limit and curr_line:
                        lines.append(" ".join(curr_line))
                        curr_line = [word]
                        curr_w = word_w
                    else:
                        curr_line.append(word)
                        curr_w += word_w
                if curr_line:
                    lines.append(" ".join(curr_line))
        else:
            lines = input_lines

        # Load and position glyphs
        all_glyphs = []
        max_pen_x = 0
        baseline_base = pixel_size + padding

        for line_idx, line in enumerate(lines):
            pen_x = padding
            line_baseline = baseline_base + (line_idx * line_height)

            for ch in line:
                # Stop if exceeding max_width in non-wrap mode
                if max_width and not wrap and (pen_x + (pixel_size // 2) > max_width - padding):
                    break

                err = _ft.FT_Load_Char(face_ptr, ord(ch), FT_LOAD_RENDER)
                if err != 0:
                    pen_x += (pixel_size // 2)
                    continue

                slot = glyph_slot(face_ptr)

                bmp = slot.bitmap
                b_data = bitmap_to_grayscale(bmp)

                adv = slot.advance.x >> 6
                if adv == 0:
                    adv = bmp.width + 1

                all_glyphs.append({
                    "x": pen_x + slot.bitmap_left,
                    "y": line_baseline - slot.bitmap_top,
                    "width": bmp.width,
                    "rows": bmp.rows,
                    "data": b_data,
                })
                pen_x += adv

            if pen_x > max_pen_x:
                max_pen_x = pen_x

        canvas_w = max(1, max_pen_x + padding)
        canvas_h = max(1, baseline_base + (len(lines) * line_height))

        # Composite glyphs onto grayscale canvas
        gray_buffer = [[0] * canvas_w for _ in range(canvas_h)]
        for g in all_glyphs:
            gx, gy = g["x"], g["y"]
            for r in range(g["rows"]):
                for c in range(g["width"]):
                    py, px = gy + r, gx + c
                    if 0 <= py < canvas_h and 0 <= px < canvas_w:
                        gray_buffer[py][px] = min(255, gray_buffer[py][px] + g["data"][r][c])

        # Convert to RGB bytearray with alpha blending
        rgb = bytearray(canvas_w * canvas_h * 3)
        idx = 0
        for y in range(canvas_h):
            for x in range(canvas_w):
                alpha = gray_buffer[y][x] / 255.0
                r = int(bg_color[0] * (1.0 - alpha) + fg_color[0] * alpha)
                g = int(bg_color[1] * (1.0 - alpha) + fg_color[1] * alpha)
                b = int(bg_color[2] * (1.0 - alpha) + fg_color[2] * alpha)
                rgb[idx] = max(0, min(255, r))
                rgb[idx + 1] = max(0, min(255, g))
                rgb[idx + 2] = max(0, min(255, b))
                idx += 3

        return canvas_w, canvas_h, rgb
    finally:
        _ft.FT_Done_Face(face_ptr)


def render_glyph_bitmap(
    filepath: str,
    codepoint: int,
    pixel_size: int = 48,
    face_index: int = 0,
    fg_color: Tuple[int, int, int] = (255, 255, 255),
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    padding: int = 4,
) -> Tuple[int, int, bytearray, Dict]:
    """
    Render a single glyph at high resolution with metrics metadata.
    Returns: (width, height, rgb_bytes, metrics_dict)
    """
    if _ft is None:
        raise FreeTypeUnavailableError('FreeType is unavailable; install the system libfreetype.so.6 library.')
    ctx = get_ft_context()
    face_ptr = ctypes.c_void_p()
    err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), face_index, ctypes.byref(face_ptr))
    if err != 0 or not face_ptr.value:
        raise RuntimeError(f"FreeType failed to load font: {filepath}")

    try:
        _ft.FT_Set_Pixel_Sizes(face_ptr, 0, pixel_size)
        err = _ft.FT_Load_Char(face_ptr, codepoint, FT_LOAD_RENDER)
        if err != 0:
            raise RuntimeError(f"Glyph U+{codepoint:04X} not found in font.")

        slot = glyph_slot(face_ptr)
        bmp = slot.bitmap

        metrics = {
            "codepoint": f"U+{codepoint:04X}",
            "char": chr(codepoint) if codepoint >= 32 else "ctrl",
            "width": bmp.width,
            "rows": bmp.rows,
            "bitmap_left": slot.bitmap_left,
            "bitmap_top": slot.bitmap_top,
            "advance_x": slot.advance.x >> 6,
        }

        b_data = bitmap_to_grayscale(bmp)
        if bmp.rows == 0 or bmp.width == 0:
            # Printable space or non-marking glyph
            canvas_w = max(16, slot.advance.x >> 6)
            canvas_h = max(16, pixel_size)
            rgb = bytearray(bg_color) * (canvas_w * canvas_h)
            return canvas_w, canvas_h, rgb, metrics

        canvas_w = bmp.width + (padding * 2)
        canvas_h = bmp.rows + (padding * 2)

        rgb = bytearray(canvas_w * canvas_h * 3)
        idx = 0
        for y in range(canvas_h):
            for x in range(canvas_w):
                gy = y - padding
                gx = x - padding
                if 0 <= gy < bmp.rows and 0 <= gx < bmp.width:
                    alpha = b_data[gy][gx] / 255.0
                else:
                    alpha = 0.0

                r = int(bg_color[0] * (1.0 - alpha) + fg_color[0] * alpha)
                g = int(bg_color[1] * (1.0 - alpha) + fg_color[1] * alpha)
                b = int(bg_color[2] * (1.0 - alpha) + fg_color[2] * alpha)
                rgb[idx] = max(0, min(255, r))
                rgb[idx + 1] = max(0, min(255, g))
                rgb[idx + 2] = max(0, min(255, b))
                idx += 3

        return canvas_w, canvas_h, rgb, metrics
    finally:
        _ft.FT_Done_Face(face_ptr)


# =========================================================================
# 2. Pure Python Minimal PNG Encoder
# =========================================================================

def encode_png(width: int, height: int, rgb_bytes: bytearray) -> bytes:
    """Encode RGB bytearray into standard PNG image bytes without external libraries."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    # IHDR: width, height, 8-bit depth, color type 2 (RGB), compression 0, filter 0, interlace 0
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))

    raw_lines = bytearray()
    row_bytes_len = width * 3
    for y in range(height):
        raw_lines.append(0)  # Filter type 0 (None)
        start = y * row_bytes_len
        raw_lines.extend(rgb_bytes[start : start + row_bytes_len])

    idat = chunk(b"IDAT", zlib.compress(raw_lines, level=6))
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend


# =========================================================================
# 3. Terminal Protocol Encoders
# =========================================================================

def detect_terminal_protocol() -> str:
    """
    Detect the optimal graphic protocol supported by the current terminal.
    Returns: 'kitty' | 'iterm2' | 'sixel' | 'blocks'
    """
    term = os.environ.get("TERM", "").lower()
    term_prog = os.environ.get("TERM_PROGRAM", "").lower()

    # Kitty Graphics Protocol
    if "kitty" in term or os.environ.get("KITTY_WINDOW_ID") or term_prog in ["kitty", "ghostty"]:
        return "kitty"

    # WezTerm supports both Kitty and iTerm2
    if term_prog == "wezterm":
        return "kitty"

    # iTerm2 Inline Images Protocol
    if term_prog in ["iterm.app", "tabby", "mintty"] or os.environ.get("LC_TERMINAL") == "iTerm2":
        return "iterm2"

    # Sixel Protocol (Foot, Konsole, BlackBox)
    if "foot" in term or term_prog in ["konsole", "blackbox"]:
        return "sixel"

    # Universal 24-bit TrueColor Half-Block Fallback
    return "blocks"


def encode_kitty(width: int, height: int, png_bytes: bytes) -> str:
    """Encode PNG into Kitty Graphics Protocol sequence."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    chunk_size = 4096
    chunks = [b64[i : i + chunk_size] for i in range(0, len(b64), chunk_size)]
    res = []
    for idx, c in enumerate(chunks):
        m = 1 if idx < len(chunks) - 1 else 0
        if idx == 0:
            res.append(f"\033_Gf=100,a=T,m={m};{c}\033\\")
        else:
            res.append(f"\033_Gm={m};{c}\033\\")
    return "".join(res)


def encode_iterm2(width: int, height: int, png_bytes: bytes) -> str:
    """Encode PNG into iTerm2 Inline Image Protocol sequence."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"\033]1337;File=inline=1;width={width}px;height={height}px:{b64}\a"


def encode_sixel(
    width: int,
    height: int,
    rgb_bytes: bytearray,
    fg_color: Tuple[int, int, int] = (255, 255, 255),
) -> str:
    """
    Encode RGB image into DEC Sixel sequence using a 16-shade anti-aliased palette
    with Run-Length Encoding (RLE) and exact pixel dimension headers.
    """
    lines = [f"\033Pq\"1;1;{width};{height}"]

    # Define 16 shades of foreground color palette (#0 to #15)
    for i in range(16):
        ratio = (i / 15.0) / 255.0
        r_pct = int(fg_color[0] * ratio * 100)
        g_pct = int(fg_color[1] * ratio * 100)
        b_pct = int(fg_color[2] * ratio * 100)
        lines.append(f"#{i};2;{r_pct};{g_pct};{b_pct}")

    def rle_compress(s: str) -> str:
        res = []
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            cnt = 1
            while i + 1 < n and s[i + 1] == ch:
                cnt += 1
                i += 1
            if cnt > 3:
                res.append(f"!{cnt}{ch}")
            else:
                res.append(ch * cnt)
            i += 1
        return "".join(res)

    # Sixel processes bands of 6 vertical pixels
    for y_band in range(0, height, 6):
        for color_idx in range(1, 16):  # skip color 0 (transparent background)
            sixel_chars = []
            has_color = False
            for x in range(width):
                six_val = 0
                for bit in range(6):
                    y = y_band + bit
                    if y < height:
                        idx = (y * width + x) * 3
                        lum = (rgb_bytes[idx] + rgb_bytes[idx + 1] + rgb_bytes[idx + 2]) // 3
                        quant = lum * 15 // 255
                        if quant == color_idx:
                            six_val |= (1 << bit)
                            has_color = True
                sixel_chars.append(chr(63 + six_val))

            if has_color:
                # Strip trailing empty sixels and apply RLE compression
                content = "".join(sixel_chars).rstrip("?")
                if content:
                    lines.append(f"#{color_idx}" + rle_compress(content) + "$")
        lines.append("-")

    lines.append("\033\\")
    return "".join(lines)


def encode_half_blocks(width: int, height: int, rgb_bytes: bytearray, threshold: int = 24) -> str:
    """
    Universal Fallback: 24-bit TrueColor Unicode Half-Blocks (▀ / ▄).
    Each terminal cell displays 2 vertical pixels at full 24-bit RGB precision.
    Features transparent background blending and automatically trims empty margins.
    """
    raw_lines = []
    for y in range(0, height, 2):
        line = []
        for x in range(width):
            t_idx = (y * width + x) * 3
            b_idx = ((y + 1) * width + x) * 3 if y + 1 < height else None

            tr, tg, tb = rgb_bytes[t_idx], rgb_bytes[t_idx + 1], rgb_bytes[t_idx + 2]
            br, bg, bb = (rgb_bytes[b_idx], rgb_bytes[b_idx + 1], rgb_bytes[b_idx + 2]) if b_idx else (0, 0, 0)

            has_top = (tr > threshold or tg > threshold or tb > threshold)
            has_bot = (br > threshold or bg > threshold or bb > threshold)

            if not has_top and not has_bot:
                line.append(" ")
            elif has_top and not has_bot:
                line.append(f"\033[38;2;{tr};{tg};{tb}m▀\033[0m")
            elif not has_top and has_bot:
                line.append(f"\033[38;2;{br};{bg};{bb}m▄\033[0m")
            else:
                line.append(f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg};{bb}m▀\033[0m")

        # Strip trailing blank spaces
        raw_lines.append("".join(line).rstrip())

    # Trim leading and trailing empty lines
    while raw_lines and not raw_lines[0]:
        raw_lines.pop(0)
    while raw_lines and not raw_lines[-1]:
        raw_lines.pop(-1)

    return "\n".join(raw_lines)


# =========================================================================
# 4. Master Graphic Display Functions
# =========================================================================

def render_font_preview_image(
    filepath: str,
    text: str,
    pixel_size: int = 24,
    face_index: int = 0,
    protocol: Optional[str] = None,
    fg_color: Tuple[int, int, int] = (255, 255, 255),
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    wrap: bool = False,
    max_width: Optional[int] = None,
) -> str:
    """
    Render text using FreeType and display it inline in the terminal using
    the requested or auto-detected terminal graphics protocol.
    Supported protocols: 'kitty', 'iterm2', 'sixel', 'blocks', 'auto'.
    """
    if _ft is None:
        raise FreeTypeUnavailableError('FreeType is unavailable; install the system libfreetype.so.6 library.')
    proto = protocol.lower() if (protocol and protocol.lower() != "auto") else detect_terminal_protocol()
    term_cols = shutil.get_terminal_size((80, 24)).columns

    if max_width is None:
        if proto == "blocks":
            max_width = max(20, term_cols - 4)
        else:
            # Pixel-based graphics protocols (Kitty/Sixel/iTerm2)
            max_width = max(300, (term_cols - 4) * 9)

    if wrap and proto == "blocks":
        # Wrap words cleanly into lines for half-block rendering
        ctx = get_ft_context()
        face_ptr = ctypes.c_void_p()
        err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), face_index, ctypes.byref(face_ptr))
        wrapped_lines = []
        if err == 0 and face_ptr.value:
            try:
                _ft.FT_Set_Pixel_Sizes(face_ptr, 0, pixel_size)
                input_lines = text.split("\n")
                limit = max_width - 4
                for raw_line in input_lines:
                    words = raw_line.split(" ")
                    curr_line = []
                    curr_w = 0
                    for word in words:
                        word_w = 0
                        for ch in (word + " "):
                            err_c = _ft.FT_Load_Char(face_ptr, ord(ch), FT_LOAD_RENDER)
                            if err_c == 0:
                                slot = glyph_slot(face_ptr)
                                adv = slot.advance.x >> 6
                                word_w += adv if adv > 0 else (slot.bitmap.width + 1)
                            else:
                                word_w += (pixel_size // 2)

                        if curr_w + word_w > limit and curr_line:
                            wrapped_lines.append(" ".join(curr_line))
                            curr_line = [word]
                            curr_w = word_w
                        else:
                            curr_line.append(word)
                            curr_w += word_w
                    if curr_line:
                        wrapped_lines.append(" ".join(curr_line))
            finally:
                _ft.FT_Done_Face(face_ptr)
        else:
            wrapped_lines = text.split("\n")

        outputs = []
        for line in wrapped_lines:
            w, h, rgb = render_text_bitmap(
                filepath=filepath,
                text=line,
                pixel_size=pixel_size,
                face_index=face_index,
                fg_color=fg_color,
                bg_color=bg_color,
                max_width=max_width,
                wrap=False,
            )
            blk = encode_half_blocks(w, h, rgb)
            if blk:
                outputs.append(blk)
        return "\n".join(outputs)

    w, h, rgb = render_text_bitmap(
        filepath=filepath,
        text=text,
        pixel_size=pixel_size,
        face_index=face_index,
        fg_color=fg_color,
        bg_color=bg_color,
        max_width=max_width,
        wrap=wrap,
    )

    if proto == "kitty":
        png = encode_png(w, h, rgb)
        return encode_kitty(w, h, png)
    elif proto == "iterm2":
        png = encode_png(w, h, rgb)
        return encode_iterm2(w, h, png)
    elif proto == "sixel":
        return encode_sixel(w, h, rgb, fg_color=fg_color)
    else:
        # Default TrueColor half-blocks (universal across 100% modern terminals)
        return encode_half_blocks(w, h, rgb)


def render_glyph_preview_image(
    filepath: str,
    codepoint: int,
    pixel_size: int = 48,
    face_index: int = 0,
    protocol: Optional[str] = None,
) -> Tuple[str, Dict]:
    """
    Render a single glyph using the requested protocol and return (graphic_output, metrics).
    """
    w, h, rgb, metrics = render_glyph_bitmap(
        filepath=filepath,
        codepoint=codepoint,
        pixel_size=pixel_size,
        face_index=face_index,
    )

    proto = protocol.lower() if (protocol and protocol.lower() != "auto") else detect_terminal_protocol()

    if proto == "kitty":
        png = encode_png(w, h, rgb)
        output = encode_kitty(w, h, png)
    elif proto == "iterm2":
        png = encode_png(w, h, rgb)
        output = encode_iterm2(w, h, png)
    elif proto == "sixel":
        output = encode_sixel(w, h, rgb)
    else:
        output = encode_half_blocks(w, h, rgb)

    return output, metrics


def render_symbol_font_showcase(
    filepath: str,
    family: str,
    codepoints: Set[int],
    face_index: int = 0,
    protocol: Optional[str] = None,
) -> str:
    """
    Render a visual showcase grid of available non-empty codepoints in a symbol / icon font.
    Samples across active cmap ranges (PUA, symbols, pictographs) and formats them
    with codepoint labels and inline vector outlines.
    """
    from cefont.core.terminal import BLUE, BOLD, CYAN, DIM, GRAY, colorize

    proto = protocol.lower() if (protocol and protocol.lower() != "auto") else detect_terminal_protocol()

    cps = sorted(codepoints)
    pua_cps = [c for c in cps if 0xE000 <= c <= 0xF8FF]
    supp_pua = [c for c in cps if 0xF0000 <= c <= 0x10FFFD]
    symbols = [c for c in cps if (0x2000 <= c <= 0x2BFF) or (0x1F000 <= c <= 0x1FAFF)]
    other_high = [c for c in cps if c >= 0x80 and c not in pua_cps and c not in supp_pua and c not in symbols]

    range_descs = []
    if pua_cps:
        range_descs.append(f"Private Use Area (U+E000–U+F8FF: {len(pua_cps):,} glyphs)")
    if supp_pua:
        range_descs.append(f"Supplementary PUA (U+F0000–U+10FFFF: {len(supp_pua):,} glyphs)")
    if symbols:
        range_descs.append(f"Symbols & Pictographs (U+2000–U+2BFF / U+1F000+: {len(symbols):,} glyphs)")
    if other_high:
        range_descs.append(f"Extended Unicode ({len(other_high):,} glyphs)")
    if not range_descs:
        range_descs.append(f"Standard Character Set ({len(cps):,} glyphs)")

    pool = pua_cps or supp_pua or symbols or other_high or cps

    # Sample non-empty glyphs across the pool
    sample_cps = []
    step = max(1, len(pool) // 64)
    for c in pool[::step]:
        try:
            w, h, rgb, m = render_glyph_bitmap(filepath, c, pixel_size=16, face_index=face_index, padding=0)
            if m["rows"] > 0 and m["width"] > 0:
                sample_cps.append(c)
                if len(sample_cps) >= 16:
                    break
        except (FreeTypeUnavailableError, BitmapFormatError):
            raise
        except Exception:
            continue

    if not sample_cps:
        sample_cps = pool[:16]

    lbl_fam = colorize("Font Family :", BOLD)
    lbl_tot = colorize("Total Glyphs:", BOLD)
    lbl_rng = colorize("Active Range:", BOLD)

    lines = []
    lines.append(colorize("┌── [SYMBOL / ICON FONT SHOWCASE] ──────────────────────────────────────┐", BOLD + CYAN))
    lines.append(f"│  {lbl_fam} {family}")
    lines.append(f"│  {lbl_tot} {len(codepoints):,}")
    for r_desc in range_descs:
        lines.append(f"│  {lbl_rng} {r_desc}")
    lines.append(colorize("└────────────────────────────────────────────────────────────────────────┘\n", BOLD + CYAN))

    cols = 4
    cell_cols = 12
    cell_w = 12
    cell_h = 16
    pixel_size = 14

    for i in range(0, len(sample_cps), cols):
        chunk = sample_cps[i : i + cols]
        header = "".join(colorize(f"U+{c:04X}".center(cell_cols), BOLD + BLUE) for c in chunk)
        lines.append(header)

        n = len(chunk)
        total_w = n * cell_w
        canvas = bytearray(total_w * cell_h * 3)
        for idx, cp in enumerate(chunk):
            w, h, rgb, m = render_glyph_bitmap(filepath, cp, pixel_size=pixel_size, face_index=face_index, padding=0)
            ox = idx * cell_w + max(0, (cell_w - w) // 2)
            oy = max(0, (cell_h - h) // 2)
            for y in range(min(h, cell_h)):
                for x in range(min(w, cell_w)):
                    s_idx = (y * w + x) * 3
                    d_idx = ((oy + y) * total_w + (ox + x)) * 3
                    canvas[d_idx : d_idx + 3] = rgb[s_idx : s_idx + 3]

        if proto == "kitty":
            png = encode_png(total_w, cell_h, canvas)
            lines.append(encode_kitty(total_w, cell_h, png))
        elif proto == "sixel":
            lines.append(encode_sixel(total_w, cell_h, canvas))
        else:
            lines.append(encode_half_blocks(total_w, cell_h, canvas))
        lines.append("")

    lines.append(colorize(f"  Tip: To view individual glyphs in higher detail, run: cefont preview \"{family}\" --char <U+XXXX>", DIM + GRAY))
    return "\n".join(lines)
