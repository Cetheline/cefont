# CeFont Bug Audit

Last updated: 2026-11-12 (Asia/Jakarta). Original audit: 2026-11-09, revision `3fda999` (`master`).

The active list tracks **open findings only** after Batches 1–5. Twenty-five resolved findings have been removed from that list; completion records and historical evidence appear below. Finding IDs remain stable; gaps are intentional. The remaining findings retain their original evidence and confidence, rather than claiming a new full audit. Source line numbers in the original evidence refer to the audit snapshot and may differ after the fixes; function names identify the affected code.

## Current status

- **25 findings fixed** across Batches 1–5; their IDs are listed in the completion record, not the open findings.
- **2 findings remain open:** both Low and Confirmed (CF-024 and CF-025).
- **All eight former Medium findings are resolved:** CF-017–023 and CF-026. The remaining configuration portion of CF-022 is now covered by locking, snapshot conflicts, and atomic publication.
- **Latest verification:** 142 tests passed on each of Python 3.11.16 and 3.14.9, including 41 added regression cases. Wheel/resource verification and the earlier batches also pass.
- **Next priority:** CF-024 argument validation, then CF-025 version consistency. Neither was changed in Batch 5.

## Scope, methodology, and discovery validation

The following methodology describes the original audit, before the subsequent fixes and regression tests. During that audit, only this report was created. Application code and existing documentation were preserved. No dependencies were installed, no fonts were installed or removed, and no actual user font directories or system Fontconfig settings were modified. Application deletion calls were mocked. SQLite writes used disposable databases; configuration writes used temporary fixtures. The wheel was built from a copy under `/tmp` using already-installed setuptools and was unpacked for inspection, not installed.

`docs/CODEBASE_DISCOVERY.md` describes the original application architecture; its metadata, packaging, and annotation findings have since been addressed. CeFont already implements a headless Python CLI, FreeType rasterization, terminal image encoding, Fontconfig XML management, persistence, installation/removal, Google catalog operations, and live terminal adapters. GUI conversion and terminal preview are not merely planned features. No GTK/Qt initialization was found. WezTerm/Konsole/Foot live-switch labels still exceed the implemented adapters. After Batch 3, Kitty/xterm mutation is explicitly disabled; image previews remain available, while KGX/GNOME and validated Alacritty changes use the recovery lifecycle.

This audit strengthens several discovery hypotheses: missing wheel resources, wrong metadata, indexing duplication, orthography failures, and recovery failure paths are now directly tested. It also narrows the native-layout concern: both the face glyph offset and glyph-slot bitmap offset equal 152 on this host, so those offsets are **not** demonstrated broken here. The metadata discrepancy instead comes from a wrong FreeType table enum. Discovery's approximate OS/2-layout hypothesis should not be substituted for that verified root cause.

Source review covered all 19 Python files, launchers, packaging, data formats, and cross-module flows. No automated test suite or relevant glossary/ADR was found. Python 3.14.9 was the available interpreter. Verification used `python3 -B` heredoc harnesses, `unittest.mock`, temporary XDG roots, read-only `fc-match`, raw SFNT table parsing with `struct`, and a temporary wheel build. No emulator reloads, actual GSettings writes, shell actions, or Google network requests were executed. Secrets were not decoded or included in this report.

The diagnosis workflow was adapted to a broad audit: each tested hypothesis used a small function/CLI probe rather than a single user-reported symptom. Fixing, persistent regression-test creation, and source instrumentation were intentionally omitted under the audit-only constraint. Reproduction recipes below describe the actual probes and the isolation required; destructive paths must retain their mocks.

## Results and priorities

There are **2 open findings: 2 Confirmed, 0 Probable, 0 Unverified**. Confirmed includes deterministic mocked execution and conclusive source evidence; it does not imply destructive effects were executed on real files.

- **Critical: 0.** No demonstrated automatic system-wide compromise is claimed.
- **High: 0.**
- **Medium: 0.**
- **Low: 2** — CF-024 and CF-025.

The remaining audit work is argument validation (CF-024) and consistent version identifiers (CF-025). These counts describe tracked findings, not a guarantee that no other defects exist.

## Low severity — validation and versioning

### CF-024 — Invalid character-map arguments escape as tracebacks

**Severity / confidence:** Low / Confirmed.

**Location:** `main.py:502`, `cmd_charmap()`, integer conversions around lines 516–533.

**Expected:** a concise argument error with a nonzero status. **Actual/root cause:** arbitrary strings are parsed after dispatch, without catching `ValueError` or validating Unicode bounds.

**Reproduction:** with isolated XDG paths and the existing read-only sample font, `charmap grid <path> --start nothex` exited 1 with an uncaught `ValueError: invalid literal for int() with base 16: 'nothex'`. **Impact:** confusing failure output for invalid input. Huge ranges/sizes were not exercised, so no denial-of-service claim is added here.

**Recommended fix:** argparse converters/range checks and structured validation errors. **Regression tests:** invalid hex, negative/reversed/out-of-Unicode ranges, invalid glyph scalar, and bounded valid inputs.

### CF-025 — Version identifiers disagree

**Severity / confidence:** Low / Confirmed (source).

**Location:** `cefont/__init__.py:3` versus `core/config.py:47` and `pyproject.toml:7`.

**Expected:** package, CLI, and distribution versions agree. **Actual:** `__version__` is `0.9.4`; CLI/distribution version is `1.0.0`. **Reproduction:** inspect/import those constants and compare with `--version`. **Impact:** misleading bug reports and embedding/version checks. **Limits:** no functional font-management failure follows merely from this mismatch.

**Recommended fix:** a single authoritative version source. **Regression test:** installed package metadata, exported version, and CLI version agree.

## Areas audited without an additional confirmed finding

