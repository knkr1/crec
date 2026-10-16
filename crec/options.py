"""The engine primitives.

This is the whole vocabulary crec understands. Presets, CLI flags and config
files all reduce to an :class:`Options` instance before anything runs, which is
what keeps new features cheap: a new idea is usually a preset (a TOML block
composing these), not new code.

Add a primitive only when no combination of the existing ones can express the
idea. When you do, add it here and everything downstream - ``--help``,
``crec presets --keys``, preset validation - picks it up for free.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class Primitive:
    name: str
    type: type
    help: str


# Order here is the order shown by `crec presets --keys`.
PRIMITIVES: tuple[Primitive, ...] = (
    Primitive("quality", int, "Max video height, e.g. 720"),
    Primitive("audio_only", bool, "Discard video, keep audio"),
    Primitive("no_audio", bool, "Discard audio, keep video"),
    Primitive("audio_quality", int, "Audio bitrate in kbps, e.g. 320"),
    Primitive("cut", str, "Trim to a range, e.g. 0:10-0:40 or 90-150"),
    Primitive("exact_cut", bool, "Frame-exact trim (re-encodes, slower)"),
    Primitive("target_mb", float, "Re-encode to land under this many MB"),
    Primitive("crf", int, "x264/x265 quality, 18=great 28=small"),
    Primitive("format", str, "Container/codec: mp4 mkv webm gif mp3 m4a wav"),
    Primitive("fps", int, "Force output frame rate"),
    Primitive("scale", int, "Max output width in pixels"),
    Primitive("thumbnail", bool, "Also save the thumbnail"),
    Primitive("playlist", bool, "Download the whole playlist, not just one item"),
    Primitive("output_dir", str, "Where finished files land"),
    Primitive("filename", str, "yt-dlp output template"),
    Primitive("cookies", str, "Borrow logins from a browser (firefox, chrome:Profile 1) or a cookies.txt"),
    Primitive("copy", bool, "Put the finished file on the clipboard"),
    Primitive("open_folder", bool, "Reveal the file in the file manager when done"),
    Primitive("ffmpeg_args", list, "Extra raw ffmpeg arguments, appended last"),
)

PRIMITIVES_BY_NAME: dict[str, Primitive] = {p.name: p for p in PRIMITIVES}


@dataclass
class Options:
    """A fully resolved run. Every field maps 1:1 to a primitive above."""

    quality: int | None = None
    audio_only: bool = False
    no_audio: bool = False
    audio_quality: int | None = None
    cut: str | None = None
    exact_cut: bool = False
    target_mb: float | None = None
    crf: int | None = None
    format: str | None = None
    fps: int | None = None
    scale: int | None = None
    thumbnail: bool = False
    playlist: bool = False
    output_dir: str | None = None
    filename: str | None = None
    cookies: str | None = None
    copy: bool = True
    open_folder: bool = False
    ffmpeg_args: list[str] | None = None

    def merged_with(self, values: dict[str, Any]) -> "Options":
        """Return a copy with *values* laid on top. Ignores None (= unset)."""
        known = {f.name for f in fields(self)}
        out = Options(**{f.name: getattr(self, f.name) for f in fields(self)})
        for key, value in values.items():
            if value is None or key not in known:
                continue
            setattr(out, key, value)
        return out

    # --- derived questions the rest of the code asks ------------------------

    @property
    def wants_audio_container(self) -> bool:
        return self.audio_only or (self.format or "") in {"mp3", "m4a", "wav", "opus", "flac"}

    @property
    def needs_reencode(self) -> bool:
        """True when ffmpeg has to actually re-compress rather than remux."""
        return any(
            (
                self.target_mb is not None,
                self.crf is not None,
                self.fps is not None,
                self.scale is not None,
                # Not exact_cut: the trim itself handles that, and counting it
                # here only bought a pointless remux of the finished file.
                (self.format or "") == "gif",
                bool(self.ffmpeg_args),
            )
        )


def validate_preset_values(preset_name: str, values: dict[str, Any]) -> list[str]:
    """Return human-readable problems with a preset's keys. Empty means fine."""
    problems: list[str] = []
    for key, value in values.items():
        if key == "description":
            continue
        primitive = PRIMITIVES_BY_NAME.get(key)
        if primitive is None:
            suggestion = _closest(key)
            hint = f" Did you mean '{suggestion}'?" if suggestion else ""
            problems.append(f"[{preset_name}] unknown key '{key}'.{hint}")
            continue
        if primitive.type is float and isinstance(value, int) and not isinstance(value, bool):
            continue  # TOML ints are fine for float primitives
        if not isinstance(value, primitive.type) or (
            primitive.type is not bool and isinstance(value, bool)
        ):
            problems.append(
                f"[{preset_name}] '{key}' should be {primitive.type.__name__}, "
                f"got {type(value).__name__}"
            )
    return problems


def _closest(key: str) -> str | None:
    import difflib

    matches = difflib.get_close_matches(key, list(PRIMITIVES_BY_NAME), n=1, cutoff=0.6)
    return matches[0] if matches else None
