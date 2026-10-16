"""Runtime dependency management for yt-dlp and ffmpeg.

Two problems solved here.

**yt-dlp goes stale weekly.** Sites break and yt-dlp patches several times a
month. If yt-dlp were frozen into ``crec.exe``, every extractor fix would need a
crec release and a user with a broken download would be waiting on us. So we
keep a user-writable copy in ``data_dir/lib`` and prepend it to ``sys.path``
*before* importing yt_dlp - it wins over whatever is bundled. This works because
yt-dlp is pure Python with no compiled extensions.

Only the ``yt_dlp`` package itself is overridden; its optional companions
(mutagen, pycryptodomex, ...) keep resolving from the bundle or site-packages.

**ffmpeg is a native binary.** We fetch it on first use into ``data_dir/bin``
rather than bundling it. Smaller download, and since we never redistribute the
binary, its GPL never becomes our licensing problem.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import __version__, paths
from .errors import MissingDependency

UPDATE_CHECK_INTERVAL = 24 * 60 * 60  # seconds
_USER_AGENT = f"crec/{__version__} (+https://github.com/knkr1/crec)"

FFMPEG_URLS = {
    "win32": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
    "darwin": "https://evermeet.cx/ffmpeg/getrelease/zip",
}


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #

def activate_ytdlp() -> None:
    """Make the updatable yt-dlp win over the bundled one.

    Must run before the first ``import yt_dlp`` anywhere in the process.
    Deliberately silent and forgiving: if anything is off we simply fall back to
    the bundled copy, because a failed override must never stop a download.
    """
    lib = paths.lib_dir()
    if not (lib / "yt_dlp" / "__init__.py").is_file():
        return
    if "yt_dlp" in sys.modules:
        return  # too late to swap; the bundled one is already loaded
    entry = str(lib)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)


def _read_version_file(package_dir: Path) -> str | None:
    """Scrape ``__version__`` out of yt_dlp/version.py without importing it.

    Importing to read a version is a trap: it pins ``sys.modules['yt_dlp']`` to
    whichever copy loaded first, which is exactly the decision we are trying to
    keep open.
    """
    version_py = package_dir / "version.py"
    try:
        text = version_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    return match.group(1) if match else None


def _normalise(version: str) -> tuple[int, ...]:
    """``2026.07.04`` and ``2026.7.4`` are the same release; compare as numbers."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def ytdlp_version() -> str:
    """Version of the yt_dlp we *would* import, without importing it if avoidable."""
    managed = _read_version_file(paths.lib_dir() / "yt_dlp")
    if managed:
        return managed
    try:
        import yt_dlp  # noqa: PLC0415 - only reached when no managed copy exists

        return getattr(yt_dlp.version, "__version__", "unknown")
    except Exception:  # pragma: no cover - only if yt-dlp is truly broken
        return "unavailable"


def ytdlp_origin() -> str:
    """Where the yt_dlp we would actually import comes from."""
    activate_ytdlp()
    try:
        import yt_dlp

        return str(Path(yt_dlp.__file__).parent)
    except Exception:
        return "unavailable"


def latest_ytdlp_version() -> str | None:
    try:
        request = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": _USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)["info"]["version"]
    except Exception:
        return None


