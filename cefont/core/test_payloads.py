"""Interactive Typographic Test Payloads & Live Session Runner for CeFont.

Provides comprehensive visual testing payloads:
1. Typographic Ambiguity Matrix (0O1lI, 8B&, 5S$, 2Z, punctuation, brackets)
2. Multi-Character Programming Ligatures (->, =>, ==, !=, <=, >=, ::, :=, |>)
3. ANSI TrueColor Syntax-Highlighted Realistic Code Snippet (Python / Rust / C)
4. Nerd Font / Powerline & Developer Glyph Verification
5. Interactive Alternate-Screen Testing Loop with Live Typing Box
"""

import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

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
    colorize,
    format_table,
)


def get_ambiguity_matrix() -> str:
    """Return an aligned typographic comparison matrix of easily confused characters."""
    lines = []
    lines.append(colorize("┌── [1] TYPOGRAPHIC AMBIGUITY DISCRIMINATION MATRIX ──────────────────────┐", BOLD + BLUE))
    lines.append(f"│  {colorize('Numbers & Letters :', BOLD)}  0 (zero)  O (capital O)  o (lowercase o)  Q  Ø (slashed) │")
    lines.append(f"│  {colorize('Ones & Ls & Pipes :', BOLD)}  1 (one)   l (lowercase L)  I (capital I)   | (pipe)   !     │")
    lines.append(f"│  {colorize('Eights & Bs & &s :', BOLD)}  8 (eight) B (capital B)    & (ampersand)   % (percent)      │")
    lines.append(f"│  {colorize('Fives & Ss & $s  :', BOLD)}  5 (five)  S (capital S)    s (lowercase s) $ (dollar)       │")
    lines.append(f"│  {colorize('Twos & Zs        :', BOLD)}  2 (two)   Z (capital Z)    z (lowercase z)                  │")
    lines.append(f"│  {colorize('Punctuation Pairs:', BOLD)}  ' (single) \" (double) ` (backtick) , (comma) . (dot) ; : │")
    lines.append(f"│  {colorize('Bracket Nesting  :', BOLD)}  ( [ {{ < / \\ > }} ] )                                     │")
    lines.append(colorize("└─────────────────────────────────────────────────────────────────────────┘", BOLD + BLUE))
    return "\n".join(lines)


def get_ligatures_sample() -> str:
    """Return organized multi-character programming ligatures and operator test."""
    lines = []
    lines.append(colorize("┌── [2] PROGRAMMING LIGATURES & MULTI-CHARACTER OPERATORS ────────────────┐", BOLD + MAGENTA))
    lines.append(f"│  {colorize('Arrows      :', BOLD)}  ->   -->   =>   ==>   <-   <--   <->   <=>   ~>   <-|       │")
    lines.append(f"│  {colorize('Equality    :', BOLD)}  ==   ===   !=   !==   <=   >=   =~   !~   :=   ::=       │")
    lines.append(f"│  {colorize('Code Glyphs :', BOLD)}  ::   ++   --   **   ///   /*   */   ??   ?.   &&   ||       │")
    lines.append(f"│  {colorize('Pipes/Packs :', BOLD)}  |>   <|   >>   <<   #{'{'}   #{'['}   <$>   <*>   <+>   ;;        │")
    lines.append(colorize("└─────────────────────────────────────────────────────────────────────────┘", BOLD + MAGENTA))
    return "\n".join(lines)


def get_syntax_highlighted_code() -> str:
    """Return a realistic, syntax-highlighted code sample to evaluate font weight & contrast."""
    c_kw = BOLD + MAGENTA
    c_fn = BOLD + BLUE
    c_str = GREEN
    c_num = YELLOW
    c_comment = DIM + GRAY
    c_type = CYAN

    lines = [
        colorize("┌── [3] REAL-WORLD SYNTAX HIGHLIGHTING & CODE WEIGHT SAMPLE ───────────────┐", BOLD + CYAN),
        f"│  {c_comment}# Process real-time typographic metrics for active font face{RESET}",
        f"│  {c_kw}from{RESET} typing {c_kw}import{RESET} {c_type}Dict{RESET}, {c_type}List{RESET}, {c_type}Optional{RESET}, {c_type}Tuple{RESET}",
        f"│  ",
        f"│  {c_kw}class{RESET} {c_fn}TypefaceMetrics{RESET}:",
        f"│      {c_kw}def{RESET} {c_fn}__init__{RESET}({c_kw}self{RESET}, family: {c_type}str{RESET}, weight: {c_type}int{RESET} = {c_num}400{RESET}) -> {c_type}None{RESET}:",
        f"│          {c_kw}self{RESET}.family: {c_type}str{RESET} = family",
        f"│          {c_kw}self{RESET}.is_monospace: {c_type}bool{RESET} = {c_kw}True{RESET}",
        f"│          {c_kw}self{RESET}.x_height_ratio: {c_type}float{RESET} = {c_num}0.528{RESET}",
        f"│  ",
        f"│      {c_kw}def{RESET} {c_fn}evaluate_ligature{RESET}({c_kw}self{RESET}, op: {c_type}str{RESET}) -> {c_type}bool{RESET}:",
        f"│          {c_comment}# Check if operator string constitutes a ligature{RESET}",
        f"│          {c_kw}if{RESET} op {c_kw}in{RESET} [{c_str}\"->\"{RESET}, {c_str}\"==\"{RESET}, {c_str}\"!=\"{RESET}, {c_str}\"|>\"{RESET}]:",
        f"│              {c_kw}return{RESET} {c_num}0x00{RESET} <= {c_fn}ord{RESET}(op[{c_num}0{RESET}]) <= {c_num}0x7F{RESET}",
        f"│          {c_kw}return{RESET} {c_kw}False{RESET}",
        colorize("└─────────────────────────────────────────────────────────────────────────┘", BOLD + CYAN),
    ]
    return "\n".join(lines)


