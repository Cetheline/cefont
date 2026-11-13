"""Terminal detection and recoverable font changes.

Persistent changes are serialized, journaled before application, and checked on
restore. Unsupported/unverifiable adapters fall back without changing settings.
"""

import atexit
import ast
import math
import uuid
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from cefont.core.config import PACKAGE_CACHE_DIR, PACKAGE_CONFIG_DIR
from cefont.core.terminal_recovery import RecoveryError, RecoveryStore
from cefont.core.terminal_config import capture_alacritty, apply_alacritty, restore_alacritty
from cefont.core.freetype_backend import FreeTypeUnavailableError

RECOVERY_FILE = os.path.join(PACKAGE_CACHE_DIR, "live_test_recovery.json")


# =========================================================================
# 1. Process Ancestry & Terminal Emulator Detection
# =========================================================================

def get_process_ancestry() -> List[Tuple[int, str]]:
    """
    Traverse parent process tree from current process up to init.
    Linux procfs (/proc/<pid>/comm and /proc/<pid>/stat) has world-readable
    permissions (0444) for own-user processes, requiring ZERO sudo/root permissions.
    Returns: [(pid, comm), (ppid, parent_comm), ...]
    """
    ancestry = []
    pid = os.getpid()

    while pid > 1:
        try:
            comm = ""
            comm_path = f"/proc/{pid}/comm"
            if os.path.isfile(comm_path):
                with open(comm_path, "r", encoding="utf-8", errors="ignore") as f:
                    comm = f.read().strip()

            stat_path = f"/proc/{pid}/stat"
            ppid = 1
            if os.path.isfile(stat_path):
                with open(stat_path, "r", encoding="utf-8", errors="ignore") as f:
                    # stat format: pid (comm) state ppid ...
                    content = f.read()
                    # Find last closing paren to handle comms with spaces
                    rparen = content.rfind(")")
                    if rparen != -1:
                        remainder = content[rparen + 1 :].strip().split()
                        if len(remainder) >= 2:
                            ppid = int(remainder[1])

            ancestry.append((pid, comm))
            if ppid == pid or ppid <= 0:
                break
            pid = ppid
        except Exception:
            break

    return ancestry


def detect_terminal_emulator() -> Tuple[str, Dict[str, Any]]:
    """
    Detect the active terminal emulator and return (terminal_type, metadata).
    Supported types:
      'kgx' | 'gnome-terminal' | 'alacritty' | 'kitty' | 'wezterm' |
      'konsole' | 'foot' | 'xterm' | 'ssh' | 'linux-vt' | 'unsupported'
    """
    # 1. Check for remote SSH session first
    if any(k in os.environ for k in ["SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION"]):
        return "ssh", {"reason": "Remote SSH session detected; cannot mutate local client fonts."}

    # 2. Check for Linux virtual console (VT)
    term = os.environ.get("TERM", "").lower()
    if term == "linux":
        return "linux-vt", {"reason": "Linux virtual console (TERM=linux) without graphical terminal emulator."}

    # 3. Inspect Process Ancestry Tree (Closest Ancestor Priority)
    # Walking ancestry from innermost child to outermost parent ensures that if a
    # terminal (e.g. xterm) was launched inside another terminal (e.g. foot), the
    # closest active terminal emulator in the process chain wins immediately.
    ancestry = get_process_ancestry()

    for pid, comm in ancestry:
        c = comm.lower()
        if c == "kgx":
            return "kgx", {"pid": pid, "backend": "gsettings"}
        if c in ["gnome-terminal", "gnome-terminal-server"] or c.startswith("gnome-terminal"):
            return "gnome-terminal", {"pid": pid, "backend": "gsettings"}
        if c == "alacritty":
            return "alacritty", {"pid": pid, "backend": "config"}
        if c == "kitty":
            return "kitty", {"pid": pid, "backend": "remote_or_config"}
        if c in ["wezterm", "wezterm-gui"] or c.startswith("wezterm"):
            return "wezterm", {"pid": pid, "backend": "config"}
        if c == "konsole":
            return "konsole", {"pid": pid, "backend": "dbus"}
        if c in ["foot", "footclient"]:
            return "foot", {"pid": pid, "backend": "config_signal"}
        if c in ["xterm", "urxvt", "urxvtd"]:
            return "xterm", {"pid": pid, "backend": "osc50"}

    # 4. Fallback: Environment Variables (evaluated ONLY if ancestry did not match)
    # This guarantees inherited variables like FOOT_SERVER_SOCKET or KITTY_WINDOW_ID
    # do NOT hijack child terminal sessions.
    if os.environ.get("TERM_PROGRAM") == "kgx":
        return "kgx", {"backend": "gsettings"}
    if os.environ.get("ALACRITTY_WINDOW_ID"):
        return "alacritty", {"backend": "config"}
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty", {"backend": "remote_or_config"}
    if os.environ.get("WEZTERM_PANE"):
        return "wezterm", {"backend": "config"}
    if os.environ.get("KONSOLE_VERSION") or os.environ.get("KONSOLE_DBUS_SERVICE"):
        return "konsole", {"backend": "dbus"}
    if os.environ.get("FOOT_SERVER_SOCKET"):
        return "foot", {"backend": "config_signal"}
    if os.environ.get("XTERM_VERSION"):
        return "xterm", {"backend": "osc50"}
    if "xterm" in term:
        return "xterm", {"backend": "osc50"}

    return "unsupported", {"ancestry": [c for _, c in ancestry]}


