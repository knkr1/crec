"""The yt-dlp half of crec.

yt-dlp is used as a *library*, not a subprocess. That buys real progress hooks,
structured format selection and the actual output paths, instead of scraping
stdout and guessing where the file went.

Import order matters: :func:`crec.deps.activate_ytdlp` has to run before
``import yt_dlp`` so the user-updatable copy wins, so the import is deferred into
the functions here rather than sitting at module scope.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import deps, media
from .errors import CrecError, ExtractorFailure
from .options import Options

# Signatures that mean "the site changed and yt-dlp is behind", as opposed to
# "this URL is wrong". Only these justify auto-updating and retrying.
_STALE_SIGNATURES = (
    "unable to extract",
    "failed to extract",
    "signature extraction failed",
    "nsig extraction failed",
    "unable to download webpage: http error 403",
    "player response",
    "requested format is not available",
    "no video formats found",
    "confirm your age",
    "unable to find",
)


@dataclass
class Progress:
    """A single rewritten status line. Quiet when not attached to a terminal."""

    enabled: bool = True
    _last: float = field(default=0.0, repr=False)
    _label: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not sys.stderr.isatty():
            self.enabled = False

    def stage(self, label: str) -> None:
        self._label = label
        if self.enabled:
            self._write(f"{label}...")

    def hook(self, status: dict) -> None:
        if not self.enabled:
            return
        state = status.get("status")
        if state == "downloading":
            now = time.monotonic()
            if now - self._last < 0.08:  # don't spend the run redrawing
                return
            self._last = now
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            speed = status.get("speed") or 0
            pct = (done * 100 / total) if total else 0
            bar_width = 24
            filled = int(pct / 100 * bar_width)
            bar = "#" * filled + "." * (bar_width - filled)
            rate = f"{speed / 1_048_576:.1f} MB/s" if speed else "--"
            eta = status.get("eta")
            eta_text = f" ETA {int(eta) // 60:d}:{int(eta) % 60:02d}" if eta else ""
            self._write(f"downloading [{bar}] {pct:5.1f}%  {rate}{eta_text}")
        elif state == "finished":
            self._write("downloaded, processing...")

    def _write(self, text: str) -> None:
        sys.stderr.write(f"\r\033[K{text}")
        sys.stderr.flush()

    def done(self) -> None:
        if self.enabled:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def _format_selector(options: Options) -> str:
    if options.audio_only:
        return "bestaudio/best"

    height = options.quality
    if options.no_audio:
        base = f"bestvideo[height<={height}]/bestvideo" if height else "bestvideo"
        return f"{base}/best"

    if height:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"bestvideo+bestaudio/best"
        )
    return "bestvideo+bestaudio/best"


def _output_template(options: Options) -> str:
    if options.filename:
        return options.filename
    if options.playlist:
        return "%(playlist_title|Playlist)s/%(playlist_index|0)02d - %(title)s.%(ext)s"
    return "%(title)s.%(ext)s"


def build_ydl_opts(options: Options, output_dir: Path, progress: Progress) -> dict:
    opts: dict = {
        "outtmpl": {"default": str(output_dir / _output_template(options))},
        "format": _format_selector(options),
        "noplaylist": not options.playlist,
        "progress_hooks": [progress.hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        # Portable names even when the output lands on a share, a USB stick, or
        # gets synced to a Windows box from elsewhere. Costs nothing.
        "windowsfilenames": True,
        "trim_file_name": 120,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
        "ffmpeg_location": str(deps.ffmpeg_path().parent),
    }

    if options.thumbnail:
        opts["writethumbnail"] = True

    postprocessors: list[dict] = []
    if options.audio_only:
        audio_format = options.format if options.wants_audio_container else "mp3"
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format or "mp3",
                "preferredquality": str(options.audio_quality or 192),
            }
        )
        if options.thumbnail:
            postprocessors.append({"key": "EmbedThumbnail"})
    elif options.format in {"mp4", "mkv", "webm"}:
        opts["merge_output_format"] = options.format

    if postprocessors:
        opts["postprocessors"] = postprocessors

    # Ask the site for only the range we want. On YouTube this downloads a
    # fraction of the bytes instead of pulling the whole video and throwing most
    # of it away. Falls back to a local trim if the extractor ignores it.
    if options.cut:
        opts["download_ranges"] = _static_range(options.cut)
        opts["force_keyframes_at_cuts"] = options.exact_cut

    return opts


def _static_range(spec: str):
    """Build yt-dlp's download-ranges callback for a fixed span."""
    start, end = media.parse_cut(spec)

    def ranges(_info, _ydl):
        return [{"start_time": start, "end_time": end if end is not None else float("inf")}]

    return ranges


