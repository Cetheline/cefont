# CeFont Complete Technical Manual & Architecture Guide

CeFont is a professional, standalone terminal font manager and typography rendering engine for Linux operating systems. Designed to operate completely headless with zero graphical dependencies, CeFont provides 100% feature parity with traditional Linux desktop font managers while delivering near-instantaneous execution via direct native C library integration.

---

## Table of Contents

1. [Architectural Overview & Core Design Philosophy](#1-architectural-overview--core-design-philosophy)
2. [Reverse-Engineering Analysis & System Findings](#2-reverse-engineering-analysis--system-findings)
3. [Filesystem Layout & Standard Specifications](#3-filesystem-layout--standard-specifications)
4. [Performance Architecture: C Engine vs. Python Orchestration](#4-performance-architecture-c-engine-vs-python-orchestration)
5. [Complete Command Reference](#5-complete-command-reference)
   - [5.1 Core Font Management (`list`, `info`, `status`, `update`)](#51-core-font-management)
   - [5.2 Font Activation (`enable`, `disable`, `keep`)](#52-font-activation)
   - [5.3 Font Installation & Removal (`install`, `remove`)](#53-font-installation--removal)
   - [5.4 Collections Management (`collections`)](#54-collections-management)
   - [5.5 Custom Font Sources (`sources`)](#55-custom-font-sources)
   - [5.6 Fontconfig Substitutions (`aliases`)](#56-fontconfig-substitutions)
   - [5.7 Rendering & Display Preferences (`rendering`, `display`)](#57-rendering--display-preferences)
   - [5.8 Character Map & ASCII Rasterizer (`charmap`)](#58-character-map--ascii-rasterizer)
   - [5.9 Typography Previews (`preview`)](#59-typography-previews)
   - [5.10 Comparison Matrices & Pinned Comparisons (`compare`)](#510-comparison-matrices--pinned-comparisons)
   - [5.11 Language Orthography Engine (`orthographies`)](#511-language-orthography-engine)
   - [5.12 Custom User Actions (`actions`)](#512-custom-user-actions)
   - [5.13 Google Fonts Catalog & Deployment (`google`)](#513-google-fonts-catalog--deployment)
   - [5.14 Configuration Backup & Migration (`export`, `import`)](#514-configuration-backup--migration)
   - [5.15 Upstream Compatibility Flags](#515-upstream-compatibility-flags)
6. [Database Relational Architecture (SQLite DDL)](#6-database-relational-architecture-sqlite-ddl)
7. [Fontconfig XML & Configuration Schemas](#7-fontconfig-xml--configuration-schemas)
8. [FreeType 2 Terminal Rasterization Engine](#8-freetype-2-terminal-rasterization-engine)
9. [128 Language Orthography Verification Matrix](#9-128-language-orthography-verification-matrix)
10. [Troubleshooting & Maintenance](#10-troubleshooting--maintenance)

---

## 1. Architectural Overview & Core Design Philosophy

CeFont operates on a modular, multi-tier architecture where Python handles high-level argument parsing, user presentation, and task orchestration, while low-level system operations are delegated directly to compiled shared C libraries:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           CeFont CLI Interface                          │
│                      (Argument Routing & ANSI TUI)                      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       ▼                             ▼                             ▼
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│  FreeType 2  │             │  Fontconfig  │             │   SQLite 3   │
│  (C Binding) │             │ (XML Engine) │             │  (B-Tree DB) │
└──────┬───────┘             └──────┬───────┘             └──────┬───────┘
       │                            │                            │
       ▼                            ▼                            ▼
• TrueType/OpenType Parsing   • 78-Reject.conf (Masking)    • Fonts Table
• OS/2 Table Decoding         • 09-Directories.conf (Dirs)  • Metadata Table
• Panose-1 Classification     • 39-Aliases.conf (Subst)     • Panose Table
• fsType Licensing Rights     • 19-DefaultProperties.conf   • Orthography Table
• High-Density ASCII Raster   • 19-DisplayProperties.conf
```

### Core Design Principles
1. **Zero Display Dependencies**: No X11 (`libX11`), Wayland (`libwayland-client`), GTK (`libgtk-4`), or Qt dependencies. Operates seamlessly over SSH, in Docker containers, and on headless servers.
2. **100% Standards Compliance**: Interacts strictly with Fontconfig XML specifications (`urn:fontconfig:fonts.dtd`), standard XDG Base Directory layouts, and FreeType 2 specifications.
3. **Non-Destructive Management**: Font disabling never deletes or changes file permissions on user or system files; instead, it relies on Fontconfig's `<rejectfont>` pattern masking.
4. **Fault Tolerance**: Upstream desktop tools crash when encountering restricted files (such as root-owned `0600` font files); CeFont handles permission boundaries defensively and continues operation without interruption.

---

## 2. Reverse-Engineering Analysis & System Findings

During reverse-engineering of Linux desktop font management subsystems using REA native exports analysis, several critical system mechanisms were documented:

### A. Font Masking & Activation (`78-Reject.conf`)
Desktop font managers do not move fonts into staging folders when disabling them. Instead, they write an XML pattern element inside the user's Fontconfig include path (`~/.config/fontconfig/conf.d/78-Reject.conf`). When Fontconfig compiles its active font cache, any pattern defined inside `<rejectfont>` is omitted from application font listings.

### B. Custom Font Directory Discovery (`09-Directories.conf` & `Sources.xml`)
When custom directories are added by the user:
- Stored persistently in `~/.config/cefont/Sources.xml` under `<source>/path</source>`.
- When activated, written to `~/.config/fontconfig/conf.d/09-Directories.conf` under `<dir>/path</dir>`.
- Fontconfig automatically monitors this path and includes fonts in active sessions without requiring root access.

### C. TrueType/OpenType OS/2 Tables & Licensing
TrueType and OpenType fonts store crucial metadata inside the binary `OS/2` table:
- **`fsType` (Embedding Rights)**: A bitmask specifying whether a font may be embedded in documents, printed, or subsetted.
- **Panose-1 Vector**: A 10-byte classification array describing Family Kind, Serif Style, Weight, Proportion, Contrast, Stroke Variation, Arm Style, Letterform, Midline, and X-Height.

---

## 3. Filesystem Layout & Standard Specifications

CeFont adheres strictly to the XDG Base Directory Specification:

| Path | Purpose |
| :--- | :--- |
| **`~/.config/cefont/`** | Persistent application configuration |
| `├── Collections.json` | User-defined hierarchical font collections |
| `├── PinnedComparisons.json` | Saved comparison sets |
| `├── Sources.xml` | Defined font directory sources |
| `└── Actions.json` | Custom user external commands |
| **`~/.cache/cefont/`** | Fast cache and local storage |
| `├── cefont.sqlite` | Relational metadata database |
| `└── gfc-*.json` | Google Fonts API cached catalogs (TTL: 2 days) |
| **`~/.config/fontconfig/conf.d/`** | Fontconfig runtime integration |
| `├── 09-Directories.conf` | Active custom font directories |
| `├── 19-DefaultProperties.conf`| Rendering rules (antialias, hinting, hintstyle) |
| `├── 19-DisplayProperties.conf`| Display rules (subpixel rgba, LCD filter, DPI) |
| `├── 39-Aliases.conf` | Family substitution and fallback aliases |
| `└── 78-Reject.conf` | Disabled font family masks |
| **`~/.local/share/fonts/`** | User-installed font storage |
| `└── <Vendor>/<Type>/<Family>/` | Standard hierarchical installation layout |

---

## 4. Performance Architecture: C Engine vs. Python Orchestration

A critical consideration in building CeFont with Python was ensuring zero performance penalty when processing thousands of fonts:

1. **Font Discovery**: CeFont does not perform slow recursive `os.walk` calls across system directories. It queries `fc-list`, which reads Fontconfig's pre-compiled binary cache files (`/var/cache/fontconfig/*`) in native C. Discovering 2,500+ font families takes **~50 milliseconds**.
2. **FreeType Ctypes Engine**: When extracting binary metadata (OpenType name tables, OS/2 tables, glyph contours), CeFont invokes `libfreetype.so.6` directly via C pointer structs (`FT_FaceRec`).
3. **SQLite Batch Transactions**: SQLite operations execute in native C (`libsqlite3.so`). Cold indexing wraps font records in explicit `BEGIN TRANSACTION ... COMMIT` batches, indexing 2,500+ fonts in **~15 seconds**.
4. **Daily Query Execution**: Once indexed, subcommands like `list`, `info`, `status`, and `compare` resolve from indexed B-Trees in **< 50 milliseconds**.

---

## 5. Complete Command Reference

### 5.1 Core Font Management

#### `cefont status`
Displays high-level statistics of the font subsystem.
```bash
cefont status
```
*Output includes:* Total families, enabled count, disabled count, collection count, active sources, and database size.

#### `cefont list [query]`
Lists font families with variant count, activation status, and sample style.
```bash
# List all fonts
cefont list

# Filter by family name
cefont list "JetBrains"

# Filter to show only enabled or disabled fonts
cefont list --enabled-only
cefont list --disabled-only

# Output raw JSON
cefont list "Noto" --json
```

#### `cefont info <family | filepath>`
Displays exhaustive metadata, licensing, and Panose classification for a font.
```bash
# Inspect by family name
cefont info "JetBrainsMono Nerd Font"

# Inspect by direct file path
cefont info /usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf

# Output raw JSON
cefont info "Cantarell" --json
```

#### `cefont update [--force]`
Synchronizes the SQLite database with system and user font directories.
```bash
# Standard synchronization
cefont update

# Force re-extraction of all metadata
cefont update --force
```

---

### 5.2 Font Activation

#### `cefont disable <family...>`
Disables one or more font families system-wide via `78-Reject.conf`.
```bash
cefont disable "Cantarell" "Adwaita Sans"
```

#### `cefont enable [family...] [--all]`
Re-enables disabled font families.
```bash
# Enable specific families
cefont enable "Cantarell"

# Enable all currently disabled fonts
cefont enable --all
```

#### `cefont keep <family...>`
Isolates specified font families by keeping them enabled and disabling all other fonts on the system.
```bash
cefont keep "JetBrainsMono Nerd Font" "Adwaita Mono"
```

---

### 5.3 Font Installation & Removal

#### `cefont install <file | archive...>`
Installs font files (`.ttf`, `.otf`, `.ttc`, `.woff`, `.woff2`) or font archives (`.zip`, `.tar.gz`, `.tar.bz2`) into `~/.local/share/fonts/` following standard foundry/family folder hierarchies.
```bash
# Install individual font file
cefont install ~/Downloads/CustomFont.ttf

# Install font archive (automatically extracted and structured)
cefont install ~/Downloads/NerdFontsPack.zip
```

#### `cefont remove <family | filepath...>`
Safely deletes user-installed fonts from `~/.local/share/fonts/` and purges database records. Refuses to delete system-wide fonts in `/usr/share/fonts`.
```bash
cefont remove "CustomFont"
cefont remove ~/.local/share/fonts/Unknown/TrueType/Test/Test_Regular.ttf
```

---

### 5.4 Collections Management

Manage font collections in `~/.config/cefont/Collections.json`:

```bash
# List all collections
cefont collections list

# Create a collection
cefont collections create "Developer Tools" --description "Monospaced coding fonts"

# Add families to a collection
cefont collections add "Developer Tools" "JetBrainsMono Nerd Font" "Adwaita Mono"

# Show collection details
cefont collections show "Developer Tools"

# Remove families from a collection
cefont collections remove "Developer Tools" "Adwaita Mono"

# Rename a collection
cefont collections rename "Developer Tools" "Code Monospace"

# Toggle all fonts in a collection on or off
cefont collections disable "Code Monospace"
cefont collections enable "Code Monospace"

# Delete a collection
cefont collections delete "Code Monospace"
```

---

### 5.5 Custom Font Sources

Synchronize custom directories with Fontconfig (`Sources.xml` and `09-Directories.conf`):

```bash
# List all custom sources
cefont sources list

# Add a directory source (automatically indexes fonts)
cefont sources add ~/Projects/GameAssets/Fonts

# Add a source without immediately activating in Fontconfig
cefont sources add ~/ExperimentalFonts --disabled

# Enable or disable an existing source
cefont sources disable ~/Projects/GameAssets/Fonts
cefont sources enable ~/Projects/GameAssets/Fonts

# Remove a source and purge its fonts from the database
cefont sources remove ~/Projects/GameAssets/Fonts
```

---

### 5.6 Fontconfig Substitutions

Configure substitution and fallback rules in `39-Aliases.conf`:

```bash
# List existing alias substitutions
cefont aliases list

# Add alias with preferred, accepted, and default fallbacks
cefont aliases add "sans-serif" --prefer "Cantarell,Noto Sans" --default "sans-serif"

# Remove an alias substitution
cefont aliases remove "sans-serif"
```

---

### 5.7 Rendering & Display Preferences

#### Rendering Properties (`19-DefaultProperties.conf`)
```bash
# View active rendering properties
cefont rendering show

# Configure antialiasing, hinting, and hintstyle
cefont rendering set --antialias true --hinting true --hintstyle hintslight

# Reset rendering properties to system defaults
cefont rendering reset
```

#### Display Properties (`19-DisplayProperties.conf`)
```bash
# View active display properties
cefont display show

# Configure subpixel geometry, LCD filter, and DPI
cefont display set --rgba rgb --lcdfilter lcddefault --dpi 96.0 --scale 1.0

# Reset display properties to system defaults
cefont display reset
```

---

### 5.8 Character Map & Glyph Rasterizer

#### Graphical Glyph Inspection (`charmap glyph`)
Renders high-resolution vector glyph outlines directly in the terminal using native FreeType Bézier outline rasterization:
```bash
# Render glyph 'A' (U+0041) at 40px using auto-detected terminal graphics
cefont charmap glyph "Cantarell" 0041 --size 40

# Force TrueColor Unicode half-blocks (works in all terminals)
cefont charmap glyph "Cantarell" 0041 --format blocks

# Kitty / WezTerm / Ghostty graphics protocol
cefont charmap glyph "Cantarell" 0041 --format kitty

# Sixel graphics protocol (Foot, Konsole, BlackBox)
cefont charmap glyph "Cantarell" 0041 --format sixel

# Classic ASCII art fallback
cefont charmap glyph "Cantarell" 0041 --format ascii
```

Output includes precise typographic metrics:
- Codepoint & character symbol
- Raster dimensions (width × rows px)
- Bounding bearing (`bitmap_left`, `bitmap_top`)
- Horizontal advance width (`advance_x` px)

#### Character Map Grid (`charmap grid`)
Renders a 16-column matrix showing available Unicode codepoints:
```bash
# Render ASCII/Latin range (U+0020 to U+007E)
cefont charmap grid "JetBrainsMono Nerd Font" --start 0020 --end 007E

# Render Extended Latin or Cyrillic range
cefont charmap grid "JetBrainsMono Nerd Font" --start 0400 --end 045F
```

---

### 5.9 Typography Previews with Terminal Graphics

Unlike standard CLI tools that merely print terminal-font characters, CeFont features a **Terminal Graphics Protocol & FreeType Vector Rendering Engine** (`cefont/core/renderer.py`). It rasterizes the font face's actual outlines into memory and displays them directly inline in your terminal!

Supported graphics formats (`--format` or `--protocol`):
- `auto`: Automatically detects Kitty, WezTerm, Ghostty, Foot, Konsole, iTerm2, or falls back to TrueColor half-blocks.
- `kitty`: High-res 24-bit PNG rendering via Kitty Graphics Protocol (`\033_Gf=100...`).
- `iterm2`: Inline PNG rendering via iTerm2 protocol (`\033]1337;File=inline=1...`).
- `sixel`: 16-shade grayscale DEC Sixel graphics protocol.
- `blocks`: Universal 24-bit TrueColor Unicode Half-Blocks (`▀` / `▄`), rendering 2 vertical pixels per cell in 100% of modern Linux terminals with zero dependencies.
- `text`: Plain text fallback using the terminal emulator's font.

#### Waterfall Preview (`preview waterfall`)
Displays sample text across escalating point sizes (12pt, 16pt, 20pt, 26pt, 34pt, 44pt), rendered with the font's actual vector outlines:
```bash
# Default auto-detected graphics protocol
cefont preview waterfall "Cantarell"

# Custom sizes and sample text
cefont preview waterfall "Cantarell" --sizes 14,20,28,40 --text "CeFont Typography"

# Force specific graphic protocol
cefont preview waterfall "Cantarell" --format kitty
cefont preview waterfall "Cantarell" --format blocks
cefont preview waterfall "Cantarell" --format text
```

#### Pangram Preview (`preview pangram`)
Displays localized pangram text with automatic word wrapping, rendering multi-line visual font previews:
```bash
# Render pangram at 20px
cefont preview pangram "Cantarell" --size 20

# Force TrueColor half-blocks
cefont preview pangram "Cantarell" --size 18 --format blocks

# Custom pangram string
cefont preview pangram "Cantarell" --text "Sphinx of black quartz, judge my vow."
```

---

### 5.10 Comparison Matrices & Pinned Comparisons

#### Side-by-Side Comparison Matrix with Rendered Outlines
Compares metadata side-by-side and renders real font outlines for each font face below the matrix:
```bash
# Compare multiple fonts with real rendered outlines
cefont compare fonts "Cantarell" "Noto Sans"

# Custom sample comparison text & size
cefont compare fonts "Cantarell" "Noto Sans" --sample "Handgloves 123" --size 24

# Force specific graphics protocol
cefont compare fonts "Cantarell" "Noto Sans" --format blocks
```

#### Pinned Comparisons
Persistently store sets of fonts for recurring evaluation in `~/.config/cefont/PinnedComparisons.json`:
```bash
# Save a comparison set
cefont compare pinned-save "Modern Sans" "Cantarell" "Noto Sans"

# List saved pinned comparisons
cefont compare pinned-list

# Display comparison matrix and rendered outlines for a saved set
cefont compare pinned-show 0
cefont compare pinned-show "Modern Sans" --format blocks

# Remove a saved pinned comparison
cefont compare pinned-delete "Modern Sans"
```

---

### 5.11 Language Orthography Engine

Evaluates font coverage against 128 global language scripts:
```bash
# List all 128 supported language orthographies
cefont orthographies list

# Analyze font coverage for a specific font
cefont orthographies analyze "JetBrainsMono Nerd Font" --min-coverage 95.0

# Filter analysis to a specific script or language
cefont orthographies analyze "JetBrainsMono Nerd Font" --lang "Cyrillic"
```

---

### 5.12 Custom User Actions

Execute custom terminal or external commands against font files with token replacement (`FILEPATH`, `FAMILY`, `STYLE`):
```bash
# List configured actions
cefont actions list

# Add an action
cefont actions add "Inspect FT" "ftdump" --arguments "FILEPATH" --comment "Run FreeType dump"

# Execute action against a font
cefont actions run "Inspect FT" "JetBrainsMono Nerd Font"

# Remove an action
cefont actions remove 0
```

---

### 5.13 Google Fonts Catalog & Deployment

Query and deploy fonts directly from the Google Web Fonts catalog:
```bash
# Search catalog with category filtering
cefont google search "Roboto" --category monospace

# Inspect family details
cefont google info "Roboto Mono"

# Download and install into ~/.local/share/fonts/Google Fonts/
cefont google install "Roboto Mono"

# Uninstall family and purge files
cefont google uninstall "Roboto Mono"

# Refresh catalog cache
cefont google update
```

---

### 5.14 Configuration Backup & Migration

Archive or restore complete configurations (collections, sources, fontconfig rules, and user fonts) into portable `.tar.gz` or `.zip` archives:
```bash
# Export complete backup
cefont export ~/cefont-backup.tar.gz

# Export settings only (exclude installed font files)
cefont export ~/cefont-settings.tar.gz --no-fonts

# Import backup archive
cefont import ~/cefont-backup.tar.gz
```

---

### 5.15 Upstream Compatibility Flags

CeFont provides drop-in compatibility with upstream desktop CLI options:

| Flag | Equivalent Command | Description |
| :--- | :--- | :--- |
| **`-l, --list`** | `cefont list` | List available font families |
| **`--list-full`**| `cefont list --json` | Output full JSON with face information |
| **`-u, --update`**| `cefont update` | Update application database |
| **`-i, --install`**| `cefont install <files>` | Install font files |
| **`-e, --enable`**| `cefont enable [fams]` | Enable font families |
| **`-d, --disable`**| `cefont disable <fams>`| Disable font families |
| **`--keep`** | `cefont keep <fams>` | Keep enabled; disable others |
| **`-a, --about`**| `cefont --about` | Application details |
### 5.16 Direct Terminal Font Switching (`cefont set`)

Directly applies and persists the active terminal emulator's font without showing interactive payloads or blocking on user input:

```bash
# Directly switch terminal font and keep it permanently
cefont set "JetBrainsMono Nerd Font"

# Set with custom point size
cefont set "CommitMono" --size 14
```

### 5.17 Live Terminal Font Testing (`cefont test` & `cefont playground`)

CeFont provides two distinct live testing experiences for active terminal emulators:

#### 1. Playground Mode (`cefont playground` or `cefont test -p`)
Minimal live test mode. The font is applied instantly to the running terminal without any clutter, tables, or preset test suites. Displays only a single clean prompt waiting for you to press `[Enter]` to revert:

```bash
# Minimal playground mode: font changes live, press [Enter] to revert
cefont playground "JetBrainsMono Nerd Font"

# Alternatively, via test command flag
cefont test "CommitMono" --size 13 --playground
```

#### 2. Preset Mode (`cefont test`)
Full visual test mode. Temporarily switches font and displays a comprehensive curated test suite (Typographic Ambiguity Matrix `0O1lI`, Programming Ligatures `->`, `==`, `!=`, Syntax-Highlighted Code, and Powerline Glyphs) in standard terminal scrollback with native mouse wheel scrolling:

```bash
# Preset test with full typographic visual suite
cefont test "JetBrainsMono Nerd Font"

# Test with custom point size
cefont test "CommitMono" --size 13

# Directly set font permanently (alias for cefont set)
cefont test "JetBrainsMono Nerd Font" --set

# Use fullscreen alternate buffer (optional)
cefont test "JetBrainsMono Nerd Font" --alt

# Force Virtual Terminal Fallback preview (uses FreeType Graphics Engine)
cefont test "Cantarell" --mock

# Restore terminal if a previous test session was abruptly killed with SIGKILL
cefont test --recover
```

#### Controls & Interaction:
* **Mouse Wheel**: Freely scroll up and down the terminal scrollback without generating escape sequences (`^[[A` / `^[[B`).
* **`[Enter]` or `[q]`**: Revert back to original terminal font cleanly and exit.
* **Type `keep`**: Keep the newly applied font permanently without reverting.

#### Supported Terminal Emulators:
- **GNOME Console (`kgx`)**: Modifies `org.gnome.Console use-system-font false` and `custom-font` via explicit `/usr/bin/gsettings` with sanitized D-Bus environment (instant live update, zero flicker).
- **GNOME Terminal**: Modifies active profile font via GSettings (`org.gnome.Terminal.Legacy.Profile`).
- **Alacritty**: Updates `~/.config/alacritty/alacritty.toml` (auto-reloads within 20ms via inotify).
- **Kitty**: Uses `kitty @ set-font` remote control or `kitty.conf` + `SIGUSR1`.
- **WezTerm**: Updates `~/.config/wezterm/wezterm.lua` or WezTerm CLI.
- **Konsole**: Invokes D-Bus session `setFont`.
- **XTerm / URxvt**: Uses OSC 50 escape sequences with TrueType Xft specs (`\033]50;xft:<font>:size=<size>\a`). Requires `allowFontOps: true` (e.g. `xterm -xrm 'XTerm*allowFontOps: true'` or in `~/.Xresources`).

#### Bulletproof Safety Guarantees:
1. **RAII Context Manager**: Traps `SIGINT` (Ctrl+C), `SIGTERM`, `SIGHUP`, and `atexit`.
2. **Crash Recovery Journal**: Atomically records `~/.cache/cefont/live_test_recovery.json` before any configuration changes. Automatically scanned and restored on startup or via `cefont test --recover`.
3. **Remote SSH & Linux Console Fallback**: Automatically switches to the **Virtual Terminal Preview Engine** (rendering the test suite with native FreeType vector graphics) when running over SSH or Linux virtual consoles (`TERM=linux`).

---

## 6. Database Relational Architecture (SQLite DDL)

Database location: `~/.cache/cefont/cefont.sqlite`

```sql
-- Core Font Face Index
CREATE TABLE Fonts (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INTEGER,
    family TEXT,
    style TEXT,
    spacing INTEGER,
    slant INTEGER,
    weight INTEGER,
    width INTEGER,
    description TEXT
);

-- Comprehensive TrueType/OpenType Metadata
CREATE TABLE Metadata (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INTEGER,
    family TEXT,
    style TEXT,
    owner INTEGER,
    psname TEXT,
    filetype TEXT,
    'n-glyphs' INTEGER,
    copyright TEXT,
    version TEXT,
    description TEXT,
    'license-data' TEXT,
    'license-url' TEXT,
    vendor TEXT,
    designer TEXT,
    'designer-url' TEXT,
    'license-type' TEXT,
    fsType INTEGER,
    filesize TEXT,
    checksum TEXT
);

-- Panose-1 Classification Vector
CREATE TABLE Panose (
    uid INTEGER PRIMARY KEY,
    P0 INTEGER, P1 INTEGER, P2 INTEGER, P3 INTEGER, P4 INTEGER,
    P5 INTEGER, P6 INTEGER, P7 INTEGER, P8 INTEGER, P9 INTEGER,
    filepath TEXT,
    findex INTEGER
);

-- Script Coverage & Pangram Samples
CREATE TABLE Orthography (
    uid INTEGER PRIMARY KEY,
    filepath TEXT,
    findex INT,
    support TEXT,  -- JSON-encoded list of supported language names
    sample TEXT    -- Representative pangram string
);

-- Optimized Retrieval Indices
CREATE INDEX font_match_idx ON Fonts (filepath, findex, family, description);
CREATE INDEX info_match_idx ON Metadata (filepath, findex, owner, filetype, vendor, 'license-type');
CREATE INDEX panose_match_idx ON Panose (filepath, findex, P0);
```

---

## 7. Fontconfig XML & Configuration Schemas

All Fontconfig files generated by CeFont adhere strictly to `urn:fontconfig:fonts.dtd`:

### `78-Reject.conf` (Font Masking)
```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- Generated by CeFont. Do NOT edit this file. -->
<fontconfig>
  <selectfont>
    <rejectfont>
      <pattern>
        <patelt name="family">
          <string>Cantarell</string>
        </patelt>
      </pattern>
    </rejectfont>
  </selectfont>
</fontconfig>
```

### `09-Directories.conf` (Custom Sources)
```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- Generated by CeFont. Do NOT edit this file. -->
<fontconfig>
  <dir>/home/user/CustomFonts</dir>
</fontconfig>
```

### `39-Aliases.conf` (Substitutions)
```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- Generated by CeFont. Do NOT edit this file. -->
<fontconfig>
  <alias>
    <family>sans-serif</family>
    <prefer>
      <family>Cantarell</family>
      <family>Noto Sans</family>
    </prefer>
    <default>
      <family>sans-serif</family>
    </default>
  </alias>
</fontconfig>
```

---

## 8. Terminal Graphics Protocol & FreeType Vector Rendering Engine

CeFont includes an advanced in-memory graphics rendering subsystem implemented in [`renderer.py`](file:///home/yosatsu/Coding/CeFont/cefont/core/renderer.py) and [`freetype_backend.py`](file:///home/yosatsu/Coding/CeFont/cefont/core/freetype_backend.py):

### 8.1 How It Works
1. **FreeType Vector Rasterization**: Loads font Bézier curves directly from native FreeType 2 (`libfreetype.so.6`) via `ctypes`.
2. **Anti-Aliasing & Compositing**: Computes character bounding boxes, baselines, and horizontal advances, then composites 8-bit anti-aliased glyph bitmaps onto an RGB canvas in memory.
3. **Zero-Dependency PNG Encoder**: Encodes the raw RGB byte buffer into a standards-compliant PNG in pure Python using `zlib` and `struct` (no PIL/Pillow or external C libraries required).
4. **Terminal Protocol Transmission**:
   - **Kitty Graphics Protocol**: Streams base64 PNG chunks via `\033_Gf=100,a=T,m=...;...\033\`. Supported in Kitty, WezTerm, and Ghostty.
   - **iTerm2 Inline Images Protocol**: Streams base64 PNG via `\033]1337;File=inline=1;width=...px;height=...px:...\a`. Supported in iTerm2, Tabby, and Mintty.
   - **DEC Sixel Graphics Protocol**: Quantizes pixel luminosity into a 16-shade grayscale palette and outputs 6-pixel vertical bands via `\033Pq...`. Supported in Foot, Konsole, and BlackBox.
   - **Universal 24-bit TrueColor Half-Blocks**: Maps 2 vertical pixels per terminal character cell using Unicode half-blocks (`▀` / `▄`) with 24-bit ANSI foreground/background escape sequences (`\033[38;2;R;G;Bm\033[48;2;R;G;Bm▀`). Operates in 100% of modern Linux terminals with razor-sharp anti-aliased clarity and transparent background blending.
   - **Classic ASCII Density Ramp**: Fallback mapping luminosity to 10 ASCII characters (` .:-=+*#%@`).

---

## 9. 128 Language Orthography Verification Matrix

CeFont bundles 128 language and script definitions from Unicode orthography specifications. Each definition contains:
- **Mandatory Key Codepoint**: A critical distinguishing character (e.g., German `ß` `U+00DF`, Cyrillic `Ж` `U+0416`).
- **Complete Codepoint Set**: All required uppercase and lowercase letters for full script fluency.
- **Representative Pangram**: Culturally verified sample text used for rendering waterfall previews.

During analysis, CeFont compares the font's active `cmap` against each script set to compute precise coverage percentages and list any missing glyphs.

---

## 10. Troubleshooting & Maintenance

### Permission Denied on System Fonts
* **Behavior**: System font directories (`/usr/share/fonts`) occasionally contain fonts with `0600` permissions readable only by `root`.
* **CeFont Solution**: CeFont checks `os.access(path, os.R_OK)` before attempting file reads, gracefully bypassing unreadable files without aborting the batch index.

### Font Changes Not Reflected in Desktop Apps
* Run `fc-cache -fv` to force cache synchronization across desktop sessions.
* Ensure application has reloaded Fontconfig (restart the browser or terminal emulator).

### Broken Pipe Errors in CLI Pipelines
* CeFont natively catches `BrokenPipeError` (SIGPIPE), ensuring clean exits with code `0` when piped into tools like `head`, `tail`, or `less`.