- **Headless initialization:** source imports/dispatch have no GTK/Qt lifecycle. Discovery's headless help/about and basic FreeType rendering results remain applicable at the same revision. GSettings/xrdb are optional live-switch integrations, not mandatory GUI startup. This does not validate every adapter on a headless host.
- **Ordinary lookup SQL:** family/path lookups use parameterized values in the inspected query functions; no direct family-name SQL injection was identified there. Batch 4 also verified parameterized literal matching for database purges.
- **FreeType face ownership:** metadata, cmap, and regular bitmap paths use `finally: FT_Done_Face`. No per-call face leak was demonstrated on those paths. `FontDatabase` keeps an unused initial connection without an explicit public close; interpreter cleanup and process lifetime prevent a conclusive persistent descriptor-leak claim from this review.
- **Standard grayscale previews:** discovery showed nonempty glyph/text RGB and encoder output for the sampled font. PNG construction and terminal encoders were inspected; no new PNG structural defect was established. Nonempty transport strings are not evidence of correct visible output on every terminal.
- **Collections and pinned comparisons:** basic CRUD/storage structures were reviewed; no separate ordinary sequential CRUD defect was established beyond empty activation and shared-state hazards. Arbitrary malformed JSON schemas and deep-recursion limits were not exhaustively exercised.
- **XML:** generation uses ElementTree/minidom rather than string interpolation of family values. No arbitrary shell invocation or external-DTD fetch was demonstrated in the XML paths. Batch 5 makes the audited configuration readers reject malformed documents before mutation; broader schema validation and external DTD behavior were not exhaustively tested.
- **Custom actions:** `shell=True` is intentional for user-configured executable content, not by itself proof of an injection vulnerability. Replacements use `shlex.quote`, but all quoting contexts were not fuzzed. Batch 5 checks foreground action exit status; a successful background spawn cannot certify the eventual child outcome.
- **Archive/runtime caveat:** Python 3.14's default TAR filter contradicts a blanket “all extractall calls are exploitable” claim. ZIP absolute/parent normalization was not tested and is not assumed broken.

## Cross-component risks and untested areas

A successful sync or directory index refreshes legacy metadata for the affected faces. Before such a refresh, lookups can still show pre-fix metadata. Batch 5 corrects orthography/vendor parsing and propagates the audited mutation failures. Database transactions do not roll back preceding font copies, deletions, or configuration changes performed by callers.

Batch 3 implements a shared capture/journal/apply/restore lifecycle with exclusive terminal ownership. Real-emulator compatibility, legacy journal recovery, storage failure behavior, and external settings edits remain bounded integration risks; Batch 5 protects cooperating JSON/XML writers. External editors, multi-file crash atomicity, and adversarial directory replacement remain separate integration limits.

Remaining investigation limits (including original audit limits):

- No real font installation/removal, live terminal switching, backup restore, hostile archive extraction, or external shell action was run.
- No malformed-font fuzzing, native crash reproduction, 32-bit/alternate ABI execution, color/bitmap/variable font matrix, or shaping comparison was performed. Batch 4 also checked a real TTC collection using a temporary copy.
- Python 3.11.16 and 3.14.9 are the available tested interpreters; Python 3.8 runtime compatibility remains unverified. Actual Kitty/GNOME/Alacritty/xterm integration, SSH/tmux/screen protocol display, and signal/crash stress testing remain outstanding.
- No Google API validity/quota/authentication, interrupted real download, catalog tampering, or network error matrix was tested. Embedded credentials remain a provenance/operational concern, not a claimed confirmed external compromise.
- A separate-process terminal lock-contention test passed in Batch 3. Batch 4 tested cache upgrades against synthetic legacy databases. Broader concurrent-writer stress tests, filesystem races, system Fontconfig interactions, production legacy cache variants, large inventories, performance limits, and upstream parity remain incompletely assessed.
- Missing `LICENSE`, stale documentation counts/help strings, unsupported adapter labels, and terminal-native text font limitations remain discovery observations; they were not inflated into extra security findings.

## Recommended fixing order

1. **Validate character-map inputs:** CF-024.
2. **Unify version identifiers:** CF-025.

Broader platform, native-font, and storage-failure testing remains useful outside these two open findings.

No real user cache was migrated or rebuilt during verification. On first use of an existing schema, CeFont saves a separate SQLite backup before applying the migration. The next successful sync or directory index refreshes the old metadata in its scope.

## Completed fixes and verification record

Removed from the open list:

- **Batch 1:** CF-001, CF-002, CF-003, CF-005, CF-027 — deletion containment, safe backup output/staging, empty-collection activation, and explicit TAR extraction policy.
- **Batch 2:** CF-004, CF-011, CF-012 — OS/2 metadata access, distribution resources, and unresolved annotation imports.
- **Batch 3:** CF-006–010 — write-ahead terminal recovery, retained failed-restoration records, disabled unverifiable Kitty switching, GSettings rollback, and validated Alacritty configuration changes. CF-022 was partially addressed for terminal journals in Batch 3; its remaining configuration cases were completed in Batch 5.
- **Batch 4:** CF-013–016 — complete discovery before publication, idempotent file/face indexing, content-aware cache invalidation, and literal purge boundaries.
- **Batch 5:** CF-017–023 and CF-026 — reference parsing and coverage, mutation exit status, rooted backup import, configuration concurrency, explicit FreeType errors, and validated raster decoding.

The recorded post-Batch-2 verification passed **39 tests on Python 3.11.16 and 3.14.9**, including Batch 1 regressions, independent metadata checks, isolated wheel/source/editable/zip loading, and sdist rebuilding. All 131 reference assets matched their original bytes in the wheel. Those results describe Batch 2. Batch 3 verification is recorded below.

