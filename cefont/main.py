"""Font Manager CLI - Pure CLI implementation of Font Manager GUI application."""

import argparse
import json
import os
import sys
from typing import List, Optional

from cefont.core.state import ConfigurationError
from cefont.core.freetype_backend import FreeTypeUnavailableError, BitmapFormatError
from cefont.core.actions import ActionManager
from cefont.core.backup import export_backup, import_backup
from cefont.core.collections import CollectionManager
from cefont.core.comparison import ComparisonManager
from cefont.core.config import (
    ACTIONS_FILE,
    COLLECTIONS_FILE,
    DATABASE_FILE,
    DIRECTORIES_CONF,
    FONTCONFIG_CONF_DIR,
    PACKAGE_CONFIG_DIR,
    PINNED_COMPARISONS_FILE,
    REJECT_CONF,
    SOURCES_FILE,
    USER_FONT_DIR,
    VERSION,
    ensure_directories,
)
from cefont.core.db import (
    FontDatabaseError,
    FontDatabase,
    get_all_families,
    get_detailed_font_info,
    get_fonts_by_family,
    sync_database,
)
from cefont.core.fontconfig import (
    disable_families,
    enable_families,
    keep_only_families,
    load_aliases,
    load_display_properties,
    load_rejected_families,
    load_rendering_properties,
    remove_alias,
    save_aliases,
    save_display_properties,
    save_rendering_properties,
)
from cefont.core.freetype_backend import (
    decode_fs_type,
    decode_panose,
    extract_font_metadata,
    get_all_codepoints,
    render_glyph_ascii,
)
from cefont.core.google_fonts import GoogleFontsManager
from cefont.core.installer import FontInstaller
from cefont.core.orthography import (
    analyze_font_orthographies,
    list_available_orthographies,
)
from cefont.core.sources import SourceManager
from cefont.core.renderer import render_symbol_font_showcase
from cefont.core.terminal_recovery import RecoveryError
from cefont.core.terminal_tester import (
    TerminalFontContext,
    check_font_terminal_suitability,
    detect_terminal_emulator,
    recover_orphaned_state,
    set_terminal_font_permanently,
)
from cefont.core.test_payloads import (
    run_interactive_test_session,
    run_minimal_playground_session,
)
from cefont.core.terminal import (
    BLUE,
    BOLD,
    CYAN,
    DIM,
    GRAY,
    GREEN,
    MAGENTA,
    RED,
    RESET,
    UNDERLINE,
    YELLOW,
    badge,
    colorize,
    format_table,
    render_charmap_grid,
    render_comparison_view,
    render_waterfall,
    status_badge,
)


class CommandError(RuntimeError):
    """A requested operation failed, including a partially successful batch."""


def _report_result(ok, message):
    if not ok:
        raise CommandError(message)
    print(colorize(message, GREEN))


def print_about():
    print(colorize(f"CeFont {VERSION}", BOLD + CYAN))
    print("Professional Terminal Font Manager & Rendering Engine for Linux.")
    print("Zero GUI dependencies, 100% headless compatible.")
    print()
    print("Paths:")
    print(f"  Configuration : {PACKAGE_CONFIG_DIR}")
    print(f"  Database      : {DATABASE_FILE}")
    print(f"  User Fonts    : {USER_FONT_DIR}")
    print(f"  Fontconfig    : {FONTCONFIG_CONF_DIR}")


# ==========================================================
# Subcommand Handlers
# ==========================================================

def cmd_list(args):
    db = FontDatabase()
    all_fams = db.get_families()
    disabled_set = load_rejected_families()

    # Filter
    query = args.query.lower() if args.query else ""
    matching = [f for f in all_fams if query in f.lower()] if query else all_fams

    if args.disabled_only:
        matching = [f for f in matching if f in disabled_set]
    elif args.enabled_only:
        matching = [f for f in matching if f not in disabled_set]

    if args.json:
        result = []
        for fam in matching:
            faces = db.get_fonts(fam)
            result.append({
                "family": fam,
                "enabled": fam not in disabled_set,
                "faces": faces
            })
        print(json.dumps(result, indent=2))
        return

    headers = ["Family", "Faces", "Status", "Sample Style"]
    rows = []
    for fam in matching:
        faces = db.get_fonts(fam)
        num_faces = len(faces)
        st = "Enabled" if fam not in disabled_set else colorize("Disabled", RED)
        sample = faces[0]["style"] if faces else "Regular"
        rows.append([colorize(fam, BOLD), num_faces, st, sample])

    print(format_table(headers, rows))
    print(f"Total: {len(matching)} families ({len(matching) - len(disabled_set.intersection(set(matching)))} enabled, {len(disabled_set.intersection(set(matching)))} disabled)")


def cmd_info(args):
    identifier = args.target
    info = get_detailed_font_info(identifier)
    if not info:
        print(colorize(f"Font '{identifier}' not found in database or filesystem.", RED), file=sys.stderr)
        sys.exit(1)

    disabled_set = load_rejected_families()
    family = info.get("family", "")
    is_enabled = family not in disabled_set

    if args.json:
        info["enabled"] = is_enabled
        print(json.dumps(info, indent=2))
        return

    print(colorize(f"Font Information: {family}", BOLD + BLUE))
    print("═" * (18 + len(family)))

    status_str = colorize("Enabled", GREEN) if is_enabled else colorize("Disabled", RED)
    print(f"Status           : {status_str}")
    print(f"Filepath         : {info.get('filepath')}")
    print(f"Index            : {info.get('findex', 0)}")
    print(f"Style            : {info.get('style', 'Regular')}")
    print(f"PostScript Name  : {info.get('psname', '—')}")
    print(f"Format/Filetype  : {info.get('filetype', '—')}")
    print(f"Glyph Count      : {info.get('n_glyphs') or info.get('n-glyphs') or '—'}")
    print(f"Version          : {info.get('version', '—')}")
    print(f"Vendor           : {info.get('vendor', '—')}")
    print(f"Designer         : {info.get('designer', '—')}")
    print(f"Designer URL     : {info.get('designer_url') or info.get('designer-url') or '—'}")
    print(f"License Type     : {info.get('license_type') or info.get('license-type') or '—'}")
    print(f"License URL      : {info.get('license_url') or info.get('license-url') or '—'}")
    print(f"Copyright        : {info.get('copyright', '—')}")
    print(f"Filesize         : {info.get('filesize', '—')}")

    # fsType
    fs_val = info.get("fsType")
    if fs_val is not None:
        perms = decode_fs_type(int(fs_val))
        print(f"fsType Licensing : {', '.join(perms)}")

    # Panose
    p_keys = [f"P{i}" for i in range(10)]
    panose_vals = [info.get(k, 0) for k in p_keys]
    p_decoded = decode_panose(panose_vals)
    print(colorize("\nPanose Classification:", BOLD))
    for k, v in p_decoded.items():
        if k != "Raw":
            print(f"  {k:16s}: {v}")


