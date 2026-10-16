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
from collections.abc import Callable
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
    "unable to find",
)

# yt-dlp's own login errors all end in a hint naming its cookie flags, which is a
# steadier signal than chasing each extractor's wording. The rest are the
# phrasings that come without that hint. Updating yt-dlp never fixes these, so
# they are checked before the stale signatures.
_LOGIN_SIGNATURES = (
    "--cookies",
    "cookies-from-browser",
    "login required",
    "log in to",
    "sign in to confirm",
    "confirm your age",
    "age-restricted",
    "private video",
    "this account is private",
)

# Chromium on Windows locks its cookie database while running and, since
# Chrome 127, encrypts cookies so only the browser itself can read them.
_CHROMIUM = {"chrome", "chromium", "edge", "brave", "opera", "vivaldi", "whale"}


@dataclass
class Progress:
    """A single rewritten status line. Quiet when not attached to a terminal.

    ``listener``, if set, hears a short plain-text status for every update
    regardless - that is how the tray icon's tooltip follows along.
    """

    enabled: bool = True
    listener: Callable[[str], None] | None = field(default=None, repr=False)
    _last: float = field(default=0.0, repr=False)
    _label: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not sys.stderr.isatty():
            self.enabled = False

    def stage(self, label: str) -> None:
        self._label = label
        if self.listener:
            self.listener(label)
        if self.enabled:
            self._write(f"{label}...")

    def hook(self, status: dict) -> None:
        if not (self.enabled or self.listener):
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
            if self.listener:
                self.listener(f"downloading {pct:.0f}%" if total else "downloading")
            if not self.enabled:
                return
            bar_width = 24
            filled = int(pct / 100 * bar_width)
            bar = "#" * filled + "." * (bar_width - filled)
            rate = f"{speed / 1_048_576:.1f} MB/s" if speed else "--"
            eta = status.get("eta")
            eta_text = f" ETA {int(eta) // 60:d}:{int(eta) % 60:02d}" if eta else ""
            self._write(f"downloading [{bar}] {pct:5.1f}%  {rate}{eta_text}")
        elif state == "finished":
            if self.listener:
                self.listener("processing")
            if self.enabled:
                self._write("downloaded, processing...")

    def _write(self, text: str) -> None:
        sys.stderr.write(f"\r\033[K{text}")
        sys.stderr.flush()

    def done(self) -> None:
        if self.enabled:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


class _SilentLogger:
    """``quiet`` does not stop yt-dlp printing ``ERROR:`` lines itself; crec
    reports every failure in its own words, so swallow them here."""

    def debug(self, _message: str) -> None:
        pass

    info = warning = error = debug


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
        # The folder goes in `paths`, not the template: yt-dlp's trim_file_name
        # cuts the whole template string, so a long output folder got truncated
        # into a wrong directory and a one-letter filename.
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": _output_template(options)},
        "format": _format_selector(options),
        "noplaylist": not options.playlist,
        "progress_hooks": [progress.hook],
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
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

    opts.update(cookie_opts(options.cookies))

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


def cookie_opts(spec: str | None) -> dict:
    """yt-dlp options for ``--cookies``: a cookies.txt path, or ``browser[:profile]``."""
    if not spec:
        return {}
    as_file = Path(spec).expanduser()
    if as_file.is_file():
        return {"cookiefile": str(as_file)}
    if any(mark in spec for mark in ("/", "\\")) or spec.lower().endswith(".txt"):
        # Clearly meant as a file; splitting "C:/..." on ':' would make browser "c".
        raise CrecError(f"--cookies: no cookies file at {as_file}")

    browser, _, profile = spec.partition(":")
    browser = browser.strip().lower()
    supported = _supported_browsers()
    if browser not in supported:
        raise CrecError(
            f"--cookies {spec}: not a file, and '{browser}' is not a browser crec can "
            f"read. Use one of: {', '.join(sorted(supported))}"
        )
    return {"cookiesfrombrowser": (browser, profile.strip() or None, None, None)}


def _supported_browsers() -> set[str]:
    deps.activate_ytdlp()
    from yt_dlp.cookies import SUPPORTED_BROWSERS  # noqa: PLC0415

    return set(SUPPORTED_BROWSERS)


def _needs_login(message: str) -> bool:
    lowered = message.lower()
    return any(signature in lowered for signature in _LOGIN_SIGNATURES)


def _login_help(url: str, cookies: str | None) -> str:
    """What to tell someone whose download needs a login."""
    from urllib.parse import urlparse

    site = urlparse(url).hostname or "this site"
    site = site.removeprefix("www.").removeprefix("m.")
    if not cookies:
        return (
            f"{site} wants you logged in. If you are logged in in your browser, "
            f"crec can borrow that:\n"
            f"  crec --cookies firefox          (or chrome, edge, brave, safari...)\n"
            f"To make it permanent, run `crec presets --edit` and add under [defaults]:\n"
            f'  cookies = "firefox"'
        )
    return (
        f"{site} still wants a login, even with cookies from '{cookies}'. Make sure "
        f"you are logged in to {site} in that browser (and in the right profile - "
        f"pick one with e.g. --cookies \"chrome:Profile 1\")."
    )


def _caused_by_cookie_load(exc: BaseException, yt_dlp) -> bool:
    cookie_error = getattr(yt_dlp.cookies, "CookieLoadError", None)
    if cookie_error is None:
        return False
    seen: BaseException | None = exc
    for _ in range(10):  # context chains are short; never loop forever on a cycle
        if seen is None:
            return False
        if isinstance(seen, cookie_error):
            return True
        if isinstance(getattr(seen, "exc_info", None), tuple) and isinstance(
            seen.exc_info[1], cookie_error
        ):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


_LOCKED_SIGNATURES = ("could not copy", "permission", "decrypt", "dpapi", "app-bound", "locked")


def _cookie_read_help(cookies: str, detail: str) -> str:
    browser, _, profile = cookies.partition(":")
    browser = browser.strip().lower()
    if "could not find" in detail.lower():
        where = f"a '{profile.strip()}' profile in {browser}" if profile.strip() else browser
        return f"Could not find {where}. Is it installed, and have you opened it at least once?"
    locked = any(s in detail.lower() for s in _LOCKED_SIGNATURES)
    if sys.platform == "win32" and browser in _CHROMIUM and locked:
        return (
            f"Could not read {browser}'s cookies. On Windows, Chrome-based browsers lock "
            f"their cookies while open and encrypt them so other apps cannot read them. "
            f"Close {browser} completely and retry, or use Firefox: --cookies firefox"
        )
    return f"Could not read cookies from '{cookies}': {detail}"


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
    except yt_dlp.utils.YoutubeDLError as exc:
        # Raised directly, or - since yt-dlp reports it before re-raising -
        # wrapped in a DownloadError with the cookie failure as its context.
        if options.cookies and _caused_by_cookie_load(exc, yt_dlp):
            raise CrecError(_cookie_read_help(options.cookies, _tidy(str(exc)))) from exc
        if not isinstance(exc, yt_dlp.utils.DownloadError):
            raise CrecError(_tidy(str(exc))) from exc
        message = str(exc)
        if _needs_login(message):
            raise CrecError(_login_help(url, options.cookies)) from exc
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


def peek(url: str, *, cookies: str | None = None) -> Peek | None:
    """Metadata-only lookup. Returns None if the site won't say.

    Used to size the download *before* making it: targeting 10 MB means there is
    no reason to pull a 4K stream just to throw the pixels away.
    """
    deps.activate_ytdlp()
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": _SilentLogger()}
    opts.update(cookie_opts(cookies))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
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