# =========================================================================
# 2. Crash-Proof Recovery Journal
# =========================================================================

def write_recovery_journal(data: Dict[str, Any], store=None) -> None:
    """Persist before mutation. Standalone callers must supply an owner token."""
    own = store is None
    store = store or RecoveryStore(RECOVERY_FILE).acquire()
    try:
        store.write(data)
    finally:
        if own:
            store.close()


def clear_recovery_journal(owner=None, store=None) -> None:
    """Clear only the named owner's record; no global unowned deletion."""
    own = store is None
    store = store or RecoveryStore(RECOVERY_FILE).acquire()
    try:
        store.clear(owner)
    finally:
        if own:
            store.close()


def _recover_locked(store):
    record = store.read()
    if record is None:
        return None
    if record.get('version') != 2 or not record.get('owner'):
        raise RecoveryError('Legacy or invalid recovery record preserved for manual recovery: ' + RECOVERY_FILE)
    if record.get('intent') == 'keep':
        store.clear(record['owner'])
        return 'Finalized a previously kept terminal font change.'
    state = record.get('original_state')
    if not isinstance(state, dict):
        raise RecoveryError('Recovery state is invalid; record retained')
    if not _restore_terminal_plan(record.get('terminal_type'), state):
        raise RecoveryError('Terminal restoration failed; recovery record retained: ' + RECOVERY_FILE)
    store.clear(record['owner'])
    return "Recovered previous terminal session for '%s'." % record['terminal_type']


def recover_orphaned_state() -> Optional[str]:
    """Recover only under the exclusive lock; errors preserve the record."""
    store = RecoveryStore(RECOVERY_FILE).acquire()
    try:
        return _recover_locked(store)
    except Exception as error:
        raise RecoveryError('Recovery failed; inspect record at %s: %s' % (RECOVERY_FILE, error)) from error
    finally:
        store.close()


# =========================================================================
# 3. System GSettings Invocation & Environment Sanitization
# =========================================================================

def get_gsettings_binary() -> str:
    """
    Return explicit system gsettings binary path (/usr/bin/gsettings)
    or fallback to shutil.which('gsettings') only if /usr/bin/gsettings does not exist.
    Prevents Linuxbrew/Homebrew binaries from hijacking D-Bus and dconf.
    """
    if os.path.isfile("/usr/bin/gsettings") and os.access("/usr/bin/gsettings", os.X_OK):
        return "/usr/bin/gsettings"
    return shutil.which("gsettings") or "gsettings"