def cmd_enable(args):
    families = args.families
    if not families or args.all:
        count, _ = enable_families()
        print(colorize(f"Enabled all fonts ({count} previously disabled fonts enabled).", GREEN))
    else:
        count, _ = enable_families(families)
        print(colorize(f"Enabled {len(families)} font families.", GREEN))


def cmd_disable(args):
    families = args.families
    if not families:
        print("Please specify font family or families to disable.", file=sys.stderr)
        sys.exit(1)
    added, current = disable_families(families)
    print(colorize(f"Disabled {len(families)} font families (total disabled: {len(current)}).", YELLOW))


def cmd_keep(args):
    families = args.families
    all_fams = get_all_families()
    rejected = keep_only_families(families, all_fams)
    print(colorize(f"Kept enabled: {len(families)} families. Disabled {len(rejected)} others.", GREEN))


def cmd_status(args):
    all_fams = get_all_families()
    disabled = load_rejected_families()
    cm = CollectionManager()
    colls = cm.load_collections()
    sm = SourceManager()
    sources = sm.load_sources()

    print(colorize("CeFont Status Summary", BOLD + BLUE))
    print("═" * 25)
    print(f"Total Font Families: {len(all_fams)}")
    print(f"Enabled Families   : {colorize(str(len(all_fams) - len(disabled)), GREEN)}")
    print(f"Disabled Families  : {colorize(str(len(disabled)), RED if disabled else GREEN)}")
    print(f"Collections        : {len(colls)}")
    print(f"Custom Sources     : {len(sources)} ({sum(1 for s in sources if s['active'])} active)")
    print(f"Database File      : {DATABASE_FILE} ({os.path.getsize(DATABASE_FILE) // 1024 if os.path.exists(DATABASE_FILE) else 0} KB)")


def cmd_update(args):
    print("Updating CeFont database...")
    try:
        total, new_meta = sync_database(force=args.force)
    except FontDatabaseError as error:
        print("CeFont: %s" % error, file=sys.stderr)
        sys.exit(1)
    print(colorize(f"Synchronization complete: {total} total fonts indexed ({new_meta} new metadata extracted).", GREEN))


def cmd_install(args):
    installer = FontInstaller()
    failures = 0
    for target in args.files:
        if any(target.endswith(ext) for ext in [".zip", ".tar", ".gz", ".tgz", ".bz2"]):
            count, errs = installer.install_archive(target)
            print(colorize(f"Installed {count} fonts from archive '{target}'", GREEN))
            if errs or count == 0:
                failures += 1
            for e in errs:
                print(colorize(f"  Error: {e}", RED))
        else:
            ok, msg, dest = installer.install_font(target)
            if ok:
                print(colorize(f"Successfully installed: {dest}", GREEN))
            else:
                failures += 1
                print(colorize(f"Failed to install {target}: {msg}", RED), file=sys.stderr)
    if failures:
        raise CommandError("%d installation target(s) failed." % failures)


def cmd_remove(args):
    installer = FontInstaller()
    failures = 0
    for target in args.targets:
        ok, msg = installer.remove_font(target)
        if ok:
            print(colorize(msg, GREEN))
        else:
            failures += 1
            print(colorize(f"Failed to remove {target}: {msg}", RED), file=sys.stderr)
    if failures:
        raise CommandError("%d removal target(s) failed." % failures)


# ==========================================================
# Collections
# ==========================================================

def cmd_collections(args):
    cm = CollectionManager()
    sub = args.coll_cmd

    if sub == "list":
        colls = cm.load_collections()
        headers = ["Collection Name", "Families", "Status", "Description"]
        rows = []
        for c in colls:
            st = colorize("Active", GREEN) if c.get("active", True) else colorize("Disabled", RED)
            rows.append([
                colorize(c.get("name", ""), BOLD),
                len(c.get("families", [])),
                st,
                c.get("comment") or c.get("description") or ""
            ])
        print(format_table(headers, rows))

    elif sub == "show":
        c = cm.get_collection(args.name)
        if not c:
            raise CommandError("Collection not found: " + args.name)
        print(colorize(f"Collection: {c.get('name')}", BOLD + BLUE))
        print(f"Description: {c.get('comment') or c.get('description') or '—'}")
        print(f"Status     : {'Active' if c.get('active', True) else 'Disabled'}")
        print(f"Families ({len(c.get('families', []))}):")
        for f in c.get("families", []):
            print(f"  • {f}")

    elif sub == "create":
        if not cm.create_collection(args.name, args.description or ""):
            raise CommandError("Collection already exists or parent was not found: " + args.name)
        print(colorize(f"Created collection '{args.name}'.", GREEN))

    elif sub == "delete":
        if cm.delete_collection(args.name):
            print(colorize(f"Deleted collection '{args.name}'.", GREEN))
        else:
            raise CommandError("Collection not found: " + args.name)

    elif sub == "rename":
        if cm.rename_collection(args.old_name, args.new_name):
            print(colorize(f"Renamed collection to '{args.new_name}'.", GREEN))
        else:
            raise CommandError("Collection not found or new name already exists: " + args.old_name)

    elif sub == "add":
        added = cm.add_families(args.name, args.families)
        print(colorize(f"Added {added} families to collection '{args.name}'.", GREEN))

    elif sub == "remove":
        removed = cm.remove_families(args.name, args.families)
        print(colorize(f"Removed {removed} families from collection '{args.name}'.", GREEN))

    elif sub == "enable":
        if not cm.set_collection_active(args.name, True):
            raise CommandError("Collection not found: " + args.name)
        print(colorize(f"Enabled collection '{args.name}'.", GREEN))

    elif sub == "disable":
        if not cm.set_collection_active(args.name, False):
            raise CommandError("Collection not found: " + args.name)
        print(colorize(f"Disabled collection '{args.name}'.", GREEN))


# ==========================================================
# Sources
# ==========================================================

def cmd_sources(args):
    sm = SourceManager()
    sub = args.source_cmd

    if sub == "list":
        sources = sm.load_sources()
        headers = ["Source Directory", "Active", "Exists", "Fonts"]
        rows = []
        for s in sources:
            act = colorize("Active", GREEN) if s["active"] else colorize("Disabled", GRAY)
            ex = "Yes" if s["exists"] else colorize("Missing", RED)
            rows.append([s["path"], act, ex, s["count"]])
        print(format_table(headers, rows))

    elif sub == "add":
        ok, msg, count = sm.add_source(args.path, activate=not args.disabled)
        if ok:
            print(colorize(f"{msg} ({count} fonts indexed)", GREEN))
        else:
            raise CommandError(msg)

    elif sub == "remove":
        ok, msg = sm.remove_source(args.path, purge_db=not args.keep_db)
        _report_result(ok, msg)

    elif sub == "enable":
        ok, msg = sm.enable_source(args.path)
        _report_result(ok, msg)

    elif sub == "disable":
        ok, msg = sm.disable_source(args.path)
        _report_result(ok, msg)