Remaining boundaries of those completed fixes: older-runtime compatibility (especially Python 3.8), nontransactional filesystem deletion on late I/O failure, trusted backup-parent assumptions, archive resource budgets, and native rendering ABI/pixel-mode coverage still require attention. Resolved defects are not being reopened solely because their broader subsystems have these limits.

## Batch 3 — Terminal switching and recovery safety (2026-11-11)

### Verification boundary and modified files

Batch 3 addresses CF-006–010 and the terminal-journal portion of CF-022. Unrelated application changes from Batches 1/2 remain uncommitted in the shared working tree and were preserved. No real terminal settings, user fonts, or system Fontconfig files were changed. All GSettings commands and emulator mutations in regression tests were mocked; filesystem fixtures and recovery records were private temporary data.

Files added: `cefont/core/terminal_recovery.py`, `cefont/core/terminal_config.py`, and `tests/test_batch3_terminal.py`. Files modified: `cefont/core/terminal_tester.py`, `cefont/main.py`, and this report. No dependencies were added. Existing public operation signatures remain usable; owner tokens are now required for journal deletion, and successful direct `apply_terminal_font()` calls hold an owned session until the matching restore or process cleanup.

The initial six focused tests reproduced the failures before changes: invalid duplicate Alacritty tables, a second context deleting the first context's journal, restoration failures returning without an error, mutation before journal failure, unsafe Kitty success/fallback paths, and partial GSettings updates left in place. The initial broader run discovered 45 tests because the six new regressions were created while discovery started; its five failures/two errors were all in those six new tests, with no failures in the existing 39. It is not being represented as a clean 39-test baseline run.

### Consistent lifecycle and ownership

1. Acquire an exclusive nonblocking `flock` on a persistent lock inode next to the recovery record. Global serialization is deliberately conservative because multiple windows can share GSettings profiles or config files. Lock contention reports an error without mutation.
2. Recover any compatible orphan under that lock, or refuse mutation if recovery fails or a legacy/corrupt record requires manual review.
3. Capture and validate complete original values/bytes. Generate a unique owner token and write a versioned recovery plan before any settings change. Atomic replacement uses a unique 0600 temporary file, file flush/fsync, replace, and directory fsync.
4. Register interruption/exit handling, then apply checked changes. Any raised error, interruption, failed command status, or failed readback enters rollback. Failed rollback raises an actionable error and retains the original record.
5. Restore only values that still match original or applied state. Conflicting external edits are preserved and block automatic overwrite. Clear only the matching owner's record after checked restoration; repeated cleanup/recovery is safe. A durable keep decision is recorded before removing the record, so interrupted keep cleanup cannot later revert the user's explicit choice.

Signal handlers unwind through the rollback path rather than claiming success. Exit-time failures print an error and preserve pending recovery. The CLI reports recovery failures with exit status 1 and does not claim “kept” or “restored” for mock/image-only fallback. The recovery lock inode is never unlinked, preventing replacement-lock races among cooperating processes. Owner checks prevent stale callers clearing another session's journal. Raw JSON/XML collection/action/config writers are not covered by this lock, so CF-022 stays open for those modules.

### Verified resolutions

- **CF-006:** original code applied the font before writing its journal and registering cleanup. Capture/journal now precede mutation. Journal open/write/replace/fsync failures cause no setting writes. Interrupted initialization and post-config-replace failure enter rollback. A completed journal remains available if restoration cannot finish.
- **CF-007:** original cleanup and orphan recovery discarded records regardless of restoration results. Restoration now checks status/readback, raises on failure, and clears records only after success. Retrying after a failure, repeated cleanup, corrupt/legacy records, and interrupted keep-finalization have regression coverage. Cleanup errors distinguish restoration failure from record-cleanup failure; a successful restore followed by failed directory synchronization does not imply terminal state remains modified.
- **CF-008:** the original `kitty @ set-font` path did not capture old font/size and its restore branch performed no operation. Kitty mutation is now explicitly disabled; mocked command success or failure cannot enable it, and unknown/legacy Kitty state never reports successful restoration. No config append, reload signal, or remote font-size change is attempted. Image previews remain available.
- **CF-009:** kgx and GNOME Terminal now capture exact GVariant values and the concrete profile schema before mutation. Every changed key is checked and read back; partial failures restore all modified keys, checking all restore outcomes. Rollback failures retain sufficient values and the schema for later recovery. Readback also rejects a command that returns success without applying its value. Pre-apply and pre-restore checks refuse unexpected external edits.
- **CF-010:** regex-based edits and fixed `.cefont.bak` files were replaced with parse/generate/parse semantic validation and atomic publication. Parent, nested, dotted, inline tables, arrays of tables, escaping, numeric values, and temporal values are handled through parsed TOML data. Exact original bytes and permissions are saved in the durable recovery plan before replacement, and successful revert restores them byte-for-byte. Invalid input/output, YAML, missing parent directories, symlinks, and missing TOML-parser support fail without destructive rewrites. Existing style and unrelated settings are preserved semantically.

### Kitty and Alacritty investigation

