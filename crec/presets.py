"""Preset loading and merging.

A preset is a named bundle of :mod:`crec.options` primitives living in TOML.
Built-ins ship inside the package; the user's file is merged on top, so any
built-in can be redefined without a crec release (which is the point - platform
upload limits change more often than we ship).

Resolution order, lowest priority first:

1. ``[defaults]``
2. each preset named on the command line, in the order given
3. explicit command-line flags

Every preset name becomes ``--<name>`` automatically, so adding a flag to crec
means adding four lines of TOML.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .errors import CrecError
from .options import validate_preset_values


def _builtin_file() -> Path:
    """Locate the shipped presets, running from source or from a frozen exe.

    PyInstaller unpacks bundled data under ``sys._MEIPASS``, where ``__file__``
    is not necessarily a real directory we can read siblings from.
    """
    beside = Path(__file__).with_name("presets.toml")
    if beside.is_file():
        return beside
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for candidate in (base / "crec" / "presets.toml", base / "presets.toml"):
            if candidate.is_file():
                return candidate
    return beside


BUILTIN_FILE = _builtin_file()

# Names that are commands rather than flags, so a preset can never shadow one.
# Flag collisions are detected against the real parser in crec.cli instead of
# being listed here - a hand-maintained list drifts the moment someone adds a flag.
RESERVED = {"update", "doctor", "presets", "tray"}

# Tables that configure something other than a preset.
HOTKEYS_TABLE = "hotkeys"


@dataclass
class Preset:
    name: str
    description: str
    values: dict[str, Any]
    source: str  # "built-in" or "user"


@dataclass
class PresetSet:
    presets: dict[str, Preset]
    defaults: dict[str, Any]
    warnings: list[str]

    def get(self, name: str) -> Preset | None:
        return self.presets.get(name)

    def names(self) -> list[str]:
        return sorted(self.presets)


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise CrecError(f"{path} is not valid TOML: {exc}") from exc


def _flag_name(name: str) -> str:
    return name.replace("_", "-").lower()


def load() -> PresetSet:
    """Load built-in presets, then merge the user's file over them."""
    presets: dict[str, Preset] = {}
    defaults: dict[str, Any] = {}
    warnings: list[str] = []

    for source, path in (("built-in", BUILTIN_FILE), ("user", paths.user_presets_file())):
        raw = _read(path)
        for name, values in raw.items():
            if name == HOTKEYS_TABLE:
                continue
            if not isinstance(values, dict):
                warnings.append(
                    f"{path.name}: '{name}' is not a table - "
                    f"presets look like [{name}] on its own line"
                )
                continue

            problems = validate_preset_values(name, values)
            if problems:
                # A bad user preset must never take the whole tool down; drop it
                # and say why, so `crec` still works while they fix it.
                warnings.extend(f"{path.name}: {p}" for p in problems)
                if source == "user":
                    continue

            body = {k: v for k, v in values.items() if k != "description"}

            if name == "defaults":
                defaults.update(body)
                continue

            flag = _flag_name(name)
            if flag in RESERVED:
                warnings.append(
                    f"{path.name}: preset '{name}' clashes with a built-in flag "
                    f"and was ignored - rename it"
                )
                continue

            presets[flag] = Preset(
                name=flag,
                description=str(values.get("description", "")),
                values=body,
                source=source,
            )

    return PresetSet(presets=presets, defaults=defaults, warnings=warnings)


def load_hotkeys() -> tuple[dict[str, str], list[str]]:
    """``{combo: crec flags}`` for the tray, plus warnings.

    The user's ``[hotkeys]`` replaces the built-in table outright rather than
    merging: if you rebind the shortcuts, the old ones shouldn't linger.
    """
    warnings: list[str] = []
    chosen: dict[str, str] = {}
    for path in (BUILTIN_FILE, paths.user_presets_file()):
        table = _read(path).get(HOTKEYS_TABLE)
        if table is None:
            continue
        if not isinstance(table, dict):
            warnings.append(f"{path.name}: [hotkeys] must be a table")
            continue
        chosen = {}
        for combo, flags in table.items():
            if not isinstance(flags, str):
                warnings.append(f"{path.name}: hotkey '{combo}' should map to a string of flags")
                continue
            chosen[combo] = flags
    return chosen, warnings


def write_starter_file(path: Path) -> bool:
    """Create a commented starter presets file. False if one already exists."""
    if path.exists():
        return False
    paths.ensure(path.parent)
    path.write_text(
        "# Your crec presets. Each table below becomes a --flag.\n"
        "# These are merged on top of the built-ins, so redefining a built-in\n"
        "# name overrides it. Run `crec presets --keys` for the available keys.\n"
        "\n"
        "# Applied to every run:\n"
        "# [defaults]\n"
        "# output_dir = \"D:/clips\"\n"
        "\n"
        "# A flag of your own -> `crec --clip`\n"
        "# [clip]\n"
        "# description = \"720p, under 8 MB, muted\"\n"
        "# quality = 720\n"
        "# target_mb = 8\n"
        "# no_audio = true\n"
        "\n"
        "# Global shortcuts for `crec tray` (this replaces the built-in ones):\n"
        "# [hotkeys]\n"
        "# \"ctrl+shift+alt+c\" = \"\"\n"
        "# \"ctrl+shift+alt+d\" = \"--discord\"\n"
        "# \"ctrl+shift+alt+g\" = \"--gif\"\n",
        encoding="utf-8",
    )
    return True