# ==========================================================
# Substitutions / Aliases
# ==========================================================

def cmd_aliases(args):
    sub = args.alias_cmd

    if sub == "list":
        aliases = load_aliases()
        if not aliases:
            print("No font aliases configured in 39-Aliases.conf.")
            return

        headers = ["Target Family", "Preferred", "Accept", "Default"]
        rows = []
        for fam, rules in sorted(aliases.items()):
            rows.append([
                colorize(fam, BOLD),
                ", ".join(rules.get("prefer", [])) or "—",
                ", ".join(rules.get("accept", [])) or "—",
                ", ".join(rules.get("default", [])) or "—",
            ])
        print(format_table(headers, rows))

    elif sub == "add":
        from cefont.core.fontconfig import add_alias
        prefer = args.prefer.split(",") if args.prefer else None
        accept = args.accept.split(",") if args.accept else None
        default = args.default.split(",") if args.default else None
        add_alias(args.target, prefer, accept, default)
        print(colorize(f"Configured alias substitution for '{args.target}'.", GREEN))

    elif sub == "remove":
        if not remove_alias(args.target):
            raise CommandError("Alias not found: " + args.target)
        print(colorize(f"Removed alias substitution for '{args.target}'.", GREEN))


# ==========================================================
# Rendering & Display
# ==========================================================

def cmd_rendering(args):
    sub = args.render_cmd
    props = load_rendering_properties()

    if sub == "show":
        print(colorize("Fontconfig Rendering Properties (19-DefaultProperties.conf):", BOLD + BLUE))
        for k, v in sorted(props.items()):
            print(f"  {k:16s}: {v}")

    elif sub == "set":
        if args.antialias is not None:
            props["antialias"] = args.antialias.lower() in ["true", "1", "yes"]
        if args.hinting is not None:
            props["hinting"] = args.hinting.lower() in ["true", "1", "yes"]
        if args.autohint is not None:
            props["autohint"] = args.autohint.lower() in ["true", "1", "yes"]
        if args.embeddedbitmap is not None:
            props["embeddedbitmap"] = args.embeddedbitmap.lower() in ["true", "1", "yes"]
        if args.hintstyle:
            props["hintstyle"] = args.hintstyle
        save_rendering_properties(props)
        print(colorize("Saved rendering preferences to 19-DefaultProperties.conf", GREEN))

    elif sub == "reset":
        save_rendering_properties({
            "antialias": True,
            "hinting": True,
            "autohint": False,
            "hintstyle": "hintslight",
            "embeddedbitmap": True,
        })
        print(colorize("Reset rendering properties to defaults.", GREEN))


def cmd_display(args):
    sub = args.display_cmd
    props = load_display_properties()

    if sub == "show":
        print(colorize("Fontconfig Display Properties (19-DisplayProperties.conf):", BOLD + BLUE))
        for k, v in sorted(props.items()):
            print(f"  {k:12s}: {v}")

    elif sub == "set":
        if args.rgba:
            props["rgba"] = args.rgba
        if args.lcdfilter:
            props["lcdfilter"] = args.lcdfilter
        if args.dpi:
            props["dpi"] = float(args.dpi)
        if args.scale:
            props["scale"] = float(args.scale)
        save_display_properties(props)
        print(colorize("Saved display preferences to 19-DisplayProperties.conf", GREEN))

    elif sub == "reset":
        save_display_properties({
            "rgba": "rgb",
            "lcdfilter": "lcddefault",
            "dpi": 96.0,
            "scale": 1.0,
        })
        print(colorize("Reset display properties to defaults.", GREEN))


# ==========================================================
# Character Map & Previews
# ==========================================================

def cmd_charmap(args):
    sub = args.charmap_cmd
    info = get_detailed_font_info(args.target)
    if not info:
        print(colorize(f"Font '{args.target}' not found.", RED), file=sys.stderr)
        sys.exit(1)

    filepath = info["filepath"]
    findex = info.get("findex", 0)

    if sub == "grid":
        cps = get_all_codepoints(filepath, findex)
        start = int(args.start, 16) if args.start else 0x0020
        end = int(args.end, 16) if args.end else 0x00FF
        print(colorize(f"Character Map: {info.get('family')} (U+{start:04X} .. U+{end:04X})", BOLD + BLUE))
        print(render_charmap_grid(list(cps), start, end))

    elif sub == "glyph":
        cp_val = args.codepoint
        if cp_val.startswith("U+") or cp_val.startswith("u+"):
            cp = int(cp_val[2:], 16)
        elif cp_val.startswith("0x") or cp_val.startswith("0X"):
            cp = int(cp_val, 16)
        elif len(cp_val) == 1:
            cp = ord(cp_val)
        else:
            cp = int(cp_val, 16)

        fmt = getattr(args, "format", "auto")
        if fmt == "ascii":
            ascii_art = render_glyph_ascii(filepath, findex, cp, pixel_size=args.size or 32)
            print(ascii_art)
        else:
            from cefont.core.renderer import render_glyph_preview_image
            try:
                graphic, metrics = render_glyph_preview_image(
                    filepath=filepath,
                    codepoint=cp,
                    pixel_size=args.size or 40,
                    face_index=findex,
                    protocol=fmt
                )
                print(colorize(f"Glyph {metrics['codepoint']} ('{metrics['char']}') — {info.get('family')} {info.get('style', 'Regular')}:", BOLD + BLUE))
                print(colorize(f"Dimensions: {metrics['width']}x{metrics['rows']}px | Bearing: ({metrics['bitmap_left']}, {metrics['bitmap_top']}) | Advance: {metrics['advance_x']}px", DIM + GRAY))
                print(graphic)
            except Exception as e:
                ascii_art = render_glyph_ascii(filepath, findex, cp, pixel_size=args.size or 32)
                print(ascii_art)


def cmd_preview(args):
    sub = args.prev_cmd
    info = get_detailed_font_info(args.target)
    if not info:
        print(colorize(f"Font '{args.target}' not found.", RED), file=sys.stderr)
        sys.exit(1)

    filepath = info.get("filepath")
    findex = info.get("findex", 0)
    fmt = getattr(args, "format", "auto")

    if sub == "waterfall":
        text = args.text or "The quick brown fox jumps over the lazy dog"
        sizes = [int(s.strip()) for s in args.sizes.split(",")] if getattr(args, "sizes", None) else None
        print(render_waterfall(
            family_or_file=info.get("family", args.target),
            text=text,
            sizes=sizes,
            filepath=filepath,
            findex=findex,
            protocol=fmt
        ))

    elif sub == "pangram":
        text = args.text or info.get("sample_text") or "The quick brown fox jumps over the lazy dog"
        print(colorize(f"Sample Pangram for {info.get('family')} ({info.get('style', 'Regular')}):", BOLD + GREEN))
        if fmt == "text" or not filepath or not os.path.isfile(filepath):
            print(f"  {text}")
        else:
            from cefont.core.renderer import render_font_preview_image
            try:
                rendered = render_font_preview_image(
                    filepath=filepath,
                    text=text,
                    pixel_size=args.size or 24,
                    face_index=findex,
                    protocol=fmt,
                    wrap=True
                )
                print(rendered)
            except Exception as e:
                print(f"  {text} (render fallback: {e})")


