"""A little memory between runs.

Exists because success is self-destructive: crec replaces the clipboard's *text*
URL with the finished *file*, so re-running ``crec`` afterwards would find no URL.
Remembering the last one turns that wart into a feature - ``crec`` then
``crec -a`` gives you the audio of the same video without re-copying anything.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import paths


def _load() -> dict[str, Any]:
    try:
        return json.loads(paths.state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        paths.ensure(paths.data_dir())
        paths.state_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # a lost memory is never worth failing a download over


def remember(url: str, outputs: list[Path] | None = None) -> None:
    data = _load()
    data["last_url"] = url
    data["last_at"] = time.time()
    if outputs:
        data["last_files"] = [str(path) for path in outputs]
    _save(data)


def last_url() -> str | None:
    return _load().get("last_url")


def last_files() -> list[Path]:
    return [Path(p) for p in _load().get("last_files", [])]