def get_gsettings_env() -> Dict[str, str]:
    """
    Sanitize environment variables for executing system gsettings.
    Removes Linuxbrew/Homebrew environment variables (GIO_MODULE_DIR,
    GIO_EXTRA_MODULES, GSETTINGS_SCHEMA_DIR, XDG_DATA_DIRS) that hijack D-Bus/dconf,
    and ensures system directories take priority in PATH so it always talks
    to the host's native ca.desrt.dconf bus.
    """
    env = os.environ.copy()

    # Strip Linuxbrew / Homebrew overrides from GIO & GSettings environment
    for var in [
        "GIO_MODULE_DIR",
        "GIO_EXTRA_MODULES",
        "GSETTINGS_SCHEMA_DIR",
        "XDG_DATA_DIRS",
    ]:
        if var in env:
            val = env[var]
            if "linuxbrew" in val.lower() or "homebrew" in val.lower():
                parts = [p for p in val.split(":") if "linuxbrew" not in p.lower() and "homebrew" not in p.lower()]
                if parts:
                    env[var] = ":".join(parts)
                else:
                    env.pop(var, None)

    # Ensure system directories take precedence in PATH and strip linuxbrew/homebrew
    path_dirs = env.get("PATH", "").split(":")
    clean_path = [p for p in path_dirs if "linuxbrew" not in p.lower() and "homebrew" not in p.lower()]
    for d in reversed(["/usr/bin", "/bin", "/usr/sbin", "/sbin"]):
        if d in clean_path:
            clean_path.remove(d)
        clean_path.insert(0, d)
    env["PATH"] = ":".join(clean_path)

    return env


def run_gsettings(args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    """Execute gsettings using explicit system binary and sanitized environment."""
    gsettings_bin = get_gsettings_binary()
    env = get_gsettings_env()
    cmd = [gsettings_bin] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check, env=env, timeout=15)


# =========================================================================
# 4. Terminal Font Handlers
# =========================================================================

def _checked_gsettings(args):
    result = run_gsettings(args, check=True)
    if result.returncode:
        raise RecoveryError('GSettings command failed: ' + (result.stderr or str(args)))
    return result.stdout.strip()


def _variant(raw):
    if raw in ('true', 'false'):
        return raw == 'true'
    value = ast.literal_eval(raw)
    if not isinstance(value, str):
        raise RecoveryError('Unexpected GSettings value type')
    return value


def _capture_gsettings(terminal_type, family, size):
    if terminal_type == 'kgx':
        schema, font_key = 'org.gnome.Console', 'custom-font'
    else:
        profile = _variant(_checked_gsettings(['get', 'org.gnome.Terminal.ProfilesList', 'default']))
        profile = str(uuid.UUID(profile))
        schema = 'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:%s/' % profile
        font_key = 'font'
    original = {key: _checked_gsettings(['get', schema, key]) for key in ('use-system-font', font_key)}
    if not isinstance(_variant(original['use-system-font']), bool):
        raise RecoveryError('Unexpected use-system-font value')
    old_font = _variant(original[font_key])
    active_size = size
    if active_size is None:
        try:
            active_size = float(old_font.rsplit(' ', 1)[1])
        except (IndexError, ValueError):
            active_size = 11
    if isinstance(active_size, bool) or not math.isfinite(active_size) or active_size <= 0:
        raise RecoveryError('Font size must be positive and finite')
    if float(active_size).is_integer():
        active_size = int(active_size)
    target = {'use-system-font': 'false', font_key: json.dumps('%s %s' % (family, active_size), ensure_ascii=False)}
    return dict(kind='gsettings', schema=schema, original_values=original,
                target_values=target, active_size=active_size)


def _apply_gsettings(state):
    for key, value in state['original_values'].items():
        if _variant(_checked_gsettings(['get', state['schema'], key])) != _variant(value):
            raise RecoveryError('GSettings changed since capture; refusing to overwrite ' + key)
    for key, value in state['target_values'].items():
        _checked_gsettings(['set', state['schema'], key, value])
    for key, value in state['target_values'].items():
        if _variant(_checked_gsettings(['get', state['schema'], key])) != _variant(value):
            raise RecoveryError('GSettings did not apply the requested value for ' + key)