# ==========================================================
# Comparison & Pinned
# ==========================================================

def cmd_compare(args):
    sub = args.compare_cmd
    cm = ComparisonManager()
    fmt = getattr(args, "format", "auto")
    pixel_size = getattr(args, "size", 22) or 22

    if sub == "fonts":
        fonts_info = []
        for fid in args.fonts:
            inf = get_detailed_font_info(fid)
            if inf:
                fonts_info.append(inf)
        if not fonts_info:
            print("No valid fonts specified.", file=sys.stderr)
            sys.exit(1)
        print(render_comparison_view(fonts_info, args.sample, protocol=fmt, pixel_size=pixel_size))

    elif sub == "pinned-list":
        pinned = cm.load_pinned()
        headers = ["ID", "Label", "Created", "Font Count", "Fonts"]
        rows = []
        for idx, p in enumerate(pinned):
            items_str = ", ".join(it.get("family", "") for it in p.get("items", []))
            rows.append([idx, colorize(p.get("label", ""), BOLD), p.get("created", ""), len(p.get("items", [])), items_str])
        print(format_table(headers, rows))

    elif sub == "pinned-save":
        cm.add_comparison(args.label, args.fonts)
        print(colorize(f"Saved pinned comparison '{args.label}'.", GREEN))

    elif sub == "pinned-delete":
        if cm.remove_comparison(args.label_or_id):
            print(colorize(f"Removed pinned comparison '{args.label_or_id}'.", GREEN))
        else:
            raise CommandError("Pinned comparison not found: " + args.label_or_id)

    elif sub == "pinned-show":
        pinned = cm.load_pinned()
        target = None
        if args.label_or_id.isdigit():
            idx = int(args.label_or_id)
            if 0 <= idx < len(pinned):
                target = pinned[idx]
        if not target:
            for p in pinned:
                if p.get("label", "").lower() == args.label_or_id.lower():
                    target = p
                    break
        if not target:
            print(colorize(f"Comparison '{args.label_or_id}' not found.", RED), file=sys.stderr)
            sys.exit(1)

        fonts_info = []
        for it in target.get("items", []):
            inf = get_detailed_font_info(it.get("filepath", it.get("family")))
            if inf:
                fonts_info.append(inf)
            else:
                fonts_info.append(it)
        print(colorize(f"Pinned Comparison: {target.get('label')}", BOLD + BLUE))
        print(render_comparison_view(
            fonts_info,
            sample_text=args.sample if hasattr(args, "sample") else None,
            protocol=fmt,
            pixel_size=pixel_size
        ))


# ==========================================================
# Live Terminal Font Testing & Setting
# ==========================================================

def cmd_set(args):
    """Directly set active terminal font permanently without running test payloads."""
    if not args.target:
        print(colorize("Error: Please specify a font family name or filepath to set.", RED), file=sys.stderr)
        print("Usage: cefont set <font_name> [--size SIZE]")
        sys.exit(1)

    info = get_detailed_font_info(args.target)
    family = info.get("family", args.target) if info else args.target
    filepath = info.get("filepath", "") if info else ""
    findex = info.get("findex", 0) if info else 0
    spacing = info.get("spacing", 100) if info else 100

    if filepath:
        is_suitable, reason, _ = check_font_terminal_suitability(filepath, findex)
        if not is_suitable:
            print(colorize(f"Error: '{family}' is a symbol/icon font without standard ASCII text glyphs.", RED), file=sys.stderr)
            print("Cannot set as primary terminal emulator font because it lacks standard alphanumeric characters.", file=sys.stderr)
            sys.exit(1)

    if spacing == 0:
        print(colorize(f"⚠ Notice: '{family}' is a proportional (variable-pitch) font.", YELLOW))
        print(colorize("  Terminal emulators require monospace fonts (e.g. JetBrains Mono, CommitMono, Adwaita Mono).", DIM + GRAY))
        print(colorize("  Non-monospace fonts may cause character clipping or trigger fallback in VTE/kgx.\n", DIM + GRAY))

    term_type, meta = detect_terminal_emulator()
    ok, msg = set_terminal_font_permanently(family, size=args.size)
    if ok:
        print(colorize(f"✔ {msg}", BOLD + GREEN))
    else:
        print(colorize(f"Error: {msg}", RED), file=sys.stderr)
        sys.exit(1)


def cmd_playground(args):
    """Playground mode: switch terminal font live with minimal prompt, no presets."""
    args.playground = True
    cmd_test(args)


