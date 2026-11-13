"""FreeType backend for font metadata extraction, glyph enumeration, and terminal rendering."""

import ctypes
import hashlib
import os
import struct
from typing import Dict, List, Optional, Set, Tuple

from cefont.core.data import get_vendor, match_license


class FreeTypeUnavailableError(RuntimeError):
    """The native dependency is unavailable, rather than a font being invalid."""


class BitmapFormatError(RuntimeError):
    """A bitmap layout cannot be decoded safely by the grayscale renderer."""


def require_freetype():
    if _ft is None:
        raise FreeTypeUnavailableError('FreeType is unavailable; install the system libfreetype.so.6 library.')

# FreeType Library loading
try:
    _ft = ctypes.CDLL("libfreetype.so.6")
except Exception as e:
    _ft = None

# FreeType structs and constants
FT_LOAD_DEFAULT = 0
FT_LOAD_NO_SCALE = 1 << 0
FT_LOAD_NO_HINTING = 1 << 1
FT_LOAD_RENDER = 1 << 2
FT_RENDER_MODE_NORMAL = 0
FT_SFNT_OS2 = 2  # FT_Sfnt_Tag: HEAD=0, MAXP=1, OS2=2 (not a table tag).
TTAG_OS2 = int.from_bytes(b"OS/2", "big")


class FT_Generic(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("finalizer", ctypes.c_void_p)]


class FT_BBox(ctypes.Structure):
    _fields_ = [
        ("xMin", ctypes.c_long),
        ("yMin", ctypes.c_long),
        ("xMax", ctypes.c_long),
        ("yMax", ctypes.c_long),
    ]


class FT_FaceRec(ctypes.Structure):
    _fields_ = [
        ("num_faces", ctypes.c_long),
        ("face_index", ctypes.c_long),
        ("face_flags", ctypes.c_long),
        ("style_flags", ctypes.c_long),
        ("num_glyphs", ctypes.c_long),
        ("family_name", ctypes.c_char_p),
        ("style_name", ctypes.c_char_p),
        ("num_fixed_sizes", ctypes.c_int),
        ("available_sizes", ctypes.c_void_p),
        ("num_charmaps", ctypes.c_int),
        ("charmaps", ctypes.c_void_p),
        ("generic", FT_Generic),
        ("bbox", FT_BBox),
        ("units_per_EM", ctypes.c_ushort),
        ("ascender", ctypes.c_short),
        ("descender", ctypes.c_short),
        ("height", ctypes.c_short),
        ("max_advance_width", ctypes.c_short),
        ("max_advance_height", ctypes.c_short),
        ("underline_position", ctypes.c_short),
        ("underline_thickness", ctypes.c_short),
        ("glyph", ctypes.c_void_p),
    ]


# Public FreeType structures use native C field alignment and widths.
class FT_Bitmap(ctypes.Structure):
    _fields_ = [
        ("rows", ctypes.c_uint),
        ("width", ctypes.c_uint),
        ("pitch", ctypes.c_int),
        ("buffer", ctypes.c_void_p),
        ("num_grays", ctypes.c_ushort),
        ("pixel_mode", ctypes.c_ubyte),
        ("palette_mode", ctypes.c_ubyte),
        ("palette", ctypes.c_void_p),
    ]


class FT_Glyph_Metrics(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_long),
        ("height", ctypes.c_long),
        ("horiBearingX", ctypes.c_long),
        ("horiBearingY", ctypes.c_long),
        ("horiAdvance", ctypes.c_long),
        ("vertBearingX", ctypes.c_long),
        ("vertBearingY", ctypes.c_long),
        ("vertAdvance", ctypes.c_long),
    ]


