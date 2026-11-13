# CeFont Codebase Discovery

Discovery date: 2026-11-09 (Asia/Jakarta). Repository snapshot: `3fda999` on `master`, with local `origin/master` pointing to the same commit. The working tree was clean before discovery.

## Evidence and scope

This is discovery only. Application code, dependencies, configuration, and existing documentation were not changed. This report is the only repository addition. Source inspection covered the Python package, launchers, packaging configuration, bundled data formats, README, technical manual, and locally available Git history. Validation used temporary directories and disabled Python bytecode writes.

Evidence labels used below:

- **Verified — source:** directly observable in this checkout or its local Git objects; this does not imply successful end-to-end execution.
- **Verified — runtime:** observed during the limited checks in section 8.
- **Inference:** architectural assessment supported by source, requiring broader verification.
- **Unknown:** not established by this investigation.

The installed graphify instructions were consulted. No existing `graphify-out/graph.json` was present. Graph generation, additional artifacts, dependency installation, and a companion canvas were omitted to honor the explicit instruction to create only this Markdown report.

## 1. Repository overview

**Verified — source:** this checkout is already a standalone Python command-line application with terminal previews. It is not a GTK application awaiting an initial CLI conversion. The task's GUI-origin context is useful provenance, but does not describe the current execution architecture.

The application has 19 Python files, including a 1,343-line command dispatcher and 17 `core` modules. Python performs orchestration, persistence, data parsing, raster compositing, and terminal encoding. FreeType is loaded through `ctypes`; Fontconfig is accessed through executables and XML files; SQLite is accessed through Python's standard `sqlite3` module.

Important paths:

- `cefont/main.py`: argument parser, handlers, validation, presentation, command routing.
- `cefont/core/`: font operations, metadata, persistence, rendering, terminal integration, and auxiliary managers.
- `bin/cefont` and `bin/font-manager-cli`: executable Python launchers that add the repository root to `sys.path` and call `cefont.main.main()`.
- `data/font-manager-vendor.h` and `data/font-manager-license.h`: C header-shaped lookup data read as text by Python, not compiled.
- `data/orthographies/`: C initializer-shaped script definitions and an inherited format/contribution document.
- `pyproject.toml`: setuptools packaging and two console entry points, both targeting `cefont.main:main`.
- `README.md` and `DOCUMENTATION.md`: installation instructions, command references, architecture claims, formats, and operational guidance.
- `.gitignore`: ignores bytecode, egg metadata, build output, and pytest cache. No checked-in CI configuration, test directory, dependency lockfile, formatter/linter configuration, Makefile, Meson configuration, or native build target was found.

**Verified — source:** packaging declares Python `>=3.8`, setuptools `>=61.0`, distribution version `1.0.0`, and GPL-3.0-or-later metadata. `config.VERSION` is also `1.0.0`, but `cefont/__init__.py.__version__` remains `0.9.4`. The README links a `LICENSE` file absent from this checkout; no top-level license text was found.

**Inference:** statements in existing documentation such as “100% feature parity,” “zero performance penalty,” universal terminal compatibility, and guaranteed recovery exceed the evidence available here. They should be treated as project claims, not acceptance results. No benchmark or upstream parity suite was found.

## 2. Architecture and component map

### Entry and control flow

**Verified — source:** both repository launchers import `main()`. Installed console scripts use the same function. `main()` calls `build_parser()`, parses arguments, handles compatibility flags, then dispatches to `cmd_*` handlers through `cmd_map`. There is no GUI event loop, window creation, or graphical-session initialization.

The command families are `list`, `info`, `enable`, `disable`, `keep`, `status`, `update`, `install`, `remove`, `collections`, `sources`, `aliases`/`substitutions`, `rendering`, `display`, `charmap`, `preview`, `compare`, `set`, `playground`, `test`, `orthographies`, `actions`, `google`, `export`, and `import`. Global compatibility flags include `--about`, `--list`, `--list-full`, `--update`, `--install`, `--enable`, `--disable`, and `--keep`. JSON output exists for selected commands, not as a universal interface.

The dependency direction is broadly:

```text
launchers / setuptools console scripts
  -> main.build_parser -> main.cmd_* handlers
     -> db.FontDatabase / module-level database functions
        -> fc-list + directory walks
        -> freetype_backend -> libfreetype.so.6
        -> data parsers + sqlite3
     -> fontconfig XML writers -> fc-cache
     -> installer / sources / collections / comparison / actions / backup / google_fonts
     -> terminal views -> renderer -> FreeType -> RGB -> terminal encoders
     -> terminal_tester -> emulator settings/configuration/OSC + recovery state
```