def _restore_gsettings(state):
    # Preflight both keys before writes, so external edits are never overwritten.
    current = {key: _checked_gsettings(['get', state['schema'], key])
               for key in state['original_values']}
    for key, value in current.items():
        if _variant(value) not in (_variant(state['original_values'][key]), _variant(state['target_values'][key])):
            raise RecoveryError('GSettings value changed externally: %s; record retained' % key)
    errors = []
    for key, value in state['original_values'].items():
        try:
            if _variant(current[key]) != _variant(value):
                _checked_gsettings(['set', state['schema'], key, value])
        except Exception as error:
            errors.append(str(error))
    for key, value in state['original_values'].items():
        try:
            if _variant(_checked_gsettings(['get', state['schema'], key])) != _variant(value):
                errors.append('GSettings restoration verification failed for ' + key)
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise RecoveryError('; '.join(errors))
    return True


def _find_alacritty_config() -> Optional[str]:
    root = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    candidates = [os.path.join(root, 'alacritty', 'alacritty.toml'),
                  os.path.join(root, 'alacritty.toml'), os.path.expanduser('~/.alacritty.toml'),
                  os.path.join(root, 'alacritty', 'alacritty.yml'), os.path.expanduser('~/.alacritty.yml'),
                  os.path.join(root, 'alacritty', 'alacritty.yaml')]
    for path in candidates:
        if os.path.lexists(path):
            return path
    return candidates[0]  # Capture refuses absent parents, with no directory creation.


def _apply_kgx(font_name, size):
    return apply_terminal_font('kgx', font_name, size)


def _apply_gnome_terminal(font_name, size):
    return apply_terminal_font('gnome-terminal', font_name, size)


def _apply_alacritty(font_name, size):
    return apply_terminal_font('alacritty', font_name, size)


def _apply_kitty(font_name, size):
    return False, {'error': 'Kitty font switching is disabled: original font state cannot be reliably restored; use image previews.'}


def _restore_kitty(state):
    return False  # An unsupported/legacy record must never claim restoration.


def _restore_kgx(state):
    return restore_terminal_font('kgx', state)


def _restore_gnome_terminal(state):
    return restore_terminal_font('gnome-terminal', state)


def _restore_alacritty(state):
    return restore_terminal_font('alacritty', state)