@dataclass
class Downloaded:
    """A finished download, plus what the *source* looked like.

    ``source_duration`` is the untrimmed length as the site reported it. Without
    it there is no way to tell whether an open-ended ``--cut`` was honoured or
    quietly ignored, which would hand the user the whole video in silence.
    """

    path: Path
    source_duration: float | None = None


def _collect(info: dict) -> list[Downloaded]:
    """Pull real output paths out of yt-dlp's result, post-processing included."""
    found: list[Downloaded] = []

    def absorb(entry: dict) -> None:
        duration = entry.get("duration")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        paths: list[Path] = []
        for requested in entry.get("requested_downloads") or []:
            for key in ("filepath", "_filename"):
                value = requested.get(key)
                if value:
                    paths.append(Path(value))
                    break
        if not paths:
            for key in ("filepath", "_filename"):
                value = entry.get(key)
                if value:
                    paths.append(Path(value))
                    break
        found.extend(Downloaded(path=path, source_duration=duration) for path in paths)

    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            if entry:
                absorb(entry)
    else:
        absorb(info)

    unique: list[Downloaded] = []
    seen: set[Path] = set()
    for item in found:
        if item.path not in seen and item.path.exists():
            seen.add(item.path)
            unique.append(item)
    return unique


def _looks_stale(message: str) -> bool:
    lowered = message.lower()
    return any(signature in lowered for signature in _STALE_SIGNATURES)


def fetch(
    url: str,
    options: Options,
    output_dir: Path,
    progress: Progress,
    *,
    allow_update_retry: bool = True,
) -> list[Downloaded]:
    """Download *url*, auto-updating yt-dlp once if the site looks broken."""
    deps.activate_ytdlp()
    import yt_dlp  # noqa: PLC0415 - must follow activate_ytdlp()

    opts = build_ydl_opts(options, output_dir, progress)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        if allow_update_retry and _looks_stale(message):
            # This is the moment an update is worth its latency: the site just
            # broke, so a stale extractor is the single likeliest cause.
            progress.done()
            print("This site looks broken - updating yt-dlp and retrying...", file=sys.stderr)
            changed, note = deps.update_ytdlp()
            print(note, file=sys.stderr)
            if changed:
                _forget_ytdlp()
                return fetch(url, options, output_dir, progress, allow_update_retry=False)
        raise ExtractorFailure(_tidy(message)) from exc
    except yt_dlp.utils.YoutubeDLError as exc:
        raise CrecError(_tidy(str(exc))) from exc

    if info is None:
        raise CrecError("yt-dlp returned nothing for that URL.")

    results = _collect(info)
    if not results:
        raise CrecError("The download finished but crec could not find the output file.")
    return results


def _forget_ytdlp() -> None:
    """Drop the loaded yt_dlp so the freshly updated copy gets imported."""
    for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        del sys.modules[name]


def _tidy(message: str) -> str:
    text = message.replace("ERROR: ", "").strip()
    for noise in (
        "; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues",
        " Confirm you are on the latest version using  yt-dlp -U",
    ):
        text = text.split(noise)[0]
    return text.splitlines()[0] if text else "Download failed."


@dataclass
class Peek:
    """What the site says about a video before we download a byte of it."""

    title: str | None = None
    duration: float | None = None
    height: int | None = None


def peek(url: str) -> Peek | None:
    """Metadata-only lookup. Returns None if the site won't say.

    Used to size the download *before* making it: targeting 10 MB means there is
    no reason to pull a 4K stream just to throw the pixels away.
    """
    deps.activate_ytdlp()
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    if not info:
        return None
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else {}

    def number(key: str) -> float | None:
        try:
            value = info.get(key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    duration = number("duration")
    height = number("height")
    if height is None:
        # Not every extractor reports a top-level height; fall back to formats.
        heights = [
            f.get("height") for f in (info.get("formats") or []) if f.get("height")
        ]
        height = float(max(heights)) if heights else None

    return Peek(
        title=info.get("title"),
        duration=duration if duration and duration > 0 else None,
        height=int(height) if height else None,
    )