def update_ytdlp(*, quiet: bool = False) -> tuple[bool, str]:
    """Fetch the newest yt-dlp wheel into ``lib_dir``.

    Returns ``(changed, message)``. Extraction happens in a sibling directory
    that is swapped in at the end, so an interrupted download can never leave a
    half-written yt_dlp behind.
    """
    target = latest_ytdlp_version()
    if target is None:
        return False, "Could not reach PyPI to check for a yt-dlp update."

    current = ytdlp_version()
    if _normalise(current) == _normalise(target) and (paths.lib_dir() / "yt_dlp").is_dir():
        return False, f"yt-dlp is already current ({current})."

    try:
        request = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": _USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            meta = json.load(response)
        wheel = next(
            url["url"]
            for url in meta["urls"]
            if url["packagetype"] == "bdist_wheel" and url["filename"].endswith(".whl")
        )
    except Exception as exc:
        return False, f"Could not locate the yt-dlp wheel: {exc}"

    lib = paths.ensure(paths.lib_dir())
    staging = lib.parent / "lib.incoming"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        archive = staging / "yt_dlp.whl"
        _download(wheel, archive, label=f"yt-dlp {target}", quiet=quiet)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(staging)
        archive.unlink(missing_ok=True)

        extracted = staging / "yt_dlp"
        if not (extracted / "__init__.py").is_file():
            return False, "Downloaded wheel did not contain a yt_dlp package."

        live = lib / "yt_dlp"
        retired = lib / "yt_dlp.old"
        shutil.rmtree(retired, ignore_errors=True)
        if live.exists():
            live.rename(retired)
        shutil.move(str(extracted), str(live))
        shutil.rmtree(retired, ignore_errors=True)
    except Exception as exc:
        return False, f"yt-dlp update failed: {exc}"
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    record_update_check()
    if _normalise(current) == _normalise(target):
        # Same release, but now as crec's own copy that future updates replace.
        return True, f"yt-dlp {target} is now kept up to date by crec."
    return True, f"yt-dlp updated: {current} -> {target}"


# --------------------------------------------------------------------------- #
# passive update checking
# --------------------------------------------------------------------------- #

def _stamp_file() -> Path:
    return paths.data_dir() / "last-update-check"


def should_check_update() -> bool:
    """True at most once per day. Never blocks a run - callers check *after*."""
    if os.environ.get("CREC_NO_UPDATE_CHECK"):
        return False
    stamp = _stamp_file()
    try:
        return (time.time() - stamp.stat().st_mtime) > UPDATE_CHECK_INTERVAL
    except FileNotFoundError:
        return True


def record_update_check() -> None:
    stamp = _stamp_file()
    try:
        paths.ensure(stamp.parent)
        stamp.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #

def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def find_ffmpeg(name: str = "ffmpeg") -> Path | None:
    """Our managed copy first, then whatever is on PATH."""
    managed = paths.bin_dir() / _exe(name)
    if managed.is_file():
        return managed
    found = shutil.which(name)
    return Path(found) if found else None


def ffmpeg_path(*, auto_fetch: bool = True, quiet: bool = False) -> Path:
    found = find_ffmpeg("ffmpeg")
    if found:
        return found
    if not auto_fetch:
        raise MissingDependency("ffmpeg is not installed. Run: crec doctor --fix")
    fetch_ffmpeg(quiet=quiet)
    found = find_ffmpeg("ffmpeg")
    if not found:
        raise MissingDependency(
            "ffmpeg could not be installed automatically. Install it manually "
            "and make sure it is on your PATH."
        )
    return found


def ffprobe_path(*, auto_fetch: bool = True, quiet: bool = False) -> Path:
    found = find_ffmpeg("ffprobe")
    if found:
        return found
    ffmpeg_path(auto_fetch=auto_fetch, quiet=quiet)  # same archive ships both
    found = find_ffmpeg("ffprobe")
    if not found:
        raise MissingDependency("ffprobe is missing. Run: crec doctor --fix")
    return found


def fetch_ffmpeg(*, quiet: bool = False) -> None:
    url = FFMPEG_URLS.get(sys.platform)
    if url is None:
        raise MissingDependency(
            f"crec cannot auto-install ffmpeg on {sys.platform}. Install it with "
            f"your package manager (e.g. apt install ffmpeg) and re-run."
        )

    target = paths.ensure(paths.bin_dir())
    staging = target.parent / "bin.incoming"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    wanted = {_exe("ffmpeg"), _exe("ffprobe")}
    try:
        archive = staging / "ffmpeg.zip"
        _download(url, archive, label="ffmpeg (one-time, ~80 MB)", quiet=quiet)
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                basename = Path(member.filename).name
                if basename not in wanted:
                    continue
                with zf.open(member) as src, (staging / basename).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        archive.unlink(missing_ok=True)

        installed = 0
        for basename in wanted:
            source = staging / basename
            if not source.is_file():
                continue
            destination = target / basename
            destination.unlink(missing_ok=True)
            shutil.move(str(source), str(destination))
            if sys.platform != "win32":
                destination.chmod(0o755)
            installed += 1
        if installed == 0:
            raise MissingDependency("ffmpeg archive did not contain the expected binaries.")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------- #