def sanitize_xterm_font_family(font_name: str) -> str:
    """
    Sanitize and extract the exact primary Fontconfig family name for XTerm libXft.
    Strips style suffixes (Regular, Bold, Italic, Medium, Light, etc.) and colon parameters,
    and queries Fontconfig (fc-match) to resolve the canonical family name.
    """
    clean = font_name.split(":")[0].strip()

    common_styles = [
        "Regular", "Bold", "Italic", "Bold Italic", "Oblique",
        "Light", "Medium", "SemiBold", "DemiBold", "ExtraBold",
        "Thin", "UltraLight", "Black", "Heavy"
    ]
    for st in sorted(common_styles, key=len, reverse=True):
        suffix = " " + st
        if clean.lower().endswith(suffix.lower()):
            candidate = clean[:-len(suffix)].strip()
            if candidate:
                clean = candidate
                break

    try:
        res = subprocess.run(
            ["fc-match", "-f", "%{family}\n", clean],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            matched = res.stdout.strip().split(",")[0].strip()
            if matched and (clean.lower() in matched.lower() or matched.lower() in clean.lower()):
                clean = matched
    except Exception:
        pass

    return clean


def detect_xterm_xft_mode() -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Detect whether XTerm is running in TrueType (Xft/FreeType) mode or Legacy X11 Bitmap mode.
    In legacy bitmap mode, OSC 50 routes to XLoadQueryFont which does not understand the 'xft:'
    prefix, causing XTerm stderr error spam.
    TrueType mode is active ONLY if launched with '-fa' (or '-fn xft:...') or if
    XTerm*faceName or renderFont: true is present in the X11 resource database (xrdb).
    Returns: (is_xft_mode: bool, orig_font: Optional[str], orig_size: Optional[int])
    """
    is_xft = False
    orig_font = None
    orig_size = None

    # 1. Inspect /proc/<pid>/cmdline of xterm ancestor
    ancestry = get_process_ancestry()
    xterm_pid = None
    for pid, comm in ancestry:
        if comm.lower() in ["xterm", "urxvt", "urxvtd"]:
            xterm_pid = pid
            break

    if xterm_pid:
        cmdline_path = f"/proc/{xterm_pid}/cmdline"
        if os.path.isfile(cmdline_path):
            try:
                with open(cmdline_path, "rb") as f:
                    raw = f.read()
                args = [a.decode("utf-8", "ignore") for a in raw.split(b"\x00") if a]

                # Check -fa <font>
                if "-fa" in args:
                    idx = args.index("-fa")
                    if idx + 1 < len(args):
                        val = args[idx + 1]
                        is_xft = True
                        if ":size=" in val:
                            p = val.split(":size=")
                            orig_font = p[0]
                            if p[1].isdigit():
                                orig_size = int(p[1])
                        else:
                            orig_font = val

                # Check -fs <size>
                if "-fs" in args:
                    idx = args.index("-fs")
                    if idx + 1 < len(args) and args[idx + 1].isdigit():
                        orig_size = int(args[idx + 1])

                # Check -fn with xft:
                if "-fn" in args:
                    idx = args.index("-fn")
                    if idx + 1 < len(args) and args[idx + 1].startswith("xft:"):
                        is_xft = True
                        spec = args[idx + 1][4:]
                        if ":size=" in spec:
                            p = spec.split(":size=")
                            orig_font = p[0]
                            if p[1].isdigit():
                                orig_size = int(p[1])
                        else:
                            orig_font = spec
            except Exception:
                pass

    # 2. Check xrdb -query (if xrdb is available)
    xrdb_bin = shutil.which("xrdb")
    if xrdb_bin and os.path.isfile(xrdb_bin):
        try:
            res = subprocess.run([xrdb_bin, "-query"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    clean = line.strip()
                    if not clean or clean.startswith("!") or ":" not in clean:
                        continue
                    k, v = clean.split(":", 1)
                    k_lower = k.strip().lower()
                    v_val = v.strip()

                    # Match *renderFont: true or xterm*renderFont: true
                    if ("xterm" in k_lower or k_lower.startswith("*")) and "renderfont" in k_lower:
                        if v_val.lower() == "true":
                            is_xft = True

                    # Match *faceName or xterm*faceName
                    if ("xterm" in k_lower or k_lower.startswith("*")) and "facename" in k_lower:
                        if v_val and v_val.lower() != "none":
                            is_xft = True
                            if not orig_font:
                                orig_font = v_val

                    # Match *faceSize or xterm*faceSize
                    if ("xterm" in k_lower or k_lower.startswith("*")) and "facesize" in k_lower:
                        if v_val.isdigit() and not orig_size:
                            orig_size = int(v_val)
        except Exception:
            pass

    if is_xft:
        return True, orig_font or "Monospace", orig_size or 12
    return False, None, None


def _apply_xterm(font_name, size):
    return False, {'error': 'XTerm switching is disabled: OSC 50 state and restoration cannot be verified; use image previews.'}


def _restore_xterm(state):
    return False


def check_font_terminal_suitability(filepath: str, face_index: int = 0) -> Tuple[bool, str, Set[int]]:
    """
    Pre-flight suitability check for terminal grid fonts.
    Inspects FreeType cmap to ensure font contains basic printable ASCII glyphs (0x20-0x7E).
    Returns: (is_suitable: bool, reason: str, codepoints: Set[int])
    """
    from cefont.core.freetype_backend import get_all_codepoints

    if not filepath or not os.path.isfile(filepath):
        return True, "Filepath not available for FreeType inspection", set()

    try:
        cps = get_all_codepoints(filepath, face_index)
        if not cps:
            return True, "No codepoints extracted", set()

        required_ascii = set(range(0x20, 0x7F))  # 95 printable ASCII chars
        present_ascii = {c for c in cps if 0x20 <= c <= 0x7E}
        missing_ascii = required_ascii - present_ascii

        alphanumerics = (
            set(range(ord('0'), ord('9') + 1)) |
            set(range(ord('A'), ord('Z') + 1)) |
            set(range(ord('a'), ord('z') + 1))
        )

        missing_alphanumeric = alphanumerics - present_ascii
        if missing_alphanumeric:
            return False, f"Missing {len(missing_alphanumeric)} essential alphanumeric characters", cps

        if len(missing_ascii) > 5:
            return False, f"Missing {len(missing_ascii)} standard ASCII printable characters", cps

        return True, "Font contains full printable ASCII coverage", cps
    except FreeTypeUnavailableError:
        raise
    except Exception as e:
        return True, f"Check bypassed due to exception: {e}", set()


# =========================================================================
# 4. Master Dispatcher & Context Manager
# =========================================================================

_detached_sessions = {}


def _capture_terminal_plan(terminal_type, family, size):
    if not isinstance(family, str) or not family or any(ord(c) < 32 for c in family):
        raise RecoveryError('Font family must be nonempty and contain no control characters')
    if terminal_type in ('kgx', 'gnome-terminal'):
        return _capture_gsettings(terminal_type, family, size)
    if terminal_type == 'alacritty':
        return capture_alacritty(_find_alacritty_config(), family, size)
    if terminal_type == 'kitty':
        raise RecoveryError(_apply_kitty(family, size)[1]['error'])
    if terminal_type == 'xterm':
        raise RecoveryError(_apply_xterm(family, size)[1]['error'])
    raise RecoveryError("Terminal type '%s' has no reversible adapter" % terminal_type)


def _apply_terminal_plan(state):
    if state['kind'] == 'gsettings':
        _apply_gsettings(state)
    elif state['kind'] == 'alacritty':
        apply_alacritty(state)
    else:
        raise RecoveryError('Unsupported terminal plan')


def _restore_terminal_plan(terminal_type, state):
    if terminal_type in ('kgx', 'gnome-terminal') and state.get('kind') == 'gsettings':
        return _restore_gsettings(state)
    if terminal_type == 'alacritty' and state.get('kind') == 'alacritty':
        return restore_alacritty(state)
    raise RecoveryError('Unsupported or legacy terminal recovery state; record retained')


def apply_terminal_font(terminal_type: str, font_name: str, size: Optional[int] = None) -> Tuple[bool, Dict[str, Any]]:
    """Apply a recoverable change; callers must restore it or explicitly keep it."""
    context = TerminalFontContext(font_name, size)
    context._terminal_override = terminal_type
    context.__enter__()
    if not context.is_active:
        return False, {'error': context.fallback_error}
    _detached_sessions[context.owner] = context
    return True, dict(context.orig_state)


def restore_terminal_font(terminal_type: str, state: Dict[str, Any]) -> bool:
    """Restore an owned in-process change; never silently accept unknown state."""
    context = _detached_sessions.get(state.get('_recovery_owner'))
    if context is None or context.terminal_type != terminal_type:
        return False
    try:
        context.cleanup()
    except Exception as error:
        state['error'] = str(error)
        return False  # Record retained; explicit recover provides the detailed error.
    _detached_sessions.pop(context.owner, None)
    return True


def set_terminal_font_permanently(font_name: str, size: Optional[int] = None) -> Tuple[bool, str]:
    """Permanent changes use the same rollback protection until commit."""
    context = TerminalFontContext(font_name, size)
    with context:
        if not context.is_active:
            return False, context.fallback_error or 'Terminal switching unavailable'
        context.keep()
    return True, "Kept '%s' as the terminal font." % font_name


class TerminalFontContext:
    """Capture -> durable journal -> apply -> checked restore (or explicit keep)."""
    def __init__(self, font_name: str, size: Optional[int] = None, force_mock: bool = False):
        self.font_name = font_name
        self.size = size
        self.force_mock = force_mock
        self.terminal_type = 'unsupported'
        self.meta = {}
        self.orig_state = {}
        self.fallback_error = ''
        self.is_active = False
        self.owner = uuid.uuid4().hex
        self._store = None
        self._record = None
        self._orig_handlers = {}
        self._terminal_override = None
        self._mutation_started = False
        self.restored = False

    def _release(self):
        try:
            for sig, handler in self._orig_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError) as error:
                    print('CeFont could not restore a signal handler: %s' % error, file=sys.stderr)
        finally:
            self._orig_handlers.clear()
            atexit.unregister(self._at_exit)
            if self._store is not None:
                self._store.close()
                self._store = None

    def _at_exit(self):
        try:
            self.cleanup()
        except BaseException as error:
            print('CeFont recovery failed; journal retained: %s' % error, file=sys.stderr)

    def __enter__(self):
        if self.force_mock:
            self.terminal_type = 'mock'
            return self
        self._store = RecoveryStore(RECOVERY_FILE).acquire()
        try:
            _recover_locked(self._store)
            detected, self.meta = detect_terminal_emulator()
            self.terminal_type = self._terminal_override or detected
            try:
                self.orig_state = _capture_terminal_plan(self.terminal_type, self.font_name, self.size)
            except Exception as error:
                self.fallback_error = str(error)
                self.terminal_type = 'mock'
                self._release()
                return self
            self.orig_state['_recovery_owner'] = self.owner
            self._record = dict(version=2, owner=self.owner, pid=os.getpid(), intent='restore',
                                terminal_type=self.terminal_type, original_state=self.orig_state)
            write_recovery_journal(self._record, store=self._store)
            self.is_active = True
            def interrupted(signum, frame):
                raise SystemExit(128 + signum)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self._orig_handlers[sig] = signal.signal(sig, interrupted)
            atexit.register(self._at_exit)
            self._mutation_started = True
            _apply_terminal_plan(self.orig_state)
            return self
        except BaseException as error:
            if self.is_active:
                # Includes interruption, partial writes and post-replace fsync errors.
                self.cleanup()
                if isinstance(error, Exception):
                    result = 'original settings restored' if self.restored else 'no settings changed'
                    self.fallback_error = 'Application failed; %s: %s' % (result, error)
                    self.terminal_type = 'mock'
                    return self
            self._release()
            if isinstance(error, Exception) and not isinstance(error, RecoveryError):
                raise RecoveryError('Initialization failed before mutation; inspect %s: %s' % (RECOVERY_FILE, error)) from error
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

    def _owned_store(self):
        if self._store is None:
            self._store = RecoveryStore(RECOVERY_FILE).acquire()
        record = self._store.read()
        if not record or record.get('owner') != self.owner:
            raise RecoveryError('This context no longer owns the recovery record')
        return self._store

    def keep(self):
        if not self.is_active:
            return False
        try:
            store = self._owned_store()
            record = dict(self._record, intent='keep')
            # A crash after this durable decision must not revert an explicit keep.
            write_recovery_journal(record, store=store)
            self._record = record
            self.is_active = False
            clear_recovery_journal(self.owner, store=store)
            return True
        except Exception as error:
            outcome = 'Keep failed; restoration remains required' if self.is_active else 'Font change kept; record cleanup failed'
            raise RecoveryError('%s; inspect %s: %s' % (outcome, RECOVERY_FILE, error)) from error
        finally:
            self._release()

    def cleanup(self):
        if not self.is_active:
            return
        try:
            store = self._owned_store()
            if self._mutation_started and not _restore_terminal_plan(self.terminal_type, self.orig_state):
                raise RecoveryError('Terminal restoration failed; recovery record retained')
            clear_recovery_journal(self.owner, store=store)
            self.is_active = False
            self.restored = self._mutation_started
        except Exception as error:
            raise RecoveryError('Restoration/recovery cleanup failed at %s: %s' % (RECOVERY_FILE, error)) from error
        finally:
            self._release()
