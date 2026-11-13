"""User actions management (Actions.json) and execution."""

import json
import os
import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from cefont.core.state import read_json, write_json, serialized
from cefont.core.config import ACTIONS_FILE, ensure_directories
from cefont.core.db import get_detailed_font_info, get_fonts_by_family


class ActionManager:
    """Manages custom user actions defined in Actions.json."""

    def __init__(self, filepath: Optional[str] = None):
        ensure_directories()
        self.filepath = filepath or ACTIONS_FILE

    def load_actions(self) -> List[Dict]:
        """Load all configured actions."""
        return read_json(self.filepath)

    def save_actions(self, actions: List[Dict]):
        """Save actions list to Actions.json."""
        write_json(self.filepath, actions)

    @serialized(lambda self, *args, **kwargs: [self.filepath])
    def add_action(
        self,
        name: str,
        executable: str,
        arguments: str = "FILEPATH",
        comment: str = "",
        icon: str = "system-run-symbolic",
    ) -> Dict:
        """Add a new action."""
        actions = self.load_actions()
        action = {
            "action_name": name,
            "executable": executable,
            "arguments": arguments,
            "comment": comment,
            "action_icon": icon,
        }
        actions.append(action)
        self.save_actions(actions)
        return action

    @serialized(lambda self, *args, **kwargs: [self.filepath])
    def remove_action(self, name_or_index: str) -> bool:
        """Remove an action by name or zero-based index."""
        actions = self.load_actions()
        idx_to_remove = None

        if name_or_index.isdigit():
            idx = int(name_or_index)
            if 0 <= idx < len(actions):
                idx_to_remove = idx

        if idx_to_remove is None:
            for i, a in enumerate(actions):
                if a.get("action_name", "").lower() == name_or_index.lower():
                    idx_to_remove = i
                    break

        if idx_to_remove is not None:
            actions.pop(idx_to_remove)
            self.save_actions(actions)
            return True
        return False

    def execute_action(
        self,
        name_or_index: str,
        font_identifier: Optional[str] = None,
        background: bool = True
    ) -> Tuple[bool, str]:
        """
        Execute an action with placeholders replaced for the given font.
        Placeholders: FILEPATH, FAMILY, STYLE.
        """
        actions = self.load_actions()
        action = None

        if name_or_index.isdigit():
            idx = int(name_or_index)
            if 0 <= idx < len(actions):
                action = actions[idx]

        if not action:
            for a in actions:
                if a.get("action_name", "").lower() == name_or_index.lower():
                    action = a
                    break

        if not action:
            return False, f"Action '{name_or_index}' not found."

        executable = action.get("executable", "").strip()
        if not executable:
            return False, "Action has no executable defined."

        raw_args = action.get("arguments", "")

        font_info = None
        if font_identifier:
            if os.path.isfile(font_identifier):
                font_info = get_detailed_font_info(font_identifier)
            else:
                fonts = get_fonts_by_family(font_identifier)
                if fonts:
                    font_info = get_detailed_font_info(fonts[0]["filepath"], fonts[0].get("findex", 0))

        filepath = font_info["filepath"] if font_info else (font_identifier or "")
        family = font_info.get("family", "") if font_info else ""
        style = font_info.get("style", "Regular") if font_info else ""

        substituted_args = raw_args
        if filepath:
            quoted_fp = shlex.quote(filepath)
            substituted_args = substituted_args.replace("FILEPATH", quoted_fp)
            substituted_args = substituted_args.replace("FAMILY", shlex.quote(family))
            substituted_args = substituted_args.replace("STYLE", shlex.quote(style))
            if quoted_fp not in substituted_args and filepath not in substituted_args:
                substituted_args += f" {quoted_fp}"

        cmd = f"{executable} {substituted_args}".strip()

        try:
            if background:
                subprocess.Popen(cmd, shell=True)
                return True, f"Spawned action: {cmd}"
            else:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                return res.returncode == 0, f"Executed (exit {res.returncode}): {cmd}\nOutput: {res.stdout}\nError: {res.stderr}"
        except Exception as e:
            return False, f"Execution failed: {str(e)}"