# a JavaScript runtime, for YouTube
# --------------------------------------------------------------------------- #
#
# YouTube hides its stream URLs behind JavaScript challenges, and yt-dlp now
# solves them by running YouTube's player code in a real JS runtime. Without one
# it falls back to a deprecated path that warns today and will stop working -
# and since yt-dlp updates itself, that day could arrive without a crec release.
#
# deno over the smaller quickjs-ng: measured on real videos, quickjs added ~6s
# to every YouTube download (its results are not cached), deno and Node ~0s.
# A 40 MB one-time download beats six seconds every time. Anyone who already
# has a recent Node gets to skip the download entirely.

_DENO_MIN = (2, 3, 0)
_NODE_MIN = (22, 0, 0)


def _deno_asset() -> str | None:
    import platform

    arm = platform.machine().lower() in {"arm64", "aarch64"}
    if sys.platform == "win32":
        return "deno-x86_64-pc-windows-msvc.zip"  # runs under emulation on ARM too
    if sys.platform == "darwin":
        return f"deno-{'aarch64' if arm else 'x86_64'}-apple-darwin.zip"
    if sys.platform.startswith("linux"):
        return f"deno-{'aarch64' if arm else 'x86_64'}-unknown-linux-gnu.zip"
    return None


def _version_of(executable: str | Path, pattern: str) -> tuple[int, ...] | None:
    try:
        result = run_quiet([str(executable), "--version"])
    except OSError:
        return None
    match = re.search(pattern, result.stdout or "", re.MULTILINE)
    return _normalise(match.group(1)) if match else None


def find_js_runtime() -> tuple[str, Path] | None:
    """``(name, path)`` of a usable runtime, without downloading anything."""
    managed = paths.bin_dir() / _exe("deno")
    if managed.is_file():
        return "deno", managed
    for name, pattern, minimum in (
        ("deno", r"^deno (\S+)", _DENO_MIN),
        ("node", r"^v(\S+)", _NODE_MIN),
    ):
        found = shutil.which(name)
        if found:
            version = _version_of(found, pattern)
            if version and version >= minimum:
                return name, Path(found)
    return None