### Persistent state and resources

**Verified — source:** `core/config.py` reads XDG environment variables at import time. Defaults are:

- Data: `~/.local/share/fonts/`, including `Google Fonts/`.
- Configuration: `~/.config/cefont/`, containing `Collections.json`, `Sources.xml`, `PinnedComparisons.json`, `Comparisons.json`, and `Actions.json`.
- Cache: `~/.cache/cefont/cefont.sqlite`, Google catalog `gfc-<sort>.json`, and terminal journal `live_test_recovery.json`.
- Shared Fontconfig configuration: `~/.config/fontconfig/conf.d/`, including `78-Reject.conf`, `09-Directories.conf`, `39-Aliases.conf`, `19-DefaultProperties.conf`, and `19-DisplayProperties.conf`.

`ensure_directories()` creates user directories and opportunistically copies the legacy `font-manager/font-manager.sqlite` and `font-manager/Collections.json` when their CeFont equivalents do not exist. Copy errors are swallowed. Other legacy configuration files are not migrated here. `LOCALE_FILE` and `SYSTEM_FONT_DIRS` are defined, but the main discovery path does not iterate `SYSTEM_FONT_DIRS`.

`data.py` resolves resources relative to the package's grandparent directory, expects a sibling top-level `data/`, and caches parsed structures in module globals. It does not use `importlib.resources`.

## 3. Inherited versus fork-specific components

### Reliable history boundary

**Verified — source/Git:** only the CeFont `origin` remote is configured: `https://github.com/turtleion/CeFont.git`. The repository is not shallow. The available history has 13 commits, beginning with `b65f405` on 2026-10-28, an initial standalone `font-manager-cli` application. No earlier GUI import history, upstream remote, or pinned upstream base revision is available locally. Local remote-tracking refs were inspected without fetching.