class FT_Vector(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class FT_GlyphSlotRec(ctypes.Structure):
    _fields_ = [
        ("library", ctypes.c_void_p),
        ("face", ctypes.c_void_p),
        ("next", ctypes.c_void_p),
        ("glyph_index", ctypes.c_uint),
        ("generic", FT_Generic),
        ("metrics", FT_Glyph_Metrics),
        ("linearHoriAdvance", ctypes.c_long),
        ("linearVertAdvance", ctypes.c_long),
        ("advance", FT_Vector),
        ("format", ctypes.c_int),
        ("bitmap", FT_Bitmap),
        ("bitmap_left", ctypes.c_int),
        ("bitmap_top", ctypes.c_int),
    ]


class FT_SfntName(ctypes.Structure):
    _fields_ = [
        ("platform_id", ctypes.c_ushort),
        ("encoding_id", ctypes.c_ushort),
        ("language_id", ctypes.c_ushort),
        ("name_id", ctypes.c_ushort),
        ("string", ctypes.POINTER(ctypes.c_ubyte)),
        ("string_len", ctypes.c_uint),
    ]


def glyph_slot(face_ptr):
    address = FT_FaceRec.from_address(face_ptr.value).glyph
    if not address:
        raise RuntimeError('FreeType returned no glyph slot')
    return FT_GlyphSlotRec.from_address(address)


def bitmap_to_grayscale(bitmap):
    """Copy a documented FT_Bitmap into grayscale rows before another load.

    MONO, GRAY, GRAY2 and GRAY4 are supported. LCD and color formats are
    deliberately refused rather than interpreted as grayscale. The native
    buffer is owned by FreeType; validate its advertised layout before reads.
    """
    width, rows, pitch = bitmap.width, bitmap.rows, bitmap.pitch
    if not width or not rows:
        return []
    bits = {1: 1, 2: 8, 3: 2, 4: 4}.get(bitmap.pixel_mode)
    if bits is None:
        raise BitmapFormatError('Unsupported FreeType bitmap pixel mode: %s' % bitmap.pixel_mode)
    row_bytes = (width * bits + 7) // 8
    if not bitmap.buffer or abs(pitch) < row_bytes:
        raise BitmapFormatError('Invalid FreeType bitmap buffer or pitch')
    if width * rows > 16 * 1024 * 1024 or abs(pitch) * rows > 64 * 1024 * 1024:
        raise BitmapFormatError('FreeType bitmap exceeds the raster size limit')
    levels = bitmap.num_grays if bits == 8 else 1 << bits
    if not 2 <= levels <= 256:
        raise BitmapFormatError('Invalid FreeType grayscale level count')
    result = []
    mask = (1 << bits) - 1
    for row in range(rows):
        # FreeType defines pitch as the signed offset to the next logical row.
        raw = ctypes.string_at(bitmap.buffer + row * pitch, row_bytes)
        result.append([min(levels - 1, (raw[column * bits // 8] >>
                       (8 - bits - column * bits % 8)) & mask) * 255 // (levels - 1)
                       for column in range(width)])
    return result


# Function prototypes
if _ft:
    _ft.FT_Init_FreeType.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    _ft.FT_Init_FreeType.restype = ctypes.c_int

    _ft.FT_Done_FreeType.argtypes = [ctypes.c_void_p]
    _ft.FT_Done_FreeType.restype = ctypes.c_int

    _ft.FT_New_Face.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)]
    _ft.FT_New_Face.restype = ctypes.c_int

    _ft.FT_Done_Face.argtypes = [ctypes.c_void_p]
    _ft.FT_Done_Face.restype = ctypes.c_int

    _ft.FT_Load_Sfnt_Table.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ulong),
    ]
    _ft.FT_Load_Sfnt_Table.restype = ctypes.c_int

    _ft.FT_Get_Sfnt_Name_Count.argtypes = [ctypes.c_void_p]
    _ft.FT_Get_Sfnt_Name_Count.restype = ctypes.c_uint

    _ft.FT_Get_Sfnt_Name.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(FT_SfntName)]
    _ft.FT_Get_Sfnt_Name.restype = ctypes.c_int

    _ft.FT_Get_First_Char.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    _ft.FT_Get_First_Char.restype = ctypes.c_ulong

    _ft.FT_Get_Next_Char.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint)]
    _ft.FT_Get_Next_Char.restype = ctypes.c_ulong

    _ft.FT_Set_Pixel_Sizes.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    _ft.FT_Set_Pixel_Sizes.restype = ctypes.c_int

    _ft.FT_Load_Char.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    _ft.FT_Load_Char.restype = ctypes.c_int

    _ft.FT_Render_Glyph.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _ft.FT_Render_Glyph.restype = ctypes.c_int


NAME_IDS = {
    0: "copyright",
    1: "family",
    2: "style",
    3: "unique_id",
    4: "full_name",
    5: "version",
    6: "psname",
    7: "trademark",
    8: "vendor",
    9: "designer",
    10: "description",
    11: "vendor-url",
    12: "designer-url",
    13: "license-data",
    14: "license-url",
}


def sanitize_filename(name: str) -> str:
    """Sanitize string to valid filename according to font-manager conventions."""
    for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        name = name.replace(ch, '_')
    return name.strip()