def fetch_deno(*, quiet: bool = False) -> Path:
    asset = _deno_asset()
    if asset is None:
        raise MissingDependency(f"crec cannot auto-install deno on {sys.platform}.")
    target = paths.ensure(paths.bin_dir())
    staging = target.parent / "deno.incoming"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        archive = staging / asset
        _download(
            f"https://github.com/denoland/deno/releases/latest/download/{asset}",
            archive, label="deno for YouTube (one-time, ~40 MB)", quiet=quiet,
        )
        with zipfile.ZipFile(archive) as zf:
            zf.extract(_exe("deno"), staging)
        destination = target / _exe("deno")
        destination.unlink(missing_ok=True)
        shutil.move(str(staging / _exe("deno")), str(destination))
        if sys.platform != "win32":
            destination.chmod(0o755)
        return destination
    except (KeyError, zipfile.BadZipFile) as exc:
        raise MissingDependency(f"The deno download was not what crec expected: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def js_runtimes(*, fetch: bool) -> dict | None:
    """yt-dlp's ``js_runtimes`` option, or None to leave yt-dlp's default.

    ``fetch`` downloads deno when nothing usable exists - pass it only for
    YouTube, so a TikTok-only user never downloads a runtime they don't need.
    A failed download must not fail the video: yt-dlp still has its fallback.
    """
    found = find_js_runtime()
    if found is None and fetch:
        try:
            found = ("deno", fetch_deno())
        except MissingDependency:
            return None
    if found is None:
        return None
    name, path = found
    return {name: {"path": str(path)}}


# --------------------------------------------------------------------------- #
# shared
# --------------------------------------------------------------------------- #

def _download(url: str, destination: Path, *, label: str, quiet: bool) -> None:
    # A redrawn bar only makes sense in a terminal; piped or logged, each redraw
    # becomes its own line. Say it once instead.
    live = not quiet and sys.stderr.isatty()
    if not quiet and not live:
        sys.stderr.write(f"fetching {label}...\n")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with destination.open("wb") as handle:
                while chunk := response.read(256 * 1024):
                    handle.write(chunk)
                    done += len(chunk)
                    if live:
                        _progress(label, done, total)
        if live:
            sys.stderr.write("\r\033[Kfetched " + label + "\n")
            sys.stderr.flush()
    except urllib.error.URLError as exc:
        raise MissingDependency(f"Could not download {label}: {exc.reason}") from exc


def _progress(label: str, done: int, total: int) -> None:
    if total:
        pct = done * 100 // total
        bar = "#" * (pct // 4) + "." * (25 - pct // 4)
        sys.stderr.write(f"\r\033[K{label} [{bar}] {pct}%")
    else:
        sys.stderr.write(f"\r\033[K{label} {done / 1_048_576:.1f} MB")
    sys.stderr.flush()


def doctor(*, fix: bool) -> int:
    """Report - and optionally repair - the runtime environment."""
    from . import clipboard  # local import: keeps startup lean

    ok = True
    print(f"crec        {_crec_version()}")
    print(f"python      {sys.version.split()[0]} ({'frozen' if is_frozen() else 'source'})")

    version = ytdlp_version()
    if version in {"unknown", "unavailable"}:
        ok = False
        print("yt-dlp      MISSING")
    else:
        managed = str(paths.lib_dir()) in ytdlp_origin()
        print(f"yt-dlp      {version} ({'managed' if managed else 'bundled'})")

    ffmpeg = find_ffmpeg("ffmpeg")
    ffprobe = find_ffmpeg("ffprobe")
    for name, found in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if found:
            print(f"{name:<11} {found}")
        else:
            ok = False
            print(f"{name:<11} MISSING")

    print(f"impersonate {_impersonation()}")
    runtime = find_js_runtime()
    if runtime:
        managed = runtime[1].parent == paths.bin_dir()
        print(f"javascript  {runtime[0]} {'(managed)' if managed else runtime[1]}")
    else:
        ok = False
        print("javascript  MISSING - YouTube needs it; fetched on first YouTube download")
    print(f"clipboard   {clipboard.describe()}")
    print(f"downloads   {paths.downloads_dir()}")
    print(f"presets     {paths.user_presets_file()}")

    if not ok and fix:
        print()
        if not (ffmpeg and ffprobe):
            fetch_ffmpeg()
        if runtime is None:
            fetch_deno()
        if version in {"unknown", "unavailable"}:
            _, message = update_ytdlp()
            print(message)
        return doctor(fix=False)

    if not ok:
        print("\nSomething is missing. Run: crec doctor --fix")
        return 1
    return 0


def _impersonation() -> str:
    """Whether yt-dlp can look like a real browser (TikTok requires it)."""
    try:
        import curl_cffi  # noqa: PLC0415

        return f"available (curl_cffi {getattr(curl_cffi, '__version__', '?')})"
    except Exception:
        return "MISSING - TikTok and some other sites will fail (pip install curl_cffi)"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _crec_version() -> str:
    from . import __version__

    return __version__


def run_quiet(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with no console window on Windows."""
    kwargs: dict = {"capture_output": True, "text": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, check=False, **kwargs)
