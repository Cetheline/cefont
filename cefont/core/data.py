"""Data parser for font-manager vendors, licenses, notices, and orthographies."""

import ast
import os
import re
from importlib import import_module, resources

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
VENDOR_HEADER = os.path.join(DATA_DIR, "font-manager-vendor.h")
LICENSE_HEADER = os.path.join(DATA_DIR, "font-manager-license.h")
ORTHOGRAPHY_DIR = os.path.join(DATA_DIR, "orthographies")

try:
    import_module("cefont.resources")
    _RESOURCE_PACKAGE = "cefont.resources"
except ModuleNotFoundError as error:
    if error.name != "cefont.resources":
        raise
    # The source checkout keeps its original top-level data/ layout. Installed
    # and editable distributions instead resolve the mapped resource package.
    _RESOURCE_PACKAGE = None


def _read_resource(name, orthography=False):
    if _RESOURCE_PACKAGE:
        package = _RESOURCE_PACKAGE + (".orthographies" if orthography else "")
        if hasattr(resources, "files"):
            with resources.files(package).joinpath(name).open(
                    "r", encoding="utf-8", errors="ignore") as source:
                return source.read()
        # Python 3.8's public API; no importlib_resources dependency required.
        return resources.read_text(package, name, encoding="utf-8", errors="ignore")
    directory = ORTHOGRAPHY_DIR if orthography else DATA_DIR
    with open(os.path.join(directory, name), "r", encoding="utf-8", errors="ignore") as source:
        return source.read()


def _orthography_names():
    if _RESOURCE_PACKAGE:
        package = _RESOURCE_PACKAGE + ".orthographies"
        if hasattr(resources, "files"):
            return [entry.name for entry in resources.files(package).iterdir() if entry.is_file()]
        return [name for name in resources.contents(package) if resources.is_resource(package, name)]
    if os.path.isdir(ORTHOGRAPHY_DIR):
        return os.listdir(ORTHOGRAPHY_DIR)
    return []

_VENDORS = None
_NOTICES = None
_LICENSES = None
_ORTHOGRAPHIES = None


def _initializer(text):
    """Read the small C initializer format used by the bundled data files.

    Tokenizing strings/comments before braces prevents quoted braces and nested
    rows from being mistaken for the end of an array. No C code is executed.
    """
    pattern = re.compile(r'\s+|/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|'
                         r'0[xX][0-9a-fA-F]+|[0-9]+|[A-Za-z_]\w*|.', re.S)
    tokens = (match.group() for match in pattern.finditer(text)
              if not match.group().isspace() and not match.group().startswith(('/*', '//')))

    def value(token):
        if token == '{':
            result = []
            token = next(tokens)
            while token != '}':
                result.append(value(token))
                token = next(tokens)
                if token == ',':
                    token = next(tokens)
                elif token != '}':
                    raise ValueError('Missing comma in reference data')
            return result
        if token.startswith('"'):
            return ast.literal_eval(token)
        if re.fullmatch(r'0[xX][0-9a-fA-F]+|[0-9]+', token):
            return int(token, 16 if token.lower().startswith('0x') else 10)
        if token in ('FONT_MANAGER_START_RANGE_PAIR', 'FONT_MANAGER_END_OF_DATA'):
            return token
        raise ValueError('Unexpected reference-data token: ' + token)

    try:
        first = next(tokens)
        if first != '{':
            raise ValueError('Expected reference-data initializer')
        return value(first)
    except StopIteration as error:
        raise ValueError('Truncated reference data') from error


def _orthography(content, identifier):
    name, native, key, sample, pangrams, points = _initializer(content)
    if not isinstance(key, int) or not 0 <= key <= 0x10ffff:
        raise ValueError('Invalid orthography key')
    ranges, codepoints = [], set()
    position = 0
    while position < len(points):
        item = points[position]
        position += 1
        if item == 'FONT_MANAGER_END_OF_DATA':
            break
        if item == 'FONT_MANAGER_START_RANGE_PAIR':
            start, end = points[position:position + 2]
            position += 2
        else:
            start = end = item
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= 0x10ffff:
            raise ValueError('Invalid orthography range')
        if item == 'FONT_MANAGER_START_RANGE_PAIR':
            ranges.append((start, end))
        codepoints.update(range(start, end + 1))
    else:
        raise ValueError('Missing orthography terminator')
    return dict(id=identifier, key=key, name=name, native=native, sample=sample,
                pangrams=[p for p in pangrams if p != 'FONT_MANAGER_END_OF_DATA'],
                ranges=ranges, codepoints=codepoints)