The public upstream project can be identified as [FontManager/font-manager](https://github.com/FontManager/font-manager). Its inspected [Meson configuration](https://github.com/FontManager/font-manager/blob/master/meson.build) declares C/Vala and dependencies including GTK4, GLib, HarfBuzz, PangoFT2, Fontconfig, FreeType, SQLite, and libxml2. This establishes that the upstream build architecture differs from this checkout. It does **not** establish a common ancestor or CeFont's exact source baseline. These URLs reference a moving branch, viewed during discovery, rather than a verified fork-base commit.

**Unknown:** exact upstream revision, byte-for-byte provenance of copied data, original porting decisions, and complete GUI feature parity. Attempts to inspect three additional upstream raw files returned cache-miss fetch errors. No speculative upstream function-by-function diff is presented.

### Apparent inherited material and conventions

**Verified — source:** bundled headers retain `FontManager*` identifiers, GLib includes, and generated-file comments referring to a missing `build-aux` directory. Orthography documentation attributes the original data to Fontaine and Edward H. Trager. Python docstrings explicitly reference Font Manager metadata tables, installation naming, synchronization, and JSON/XML conventions. The database has `Fonts`, `Metadata`, `Panose`, and `Orthography` tables with Font Manager-style field names. Compatibility flags and legacy migration paths remain.

**Inference:** lookup data, orthographies, schema conventions, file naming, and some behavioral requirements are inherited or adapted from Font Manager. This is stronger evidence of data/format lineage than of retained GUI source. GLib includes in text assets do not create a compiled GLib dependency.

### Changes demonstrable within CeFont history

**Verified — Git:** comparing the initial CLI commit with HEAD establishes:

- `f3353b0`: rebranding from `font_manager_cli` to `cefont`, an added launcher, documentation, and changed branding/configuration paths. The current legacy migration logic is visible in `config.py`.
- `83ef6d5`: added `renderer.py` and terminal image preview integration.
- `aac1eae`: Sixel encoder compression and dimension-related changes.
- `74cc588`: added `terminal_tester.py` and `test_payloads.py` for live testing and restoration.
- `4681c8c` and subsequent commits: direct `set`, kgx behavior, proportional-font notices, and system GSettings invocation changes.
- `449044d`: minimal playground mode.
- `421ff4a`, `065162f`, and `3fda999`: ancestor-based terminal detection, xterm family handling/Xft mode checks, and symbol-font suitability/showcase behavior.

`git log -- data` shows only the initial commit for the bundled data. Most pre-existing core modules differ from that first CLI snapshot principally through namespace and branding changes. These are verified **within-fork** comparisons, not verified differences from the GUI upstream.

**Verified — source:** no legacy windows, GTK widgets, Vala controllers, GUI resources, or desktop application entry points remain in the tracked current tree. Retained graphical concepts include an action icon field and shared settings formats; neither launches a GUI by itself.

## 4. Font management subsystems and execution flows

### 4.1 Discovery, family/style identity, and search

**Verified — source:** `db.scan_available_fonts()` executes `fc-list -f` requesting file, face index, family, style, weight, slant, width, and spacing. It returns dictionaries, skips unreadable/nonexistent files, and deduplicates by `(filepath, findex)`. For comma-separated family/style names, it retains the first name.

It additionally walks `USER_FONT_DIR` and directories loaded from `09-Directories.conf`. This fallback recognizes `.ttf`, `.otf`, `.ttc`, `.woff`, and `.woff2`, extracts face zero, and supplies default classification values. It does not walk every configured system directory independently of Fontconfig. `fc-list` failure is silently ignored, so partial fallback results can look like a successful inventory.

`scan_and_index_directory()` recognizes a wider extension set, including `.otc`, `.dfont`, `.pfa`, and `.pfb`, and iterates faces when the metadata file type identifies a collection. Directory indexing hardcodes spacing/slant/weight/width differently from the Fontconfig path. Family names from SFNT metadata may also differ from the first Fontconfig name.

`get_all_families()` returns case-insensitively sorted distinct database families. `get_fonts_by_family()` uses exact family equality, joins metadata by file/index, and sorts by weight/slant/style. `main.cmd_list()` performs case-insensitive substring search in Python and filters against the local reject-family set. These queries read the application's index; they do not automatically scan a fresh system.

### 4.2 Metadata extraction and inspection

**Verified — source:** `freetype_backend.extract_font_metadata(filepath, index)` opens a FreeType face and returns a dictionary or `None`. Inputs are a readable path and face index. Output includes family/style, SFNT name strings, glyph count, PostScript name, designer, license fields, vendor, file type, file size, SHA-256, owner classification, Panose, embedding flags, and face count.

It depends on `libfreetype.so.6`, hand-declared ctypes structures, direct OS/2 table memory reads, filesystem access, and `data.get_vendor()`/`match_license()`. SFNT names are chosen using the longest available string, rather than a locale-aware name-selection policy. The owner flag reflects writability, not actual Unix ownership. Installation checks for variable-font fields that this extractor does not emit; variable-axis discovery is not established.

`get_all_codepoints()` iterates the current FreeType charmap and returns a set. `db.get_detailed_font_info()` resolves a database file/index or a representative face of an exact family, joins all four tables, and may extract/cache missing metadata. For an existing path absent from the index, it falls back to direct metadata extraction. Raw and database dictionaries use different keys, such as `n-glyphs` versus `n_glyphs`; several handlers explicitly accommodate both.

### 4.3 Caching and indexing

**Verified — source:** `get_db_connection()` creates directories, opens SQLite, creates tables and nonunique indexes, and returns rows as `sqlite3.Row`. `FontDatabase` is a thin proxy around module-level functions and additionally holds an initial connection; most methods open their own connections.

`main.cmd_update()` calls `sync_database`, an alias for `update_database()`:

1. Optionally clear metadata/Panose/orthography on `force=True`.
2. Read known metadata file paths, clear `Fonts`, and enumerate available fonts.
3. Insert font faces and extract metadata, Panose, and complete orthography support for previously unknown paths.
4. Commit in batches of 200, invoke an optional progress callback, then `VACUUM`.

Its outputs are `(total_fonts_indexed, new_metadata_extracted)`. Known metadata is keyed by path, without content-change or per-face invalidation. Deleted-file metadata is not globally pruned by a normal sync. `scan_and_index_directory()` adds records without first deleting matches; `INSERT OR REPLACE` has no unique file/index constraint to trigger replacement.

**Verified — runtime:** a fresh database contained zero families. Indexing one copied system font twice returned one face each time but increased its directory row count from one to two. No real user database was touched.

**Inference:** repeated source/install indexing can duplicate rows; joins may amplify duplicates. Modified-in-place fonts and later faces of collections can retain missing/stale metadata under normal synchronization. Clearing `Fonts` before the rebuild and batch commits also mean synchronization is not an all-or-nothing operation.

### 4.4 Installation and removal

**Verified — source:** `main.cmd_install()` routes paths to `FontInstaller.install_font()` or `install_archive()`. `get_suggested_installation_path()` derives `<USER_FONT_DIR>/<Vendor>/<Filetype>/<Family>/<Family>_<Style>.<ext>` from sanitized metadata. A metadata failure falls back to `Misc/<basename>` rather than rejecting the file. Files are copied with `shutil.copy2`, their target directory indexed, and `fc-cache` invoked. Single-file output is `(success, message, destination)`; archive output is `(installed_count, errors)`.

`install_archive()` extracts ZIP/TAR input into `TemporaryDirectory`, walks recognized font extensions, and installs each separately. Declared archive extensions and CLI routing are inconsistent: `.xz` is declared in the installer but omitted from the handler's archive suffix list. Cache refresh can happen once per installed file.

`remove_font(identifier)` accepts a file or database family. It applies a string-prefix check against `USER_FONT_DIR`, removes eligible files and empty parent directories, deletes database records, and refreshes Fontconfig. Outputs are `(success, message)`; partial removal can precede a failure. No root/system installation service is implemented.

### 4.5 Activation, substitution, rendering preferences, and sources

**Verified — source:** `fontconfig.disable_families()`, `enable_families()`, and `keep_only_families()` update family-based `<rejectfont>` patterns in `78-Reject.conf`. Disabling preserves files; enabling removes reject entries. `keep` replaces the reject set with indexed families other than those requested. These operations return counts/sets and refresh Fontconfig.

`_format_xml_and_save()` writes XML with the Fontconfig DTD and CeFont header. `load_aliases()`/`set_alias()`/`remove_alias()` maintain preferred/accepted/default substitutions in `39-Aliases.conf`. Rendering/display load/save/reset functions maintain antialiasing, hinting, subpixel geometry, DPI, and related properties in the two `19-*` files. These are configuration for Fontconfig clients; the preview rasterizer does not read them to reproduce all desktop rendering settings.

`SourceManager` normalizes paths, stores source registrations in `Sources.xml`, mirrors active directories into `09-Directories.conf`, and indexes added/enabled directories. `load_sources()` returns path/active/exists/count dictionaries. Removal can purge index entries without deleting font files. Disabling removes the active Fontconfig directory but does not immediately purge existing database records.

**Inference:** displayed “enabled” means absence from CeFont's local reject file, not a full Fontconfig resolution test. Other system/user rules and client caches can affect actual availability. A subsequent full sync can lose disabled system families if `fc-list` excludes them, while fallback user-directory walks can retain disabled user fonts. This interaction needs controlled validation.

### 4.6 Collections, orthographies, comparison, network, actions, and backup

**Verified — source:** the remaining managers are implemented, not just planned:

- `collections.py`: `load_collections()`/`save_collections()` persist nested list/dictionary JSON. Create/delete/rename and family membership functions operate recursively. `get_all_families_in_collection()` includes child collections; enable/disable delegate to Fontconfig and state is derived from rejected families. `CollectionManager` supplies CLI-facing wrappers.
- `orthography.py`: `list_available_orthographies()` exposes parsed definitions; `analyze_font_orthographies(family_or_file, index, filter_lang, min_coverage)` resolves a face, intersects its codepoints with required sets, and returns percentages, missing samples, pangrams, and support flags. The database synchronization path stores only fully covered definition names and a sample string.
- `comparison.py`: `ComparisonManager.add_comparison(label, identifiers)` resolves faces where possible and writes timestamped records to `PinnedComparisons.json`; loading can fall back to `Comparisons.json`. CLI comparison delegates presentation to `terminal.render_comparison_view()`.
- `google_fonts.py`: `GoogleFontsManager` fetches a catalog with `urllib.request`, caches JSON for two days, filters by name/category/subset, reports directory-based installed status, downloads variants into `Google Fonts/<Family>/`, indexes results, and refreshes Fontconfig. Methods return lists/dictionaries or success/message/count tuples. An embedded base64-encoded API credential exists in source; validity/quota/ownership were not tested or reproduced here. Per-variant download errors are swallowed, permitting partial success.
- `actions.py`: `ActionManager` stores action definitions, resolves file/family/style tokens, quotes replacements with `shlex.quote`, then launches a constructed command with `shell=True`, either in background or capturing output. The configured action is executable shell content; a foreground nonzero exit is not translated into a false success result. Actions may launch GUI programs at user choice, but the manager has no required GUI framework.
- `backup.py`: `export_backup()` copies chosen JSON/XML/settings/fonts into a directory or ZIP/TAR archive; `import_backup()` extracts/copies them, refreshes Fontconfig, and forces a database rebuild. Outputs are `(success, message)`. Export includes all `.conf` files in the user's Fontconfig `conf.d`, not only CeFont's five named files. Restore overwrites matching targets and is not transactional.

### 4.7 Filesystem and error handling boundaries

**Verified — source:** operations use standard `os`, `pathlib`, `shutil`, `tempfile`, `tarfile`, `zipfile`, JSON, and ElementTree. There is no central filesystem service, structured domain error hierarchy, or logging framework. Many manager methods return boolean/message tuples; others raise, print, or silently return empty data. Database metadata failures, malformed configuration, unavailable `fc-list`, and cache-refresh failures can be swallowed.

`trigger_fontconfig_cache()` discards output and does not enforce a successful return code. Some CLI handlers exit explicitly on errors; install/remove handlers can print failures while returning normally. The launchers and direct `main.py` execution catch `BrokenPipeError`; setuptools-generated entry points target the bare `main()` function and do not contain that wrapper.

## 5. GUI dependency and coupling analysis

### Reusable

**Assessment — inference:** most core font logic is already suitable for a CLI or headless library:

- `data.py`, `freetype_backend.py`: lookup loading, metadata, and codepoint extraction.
- `db.py`: enumeration, indexing, and querying, subject to correctness/state-management risks above.
- `fontconfig.py`, `installer.py`, `sources.py`, `collections.py`: management operations, subject to explicit filesystem side effects.
- `orthography.py`, `comparison.py`, `google_fonts.py`, `backup.py`, `actions.py`: domain operations and persistence; their global configuration and error contracts limit composability but do not require a GUI.
- `renderer.py` bitmap generation and protocol encoders: operate in memory without display surfaces.

### Requires separation

**Assessment — inference:** useful logic is coupled to command presentation and environment-specific integration, rather than inherited GTK controllers:

- `main.cmd_*` mixes family resolution, business decisions, validation, ANSI output, and exit policy; `cmd_test()` and `cmd_set()` are particularly substantial orchestrators.
- `terminal.py` mixes table formatting, text fallback, and image preview calls. `renderer.py` combines rasterization, terminal-size heuristics, transport selection, and formatted symbol-showcase output.
- `terminal_tester.py` combines detection, external programs, desktop configuration, journaling, process signals, and restoration. Its adapter operations could be reused, but assume a particular running emulator/session.
- `test_payloads.py` combines curated text, image fallback, stdout control, alternate-screen sequences, and blocking input.
- `config.py` and core constructors use process-global paths and create directories/migrate state. Read-looking operations can initialize state, complicating library embedding and isolated tests.

### GUI-specific

**Verified — source:** no GTK/Qt widgets, UI controllers, graphical event handlers, GObject application lifecycle, Cairo/Pango surface creation, or required X11/Wayland initialization was found. There is no GUI-specific application subsystem to identify for deletion.

Some **optional desktop integrations** require facilities normally associated with a graphical session: kgx/GNOME Terminal use `gsettings` and installed schemas/settings backends; xterm detection can invoke `xrdb -query`; live terminal operations require the target emulator. Those requirements belong to font switching, not discovery, database access, or in-memory rendering.

## 6. Reusable core and architectural boundaries

**Inference:** the strongest reusable boundaries are metadata/codepoint extraction, font inventory persistence, Fontconfig configuration, file installation, and bitmap/encoding functions. A future CLI effort would improve this existing implementation rather than first extract logic from a GUI tree.

The interfaces are mostly simple Python paths, face indexes, lists, dictionaries, sets, and tuples. There is no shared typed font model, dependency-injection layer, background worker system, database migration framework, or stable public library API. Global XDG-derived constants and the FreeType singleton bind operations to process state. These are preliminary observations, not instructions to refactor.

## 7. Rendering and terminal preview feasibility

### Existing rasterization pipeline

**Verified — source/runtime:** font rasterization already works headlessly on the sampled environment. `renderer.render_text_bitmap(filepath, text, pixel_size, face_index, ...)` and `render_glyph_bitmap()` use FreeType to load/rasterize glyphs and compose RGB bytearrays. Text returns `(width, height, rgb)`; glyph rendering also returns metrics. `encode_png()` generates PNG bytes with `struct` and `zlib` without image-library dependencies.

`render_font_preview_image()` and `render_glyph_preview_image()` select an encoder and return terminal output strings. Implemented output paths are Kitty PNG chunks, iTerm2 inline PNG, Sixel with 16 grayscale shades/RLE, and 24-bit ANSI Unicode half blocks. `freetype_backend.render_glyph_ascii()` supplies a separate shade-character raster approximation. `terminal.render_waterfall()` and `render_comparison_view()` render actual font outlines when given a valid file and non-text format; text fallback prints ordinary strings. Font sizes passed to FreeType are pixels even where labels say “pt.”

`detect_terminal_protocol()` uses `TERM`, `TERM_PROGRAM`, Kitty variables, and iTerm hints. It maps names to protocols; it does not negotiate capability, inspect multiplexer passthrough, or prove successful display. `render_symbol_font_showcase()` samples available symbol/PUA glyphs into a small raster grid. Its routing only explicitly handles Kitty and Sixel; other selections use blocks, despite general iTerm2 support elsewhere.

### Native text versus image previews

**Verified — source:** `render_charmap_grid()` prints Unicode characters, and `--format text` prints sample strings. Those are drawn by the terminal's currently configured fonts/fallbacks; choosing a CeFont file does not make ordinary ANSI text use that face or the labeled size. A grid can indicate target-font coverage while visually showing a different font.

ASCII shade glyphs and Unicode half blocks approximate pixels rasterized from the requested font. They can therefore convey its shape without asking the emulator to use it as the text font, at reduced resolution and dependent on terminal cell geometry/color support. Kitty, iTerm2, and Sixel transport image pixels rather than switch the terminal's font.

### Live switching is a separate capability

**Verified — source:** `terminal_tester.detect_terminal_emulator()` checks SSH/virtual console, Linux `/proc` ancestry, and environment hints. `apply_terminal_font()` implements kgx, GNOME Terminal, Alacritty, Kitty, and xterm adapters. Detection also names WezTerm, Konsole, and Foot, but the apply dispatcher returns unsupported for them; descriptive labels are not implemented adapters.

kgx/GNOME Terminal use system `gsettings`; Alacritty uses regex-based config editing/backups; Kitty attempts remote control then config append/reload through `SIGUSR1`; xterm inspects Xft-related process arguments/resources and writes OSC 50. SSH, Linux VT, and unsupported terminals use a preview fallback. `check_font_terminal_suitability()` checks printable ASCII before swapping and routes symbol fonts to raster showcase output.

`TerminalFontContext` applies changes, records recovery data, registers `SIGINT`/`SIGTERM`/`SIGHUP` and `atexit` cleanup, then restores unless `keep()` is chosen. Explicit recovery is available through `test --recover`. Recovery is called when entering this context, not universally on every application startup. `run_minimal_playground_session()` only prints a prompt; an unsupported/mock playground path does not itself show the raster fallback used by preset test mode.

### Limits and feasibility assessment

**Inference:** existing in-memory raster functions and encoders provide substantial infrastructure for terminal image previews. No final protocol choice is required at discovery time. No external image-rendering utility, Pillow, ImageMagick, terminal graphics package, curses, Rich, or Textual dependency was found.

The text renderer loads characters individually and advances them without HarfBuzz shaping, bidi layout, explicit kerning, or fallback-font composition. Complex scripts, ligatures, combining marks, and missing-glyph behavior need investigation. Bitmap reads assume one coverage byte per pixel and do not branch on pixel mode, leaving monochrome/color/embedded-bitmap fonts uncertain. Codepoints are read from the active charmap without an explicit Unicode charmap selection.

Both raster paths read the face's glyph pointer at a hardcoded byte offset of 152; legacy ASCII rendering also uses hardcoded glyph bitmap offsets. Native structures and OS/2 fields assume ABI/layout details, so success with one font on this host does not establish portability. Unsafe native-memory mistakes can terminate the process outside Python exception handling. The legacy ASCII path was not exercised.

**Unknown:** actual display compatibility across emulators, SSH/multiplexers, 32-bit platforms, color/variable/collection fonts, unusual bitmaps, and large preview workloads. Encoder smoke results validate construction, not visible terminal output or typography fidelity.

## 8. Build, dependencies, tests, and validation

### Build and development setup

**Verified — source:** documented source execution is `./bin/cefont --help`; editable installation is `pip install -e .`. Setuptools console scripts expose both executable names. A distribution build would use the setuptools PEP 517 backend; this repository has no C compilation step or Meson project. No installation/build was performed because it could introduce dependencies or repository metadata.

There are no declared Python runtime dependencies. Runtime nevertheless relies on standard-library modules including `ctypes`, `sqlite3`, XML, archives, urllib, and zlib, plus the separately installed FreeType shared library and Fontconfig tools. `fc-match` is used for xterm name normalization. Desktop switching optionally requires `gsettings`, schemas/backend access, Kitty command support, emulator configuration/reload behavior, or `xrdb`. Linux sonames, `/proc`, POSIX signals, user font paths, and terminal escape sequences make Linux the practical target. Cross-platform Python alone is insufficient.

**Inference:** resource installation needs verification: setuptools discovers `cefont*` packages, while the `data/` directory is outside the package and `data.py` expects repository-relative resources. Package-data globs alone do not establish that a built wheel will contain assets at the required location. Source-launcher success does not prove wheel correctness.

**Verified — source:** `fontconfig.py` annotates two return values with `Tuple` but imports only `Dict`, `List`, `Optional`, and `Set`. It does not enable postponed annotations. This is a compatibility issue for the declared older Python range where annotations are evaluated at definition time. The available Python 3.14 smoke run passed; older-interpreter execution remains untested. Other annotations use `any` rather than `typing.Any`, a separate contract-quality issue.

### Existing tests and fixtures

**Verified — source:** no automated tests, test runner settings, CI checks, benchmark harness, or bundled sample font binaries were found. `test_payloads.py` and the `test` command are interactive visual font exercises, not automated tests. Bundled orthographies/vendor/license files are reference data, not comprehensive fixtures.

### Commands/checks performed

All Python invocations used `-B`/`PYTHONDONTWRITEBYTECODE=1`. CLI/core subprocess checks redirected XDG data/config/cache paths into disposable `/tmp/cefont-discovery-*` directories and removed `DISPLAY`/`WAYLAND_DISPLAY`. Temporary database/fixture creation exercised application code without changing real user settings. Full host indexing and Fontconfig-writing operations were not run.

- `git status --short`, remote/log/tree inspection, `git diff --stat b65f405 HEAD`, `git log -- data`, and `git rev-parse --is-shallow-repository`: succeeded; established the history boundaries above.
- `ast.parse()` over all 19 Python files: passed. This checks syntax, not older-Python compatibility or behavior.
- `python3 -B bin/cefont --help`: exit 0, all command groups available without a graphical display.
- `python3 -B bin/font-manager-cli --about`: exit 0, reports CeFont 1.0.0 and isolated XDG paths.
- `python3 -B -m unittest discover -v`: ran zero tests; exit 5 on Python 3.14.9, output `NO TESTS RAN`. This is absence of coverage, not a passing test suite.
- Direct data loaders: 127 orthographies, 1,531 vendor-map entries, zero notice entries, and 23 licenses. Existing docs repeatedly claim 128 orthographies. No data files were edited.
- Headless smoke using `fc-match -f '%{file}' monospace` selected `/usr/share/fonts/noto/NotoSansMono-Regular.ttf`. Metadata returned Noto Sans Mono / Regular, 3,920 glyphs; cmap enumeration returned 3,490 codepoints.
- `render_text_bitmap(..., 'Ab 012', 16)` returned a nonempty 64×39 RGB image (7,488 bytes); `render_glyph_bitmap(..., 65, 24)` returned a nonempty 22×25 RGB image (1,650 bytes). PNG output had the expected signature; Kitty, iTerm2, Sixel, and block encoders produced nonempty strings kept in memory.
- Basic Latin analysis of that font returned coverage `100.0` but `fully_supported=False`. `data.load_orthographies()` stores a filename string in `key`, whereas analysis tests that key against integer codepoints. This mismatch is directly visible in source and explains the sampled false support flag.
- A copied sample font indexed in an isolated temporary directory returned one face per scan, with directory row counts one then two. This confirms non-idempotent indexing on that fixture.

Host tools available included Python 3.14.9, pip, `fc-list`, `fc-cache`, `fc-match`, Meson, Ninja, `gsettings`, and `xrdb`; pytest was not available. Library lookup found `libfreetype.so.6`, `libfontconfig.so.1`, and `libsqlite3.so.0`. Finding an optional tool/library does not prove CeFont directly binds it or requires it for every command.

### Checks intentionally not performed

No dependency installation, wheel build, live terminal font switching, settings modification, Google API call/download, real font install/removal, backup restoration, GUI launch, or full system database rebuild was performed. Archive safety, real migration/schema compatibility, crash restoration, visual protocol support, and performance therefore remain limited to source analysis. Upstream file fetch failures were recorded rather than worked around by guessing provenance.

## 9. Architectural risks and technical limitations

These findings describe existing behavior and investigation priorities; no repairs were attempted.

1. **Native memory layout assumptions — verified source; portability impact inferred.** Hardcoded offsets and custom ctypes structures can misread memory. OS/2 extraction reads raw offsets from a FreeType-returned structure and requires validation against the actual native layout. Python exception handling does not make invalid pointer reads safe.
2. **Index integrity — runtime reproduced.** Directory scans duplicate rows; normal cache reuse is path-based, and tables lack unique file/index keys. Multi-face metadata, stale deleted files, and interrupted synchronization warrant focused validation.
3. **Data fidelity — runtime/source evidence.** Basic Latin support is incorrectly false despite complete coverage. The notice loader's regex stops at a closing brace inside a multi-entry initializer; zero notice records were parsed. Orthography parsing pairs hexadecimal values without interpreting every individual-codepoint/range sentinel described by the format; irregular definitions may be misread. The extent of affected data is unknown.
4. **Terminal recovery guarantees exceed implementation — verified source.** The journal is written after applying the font, leaving a failure window before recoverable state is saved. Cleanup and orphan recovery clear the journal even when restoration returns false. Kitty's successful remote-control path does not capture the original font, and its restore function does nothing for that method. A fixed journal/backup name and no session coordination also create concurrent-run risks. These paths were not fault-injection tested.
5. **Filesystem containment and archives — verified source; exploitability untested.** Removal uses `startswith(USER_FONT_DIR)` rather than a resolved directory-boundary check; sibling prefixes and symlinks need investigation. Archive paths call `extractall()` without an explicit application-level containment policy; TAR behavior depends on Python version/default filters. Backup import identifies font paths using substring matching, and Google uninstall joins an identifier into a directory path without an explicit containment check. No hostile-input execution was attempted.
6. **Overwrites and partial operations — verified source.** Install can overwrite an existing destination; metadata failure still permits copying arbitrary input to `Misc`. Backup export uses a predictable `.staging` directory and can remove an existing `fonts` subtree there. Import overwrites configuration/fonts, while install/download/remove/restore are not transactional.
7. **Database deletion scope — verified source.** `delete_fonts_by_paths()` deletes with substring `LIKE '%path%'`; directory queries use SQL `LIKE` without escaping wildcard characters. Prefix overlaps and `%`/`_` in paths can match unintended records.
8. **Shared configuration ownership — inference from writes.** CeFont generates fixed Fontconfig filenames also associated with upstream conventions. It does not merge arbitrary externally edited XML when regenerating files. Coexistence with Font Manager or manual configuration needs explicit round-trip testing.
9. **Silent errors and automation contracts — verified source.** Broad exception suppression, inconsistent exit handling, and no common error format can make partial discovery or failed mutations appear successful. JSON output availability is uneven. Normal table widths use character counts rather than terminal display-cell widths.
10. **Distribution/support claims — source limitations.** Resource packaging, Python 3.8 support, license text/provenance, and version consistency are unresolved. Embedded API credential usability and network error reporting need review. No automated regression suite protects management or native rendering behavior.

## 10. Unresolved questions

- What exact Font Manager revision and asset-generation process were used? Can original porting notes or upstream objects establish provenance and schema compatibility?
- Which existing CLI behaviors are intended acceptance requirements, and which advertised parity claims have been independently tested?
- Which Python versions, CPU ABIs, FreeType builds, and font formats are supported? Does a clean wheel include every required resource?
- How should disabled fonts remain discoverable, and should disabling a source hide it from CeFont's index immediately or only from Fontconfig clients?
- What is the intended identity/invalidation rule for file replacements, collection faces, variable instances, and duplicate families?
- Which orthography definitions are parsed accurately, and how should key codepoints, singleton codepoints, and explicit ranges be represented?
- Which terminals actually accept the emitted protocols and live-switch commands, under local sessions, SSH, tmux/screen, and redirected output?
- How should recovery handle partial application, failed restoration, concurrency, changed terminal profiles, or an already-running session?
- Are arbitrary input archives/actions trusted? What overwrite, containment, and backup scope contracts are intended?
- Which preview fidelity requirements matter: simple Latin outlines, shaping, kerning, complex scripts, color glyphs, variable axes, or exact desktop rendering settings?

## 11. Recommended areas for further investigation

These are follow-up discovery/validation areas, not authorization to implement changes:

1. Establish upstream/data provenance and inspect actual legacy migration samples before relying on compatibility guarantees.
2. Validate ctypes layouts and raster formats across supported ABIs and font fixtures; distinguish native crashes from recoverable errors.
3. Characterize index invariants with repeated scans, changed/deleted files, disabled families, custom sources, and font collections.
4. Compare all parsed orthographies and vendor notice records against their source initializer formats and expected coverage results.
5. Examine recovery ordering and adapter semantics using controlled emulator profiles; test failure and concurrency cases without risking normal user profiles.
6. Inspect built distribution contents in a disposable build workspace and test declared Python versions without altering this checkout.
7. Define terminal preview acceptance criteria and assess existing image/text/live-switch paths against those criteria before choosing any final approach.
8. Review archive/path containment, overwrite rules, error contracts, and shared Fontconfig ownership before expanding mutation workflows.

The immediate architectural conclusion is that CeFont already contains a reusable headless font-management core and a working sampled FreeType-to-terminal-image pipeline. The principal uncertainties are correctness, portability, provenance, packaging, rendering fidelity, and terminal-state safety. Discovery stops here; no implementation work has been started.
