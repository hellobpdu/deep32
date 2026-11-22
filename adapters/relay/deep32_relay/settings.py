"""Operator settings of the relay service, read from its environment once, when the service starts.

None of them is part of the recorded install configuration (connector._config): an install receipt stores that
configuration and every start compares it whole, so a setting kept there could not change without breaking
installed relays. A name that is absent or empty means its default. A value that is present and not valid stops
the service with the reason invalid-relay-setting: the relay never runs with a setting its operator did not write.

    DEEP32_RELAY_IDLE_SECONDS   60 to 900; how long a conversation may sit idle before the relay closes it (900)
    DEEP32_RELAY_CHECKS         footer or off; footer checks every finished answer by script (answer_check.py)
                                and shows the result under the answer (off)
    DEEP32_RELAY_MODEL          a full model id; what the default model choice of an Ask conversation runs
                                (unset: the agent's own default, as before)
    DEEP32_RELAY_EFFORT         an effort level; what the default effort choice runs (unset: the agent's own)
    DEEP32_RELAY_ANSWER_RECORD  on or off; on writes one record per answer (record.py): what answered, on which
                                index, the tool calls' ids and the check's counts, never a text (off)
    DEEP32_RELAY_SAVE_DIR       the absolute path of an existing folder, an Obsidian vault: every finished answer is
                                saved there as a note (vault.py), and each turn asks the agent to end its answer
                                with a Blueprint line naming its exam topic (unset: nothing is saved)
    DEEP32_RELAY_SAVE_MAP       the absolute path of a map file (TSV: exam id, tab, folder under the vault); a note
                                whose Blueprint id it maps to an existing folder is saved there, every other one in
                                the vault's _inbox; needs DEEP32_RELAY_SAVE_DIR (unset: every note goes to _inbox)
    DEEP32_RELAY_SAVE_HOOK      the absolute path of a program run after saving, with the vault folder as its only
                                argument, no shell and at most 60 seconds; its failure is logged and never fails an
                                answer; needs DEEP32_RELAY_SAVE_DIR (unset: none)
"""
from dataclasses import dataclass
import os
import re

from .state import StateError

IDLE_MIN, IDLE_MAX, IDLE_DEFAULT = 60, 900, 900
CHECKS_DEFAULT = "off"
# A model id as an agent takes it on its command line or in its configuration: no space, never a leading dash.
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]/-]{0,127}")
EFFORT = re.compile(r"[a-z]{1,16}")


@dataclass(frozen=True)
class Settings:
    idle_seconds: int = IDLE_DEFAULT
    checks: str = CHECKS_DEFAULT
    model: str = ""
    effort: str = ""
    answer_record: bool = False
    save_dir: str = ""
    save_folders: tuple = ()    # the map's ("<exam> <id>", folder under the vault) pairs (DEEP32_RELAY_SAVE_MAP)
    save_hook: str = ""


def load(environ, agent=None):
    """The settings in `environ` (a mapping such as os.environ) for a relay of `agent`. Raises StateError."""
    def text(name):
        value = environ.get(name, "")
        return value.strip() if isinstance(value, str) else ""

    idle = text("DEEP32_RELAY_IDLE_SECONDS")
    if idle and not (idle.isascii() and idle.isdigit() and len(idle) <= 4 and IDLE_MIN <= int(idle) <= IDLE_MAX):
        raise StateError("invalid-relay-setting")
    checks = text("DEEP32_RELAY_CHECKS")
    if checks not in ("", "footer", "off"):
        raise StateError("invalid-relay-setting")
    model = text("DEEP32_RELAY_MODEL")
    if model and not MODEL.fullmatch(model):
        raise StateError("invalid-relay-setting")
    effort = text("DEEP32_RELAY_EFFORT")
    if effort:
        from .native import CLAUDE_EFFORTS
        # Claude Code's --effort takes these levels only. Codex's are its own; "default" is said by leaving it unset.
        known = effort in CLAUDE_EFFORTS if agent == "claude" else EFFORT.fullmatch(effort) and effort != "default"
        if not known:
            raise StateError("invalid-relay-setting")
    record = text("DEEP32_RELAY_ANSWER_RECORD")
    if record not in ("", "on", "off"):
        raise StateError("invalid-relay-setting")
    save_dir, save_map, save_hook = text("DEEP32_RELAY_SAVE_DIR"), text("DEEP32_RELAY_SAVE_MAP"), \
        text("DEEP32_RELAY_SAVE_HOOK")
    folders = ()
    if save_dir or save_map or save_hook:
        from . import vault
        try:
            # A map or a hook without the vault would do nothing: that is a setting written wrongly too.
            if not save_dir or not os.path.isabs(save_dir) or not os.path.isdir(save_dir):
                raise ValueError("invalid-save-dir")
            folders = vault.read_map(save_map) if save_map else ()
            if save_hook and not vault.runnable(save_hook):
                raise ValueError("invalid-save-hook")
        except (OSError, ValueError):
            raise StateError("invalid-relay-setting") from None
    return Settings(idle_seconds=int(idle) if idle else IDLE_DEFAULT, checks=checks or CHECKS_DEFAULT,
                    model=model, effort=effort, answer_record=record == "on", save_dir=save_dir,
                    save_folders=folders, save_hook=save_hook)