def get_filetype_from_header(filepath: str) -> str:
    """Determine font filetype from magic bytes or extension."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(4)
        if header == b"\x00\x01\x00\x00" or header == b"true":
            return "TrueType"
        elif header == b"OTTO":
            return "OpenType"
        elif header == b"ttcf":
            return "TrueType Collection"
        elif header == b"wOFF":
            return "WOFF"
        elif header == b"wOF2":
            return "WOFF2"
    except Exception:
        pass

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".ttf":
        return "TrueType"
    elif ext == ".otf":
        return "OpenType"
    elif ext == ".ttc":
        return "TrueType Collection"
    elif ext in (".pfa", ".pfb"):
        return "Type 1"
    elif ext == ".woff":
        return "WOFF"
    elif ext == ".woff2":
        return "WOFF2"
    return "Unknown"


class FreeTypeContext:
    def __init__(self):
        self.lib = ctypes.c_void_p()
        self.library = _ft
        require_freetype()
        error = self.library.FT_Init_FreeType(ctypes.byref(self.lib))
        if error or not self.lib.value:
            self.lib = ctypes.c_void_p()
            raise FreeTypeUnavailableError('FreeType initialization failed (code %s).' % error)

    def __del__(self):
        if self.library and self.lib.value:
            self.library.FT_Done_FreeType(self.lib)


_GLOBAL_FT = None


def get_ft_context():
    global _GLOBAL_FT
    require_freetype()
    if _GLOBAL_FT is None:
        _GLOBAL_FT = FreeTypeContext()
    return _GLOBAL_FT


def _read_os2_metadata(face_ptr):
    """Read serialized SFNT bytes, never cast FreeType's native TT_OS2 struct.

    TT_OS2 contains native FT_ULong fields and ABI-dependent padding. The
    FT_Load_Sfnt_Table API instead returns the original big-endian table bytes.
    Only the fixed 62-byte prefix needed here is allocated/read. Missing, short,
    unreadable or unsupported-version tables have unknown embedding rights.
    """
    unknown = (None, [0] * 10, "")
    size = ctypes.c_ulong(0)
    if _ft.FT_Load_Sfnt_Table(face_ptr, TTAG_OS2, 0, None, ctypes.byref(size)):
        return unknown
    if size.value < 62:
        return unknown
    buffer = (ctypes.c_ubyte * 62)()
    size.value = len(buffer)
    if _ft.FT_Load_Sfnt_Table(face_ptr, TTAG_OS2, 0, buffer, ctypes.byref(size)):
        return unknown
    if size.value != len(buffer):
        return unknown
    raw = bytes(buffer)
    if struct.unpack_from(">H", raw)[0] not in (0, 1, 2, 3, 4, 5):
        return unknown
    return (struct.unpack_from(">H", raw, 8)[0], list(raw[32:42]),
            raw[58:62].decode("latin1").strip())


def extract_font_metadata(filepath: str, index: int = 0) -> Optional[Dict]:
    """Extract complete metadata for font file at index matching font-manager's metadata table."""
    require_freetype()
    if not os.path.isfile(filepath):
        return None

    ctx = get_ft_context()
    face_ptr = ctypes.c_void_p()
    err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), index, ctypes.byref(face_ptr))
    if err != 0 or not face_ptr.value:
        return None

    try:
        face = FT_FaceRec.from_address(face_ptr.value)

        # Basic names
        family = face.family_name.decode("utf-8", "replace") if face.family_name else ""
        style = face.style_name.decode("utf-8", "replace") if face.style_name else "Regular"
        num_glyphs = face.num_glyphs

        names = {}
        count = _ft.FT_Get_Sfnt_Name_Count(face_ptr)
        name_entry = FT_SfntName()
        for i in range(count):
            if _ft.FT_Get_Sfnt_Name(face_ptr, i, ctypes.byref(name_entry)) == 0:
                nid = name_entry.name_id
                if nid in NAME_IDS:
                    raw = ctypes.string_at(name_entry.string, name_entry.string_len)
                    try:
                        if name_entry.platform_id in (0, 3):
                            val = raw.decode("utf-16-be", "replace").strip()
                        else:
                            val = raw.decode("utf-8", "replace").strip()
                        key = NAME_IDS[nid]
                        if key not in names or len(val) > len(names[key]):
                            names[key] = val
                    except Exception:
                        pass

        if "family" in names and names["family"]:
            family = names["family"]
        if "style" in names and names["style"]:
            style = names["style"]

        psname = names.get("psname", "")
        copyright_text = names.get("copyright", "")
        version_str = names.get("version", "")
        description = names.get("description", "")
        license_data = names.get("license-data", "")
        license_url = names.get("license-url", "")
        designer = names.get("designer", "")
        designer_url = names.get("designer-url", "")
        vendor_name = names.get("vendor", "")

        fs_type, panose, ach_vend_id = _read_os2_metadata(face_ptr)

        # Resolve vendor
        resolved_vendor = get_vendor(ach_vend_id, copyright_text)
        if not vendor_name or vendor_name == "Unknown":
            vendor_name = resolved_vendor

        # Resolve license
        license_type, matched_url = match_license(license_data, copyright_text, license_url)
        if not license_url:
            license_url = matched_url

        # File size and hash
        stat = os.stat(filepath)
        filesize = str(stat.st_size)
        owner = 0 if os.access(filepath, os.W_OK) else -1

        # Checksum (SHA-256)
        with open(filepath, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        filetype = get_filetype_from_header(filepath)

        return {
            "filepath": filepath,
            "findex": index,
            "family": family,
            "style": style,
            "owner": owner,
            "psname": psname,
            "filetype": filetype,
            "n-glyphs": num_glyphs,
            "copyright": copyright_text,
            "version": version_str,
            "description": description,
            "license-data": license_data,
            "license-url": license_url,
            "vendor": vendor_name,
            "designer": designer,
            "designer-url": designer_url,
            "license-type": license_type,
            "fsType": fs_type,
            "filesize": filesize,
            "checksum": checksum,
            "panose": panose,
            "revision": version_str,
            "num_faces": face.num_faces,
        }
    finally:
        _ft.FT_Done_Face(face_ptr)


def get_all_codepoints(filepath: str, index: int = 0) -> Set[int]:
    """Return all unicode codepoints supported by this font face."""
    require_freetype()
    if not os.path.isfile(filepath):
        return set()

    ctx = get_ft_context()
    face_ptr = ctypes.c_void_p()
    err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), index, ctypes.byref(face_ptr))
    if err != 0 or not face_ptr.value:
        return set()

    try:
        codepoints = set()
        gindex = ctypes.c_uint()
        charcode = _ft.FT_Get_First_Char(face_ptr, ctypes.byref(gindex))
        while gindex.value != 0:
            codepoints.add(charcode)
            charcode = _ft.FT_Get_Next_Char(face_ptr, ctypes.c_ulong(charcode), ctypes.byref(gindex))
        return codepoints
    finally:
        _ft.FT_Done_Face(face_ptr)