def cmd_test(args):
    if getattr(args, "set", False) or getattr(args, "apply", False):
        cmd_set(args)
        return

    if getattr(args, "recover", False):
        res = recover_orphaned_state()
        if res:
            print(colorize(res, GREEN))
        else:
            print(colorize("No orphaned live test state found to recover.", GREEN))
        return

    if not args.target:
        print(colorize("Error: Please specify a font family name or filepath to test.", RED), file=sys.stderr)
        print("Usage: cefont test <font_name> [--size SIZE] [-p|--playground] [--mock] [--recover] [--set] [--alt]")
        sys.exit(1)

    info = get_detailed_font_info(args.target)
    if not info:
        print(colorize(f"Font '{args.target}' not found in database or filesystem.", RED), file=sys.stderr)
        sys.exit(1)

    family = info.get("family", args.target)
    filepath = info.get("filepath", "")
    findex = info.get("findex", 0)
    size = args.size
    spacing = info.get("spacing", 100)

    # Pre-Flight Font Suitability Check: ensure font has basic printable ASCII coverage
    if filepath:
        is_suitable, reason, codepoints = check_font_terminal_suitability(filepath, findex)
        if not is_suitable:
            print(colorize(f"'{family}' is a symbol/icon font without standard ASCII text glyphs; bypassing live terminal font swap and rendering available glyphs inline.\n", YELLOW))
            showcase = render_symbol_font_showcase(
                filepath=filepath,
                family=family,
                codepoints=codepoints,
                face_index=findex,
            )
            print(showcase)
            return

    if spacing == 0:
        print(colorize(f"⚠ Notice: '{family}' is a proportional font, not a monospace font.", YELLOW))
        print(colorize("  Terminal grids will fall back to monospace. Use --mock for direct outline rendering.\n", DIM + GRAY))

    term_type, meta = detect_terminal_emulator()
    is_playground = getattr(args, "playground", False) or getattr(args, "subcommand", "") == "playground"

    if getattr(args, "mock", False) or term_type in ["ssh", "linux-vt", "unsupported"]:
        if is_playground:
            run_minimal_playground_session(
                font_info=info,
                active_size=size or 12,
                terminal_type=term_type if not getattr(args, "mock", False) else "mock",
            )
        else:
            run_interactive_test_session(
                font_info=info,
                active_size=size or 12,
                terminal_type=term_type if not getattr(args, "mock", False) else "mock",
                is_fallback=True,
                use_alt_buffer=getattr(args, "alt", False)
            )
        return

    # Local supported terminal: run with TerminalFontContext
    mode_label = "playground" if is_playground else "preset test"
    print(colorize(f"Applying live terminal font switch to '{family}' ({mode_label})...", BOLD + BLUE))
    with TerminalFontContext(font_name=family, size=size, force_mock=args.mock) as ctx:
        if getattr(ctx, "fallback_error", None):
            print(colorize(f"  ⚠ {ctx.fallback_error}\n", YELLOW))
        active_size = ctx.orig_state.get("active_size", size or 11)
        if ctx.orig_state.get("note"):
            print(colorize(f"  (Note: {ctx.orig_state['note']})", DIM + GRAY))
        if is_playground:
            action = run_minimal_playground_session(
                font_info=info,
                active_size=active_size,
                terminal_type=ctx.terminal_type,
            )
        else:
            action = run_interactive_test_session(
                font_info=info,
                active_size=active_size,
                terminal_type=ctx.terminal_type,
                is_fallback=(ctx.terminal_type == "mock"),
                use_alt_buffer=getattr(args, "alt", False)
            )
        if action == "keep":
            if ctx.keep():
                print(colorize(f"✔ Kept '{family} {active_size}pt' as your terminal font permanently.", BOLD + GREEN))
            else:
                print("Preview only: no terminal settings were changed.")
            return

    if ctx.restored:
        print(colorize(f"✔ Restored terminal '{term_type}' back to its original font state.", GREEN))
    else:
        print("Preview only: no terminal settings were changed.")


# ==========================================================
# Orthographies
# ==========================================================

def cmd_orthographies(args):
    sub = args.orth_cmd

    if sub == "list":
        orths = list_available_orthographies()
        headers = ["Orthography Name", "Native Name", "Codepoints", "Pangrams"]
        rows = []
        for o in orths:
            rows.append([
                colorize(o["name"], BOLD),
                o["native"],
                o["total_codepoints"],
                o["pangram_count"]
            ])
        print(format_table(headers, rows))

    elif sub == "analyze":
        res = analyze_font_orthographies(
            args.target,
            filter_lang=args.lang,
            min_coverage=args.min_coverage or 0.0
        )
        print(colorize(f"Orthography Coverage Analysis: {res['font']}", BOLD + BLUE))
        print(f"Total Font Glyphs: {res['total_font_glyphs']}")
        print()

        headers = ["Language / Script", "Coverage", "Present", "Required", "Sample Pangram"]
        rows = []
        for r in res["results"]:
            cov_str = colorize(f"{r['coverage']:5.1f}%", GREEN if r['fully_supported'] else YELLOW)
            rows.append([
                colorize(r["name"], BOLD),
                cov_str,
                r["present_count"],
                r["required_count"],
                (r["pangram"][:40] + "...") if len(r["pangram"]) > 40 else r["pangram"]
            ])
        print(format_table(headers, rows))


# ==========================================================
# User Actions
# ==========================================================

def cmd_actions(args):
    am = ActionManager()
    sub = args.action_cmd

    if sub == "list":
        actions = am.load_actions()
        headers = ["ID", "Action Name", "Executable", "Arguments", "Comment"]
        rows = []
        for idx, a in enumerate(actions):
            rows.append([
                idx,
                colorize(a.get("action_name", ""), BOLD),
                a.get("executable", ""),
                a.get("arguments", ""),
                a.get("comment", "")
            ])
        print(format_table(headers, rows))

    elif sub == "add":
        am.add_action(args.name, args.executable, args.arguments or "FILEPATH", args.comment or "")
        print(colorize(f"Added custom action '{args.name}'.", GREEN))

    elif sub == "remove":
        if am.remove_action(args.name_or_id):
            print(colorize(f"Removed custom action '{args.name_or_id}'.", GREEN))
        else:
            raise CommandError("Action not found: " + args.name_or_id)

    elif sub == "run":
        ok, msg = am.execute_action(args.name_or_id, args.target, background=not args.wait)
        _report_result(ok, msg)


# ==========================================================
# Google Fonts
# ==========================================================

def cmd_google(args):
    gfm = GoogleFontsManager()
    sub = args.google_cmd

    if sub == "search":
        results = gfm.search_catalog(query=args.query or "", category=args.category, sort=args.sort or "alpha")
        headers = ["Family", "Category", "Variants", "Subsets", "Installed"]
        rows = []
        for r in results[:args.limit or 50]:
            inst = colorize("Yes", GREEN) if r["installed"] else "No"
            rows.append([
                colorize(r["family"], BOLD),
                r["category"],
                len(r["variants"]),
                ", ".join(r["subsets"][:3]) + ("..." if len(r["subsets"]) > 3 else ""),
                inst
            ])
        print(format_table(headers, rows))
        print(f"Showing {min(len(results), args.limit or 50)} of {len(results)} matching Google Font families.")

    elif sub == "info":
        inf = gfm.get_family_info(args.family)
        if not inf:
            print(colorize(f"Family '{args.family}' not found in Google Fonts catalog.", RED), file=sys.stderr)
            sys.exit(1)
        print(colorize(f"Google Font Family: {inf['family']}", BOLD + BLUE))
        print(f"Category : {inf['category']}")
        print(f"Variants : {', '.join(inf['variants'])}")
        print(f"Subsets  : {', '.join(inf['subsets'])}")
        print(f"Installed: {'Yes' if inf['installed'] else 'No'}")

    elif sub == "install":
        ok, msg, count = gfm.install_family(args.family)
        _report_result(ok, msg)

    elif sub == "uninstall":
        ok, msg = gfm.uninstall_family(args.family)
        _report_result(ok, msg)

    elif sub == "update":
        ok, msg, count = gfm.update_catalog(sort=args.sort or "alpha", force=True)
        _report_result(ok, msg)


# ==========================================================
# Backup (Export / Import)
# ==========================================================

def cmd_export(args):
    ok, msg = export_backup(
        args.destination,
        include_fonts=not args.no_fonts,
        include_settings=not args.no_settings,
        include_collections=not args.no_collections,
        include_sources=not args.no_sources,
        include_actions=not args.no_actions,
    )
    _report_result(ok, msg)