The inspected [Kitty remote-control command reference](https://sw.kovidgoyal.net/kitty/remote-control/) documents `set-font-size`, and the [protocol](https://sw.kovidgoyal.net/kitty/rc_protocol/) documents `load-config` with overrides. Neither establishes a general snapshot/restore pair for the former arbitrary-face `set-font` invocation. Rather than equate configuration reload with restoration of unknown runtime overrides, this batch disables the font-changing path. This is a conservative product limitation, not a claim that Kitty has no possible reversible mechanism.

Temporary xterm switching is likewise disabled: inferred startup arguments/resources and writing OSC 50 are insufficient proof of the current original face or a successful restore. Permanent CLI switching uses the same safe lifecycle, so unsupported Kitty/xterm paths return errors rather than retaining the unsafe config/OSC fallback. Terminal detection and image preview remain available.

[Alacritty's configuration reference](https://alacritty.org/config-alacritty.html) specifies TOML and import precedence. The adapter preserves parsed values in a canonical valid TOML representation, including imports. **If kept, comments/formatting become canonical; revert restores exact original bytes.** The CLI displays this distinction before the user chooses keep. Live config reload behavior and effective runtime overrides are not certified by file validation. No emulator reload was exercised during tests.

### Regression tests and actual results

`tests/test_batch3_terminal.py` contains 33 focused tests covering:

- Journal creation, replacement, synchronization ordering/failure, interrupted initialization, and checked rollback.
- Successful/failed/repeated cleanup, orphan failure/retry, corrupt/legacy records, owner mismatch, keep decision durability, and two contexts sharing a cache.
- A real separate Python process attempting to acquire an active recovery lock (no settings commands in that child).
- Kitty command success/failure suppression, rejected unknown restore, disabled xterm writes, and direct apply/restore/permanent APIs.
- Both GSettings adapters' partial failure, failed rollback, nonzero statuses, ignored writes, external-edit protection, and CLI error reporting.
- Alacritty parent/nested/dotted/inline tables, arrays of tables, quotes/backslashes, NaN/date/time values, permissions/exact-byte restoration, unsupported or invalid input, invalid generated output, post-replace failure, missing parser, and externally edited files.

Focused verification: `python3 -B -m unittest tests.test_batch3_terminal -q` and `python3.11 -B -m unittest tests.test_batch3_terminal -q` each passed all 33 tests. The final added case checks that a retry synchronizes an already-restored config directory before clearing its journal. The preceding 32-case suite plus three compatibility tests also passed on Python 3.11. Application modules parse under Python 3.8 grammar, and `git diff --check` passes. Final full-suite verification after the durability retry correction:

- `python3 -B -m unittest discover -q` — **72 tests passed** on Python 3.14.9 (303.815 seconds).
- `python3.11 -B -m unittest discover -q` — **72 tests passed** on Python 3.11.16 (263.578 seconds).
- These totals include all 39 prior regressions plus the 33 Batch 3 cases; there were no skipped tests on these interpreters. Prior broader runs with the initial 32 Batch 3 cases also passed all 71 tests on both interpreters.
- Mocked behavior is not being treated as real-emulator compatibility evidence.

### Remaining limits and operational safety

- Actual KGX/GNOME D-Bus/dconf, Alacritty reload, storage/power-loss, signal-storm, and hardware integration have not been tested. Verification concerns command statuses/settings readback or exact config-file contents, not proof of rendered font selection in a running window.
- Python 3.11 and 3.14 are tested. The CLI/GSettings implementation avoids a new parser dependency; Alacritty mutation is refused when `tomllib` is unavailable, as on an ordinary Python 3.8–3.10 installation. Full older-version compatibility remains unverified. YAML and TOML syntax newer than the available parser supports are refused, preserving originals.
- Legacy version-1 records (including old Kitty/config backups) and corrupt records are retained and block new mutation until manually reviewed. They are not silently discarded or guessed into a new format. Version-2 orphan recovery remains automatic under the lock.
- Global locking protects cooperating CeFont processes. Trusted cache/config parent directories, reliable POSIX locking/fsync semantics, and lack of malicious same-user directory relocation are assumptions. External editors are checked for conflicts, but no cross-application lock makes the GSettings/config sequence globally atomic.
- Recovery metadata contains exact configuration bytes and is written as a private 0600 record. There is one outstanding global transaction at a time; different terminals are intentionally serialized. The record is removed only after successful restoration or an explicit durable keep decision. Filesystem cleanup/fsync errors are surfaced; catastrophic storage loss cannot be made recoverable by software alone.
- Recovery requiring manual intervention or an external setting conflict is reported as a failure and retains evidence. No further-batch defects were automatically fixed, and remaining audit findings above remain open.

## Batch 4 — Database integrity, discovery, and cache consistency (2026-11-11)

Batch 4 changed `cefont/core/db.py`, `cefont/core/fontconfig.py`, `cefont/main.py`, and this report. It added `cefont/core/database_schema.py` and `tests/test_batch4_database.py`. Terminal recovery behavior is unchanged. Database and filesystem writes in verification used disposable fixtures, and external command failures were mocked.

- **CF-013 (High, Confirmed):** The old sync cleared and committed the inventory before discovering fonts. Missing Fontconfig returned an apparently successful zero-font result; commits every 200 rows could also expose an interrupted rebuild. Discovery now returns a complete list or raises `FontDiscoveryError` before the database is opened. A missing, failing, or timed-out `fc-list`, invalid output, an unreadable reported font or configured source, malformed custom source XML, or a failed fallback traversal aborts the update. Successful empty discovery is valid. The inventory and cache changes are committed together; errors or interruptions roll them back. Both `cefont update` and `cefont --update` report the error and exit with status 1.
- **CF-014 (Medium, Confirmed):** A second directory scan previously inserted a second copy of each face because the replacement statement used a new row ID and the schema lacked a unique file/face key. A normalized absolute path and face index now identify records, using case-sensitive path comparisons. Unique indexes prevent duplicates in all four original tables. Upserts keep row IDs stable, directory indexing enumerates TTC/OTC base faces, and repeated scans keep counts and joins stable.
- **CF-015 (Medium, Confirmed):** The old known-file set stored paths alone. A two-face scan extracted metadata for the first face only, even when forced, and replacing bytes at the same path did not invalidate it. Cache reuse now requires a matching face, SHA-256 digest, extraction revision, and complete related rows. Full sync refreshes every applicable face when forced and removes obsolete records; directory indexing refreshes and prunes only within its scope.
- **CF-016 (Medium, Confirmed):** The old substring-based purge also removed a similarly named backup entry, and SQL wildcard characters affected directory counts and deletion. File purge now uses parameterized exact equality for the supplied stored paths. Directory count and purge compare the same absolute, separator-terminated prefix literally. Purge covers all faces and related cache rows in one transaction.

### Migration and cache policy

Schema version 1 adds the unique indexes, a `CacheState` table keyed by path and face index, and a `CeFontSchema` marker. Before changing a legacy database, SQLite's backup API writes a private, uniquely named `*.pre-migration-*.sqlite` sibling. The backup is synchronized before the transaction deduplicates rows. For each exact identity in each table, migration retains the most populated row, using the highest existing row ID to break ties. Other face indexes and unrelated tables are preserved. Rows without an identifiable path or face index are left intact during migration. The backup retains original rows, including discarded duplicates. Failure rolls back the schema changes and leaves any completed backup available; subsequent opens of a current schema do not repeat migration. Backup creation or synchronization failure aborts migration rather than proceeding without a recovery copy.

Each index or sync hashes the current contents once per file to detect replacements even if size and modification time are preserved. Metadata, PANOSE, and coverage reuse is per face. A cache revision change also invalidates old extraction results, including pre-CF-004 metadata, when the user next requests indexing or sync. The indexer checks file identity and timestamps again before publishing. A metadata or file-read failure rolls back the entire indexing transaction.

### Regression evidence

The initial 15 isolated regression cases exposed 16 assertion failures against the old code, including the original failure paths for all four findings. The malformed custom-source and named-instance regressions also failed before their respective corrections. The final Batch 4 suite contains 29 tests covering dependency errors and valid empty discovery; interrupted rebuilds past the old commit boundary; duplicate and multi-face indexing; named instances on unchanged files; cold, warm, forced, stale, and incomplete caches; same-size replacements; removed files and faces; exact and scoped purges with quotes and wildcard characters; and legacy backup, rollback, and repeat migration. The targeted run, including the six existing native metadata and rendering checks, passed:

- `python3 -B -m unittest tests.test_batch4_database tests.test_batch2_metadata -q` — **35 passed** on Python 3.14.9.

Full regression suite, including the previous batches and the wheel, sdist, editable-install, and resource checks:

- `python3.11 -B -m unittest discover -q` — **101 passed** on Python 3.11.16 (295.240 seconds).
- `python3 -B -m unittest discover -q` — **101 passed** on Python 3.14.9 (339.949 seconds).

An additional isolated `python3 -B -` integration probe copied `NotoSansCJK-Regular.ttc` into a temporary directory, pointed real Fontconfig at that directory alone, and used a temporary SQLite database. Cold, warm, directory, and forced scans indexed all ten faces without duplication. FreeType embedding flags and PANOSE values for each face matched an independent decode of its SFNT OS/2 bytes. The installed source font and user caches were not modified by the probe.

An isolated WAL-mode migration probe held a legacy connection open with committed rows still in its WAL. The 0600 backup contained all three original rows, the migrated cache retained both distinct faces, and both databases passed SQLite's integrity check.

All 25 application modules parse under Python 3.8 grammar. This is a syntax check; it does not establish runtime compatibility.

### Remaining limits

- Strict discovery preserves the old inventory when completeness cannot be established. A stale Fontconfig entry pointing to a missing file, a fallback font that FreeType cannot read, or an ambiguous delimiter in a Fontconfig record can therefore block a refresh until the source is corrected. Fontconfig remains authoritative for system discovery; the CLI cannot independently detect omissions from an otherwise successful command. A read-only sample on the development host included four paths reported by Fontconfig that were missing or unreadable, so the strict policy can affect ordinary updates.
- Identity is lexical: two paths that name the same underlying file are separate entries. Fontconfig can supply named variable instances. Filesystem indexing enumerates base TTC/OTC faces and retains already cached named instances for unchanged files. After a file changes, full sync is needed to discover its named instances again. Directory indexing retains existing Fontconfig classification fields; a full sync refreshes those fields.
- Ordinary lookups do not hash files. The next explicit sync or directory index detects changed bytes and prunes obsolete rows. Warm sync still reads every file to compute the digest, but skips native extraction for valid cached faces. Cache publication is atomic in SQLite; the filesystem and Fontconfig are not locked into the same snapshot. No production-size performance or crash-recovery stress test was run.
- Collection regression fixtures use synthetic headers and face-specific metadata. The native probe covers one TTC, so broader OTC and variable-font coverage is still limited. The coverage and dependency findings were subsequently resolved in Batch 5. Python 3.8 runtime compatibility remains unverified.

## Batch 5 — Remaining Medium findings (2026-11-12)

Batch 5 addresses CF-017–023 and CF-026. Each defect was reproduced before its implementation change at a public operation boundary or with controlled, owned native buffers. Both Low findings remain open. No new dependencies were introduced. The original Batch 3 terminal recovery lifecycle is preserved; missing FreeType now propagates through terminal suitability checks instead of bypassing validation.

### Root causes, changes, and regression evidence

- **CF-017:** The orthography loader used a filename string as the key instead of the numeric key codepoint. The Basic Latin regression initially observed `"BasicLatin" != 65`. The structured initializer reader now retains filename identity as `id` and the numeric codepoint as `key`. Full-support checks handle missing and zero keys as optional, and require other keys. The SQLite coverage cache now enforces the same rule, fixing a reproduced mismatch where cached support was true despite a missing key. Tests cover full coverage, missing keys, optional keys, and cached results.
- **CF-018:** The old parser paired every two numbers and discarded an odd final singleton. The Afrikaans regression reproduced 26 invented codepoints and a missing U+0149. The parser now honors `FONT_MANAGER_START_RANGE_PAIR` and end sentinels, preserving singleton and mixed arrays. Strings, comments, nested initializers, and escaped quotes/braces are tokenized without executing C. Tests compare independently written expected sets for singleton, range, and mixed fixtures. The bundled resources still produce 127 named orthographies; existing duplicate-name source data was not expanded or rewritten.
- **CF-019:** The notice regex stopped at an inner brace, and the vendor regex scanned both arrays. A regression reproduced an empty notice list and a failed Adobe fallback. Each named array is now parsed independently, preserving notice order and direct vendor-ID precedence. The corrected loader returns 1,517 distinct vendor IDs and 29 notice rows, rather than the polluted 1,531-ID map. Packaging expectations were updated for the corrected result; the resource files themselves are unchanged.
- **CF-020:** Mutation handlers printed failures but returned normally, foreground actions ignored return codes, and Google installation reported partial downloads as complete success. Install/remove now aggregate failures across attempted items; empty/failed archive installs also fail. The common CLI error boundary returns status 1 for the audited failed source, collection, alias, action, comparison, Google, backup, configuration, and native-dependency operations, including legacy install flags. Successful operations still exit 0. Foreground action status is checked, and partial Google downloads return failure with the installed count. Tests mock all destructive/network/shell boundaries and exercise successful, failed, and mixed outcomes.
- **CF-021:** Backup import searched absolute ancestor paths for `fonts` and classified configuration files by basename anywhere in the tree. The original regression copied unrelated files and constructed an extra ancestor subtree. Import now matches exact relative components: `fonts/...`, top-level known configuration files, and `fontconfig/conf.d/<name>.conf`. A file named `Actions.json` beneath `fonts/` remains font payload. It preflights source links and target link/non-file conflicts before copying, preserves the relative font hierarchy, and ignores unrelated siblings. Directory, ZIP, misleading ancestor/sibling names, and rejected-link cases use disposable targets. Existing regular files are deliberately overwritten during restore; configuration files are published atomically under locks. Font restores remain ordinary file copies, not an all-or-nothing restore transaction.
- **CF-022:** Separate read/modify/write snapshots lost committed changes, and direct truncation could corrupt JSON/XML. `state.py` provides persistent POSIX directory locks, nested thread/process-safe ownership, tracked snapshots, and atomic publication. High-level mutations hold their locks across the whole operation. Public loader snapshots retain a content revision; saving an outdated snapshot raises a conflict rather than overwriting newer bytes. Plain replacement lists/dicts/sets intentionally replace the latest document under a lock. Collections, actions, comparisons, aliases, rejection/source lists, rendering/display preferences, source-manager updates, backup configuration restore, and legacy collection bootstrap use this protocol. Malformed or linked documents fail before mutation. Tests reproduced stale collection loss and write interruption, then verified stale JSON/XML rejection, exact byte preservation on pre-publication failures, mode preservation, persistent lock identity, and real separate-process collection/source updates.
- **CF-023:** An unavailable FreeType library returned empty metadata/cmap or caused renderer `AttributeError`, enabling installation fallback. Operations now raise `FreeTypeUnavailableError`, initialization status is checked, and a missing parser cannot reach installation copy/fallback. Help remains available without FreeType. CLI info, preview, indexing, and suitability checks report the dependency error; presentation wrappers no longer hide it behind successful text fallback. Tests inject absent libraries and failed initialization, verify no copy, preserve an existing inventory after failed indexing, and check concise nonzero CLI failures.
- **CF-026:** Originally Probable, the format-handling defect is now **Confirmed and resolved within the supported raster contract**. A controlled MONO bitmap reproduced byte value 160 where a decoded pixel should be 255, without accessing unowned memory. All text/glyph/ASCII readers now share a validated decoder for MONO, GRAY, GRAY2, and GRAY4, including signed pitch, padding, grayscale levels, and empty bitmaps. LCD, LCD_V, BGRA/color, unknown modes, invalid pitch/null buffers, and excessive raster sizes return explicit `BitmapFormatError` before pixel reads. Face/glyph-slot access uses native ctypes field layouts rather than hardcoded byte offsets. A compiled probe compares layouts and mode constants against the installed FreeType headers. Existing native grayscale rendering remains covered. No native crash or color rendering support is claimed.

The metadata cache revision is now 3. An explicit sync or directory index refreshes affected old vendor/coverage results; tests do not rebuild user databases automatically.

### Files changed

Application changes: `cefont/core/state.py` (new), `actions.py`, `backup.py`, `collections.py`, `comparison.py`, `config.py`, `data.py`, `db.py`, `fontconfig.py`, `freetype_backend.py`, `google_fonts.py`, `renderer.py`, `sources.py`, `terminal.py`, `terminal_tester.py`, and `cefont/main.py`.

New tests: `tests/test_batch5_data.py`, `tests/test_batch5_backup.py`, `tests/test_batch5_state.py`, `tests/test_batch5_cli.py`, and `tests/test_batch5_native.py`. Existing database tests gained the cached-key consistency case; packaging tests now expect the independently parsed vendor map. This report is the only documentation file changed.

### Actual verification and limits

- The pre-packaging broader run passed 134 tests on Python 3.14.9; the final lock-inode guard test was then added and its focused suite passed.
- `python3 -B -m unittest tests.test_batch5_state tests.test_batch5_native tests.test_batch5_cli -q` — 33 passed on Python 3.14.9.
- `python3.11 -B -m unittest discover -q` — **142 passed** on Python 3.11.16 (144.475 seconds), including wheel, source, editable/zip loading, sdist rebuilds, and earlier-batch regressions.
- `python3 -B -m unittest discover -q` — **142 passed** on Python 3.14.9 (211.130 seconds), with no skipped tests. There were no skipped tests on Python 3.11 either. The 40 new Batch 5 cases plus the database cached-key regression bring the previous 101 tests to 142. All 131 packaged reference assets still match the source bytes.
- An isolated `python3 -B -` native probe rendered a real Noto Sans Mono `A` in MONO mode from a temporary copy. The 14×17-pixel output matched FreeType's own `FT_Bitmap_Convert` output exactly. The installed source font was only read.
- All 26 application modules parse under Python 3.8 grammar. Python 3.8 runtime compatibility is still unverified.
- Source/header-based and controlled-buffer raster checks do not certify arbitrary fonts, every native ABI, or real color/LCD output. Those formats are explicitly refused by the grayscale renderer.
- Configuration locks coordinate cooperating CeFont writers on local POSIX filesystems. Cross-file updates, backup font copies, and filesystem-plus-database changes are not globally transactional. If directory fsync fails after replacement, the complete new file may already be visible; that error is surfaced. Uncooperative external editors and hostile same-user parent-directory relocation are outside the locking guarantee. Plain full-document replacements are intentional replacements, so callers wanting conflict detection must retain the loaded snapshot.
- Background actions report successful spawning only; eventual child exit requires foreground/wait mode. Successful items in a failed batch are retained. Optional `fc-cache` refresh remains best effort.
- Verification used temporary configuration/font/database fixtures and mocked mutations. One initial dependency test accidentally left the `fc-cache` hook unmocked; that test and its child were stopped, and the hook was mocked before rerunning. Copying was mocked, so no font files were installed or removed, but Fontconfig cache files may have been refreshed before interruption. No terminal settings or system font configuration were changed.

<details>
<summary>Original evidence for the eight Batch 5 findings (historical, resolved)</summary>



#### Original CF-017 — Complete coverage is never marked fully supported

**Severity / confidence:** Medium / Confirmed.

**Location:** `data.py:148`, `load_orthographies()`; `orthography.py:83–108`, `analyze_font_orthographies()`.

**Expected:** full required coverage plus the integer key codepoint yields true support. **Actual/root cause:** the loader stores the filename string, e.g. `BasicLatin`, as `key`; analysis checks membership in a set of integer codepoints, so `key_present` is false.

**Reproduction:** analyze the host Noto Sans Mono file read-only with `filter_lang='Basic Latin'`. Result: `(coverage=100.0, fully_supported=False)`. **Impact:** incorrect support flags and sorting across all nonempty definitions. **Limits:** numeric coverage itself is a separate calculation and can still be correct for range-only definitions.

**Recommended fix:** retain filename identity separately and parse the actual key codepoint. **Regression tests:** complete Basic Latin, missing key, zero/optional key semantics, and numeric coverage versus support consistency.

#### Original CF-018 — Singleton orthography codepoints are expanded as ranges

**Severity / confidence:** Medium / Confirmed.

**Location:** `data.py:133–145`, `load_orthographies()`; format in `data/orthographies/CONTRIBUTING.md`.

**Expected:** honor individual codepoints and `FONT_MANAGER_START_RANGE_PAIR` markers. **Actual/root cause:** the parser discards markers and pairs every two hexadecimal numbers as an inclusive interval, dropping an odd final number.

**Reproduction:** the singleton codepoint list in bundled `Afrikaans` has 37 expected values. Compare those literal values from its third initializer block with the loaded set: 62 parsed values, 26 invented values, and one missing expected value. **Impact:** incorrect language coverage percentages, support decisions, and cached samples/support names.

**Limits:** Basic Latin's explicitly paired ranges happen to work. Not every orthography was differentially validated. **Recommended fix:** parse the format's tokens and sentinel semantics. **Regression tests:** singleton-only, range-only, mixed and odd-count data against independent expected sets.

#### Original CF-019 — Vendor notice fallback is never loaded

**Severity / confidence:** Medium / Confirmed.

**Location:** `data.py:32–42`, `load_vendors_and_notices()` and `get_vendor()` at 84.

**Expected:** header notice strings support vendor resolution when the foundry ID is absent/unknown. **Actual/root cause:** the notice regex cannot span inner closing braces to reach the array terminator. The vendor regex instead scans both notice and vendor arrays, polluting the ID map with reversed notice entries.

**Reproduction:** bundled headers contain Adobe notice entries; loader returned zero notices. `get_vendor('', 'Adobe')` returned `Unknown`. **Impact:** missing or wrong vendor fallback can propagate into metadata and suggested installation paths. **Limits:** direct known vendor-ID lookup often still works; 1,531 parsed map keys does not validate every mapping.

**Recommended fix:** parse each array independently using a structure-aware reader. **Regression tests:** nonempty known notices, notice-only resolution, ID precedence, and absence of notice strings in the ID map.



#### Original CF-020 — Mutation errors still produce a successful CLI exit status

**Severity / confidence:** Medium / Confirmed.

**Location:** `main.py:252`, `cmd_install()`; `cmd_remove()` at 268; analogous action/Google/backup handlers at 858–976.

**Expected:** command failure yields a nonzero status usable by automation. **Actual/root cause:** several handlers print errors but return normally; dispatch does not aggregate failures.

**Reproduction:** with temporary XDG roots, `python3 -B bin/cefont remove definitely-not-a-real-family` printed not found but exited 0. `install <temporary-root>/nonexistent.ttf` likewise printed failure and exited 0 without copying anything. **Impact:** scripts continue after failed management operations or falsely report successful deployment/backup.

**Scope update:** terminal recovery errors and database update failures now exit with status 1. Other install/remove/action/Google/backup failure paths remain open under CF-020.

**Limits:** selected handlers do call `sys.exit(1)`; this is not a claim that all errors return success. **Recommended fix:** consistent result/exit policy with partial batch failures reflected in status. **Regression tests:** nonexistent input, per-item failure, mixed batch, failed backup/action/Google mocks, and successful operations.

#### Original CF-021 — Backup import anchors font paths to an ancestor substring

**Severity / confidence:** Medium / Confirmed (temporary input, copy calls mocked).

**Location:** `backup.py:145–151`, `import_backup()`.

**Expected:** restore the relative subtree under the backup's own `fonts/` component. **Actual/root cause:** `root.find('fonts')` searches the entire absolute path and takes the first occurrence, including names of parent directories.

**Reproduction:** make a temporary `myfonts-backup/restore/fonts/Family/sample.txt` with dummy text. Mock `copy2`, directory creation, Fontconfig refresh, and database sync. Import selected a destination ending `fonts/-backup/restore/fonts/Family/sample.txt`, returned success, and did not preserve the intended relative layout.

**Impact:** backups restored from common `fonts-backup`-style parent paths get extra hierarchy; unrelated files can also be misclassified as fonts when only an ancestor name matches. **Limits:** this is path misclassification, not demonstrated traversal beyond `USER_FONT_DIR`.

**Recommended fix:** classify entries by validated relative components from the import root. **Regression tests:** ancestors containing `fonts`, non-font sibling data, exact `fonts/Family` hierarchy, and similarly named components.

#### Original CF-022 — Concurrent configuration updates lose committed changes

**Batch 3 scope update:** terminal changes and recovery now hold an exclusive process lock and enforce journal owner tokens. The JSON/XML read-modify-write defects below are unchanged, so this finding remains open.

**Severity / confidence:** Medium / Confirmed (deterministic JSON-update interleaving; broader writer concurrency not stress-tested).

**Location:** `cefont/core/collections.py`, `load_collections()`, `save_collections()`, and mutation functions; related JSON/XML read-modify-write patterns in `actions.py`, `comparison.py`, and `fontconfig.py`. The terminal journal implementation is excluded from the remaining defect.

**Expected:** two concurrent successful updates are preserved or one receives a conflict. **Actual/root cause:** independent snapshots are written without locks/version checks or merge; JSON writes truncate their targets directly.

**Reproduction:** using only temporary collection JSON, load snapshots A and B of `Base`; append `A` to one and `B` to the other; save A then B. Final names were `['Base','B']`, losing A. This is the schedule two CLI processes can follow. Other modules' races are source-supported, not independently reproduced.

**Impact:** lost collections/membership/actions/settings; interrupted direct writes can leave corrupt configuration. The former overlapping terminal-journal hazard was addressed in Batch 3 and is no longer part of this open finding.

**Limits:** single-process sequential commands avoid the demonstrated stale-snapshot schedule. **Recommended fix:** locking or optimistic versioning across each remaining writer's entire read-modify-write transaction, plus atomic replacement. **Regression tests:** deterministic two-writer interleavings and interrupted JSON/XML writes. Terminal journal ownership and overlapping-session tests already pass.

#### Original CF-023 — Missing FreeType is reported as missing data or an internal exception

**Severity / confidence:** Medium / Confirmed (dependency injection).

**Location:** `freetype_backend.py:10–13`, load suppression; `extract_font_metadata()` at 196; `renderer.py:87`, `render_text_bitmap()` and related paths.

**Expected:** distinguish unavailable native dependency from invalid font and provide a clear CLI failure. **Actual/root cause:** load errors set `_ft=None`; metadata quietly returns `None`, while bitmap rendering dereferences `_ft` without a guard.

**Reproduction:** patch backend `_ft=None`: metadata returned `None`. Patch renderer `_ft=None`: rendering raised `AttributeError`. **Impact:** an unavailable shared library can masquerade as invalid/missing font data, empty coverage, or a fallback/internal error. Installer metadata fallback can copy input despite validation being unavailable.

**Limits:** optional dependency absence should not prevent `--help`; this audit does not recommend making every import fail. Some presentation functions catch rendering exceptions and show text. **Recommended fix:** explicit dependency status/errors at operation boundaries and no validation-success fallback on unavailable parsers. **Regression tests:** missing library with help, info, indexing, preview, and mocked installation entry points.



#### Original CF-026 — Raster readers assume a byte-per-pixel grayscale bitmap

**Severity / confidence:** Medium / Probable.

**Location:** `renderer.py:87`, `render_text_bitmap()` row-byte loop; `render_glyph_bitmap()` at 225; `freetype_backend.py:336`, ASCII path.

**Expected:** support or explicitly reject bitmap pixel modes/pitches returned by FreeType. **Suspected behavior/root cause:** readers use `row*pitch + column` for every pixel without branching on `pixel_mode`; monochrome bit-packed rows can be read as if each bit were a byte, and color/other modes are not decoded. A narrow pitch can make this an out-of-row native read. Hardcoded native offsets separately restrict ABI portability.

**Evidence/reproduction plan:** source traces from `FT_Load_Char(FT_LOAD_RENDER)` to direct memory reads are conclusive assumptions. Supply a controlled font producing MONO/BGRA bitmap output and compare to a trusted renderer in a constrained child process; this was **not** done. **Counterevidence:** regular scalable grayscale Noto rendering passed in discovery, and both hardcoded offsets match this host ABI. No invalid-access/crash or color-font defect is claimed confirmed.

**Impact:** potentially wrong previews or process failure for specific font formats/platforms. **Recommended fix:** use documented layouts, validate mode/pitch/buffer dimensions, and decode supported formats explicitly. **Regression tests:** grayscale, mono, color, negative pitch, space/zero bitmap, ABI checks, and isolated malformed-font cases.


</details>