def render_glyph_ascii(filepath: str, index: int, char_or_codepoint, pixel_size: int = 18) -> str:
    """Render a single character glyph into ASCII art representation for terminal viewing."""
    require_freetype()
    if not os.path.isfile(filepath):
        return "Font file missing."

    codepoint = ord(char_or_codepoint) if isinstance(char_or_codepoint, str) else int(char_or_codepoint)

    ctx = get_ft_context()
    face_ptr = ctypes.c_void_p()
    err = _ft.FT_New_Face(ctx.lib, filepath.encode("utf-8"), index, ctypes.byref(face_ptr))
    if err != 0 or not face_ptr.value:
        return "Failed to open font face."

    try:
        _ft.FT_Set_Pixel_Sizes(face_ptr, 0, pixel_size)
        err = _ft.FT_Load_Char(face_ptr, codepoint, FT_LOAD_RENDER)
        if err != 0:
            return f"Glyph U+{codepoint:04X} not found in font."

        bitmap = glyph_slot(face_ptr).bitmap
        raster = bitmap_to_grayscale(bitmap)
        rows, width = bitmap.rows, bitmap.width
        if rows == 0 or width == 0:
            return f"[Glyph U+{codepoint:04X} has no printable raster representation (space or zero-width)]"

        shades = " .:-=+*#%@"
        lines = []
        for r in range(rows):
            line = ""
            for c in range(width):
                b = raster[r][c]
                line += shades[b * (len(shades) - 1) // 255]
            lines.append(line)

        header = f"Glyph U+{codepoint:04X} ({chr(codepoint) if codepoint >= 32 else 'ctrl'}) [{width}x{rows}px]:"
        return header + "\n" + "\n".join(lines)
    finally:
        _ft.FT_Done_Face(face_ptr)


def decode_fs_type(fs_type: int) -> List[str]:
    """Decode OpenType fsType embedding licensing rights flags."""
    perms = []
    base = fs_type & 0x000F
    if base == 0:
        perms.append("Installable Embedding (no restrictions)")
    if base & 0x0002:
        perms.append("Restricted License Embedding")
    if base & 0x0004:
        perms.append("Preview & Print Embedding")
    if base & 0x0008:
        perms.append("Editable Embedding")
    if fs_type & 0x0100:
        perms.append("No Subsetting")
    if fs_type & 0x0200:
        perms.append("Bitmap Only Embedding")
    return perms or ["Unspecified"]


def decode_panose(p: List[int]) -> Dict[str, str]:
    """Decode 10-digit Panose classification array."""
    if len(p) < 10:
        p = (p + [0] * 10)[:10]

    family_kinds = {
        0: "Any", 1: "No Fit", 2: "Text and Display",
        3: "Script", 4: "Decorative", 5: "Pictorial"
    }
    return {
        "Family Kind": family_kinds.get(p[0], f"Unknown ({p[0]})"),
        "Serif Style": str(p[1]),
        "Weight": str(p[2]),
        "Proportion": str(p[3]),
        "Contrast": str(p[4]),
        "Stroke Variation": str(p[5]),
        "Arm Style": str(p[6]),
        "Letterform": str(p[7]),
        "Midline": str(p[8]),
        "X-Height": str(p[9]),
        "Raw": " ".join(str(x) for x in p)
    }