def cmd_import(args):
    ok, msg = import_backup(args.source)
    _report_result(ok, msg)


# ==========================================================
# CLI Argument Parser
# ==========================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cefont",
        description="CeFont - Professional Terminal Font Manager & Rendering Engine for Linux."
    )

    # Legacy / GUI top-level compatibility options
    parser.add_argument("-v", "--version", action="version", version=f"CeFont {VERSION}")
    parser.add_argument("-a", "--about", action="store_true", help="About the application")
    parser.add_argument("-l", "--list", action="store_true", help="List available font families")
    parser.add_argument("--list-full", action="store_true", help="Full listing including face information (JSON)")
    parser.add_argument("-u", "--update", action="store_true", help="Update application database")
    parser.add_argument("-i", "--install", nargs="+", metavar="FILE", help="List of files to install")
    parser.add_argument("-e", "--enable", nargs="*", metavar="FAMILY", help="List of font families to enable (empty = all)")
    parser.add_argument("-d", "--disable", nargs="+", metavar="FAMILY", help="List of font families to disable")
    parser.add_argument("--keep", nargs="+", metavar="FAMILY", help="List of font families to keep enabled, disables all others")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available Commands")

    # list
    p_list = subparsers.add_parser("list", help="List and search fonts")
    p_list.add_argument("query", nargs="?", default="", help="Filter families by substring")
    p_list.add_argument("--enabled-only", action="store_true", help="Show only enabled fonts")
    p_list.add_argument("--disabled-only", action="store_true", help="Show only disabled fonts")
    p_list.add_argument("--json", action="store_true", help="Output JSON format")

    # info
    p_info = subparsers.add_parser("info", help="Detailed font metadata and properties")
    p_info.add_argument("target", help="Font family name or filepath")
    p_info.add_argument("--json", action="store_true", help="Output JSON format")

    # enable / disable / keep / status / update
    p_en = subparsers.add_parser("enable", help="Enable font families")
    p_en.add_argument("families", nargs="*", help="Families to enable")
    p_en.add_argument("--all", action="store_true", help="Enable all fonts")

    p_dis = subparsers.add_parser("disable", help="Disable font families")
    p_dis.add_argument("families", nargs="+", help="Families to disable")

    p_kp = subparsers.add_parser("keep", help="Keep only specified font families enabled")
    p_kp.add_argument("families", nargs="+", help="Families to keep")

    subparsers.add_parser("status", help="Show overview of fonts and configurations")

    p_upd = subparsers.add_parser("update", help="Update and synchronize font database")
    p_upd.add_argument("--force", action="store_true", help="Force re-extraction of all metadata")

    # install / remove
    p_ins = subparsers.add_parser("install", help="Install font files or archives")
    p_ins.add_argument("files", nargs="+", help="Font files (.ttf, .otf) or archives (.zip, .tar.gz)")

    p_rm = subparsers.add_parser("remove", help="Remove user-installed font files or families")
    p_rm.add_argument("targets", nargs="+", help="Font family names or filepaths to remove")

    # collections
    p_coll = subparsers.add_parser("collections", help="Manage font collections")
    s_coll = p_coll.add_subparsers(dest="coll_cmd", required=True)
    s_coll.add_parser("list", help="List all collections")
    p_c_show = s_coll.add_parser("show", help="Show details of a collection")
    p_c_show.add_argument("name", help="Collection name")
    p_c_create = s_coll.add_parser("create", help="Create a new collection")
    p_c_create.add_argument("name", help="Collection name")
    p_c_create.add_argument("--description", help="Description")
    p_c_del = s_coll.add_parser("delete", help="Delete a collection")
    p_c_del.add_argument("name", help="Collection name")
    p_c_ren = s_coll.add_parser("rename", help="Rename a collection")
    p_c_ren.add_argument("old_name", help="Old name")
    p_c_ren.add_argument("new_name", help="New name")
    p_c_add = s_coll.add_parser("add", help="Add families to collection")
    p_c_add.add_argument("name", help="Collection name")
    p_c_add.add_argument("families", nargs="+", help="Font families to add")
    p_c_rm = s_coll.add_parser("remove", help="Remove families from collection")
    p_c_rm.add_argument("name", help="Collection name")
    p_c_rm.add_argument("families", nargs="+", help="Font families to remove")
    p_c_en = s_coll.add_parser("enable", help="Enable collection")
    p_c_en.add_argument("name", help="Collection name")
    p_c_dis = s_coll.add_parser("disable", help="Disable collection")
    p_c_dis.add_argument("name", help="Collection name")

    # sources
    p_src = subparsers.add_parser("sources", help="Manage custom font directory sources")
    s_src = p_src.add_subparsers(dest="source_cmd", required=True)
    s_src.add_parser("list", help="List sources")
    p_s_add = s_src.add_parser("add", help="Add source directory")
    p_s_add.add_argument("path", help="Directory path")
    p_s_add.add_argument("--disabled", action="store_true", help="Add without activating in fontconfig")
    p_s_rm = s_src.add_parser("remove", help="Remove source directory")
    p_s_rm.add_argument("path", help="Directory path")
    p_s_rm.add_argument("--keep-db", action="store_true", help="Do not purge fonts from database")
    p_s_en = s_src.add_parser("enable", help="Enable source directory")
    p_s_en.add_argument("path", help="Directory path")
    p_s_dis = s_src.add_parser("disable", help="Disable source directory")
    p_s_dis.add_argument("path", help="Directory path")

    # aliases / substitutions
    p_als = subparsers.add_parser("aliases", aliases=["substitutions"], help="Manage fontconfig substitutions (39-Aliases.conf)")
    s_als = p_als.add_subparsers(dest="alias_cmd", required=True)
    s_als.add_parser("list", help="List aliases")
    p_a_add = s_als.add_parser("add", help="Add alias substitution")
    p_a_add.add_argument("target", help="Target family name (e.g. sans-serif or Helvetica)")
    p_a_add.add_argument("--prefer", help="Comma-separated preferred families")
    p_a_add.add_argument("--accept", help="Comma-separated accepted families")
    p_a_add.add_argument("--default", help="Comma-separated default families")
    p_a_rm = s_als.add_parser("remove", help="Remove alias substitution")
    p_a_rm.add_argument("target", help="Target family name")

    # rendering
    p_rnd = subparsers.add_parser("rendering", help="Fontconfig rendering properties (19-DefaultProperties.conf)")
    s_rnd = p_rnd.add_subparsers(dest="render_cmd", required=True)
    s_rnd.add_parser("show", help="Show current rendering properties")
    p_r_set = s_rnd.add_parser("set", help="Configure rendering properties")
    p_r_set.add_argument("--antialias", choices=["true", "false"])
    p_r_set.add_argument("--hinting", choices=["true", "false"])
    p_r_set.add_argument("--autohint", choices=["true", "false"])
    p_r_set.add_argument("--embeddedbitmap", choices=["true", "false"])
    p_r_set.add_argument("--hintstyle", choices=["hintnone", "hintslight", "hintmedium", "hintfull"])
    s_rnd.add_parser("reset", help="Reset rendering properties to defaults")

    # display
    p_dsp = subparsers.add_parser("display", help="Fontconfig display properties (19-DisplayProperties.conf)")
    s_dsp = p_dsp.add_subparsers(dest="display_cmd", required=True)
    s_dsp.add_parser("show", help="Show current display properties")
    p_d_set = s_dsp.add_parser("set", help="Configure display properties")
    p_d_set.add_argument("--rgba", choices=["none", "rgb", "bgr", "vrgb", "vbgr"])
    p_d_set.add_argument("--lcdfilter", choices=["lcdnone", "lcddefault", "lcdlight", "lcdlegacy"])
    p_d_set.add_argument("--dpi", type=float)
    p_d_set.add_argument("--scale", type=float)
    s_dsp.add_parser("reset", help="Reset display properties to defaults")

    # charmap
    p_cm = subparsers.add_parser("charmap", help="Character map & ASCII rasterization")
    s_cm = p_cm.add_subparsers(dest="charmap_cmd", required=True)
    p_cm_g = s_cm.add_parser("grid", help="Show Unicode character map grid")
    p_cm_g.add_argument("target", help="Font family name or filepath")
    p_cm_g.add_argument("--start", help="Hex start codepoint (e.g. 0020)")
    p_cm_g.add_argument("--end", help="Hex end codepoint (e.g. 00FF)")
    p_cm_gl = s_cm.add_parser("glyph", help="Render glyph outlines with terminal graphics or ASCII art")
    p_cm_gl.add_argument("target", help="Font family name or filepath")
    p_cm_gl.add_argument("codepoint", help="Hex codepoint (e.g. 0041, U+0041) or character")
    p_cm_gl.add_argument("--size", type=int, default=40, help="Pixel size for rasterization (default: 40)")
    p_cm_gl.add_argument("--format", "--protocol", dest="format", choices=["auto", "kitty", "iterm2", "sixel", "blocks", "ascii"], default="auto", help="Graphics rendering protocol (default: auto)")

    # preview
    p_prv = subparsers.add_parser("preview", help="Font preview and waterfall with terminal graphics")
    s_prv = p_prv.add_subparsers(dest="prev_cmd", required=True)
    p_prv_wf = s_prv.add_parser("waterfall", help="Display waterfall preview at multiple point sizes")
    p_prv_wf.add_argument("target", help="Font family name or filepath")
    p_prv_wf.add_argument("--text", help="Custom sample text")
    p_prv_wf.add_argument("--sizes", help="Comma-separated pixel sizes (default: 12,16,20,26,34,44)")
    p_prv_wf.add_argument("--format", "--protocol", dest="format", choices=["auto", "kitty", "iterm2", "sixel", "blocks", "text"], default="auto", help="Graphics rendering protocol (default: auto)")

    p_prv_pg = s_prv.add_parser("pangram", help="Display sample pangram with rendered font outlines")
    p_prv_pg.add_argument("target", help="Font family name or filepath")
    p_prv_pg.add_argument("--text", help="Custom sample text")
    p_prv_pg.add_argument("--size", type=int, default=24, help="Pixel size (default: 24)")
    p_prv_pg.add_argument("--format", "--protocol", dest="format", choices=["auto", "kitty", "iterm2", "sixel", "blocks", "text"], default="auto", help="Graphics rendering protocol (default: auto)")

    # compare
    p_cmp = subparsers.add_parser("compare", help="Compare fonts side-by-side with real rendered outlines")
    s_cmp = p_cmp.add_subparsers(dest="compare_cmd", required=True)
    p_cmp_f = s_cmp.add_parser("fonts", help="Compare two or more fonts")
    p_cmp_f.add_argument("fonts", nargs="+", help="Font family names or filepaths")
    p_cmp_f.add_argument("--sample", help="Custom sample comparison text")
    p_cmp_f.add_argument("--size", type=int, default=22, help="Sample preview pixel size (default: 22)")
    p_cmp_f.add_argument("--format", "--protocol", dest="format", choices=["auto", "kitty", "iterm2", "sixel", "blocks", "text"], default="auto", help="Graphics rendering protocol (default: auto)")

    s_cmp.add_parser("pinned-list", help="List pinned comparisons")
    p_cmp_ps = s_cmp.add_parser("pinned-save", help="Save a pinned comparison")
    p_cmp_ps.add_argument("label", help="Comparison label")
    p_cmp_ps.add_argument("fonts", nargs="+", help="Font family names or filepaths")
    p_cmp_pd = s_cmp.add_parser("pinned-delete", help="Delete a pinned comparison")
    p_cmp_pd.add_argument("label_or_id", help="Label or zero-based index")
    p_cmp_pw = s_cmp.add_parser("pinned-show", help="Display a pinned comparison")
    p_cmp_pw.add_argument("label_or_id", help="Label or zero-based index")
    p_cmp_pw.add_argument("--sample", help="Custom sample comparison text")
    p_cmp_pw.add_argument("--size", type=int, default=22, help="Sample preview pixel size (default: 22)")
    p_cmp_pw.add_argument("--format", "--protocol", dest="format", choices=["auto", "kitty", "iterm2", "sixel", "blocks", "text"], default="auto", help="Graphics rendering protocol (default: auto)")

    # set (Direct active terminal font switcher)
    p_set = subparsers.add_parser("set", help="Directly set and persist active terminal font")
    p_set.add_argument("target", help="Font family name or filepath to set")
    p_set.add_argument("--size", type=int, help="Optional point size (e.g. 11, 12, 14)")

    # playground (Minimal playground mode: font switches live without presets)
    p_play = subparsers.add_parser("playground", help="Playground test: live font change with minimal prompt, no presets")
    p_play.add_argument("target", help="Font family name or filepath to test")
    p_play.add_argument("--size", type=int, help="Optional point size (e.g. 11, 12, 14)")
    p_play.add_argument("--mock", action="store_true", help="Force Virtual Terminal Fallback preview without modifying terminal")

    # test (Live interactive testing with auto-reversion)
    p_tst = subparsers.add_parser("test", help="Live test font in active terminal emulator with safe auto-reversion")
    p_tst.add_argument("target", nargs="?", default="", help="Font family name or filepath to test")
    p_tst.add_argument("--size", type=int, help="Optional point size (default: active terminal size)")
    p_tst.add_argument("-p", "--playground", action="store_true", help="Minimal playground mode (no preset test payloads)")
    p_tst.add_argument("--preset", action="store_true", help="Preset test mode with full typographic suite (default)")
    p_tst.add_argument("--set", "--apply", action="store_true", help="Directly set font permanently without test session")
    p_tst.add_argument("--alt", action="store_true", help="Use fullscreen alternate screen buffer")
    p_tst.add_argument("--mock", action="store_true", help="Force Virtual Terminal Fallback preview without modifying terminal")
    p_tst.add_argument("--recover", action="store_true", help="Restore terminal if a previous session was abruptly killed")

    # orthographies
    p_ort = subparsers.add_parser("orthographies", help="Language and script orthography coverage")
    s_ort = p_ort.add_subparsers(dest="orth_cmd", required=True)
    s_ort.add_parser("list", help="List all 128 orthographies")
    p_ort_an = s_ort.add_parser("analyze", help="Analyze font coverage for languages")
    p_ort_an.add_argument("target", help="Font family name or filepath")
    p_ort_an.add_argument("--lang", help="Filter by language name")
    p_ort_an.add_argument("--min-coverage", type=float, default=0.0, help="Minimum coverage percentage (0-100)")

    # actions
    p_act = subparsers.add_parser("actions", help="Manage custom user actions (Actions.json)")
    s_act = p_act.add_subparsers(dest="action_cmd", required=True)
    s_act.add_parser("list", help="List actions")
    p_act_add = s_act.add_parser("add", help="Add custom action")
    p_act_add.add_argument("name", help="Action name")
    p_act_add.add_argument("executable", help="Executable name or path")
    p_act_add.add_argument("--arguments", default="FILEPATH", help="Command arguments with placeholders (FILEPATH, FAMILY, STYLE)")
    p_act_add.add_argument("--comment", help="Description")
    p_act_rm = s_act.add_parser("remove", help="Remove action by name or ID")
    p_act_rm.add_argument("name_or_id", help="Name or zero-based index")
    p_act_rn = s_act.add_parser("run", help="Execute an action for a font")
    p_act_rn.add_argument("name_or_id", help="Name or zero-based index")
    p_act_rn.add_argument("target", nargs="?", help="Target font family or filepath")
    p_act_rn.add_argument("--wait", action="store_true", help="Wait for process to complete synchronously")

    # google
    p_ggl = subparsers.add_parser("google", help="Google Fonts catalog, search, and installation")
    s_ggl = p_ggl.add_subparsers(dest="google_cmd", required=True)
    p_g_s = s_ggl.add_parser("search", help="Search Google Fonts catalog")
    p_g_s.add_argument("query", nargs="?", default="", help="Search query")
    p_g_s.add_argument("--category", choices=["sans-serif", "serif", "display", "handwriting", "monospace"])
    p_g_s.add_argument("--sort", choices=["alpha", "date", "popularity", "style", "trending"], default="alpha")
    p_g_s.add_argument("--limit", type=int, default=50)
    p_g_i = s_ggl.add_parser("info", help="Show Google Font details")
    p_g_i.add_argument("family", help="Family name")
    p_g_in = s_ggl.add_parser("install", help="Download and install Google Font family")
    p_g_in.add_argument("family", help="Family name")
    p_g_un = s_ggl.add_parser("uninstall", help="Uninstall Google Font family")
    p_g_un.add_argument("family", help="Family name")
    p_g_up = s_ggl.add_parser("update", help="Update Google Fonts catalog cache")
    p_g_up.add_argument("--sort", choices=["alpha", "date", "popularity", "style", "trending"], default="alpha")

    # export / import
    p_exp = subparsers.add_parser("export", help="Export configuration backup")
    p_exp.add_argument("destination", help="Target directory or archive (.tar.gz / .zip)")
    p_exp.add_argument("--no-fonts", action="store_true", help="Exclude user font files")
    p_exp.add_argument("--no-settings", action="store_true", help="Exclude fontconfig configuration")
    p_exp.add_argument("--no-collections", action="store_true", help="Exclude collections and comparisons")
    p_exp.add_argument("--no-sources", action="store_true", help="Exclude font sources")
    p_exp.add_argument("--no-actions", action="store_true", help="Exclude custom actions")

    p_imp = subparsers.add_parser("import", help="Import configuration backup")
    p_imp.add_argument("source", help="Source directory or archive (.tar.gz / .zip)")

    return parser


