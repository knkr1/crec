"""Where crec keeps things.

Three separate roots, deliberately:

* ``config_dir``   - user-editable. Holds ``presets.toml``. Never written by us.
* ``data_dir``     - our private working area: fetched binaries, state, cache.
* ``downloads_dir``- where finished media lands.

``data_dir`` must be user-writable even for a frozen ``crec.exe``, because the
runtime-updatable copy of yt-dlp lives in ``data_dir/lib`` and gets prepended to
``sys.path`` before we import it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "crec" if base else Path.home() / "AppData" / "Local" / "crec"


def _windows_config() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) / "crec" if base else Path.home() / "AppData" / "Roaming" / "crec"


def data_dir() -> Path:
    if sys.platform == "win32":
        return _windows_data()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "crec"
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "crec" if base else Path.home() / ".local" / "share" / "crec"


def config_dir() -> Path:
    if sys.platform == "win32":
        return _windows_config()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "crec"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "crec" if base else Path.home() / ".config" / "crec"


def bin_dir() -> Path:
    """Fetched ffmpeg/ffprobe live here."""
    return data_dir() / "bin"


def lib_dir() -> Path:
    """The updatable yt-dlp lives here. See deps.activate_ytdlp()."""
    return data_dir() / "lib"


def user_presets_file() -> Path:
    return config_dir() / "presets.toml"


def state_file() -> Path:
    return data_dir() / "state.json"


def downloads_dir() -> Path:
    """Default output folder.

    ``~/crec`` for continuity with v1 - short to type, easy to find, and users
    coming from v1 already have it. Overridable per-run with ``-o`` or globally
    via ``output_dir`` in presets.toml's ``[defaults]``.
    """
    override = os.environ.get("CREC_OUTPUT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "crec"


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