def get_nerd_font_symbols() -> str:
    """Return Powerline statusline and developer glyph verification."""
    lines = [
        colorize("┌── [4] POWERLINE STATUSLINE & DEVELOPER GLYPHS ──────────────────────────┐", BOLD + YELLOW),
        f"│  Powerline Arrows :  \033[44;30m NORMAL \033[42;34m\033[42;30m  main \033[40;32m\033[0m  file.py \033[34m\033[0m utf-8 \033[30;44m\033[44;30m 100% \033[0m",
        f"│  Developer Icons  :   Python   󰘧 JSON    Rust    C++    Arch   󰊢 Git │",
        colorize("└─────────────────────────────────────────────────────────────────────────┘", BOLD + YELLOW),
    ]
    return "\n".join(lines)


# =========================================================================
# 5. Interactive Testing Session Loop
# =========================================================================

def run_interactive_test_session(
    font_info: Dict[str, Any],
    active_size: int = 11,
    terminal_type: str = "kgx",
    is_fallback: bool = False,
    use_alt_buffer: bool = False,
) -> str:
    """
    Run an interactive test session.
    Prints typographic payloads directly to terminal buffer to ensure native mouse
    wheel scrolling without sending ^[[A / ^[[B escape codes.
    Returns: 'keep' or 'revert'
    """
    family = font_info.get("family", "Font")
    style = font_info.get("style", "Regular")

    if use_alt_buffer:
        sys.stdout.write("\033[?1049h\033[H")
        sys.stdout.flush()

    try:
        # Header Banner
        term_label = {
            "kgx": "GNOME Console (kgx) via GSettings",
            "gnome-terminal": "GNOME Terminal via GSettings",
            "alacritty": "Alacritty (inotify config reload)",
            "kitty": "Kitty (remote control / config)",
            "wezterm": "WezTerm (config auto-reload)",
            "konsole": "KDE Konsole (D-Bus session)",
            "foot": "Foot (foot.ini + SIGUSR1)",
            "xterm": "XTerm (OSC 50 protocol)",
            "mock": "Virtual Terminal Fallback (FreeType Engine)",
            "ssh": "Virtual Terminal Fallback (SSH Remote Client)",
            "linux-vt": "Virtual Terminal Fallback (Linux Console)",
        }.get(terminal_type, f"Terminal ({terminal_type})")

        print(colorize("═══════════════════════════════════════════════════════════════════════════════", BOLD + BLUE))
        print(colorize(f"  CeFont Live Font Test: {family} ({style}) — {active_size}pt", BOLD + GREEN))
        print(colorize(f"  Active Terminal Backend: {term_label}", DIM + GRAY))
        print(colorize("  Controls: [Enter] or [q] to Revert | Type 'keep' to keep permanently", YELLOW))
        print(colorize("═══════════════════════════════════════════════════════════════════════════════", BOLD + BLUE))
        print("")

        if is_fallback or terminal_type in ["mock", "ssh", "linux-vt", "unsupported"]:
            print(colorize("Notice: Virtual Terminal Fallback active.", BOLD + YELLOW))
            print(colorize("Rendering typographic outlines via CeFont FreeType Vector Engine:", DIM + GRAY))
            print("")
            # Render visual mock preview
            from cefont.core.renderer import render_font_preview_image
            filepath = font_info.get("filepath", "")
            findex = font_info.get("findex", 0)
            if filepath and os.path.isfile(filepath):
                try:
                    mock_code = render_font_preview_image(
                        filepath=filepath,
                        text="def hello(): -> int:\n    return 42 != 0",
                        pixel_size=20,
                        face_index=findex,
                        wrap=True
                    )
                    print(mock_code)
                    print("")
                except Exception:
                    pass

        # Print Test Payloads (freely scrollable via mouse wheel)
        print(get_ambiguity_matrix())
        print("")
        print(get_ligatures_sample())
        print("")
        print(get_syntax_highlighted_code())
        print("")
        print(get_nerd_font_symbols())
        print("")

        # Interactive user prompt
        print(colorize("───────────────────────────────────────────────────────────────────────────────", GRAY))
        prompt_str = colorize(
            f"[{family} {active_size}pt] Press [Enter] or [q] to Revert & Exit (or type 'keep' to keep): ",
            BOLD + GREEN,
        )
        user_choice = ""
        try:
            user_choice = input(prompt_str).strip().lower()
        except (EOFError, KeyboardInterrupt):
            pass

        if user_choice in ["keep", "k", "save"]:
            return "keep"
        return "revert"

    finally:
        if use_alt_buffer:
            sys.stdout.write("\033[?1049l")
            sys.stdout.flush()


def run_minimal_playground_session(
    font_info: Dict[str, Any],
    active_size: int = 11,
    terminal_type: str = "kgx",
) -> str:
    """
    Run a minimal playground session: no test payloads or matrices are displayed.
    Applies font live and shows only a clean single-line prompt waiting for [Enter] to revert or 'keep'.
    Returns: 'keep' or 'revert'
    """
    family = font_info.get("family", "Font")
    prompt_str = colorize(
        f"[{family} {active_size}pt] Live font active. Press [Enter] or [q] to revert (or type 'keep' to keep): ",
        BOLD + GREEN,
    )
    user_choice = ""
    try:
        user_choice = input(prompt_str).strip().lower()
    except (EOFError, KeyboardInterrupt):
        pass

    if user_choice in ["keep", "k", "save"]:
        return "keep"
    return "revert"