def _run_cli():
    parser = build_parser()
    args = parser.parse_args()

    # GUI-compatible flags
    if args.about:
        print_about()
        return

    if args.list:
        class ArgsWrapper:
            query = ""
            enabled_only = False
            disabled_only = False
            json = False
        cmd_list(ArgsWrapper())
        return

    if args.list_full:
        class ArgsWrapper:
            query = ""
            enabled_only = False
            disabled_only = False
            json = True
        cmd_list(ArgsWrapper())
        return

    if args.update:
        class ArgsWrapper:
            force = False
        cmd_update(ArgsWrapper())
        return

    if args.install is not None:
        class ArgsWrapper:
            files = args.install
        cmd_install(ArgsWrapper())
        return

    if args.enable is not None:
        class ArgsWrapper:
            families = args.enable
            all = len(args.enable) == 0
        cmd_enable(ArgsWrapper())
        return

    if args.disable is not None:
        class ArgsWrapper:
            families = args.disable
        cmd_disable(ArgsWrapper())
        return

    if args.keep is not None:
        class ArgsWrapper:
            families = args.keep
        cmd_keep(ArgsWrapper())
        return

    # Subcommands
    if not args.subcommand:
        parser.print_help()
        return

    cmd_map = {
        "list": cmd_list,
        "info": cmd_info,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "keep": cmd_keep,
        "status": cmd_status,
        "update": cmd_update,
        "install": cmd_install,
        "remove": cmd_remove,
        "collections": cmd_collections,
        "sources": cmd_sources,
        "aliases": cmd_aliases,
        "substitutions": cmd_aliases,
        "rendering": cmd_rendering,
        "display": cmd_display,
        "charmap": cmd_charmap,
        "preview": cmd_preview,
        "compare": cmd_compare,
        "test": cmd_test,
        "playground": cmd_playground,
        "set": cmd_set,
        "orthographies": cmd_orthographies,
        "actions": cmd_actions,
        "google": cmd_google,
        "export": cmd_export,
        "import": cmd_import,
    }

    handler = cmd_map.get(args.subcommand)
    if handler:
        try:
            handler(args)
        except (RecoveryError, FontDatabaseError) as error:
            parser.exit(1, "CeFont: %s\n" % error)
    else:
        parser.print_help()


def main():
    try:
        return _run_cli()
    except (CommandError, ConfigurationError, FreeTypeUnavailableError,
            BitmapFormatError, FontDatabaseError, RecoveryError, OSError) as error:
        print("CeFont: %s" % error, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