def load_vendors_and_notices():
    global _VENDORS, _NOTICES
    if _VENDORS is not None and _NOTICES is not None:
        return _VENDORS, _NOTICES

    _VENDORS = {}
    _NOTICES = []

    try:
        text = _read_resource("font-manager-vendor.h")

        def rows(name):
            section = re.search(r'\b' + name + r'\s*\[\s*\]\s*=\s*', text)
            if not section:
                raise ValueError('Missing vendor reference array: ' + name)
            return _initializer(text[section.end():])

        _NOTICES = [(vendor.strip(), keyword.strip())
                    for vendor, keyword in rows('FontManagerNoticeData')]
        vendor_pairs = rows('FontManagerVendorData')
        for vid, vendor in vendor_pairs:
            vid_clean = vid.strip()
            if vid_clean not in _VENDORS:
                _VENDORS[vid_clean] = vendor.strip()
    except Exception:
        pass

    return _VENDORS, _NOTICES


def load_licenses():
    global _LICENSES
    if _LICENSES is not None:
        return _LICENSES

    _LICENSES = []
    try:
        text = _read_resource("font-manager-license.h")

        entries = re.findall(r'N_\(\"([^\"]+)\"\)\s*,\s*\"([^\"]*)\"\s*,\s*\{([^}]+)\}', text)
        for lic, url, kws in entries:
            kw_list = re.findall(r'\"([^\"]+)\"', kws)
            _LICENSES.append({
                "license": lic.strip(),
                "url": url.strip(),
                "keywords": [k.strip() for k in kw_list]
            })
    except Exception:
        pass

    if not _LICENSES:
        # Fallback standard licenses
        _LICENSES = [
            {"license": "SIL Open Font License", "url": "https://scripts.sil.org/OFL", "keywords": ["OFL", "SIL Open Font License"]},
            {"license": "Apache 2.0", "url": "http://www.apache.org/licenses/LICENSE-2.0", "keywords": ["Apache"]},
            {"license": "GNU General Public License", "url": "http://www.gnu.org/licenses/gpl.html", "keywords": ["GPL", "GNU General Public License"]},
            {"license": "MIT", "url": "http://www.opensource.org/licenses/mit-license.php", "keywords": ["MIT"]},
            {"license": "Proprietary / Unknown", "url": "", "keywords": []}
        ]

    return _LICENSES


def get_vendor(ach_vend_id: str, notice: str = None) -> str:
    """Resolve font foundry/vendor using 4-character achVendID and PostScript notice."""
    vendors, notices = load_vendors_and_notices()
    vend_clean = ach_vend_id.strip() if ach_vend_id else ""
    if vend_clean in vendors:
        return vendors[vend_clean]

    if notice:
        for vendor, keyword in notices:
            if keyword.lower() in notice.lower():
                return vendor

    return vend_clean if vend_clean else "Unknown"


def match_license(license_data: str, copyright_text: str, license_url: str):
    """Find matching license and url from text and copyright fields."""
    licenses = load_licenses()
    for item in licenses:
        for kw in item["keywords"]:
            if (license_data and kw.lower() in license_data.lower()) or \
               (copyright_text and kw.lower() in copyright_text.lower()) or \
               (license_url and kw.lower() in license_url.lower()):
                return item["license"], item["url"] or license_url
    return "Unknown", license_url or ""


def load_orthographies():
    """Load the bundled orthography definitions."""
    global _ORTHOGRAPHIES
    if _ORTHOGRAPHIES is not None:
        return _ORTHOGRAPHIES

    _ORTHOGRAPHIES = {}
    for fname in _orthography_names():
        if fname.endswith((".md", ".py", ".pyc")):
            continue
        try:
            content = _read_resource(fname, orthography=True)

            definition = _orthography(content, fname)
            _ORTHOGRAPHIES[definition['name']] = definition
        except Exception:
            continue

    return _ORTHOGRAPHIES
