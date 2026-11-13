# CeFont installation manager

Run `./install.sh` from the checkout to install or uninstall CeFont. The shell
launcher locates its backend relative to itself, so it also works when launched
from another working directory. This is an installation manager, separate from
the upcoming CeFont application TUI.

## Full-screen setup

Run `./install.sh` in a terminal to open the setup studio, built with
[Bubble Tea v2](https://github.com/charmbracelet/bubbletea),
[Lip Gloss v2](https://github.com/charmbracelet/lipgloss), and
[Bubbles v2](https://github.com/charmbracelet/bubbles). Gum is no longer used.

The alternate-screen interface includes a large CeFont wordmark,
a persistent navigation rail on wide terminals, destination cards,
preflight results, a fixed confirmation bar, and a live deployment timeline.
It restores the original terminal screen on normal exit. It does not change
terminal font settings or edit terminal color profiles.

The route is **Welcome > Operation > Method > Destination > Preflight > Review > Apply**.
The welcome screen shows the checkout version and separate installation status
for your account and the system. A receipt found on disk is explicitly labelled
**not yet verified**. Full inventory verification happens during preflight and
again immediately before removal. Uninstall skips the Method page.

- Arrow keys, Tab, or j/k move between choices; Enter selects.
- b or Esc goes back; q exits before deployment.
- Page Up / Page Down scroll long checks, paths, and error details.
- Review always starts on **Cancel**. Select **Apply changes** explicitly.
- During work, q requests a stop and waits for backend cleanup. An already
  elevated administrator operation is allowed to finish safely before exit.
- Update remains locked and cannot be selected.

Use at least **60 columns by 20 rows**. Below that size, setup shows a resize
notice and prevents confirmation. The rail appears at 100 columns and wider;
compact layouts retain the content and fixed confirmation actions. Light and
dark themes keep the same sage identity and provide different contrast.
Wide layouts include a six-column gutter, roomier sidebar padding, and more
space between welcome sections. Additional vertical spacing is enabled only
when the terminal has enough rows to keep the main actions visible.

```bash
./install.sh
./install.sh --theme light
./install.sh --no-animation
./install.sh --plain
./install.sh install --user --dry-run
```

`--no-animation` stops the activity spinner. `--theme auto` uses `COLORFGBG`
when available and otherwise assumes a dark terminal. Use an explicit theme
when necessary. `--plain`, `NO_COLOR`, `TERM=dumb`, automation with `--yes`,
and dry runs use the Python text interface without compiling the Go UI.
The text interface has numbered choices, Back/Cancel, checks, and review.

## Customer releases: no Go required

Precompiled, static UI binaries are included for **Linux x86-64 (amd64)** and
**Linux ARM64 (aarch64)** under `installer/bin/`. `./install.sh` selects the
matching binary first, checks its SHA-256 (and source fingerprint when UI source is present) against
`installer/bin/manifest.json`, and starts the full-screen UI. Customer releases
need **no Go installation, module download, or UI compilation**.

Each platform has two packages:

- **Standard:** `cefont-1.0.0-linux-amd64.tar.gz` (or `linux-arm64`). Contains the
  installer, matching prebuilt UI, verified application wheel, and license
  notices. It omits the working application checkout and developer build files.
- **Developer:** `cefont-1.0.0-linux-amd64-developer.tar.gz` (or ARM64 equivalent).
  Contains the same prebuilt wheel plus application/resource source, Go UI
  source, and release build tools. `SOURCE.txt` identifies the matching source
  archive in both packages. Python modules and resources also exist inside the
  runtime wheel; the standard package is not a source-obfuscated application.

```bash
tar -xzf cefont-1.0.0-linux-amd64.tar.gz
cd cefont-1.0.0
./install.sh
```

Extract the whole archive. The Go executable handles the installer UI; Python
runs the application and owns deployment. Normal installation requires Python
3.11+, FreeType, Fontconfig, and Python's `venv`/`ensurepip` modules. It does not
require Go, setuptools, a compiler, or wheel-building tools.

## Application installation methods

**Prebuilt release** is selected by default. It installs the wheel declared in
`installer/packages/manifest.json`, verifying its checksum, metadata, and
required resources. Changing local application source does not alter or
invalidate this release. Only explicitly rebuilding the release wheel changes
what the prebuilt method installs.

**Build local source** builds exactly the current checkout, including patches,
and additionally requires setuptools 61+. It does not clone another checkout,
download a replacement source tree, or silently switch to prebuilt when a build
fails. The standard package reports that the checkout is unavailable and points
to the developer package. Missing or corrupted prebuilt wheels are likewise
reported without silently building source.

```bash
./install.sh install --user                         # bundled wheel
./install.sh install --user --method source          # local checkout and patches
./install.sh install --system --method prebuilt
./install.sh install --user --method source --dry-run
```

Both methods use the same runtime locations, no-overwrite policy, locks,
receipt validation, rollback, and uninstall path. Receipts record
`installation_method` (`prebuilt` or `source`), the actual wheel version, and
`wheel_sha256`. Older receipts remain removable. The installed command is
verified inside its new runtime before the launcher is published.

The UI itself always prefers the bundled executable, regardless of the chosen
application method. Building patched Python application source does not require
rebuilding the Go UI. Method and version appear in review; switching method
requires an explicit choice. Update stays disabled.

The release binaries are built with `CGO_ENABLED=0`, Linux amd64 baseline v1,
and ARM64 baseline v8.0. They do not require a Go runtime or libc dynamic loader.
The x86-64 build is run-tested locally; ARM64 is cross-compiled and its ELF
architecture checked, but not run-tested on ARM hardware. Checksum validation
detects damaged or mismatched files; it is not a release signature.

## Developer builds and fallback

To rebuild the UI binaries, bundled application wheel, and all four customer archives:

```bash
python3 -B scripts/build_installer_ui.py
```

Developer builds need **Go 1.26.7+** (or Go's compatible toolchain selection).
Pinned modules in `go.mod` and checksums in `go.sum` may be downloaded during
building. The build records binary checksums and the current source fingerprint,
copies licenses for the modules actually linked, and writes customer archives
and detached `.sha256` files under `dist/`. `--binaries-only` rebuilds just the
UI binaries; `--wheel-only` rebuilds just the application wheel without Go.
`--package-only` packages existing verified binaries and wheel without Go or
setuptools. Packaging checks that the developer source matches the release
wheel build, so matching source is not accidentally shipped from a different
checkout state.

If a bundle is absent, unsupported, or older than the frontend source, the
launcher retains its developer-build fallback. It reuses a content-keyed UI
cache under `${XDG_CACHE_HOME:-$HOME/.cache}/cefont-installer/ui/`, or builds there
when Go is available. This cache is separate from the font database. Missing Go
falls back to the text interface; corrupt bundles or unsafe executable paths
are refused. The updater remains disabled and no binaries are auto-downloaded.

`--plain` and dry runs bypass the full-screen UI entirely. The full-screen
installer is independent of the upcoming CeFont application TUI. The Go UI is
not installed inside CeFont's Python runtime.

## Backend and operation safety

The UI delegates to the existing Python backend through JSON-lines events.
It never installs, deletes, or adopts files itself. Status and preflight are
read-only. Preflight checks the selected interpreter, method-specific requirements,
FreeType, Fontconfig, destination conflicts, and applicable permissions.
System removal by an ordinary user marks inventory verification **pending**
until the administrator step, rather than claiming success prematurely.

System operations authenticate with `sudo -v` only after Apply. The UI pauses
and gives sudo the terminal, then resumes; the backend's deployment uses
noninteractive sudo and reports failure if authorization is unavailable.
The elevated step still receives only the verified local wheel. Cancelling
before Apply cannot request elevation or start deployment.

During work, Bubbles activity indicators and elapsed time accompany actual
backend stages. There is no invented download percentage. Failures retain
sanitized diagnostics, and EOF without a successful backend result is treated
as failure. Child terminal control sequences are never replayed. Success gives
a full command path and PATH guidance. Filesystem checks, receipt validation,
and rollback remain in the Python backend.

## Requirements and offline operation

- Linux and Python 3.11 or newer.
- Both install methods need `venv` and `ensurepip`; only local-source builds need setuptools 61+.
- FreeType's `libfreetype.so.6`, and Fontconfig's `fc-list` and `fc-cache`.
- No Go is required for customer bundles. Go 1.26.7+ is needed only to rebuild the UI.

Source mode checks build prerequisites, copies application/package resources
into a disposable source snapshot, builds a wheel using local setuptools, and
checks the packaged command with `--help`. Prebuilt mode verifies the bundled
wheel and skips these build steps. Both verify the installed command. It does not clone a remote source,
access a Python package index, install distribution dependencies, or refresh font
caches. Install missing prerequisites through your normal package manager.

Use `--python /path/to/python3.11` to choose another interpreter. The shell
launcher itself defaults to `python3`; set `CEFONT_INSTALL_PYTHON` to a Python
3.11+ executable if your default `python3` is older. The built application is
installed non-editably into an isolated venv, so it does not depend on keeping
the source checkout at its original path.

## Install and uninstall

For your current account:

```bash
./install.sh install --user
./install.sh uninstall --user
```

The runtime goes to `${XDG_DATA_HOME:-$HOME/.local/share}/cefont/runtime`, with
the command at `$HOME/.local/bin/cefont`. User mode follows the account's actual
`HOME`; launch normally, without sudo, to install for yourself.

For the system:

```bash
./install.sh install --system
./install.sh uninstall --system
```

The runtime goes to `/opt/cefont`, and the command to `/usr/local/bin/cefont`.
When launched as an ordinary user, a system install builds and checks the wheel
before requesting sudo for deployment. The elevated step receives a wheel and
its SHA-256 hash, verifies the bytes, and installs from a private local copy.
System uninstall requests elevation without building a package. Normal sudo
policy and authentication apply; the script does not configure them.

For a custom destination or disposable test installation:

```bash
./install.sh install --user --prefix /tmp/cefont-demo
./install.sh uninstall --user --prefix /tmp/cefont-demo
```

A custom prefix uses `PREFIX/lib/cefont` and `PREFIX/bin/cefont`. It does not
request sudo automatically; permissions are those of the invoking account.
Reuse the same scope and prefix for uninstall. Availability to other users
depends on directory permissions and their `PATH`.

## Automation and review

```bash
./install.sh install --user --dry-run
./install.sh install --user --yes --plain
./install.sh uninstall --user --yes --plain
```

Without a terminal, action and scope must be explicit, and mutations require
`--yes`. A dry run prints the layout without creating files, building packages,
or requesting sudo. It is a plan, not a complete dependency/ownership check.

The installed launcher runs Python with `-I -B`: it ignores working-directory
imports and suppresses bytecode writes into the managed runtime. It exposes
`cefont`; the package's legacy `font-manager-cli` entry point remains inside
the isolated venv rather than overwriting an existing global alias.

## Ownership and filesystem policy

- Existing command or runtime destinations are refused, including empty
  directories and dangling links. Installs do not overwrite or upgrade an
  existing installation.
- Symlink components and control characters in destination paths are refused.
  Use physical directory paths for custom prefixes.
- Cooperating installer processes hold an exclusive lock on the destination.
  The lock inode is retained after the operation to prevent replacement races.
- The runtime contains `installation.json`, recording its scope, exact paths,
  installation ID, installation method, package version, wheel hash, and owned-file inventory.
- Uninstall checks that receipt, the command contents, and the full runtime
  inventory. Modified, extra, or foreign files cause refusal before removal.
  Previously installed copies managed by pip or another tool are not adopted
  or removed by this installer.
- Uninstall removes the owned application runtime and command. User fonts,
  Fontconfig settings, CeFont configuration, and user caches are preserved.
  A small installer lock file and empty parent directories can remain.

New installation failures and catchable interruptions attempt to remove only
the runtime reserved by that operation. The command is published last, without
overwriting a command that appeared concurrently. Power loss, SIGKILL, parent
directory replacement by an uncooperative same-user process, and storage
failures cannot be made globally atomic. An incomplete runtime can require
manual review. Uninstall deletion is likewise not an all-or-nothing operation;
a late I/O failure can leave an incomplete installation and returns an error.

User and system installations may coexist. Removing one does not remove the
other; `command -v cefont` shows which command your current `PATH` selects.

## Updates are inactive

```bash
./install.sh update
```

This returns status 2 and an explicit disabled message. It performs no update
checks, downloads, package builds, elevation, or filesystem changes. There is
no hidden working update path.

`cefont update` is a different command: it synchronizes the font index and is
unaffected by the installer's disabled updater.

## Verification

All deployment and removal fixtures use temporary destinations. Administrator
execution is mocked; no tests install into actual user font folders or `/opt`.

```bash
python3 -B -m unittest discover -q
python3.11 -B -m unittest tests.test_tui_installer tests.test_installer_ui tests.test_installer_bridge tests.test_installation_methods -q
cd installer/tui
go test -race ./...
go vet ./...
```

Python coverage includes isolated user/system-prefix deployments, resource
loading outside the checkout, uninstall ownership checks, preserved personal
data, modified or foreign files, symlinks, concurrent publication, locks,
rollback, disabled updates, dry runs, text navigation, Unicode wrapping, and
spinner cleanup. Bridge regressions verify read-only status/check requests,
explicit execution confirmation, JSON-only diagnostics, an actual disposable
install/uninstall cycle, cache reuse without Go, rejected writable cache
parents, deferred cancellation of administrator work, bundled-binary selection without Go,
checksum rejection, stale-source detection, and fixed executable paths. Method
regressions also prove that local patches are ignored by prebuilt but included
by source, prebuilt works with setuptools absent, and a missing source build
prerequisite fails without falling back or creating an installation.

Go tests cover default-Cancel review, Back navigation, disabled updates,
resize guards, unsuccessful/missing backend completion, cancellation before
process start, and a real disposable child process that removes its temporary
fixture on SIGTERM. Frame checks cover all setup screens at 60x20, 80x24,
100x24, 120x36, and 160x45 in both themes. Rendered welcome screens were also
visually inspected.

Live pseudo-terminal verification exercised the compiled Bubble Tea frontend
at 120x36 (dark) and 80x24 (light), including the complete read-only route to
Review, default cancellation, and alternate-screen restoration. No installation
destination was created. The initial screen-detection harness expected entire
strings, which a differential renderer need not reprint; the corrected probe
waited for the unique review action and passed.

Recorded full-screen verification (2026-11-13):

- Python 3.14.9: **199 tests passed**, with no skips (142.451 seconds).
- Python 3.11.16: **57 installer/UI/bridge/method tests passed**, with no skips
  (112.945 seconds).
- Go 1.27.3: **13 UI/protocol tests passed**, including `go test -race ./...`;
  `go vet ./...` passed. Spacing verification also covers intermediate heights
  (100x28, 100x29, 120x30, 120x31, 120x32, and 140x30) in both themes.
- A real `install.sh` first build and launch passed using a temporary UI cache.
  A second launch with Go absent from PATH reused the cached full-screen
  executable. Both entered and restored the alternate screen; neither created
  an application installation.
- Live dark/light navigation and default cancellation passed. `sh -n install.sh`
  and `git diff --check` passed.
- Customer release: both stripped ELF binaries are statically linked and have
  the expected x86-64/AArch64 architecture. Each archive contains only its
  matching UI binary, a matching manifest/checksum file, and license notices.
  The extracted x86-64 archive launched and restored the full-screen UI with Go
  absent from PATH, creating no UI cache. An isolated install, command-help
  check, and uninstall from that extracted archive also passed without Go;
  a disposable personal-font fixture remained intact. ARM64 was not executed.
- Method verification: a fresh standard package navigated Welcome, Operation,
  Method, Destination, Checks, and default-Cancel review with Go and setuptools
  absent. Prebuilt installation, all 127 packaged orthographies, and uninstall
  worked from that package. Source mode reported the missing checkout without
  falling back. In a developer package, a temporary local Python-module patch
  was ignored by prebuilt and included by source; receipts recorded distinct
  wheel hashes and the correct method. Both uninstalled through the same path.
  Personal-font fixtures were preserved throughout.
- The release archives were also regenerated with Go absent from PATH using a
  Python environment without setuptools. All four archives were checked for
  unique safe file entries, matching wheel/UI checksums, and correct profiles:
  only developer packages contain the application checkout, Go source, build
  scripts, and tests. Standard packages contain the verified runtime wheel.

The previous Python/Gum installer passed 160 regressions before redesign; the
intermediate Python-only redesign passed 175. These historical checks are not
substitutes for the full-screen verification above.

Limits: actual sudo authentication/elevated deployment, every terminal's Unicode
or color behavior, and older Go toolchains were not exercised. SIGKILL, power
loss, terminal-session loss, and late filesystem failures can leave a partial
installation requiring manual review. These limits do not change the strict
ownership and no-overwrite policies above.
