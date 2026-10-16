"""Updating and uninstalling crec itself (the installed Windows app).

yt-dlp updates itself in-process (see :mod:`crec.deps`). crec cannot: a running
program cannot replace its own exe or the DLLs it has loaded. So `crec update`
does everything that *can* happen while it runs - download, verify, unpack next
to the install - and hands the last step, a folder swap, to a small PowerShell
script that waits for this process to exit first. The swap is two renames, so
it either happens or it doesn't; a failure leaves the old crec working.

Only the installed app updates this way. A pip/pipx install is pip's to manage.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__, deps, paths
from .errors import CrecError

REPO = "knkr1/crec"
ASSET = "crec-windows-x64.zip"
_USER_AGENT = f"crec/{__version__} (+https://github.com/{REPO})"


@dataclass
class Release:
    version: str
    zip_url: str
    sha256_url: str | None


def is_installed_app() -> bool:
    """True for the frozen Windows build, the only kind that updates itself."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def newer(candidate: str, current: str = __version__) -> bool:
    return deps._normalise(candidate) > deps._normalise(current)


def _local_source() -> Path | None:
    """CREC_UPDATE_SOURCE: a folder standing in for the GitHub release (testing)."""
    folder = os.environ.get("CREC_UPDATE_SOURCE")
    return Path(folder) if folder else None


def latest_release(timeout: float = 10) -> Release | None:
    """The newest published release that has a Windows zip, or None if unknown."""
    local = _local_source()
    if local:
        version = (local / "version.txt").read_text(encoding="utf-8").strip()
        sha = local / f"{ASSET}.sha256"
        return Release(version, (local / ASSET).as_uri(), sha.as_uri() if sha.exists() else None)
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except Exception:
        return None
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if ASSET not in assets:
        return None
    return Release(
        version=str(data.get("tag_name", "")).lstrip("v"),
        zip_url=assets[ASSET],
        sha256_url=assets.get(f"{ASSET}.sha256"),
    )


def _expected_hash(url: str) -> str:
    # urlopen also reads file:// URLs, which is what CREC_UPDATE_SOURCE produces.
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8", errors="replace")
    return text.strip().split()[0].upper()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _work_dir() -> Path:
    return paths.ensure(paths.data_dir() / "update")


def _tray_running() -> bool:
    from .tray import _find_window

    return bool(_find_window())


def _launch_helper(script: str, **params: str) -> None:
    """Run a PowerShell script detached, after this process is gone.

    The script lives in %TEMP%, not crec's data folder: uninstall deletes that.
    """
    import tempfile

    path = Path(tempfile.gettempdir()) / f"crec-helper-{os.getpid()}.ps1"
    path.write_text(script, encoding="utf-8-sig")  # BOM: Windows PowerShell 5 reads it as UTF-8
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(path), "-ParentPid", str(os.getpid())]
    for key, value in params.items():
        args += [f"-{key}", value]
    subprocess.Popen(
        args,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# The swap. Waits for crec to exit, then two renames; retries while files are
# still locked (a crec in another terminal), and rolls back if the second fails.
_SWAP_SCRIPT = r"""
param([int]$ParentPid, [string]$Dir, [string]$Incoming, [string]$Log, [string]$RestartTray)
function Note($m) { Add-Content -Path $Log -Value "[$(Get-Date -Format s)] $m" }
try { Wait-Process -Id $ParentPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
$retired = "$Dir.old"
Remove-Item $retired -Recurse -Force -ErrorAction SilentlyContinue
$moved = $false
for ($i = 0; $i -lt 60 -and -not $moved; $i++) {
    try { Move-Item $Dir $retired -ErrorAction Stop; $moved = $true } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $moved) {
    Note "update failed: crec is still running somewhere, kept the old version"
    Remove-Item $Incoming -Recurse -Force -ErrorAction SilentlyContinue
} else {
    try {
        Move-Item $Incoming $Dir -ErrorAction Stop
        Remove-Item $retired -Recurse -Force -ErrorAction SilentlyContinue
        Note "updated"
    } catch {
        Move-Item $retired $Dir -ErrorAction SilentlyContinue
        Note "update failed while swapping, rolled back: $($_.Exception.Message)"
    }
}
if ($RestartTray -eq "1") { Start-Process -FilePath (Join-Path $Dir "crecw.exe") -ArgumentList "tray","--run" }
"""


def update_crec() -> tuple[bool, str]:
    """Download the newest crec and schedule the swap. Returns ``(started, message)``."""
    if not is_installed_app():
        return False, (
            "crec itself was installed with pip - update it with: pipx upgrade crec "
            "(or pip install -U crec)"
        )

    release = latest_release()
    if release is None:
        return False, "Could not check GitHub for a newer crec."
    if not newer(release.version):
        return False, f"crec is already current ({__version__})."
    if not release.sha256_url:
        # Replacing the program itself is not something to do on trust.
        return False, f"crec {release.version} has no published checksum - not installing it."

    work = _work_dir()
    archive = work / ASSET
    deps._download(release.zip_url, archive, label=f"crec {release.version}", quiet=False)
    try:
        expected = _expected_hash(release.sha256_url)
    except Exception as exc:
        raise CrecError(f"Could not verify the crec {release.version} download: {exc}") from exc
    if _sha256(archive) != expected:
        archive.unlink(missing_ok=True)
        raise CrecError(f"The crec {release.version} download failed its checksum - not installing it.")

    target = install_dir()
    incoming = target.with_name(target.name + ".incoming")
    shutil.rmtree(incoming, ignore_errors=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(incoming)
    archive.unlink(missing_ok=True)
    if not (incoming / "crec.exe").is_file():
        shutil.rmtree(incoming, ignore_errors=True)
        raise CrecError(f"The crec {release.version} download did not contain crec.exe.")

    restart_tray = _tray_running()
    if restart_tray:
        from .tray import cmd_tray

        cmd_tray(["--stop"])

    _launch_helper(
        _SWAP_SCRIPT,
        Dir=str(target), Incoming=str(incoming), Log=str(work / "update.log"),
        RestartTray="1" if restart_tray else "0",
    )
    return True, (
        f"crec {__version__} -> {release.version}: downloaded and verified, "
        f"finishing in the background - ready in a few seconds."
    )


# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #

_REMOVE_SCRIPT = r"""
param([int]$ParentPid, [string]$Dir)
try { Wait-Process -Id $ParentPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
for ($i = 0; $i -lt 60 -and (Test-Path $Dir); $i++) {
    Remove-Item $Dir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $Dir) { Start-Sleep -Milliseconds 500 }
}
"""


def _remove_from_user_path(folder: Path) -> bool:
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False
        wanted = os.path.normcase(str(folder).rstrip("\\"))
        parts = [p for p in value.split(";") if p]
        kept = [p for p in parts if os.path.normcase(p.rstrip("\\")) != wanted]
        if len(kept) == len(parts):
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    # Tell open programs (Explorer, new terminals) that the environment changed.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)
    return True


def _remove_data(data: Path, *, keep: Path) -> None:
    """Delete crec's data folder - but never the presets we promised to keep.

    On Windows presets live in Roaming and data in Local, but nothing stops
    the two from being the same folder (they are on macOS, and APPDATA can
    point anywhere), so check rather than assume.
    """
    keep = keep.resolve()
    if data.resolve() not in keep.parents:
        shutil.rmtree(data, ignore_errors=True)
        return
    for child in data.iterdir():
        if child.resolve() == keep or child.resolve() in keep.parents:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def uninstall(*, assume_yes: bool) -> int:
    if not is_installed_app():
        print("This crec was installed with pip. Remove it with: pipx uninstall crec "
              "(or pip uninstall crec)")
        return 1

    target = install_dir()
    data = paths.data_dir()
    print("This removes:")
    print(f"  crec itself         {target}")
    print(f"  its ffmpeg, yt-dlp  {data}")
    print("  the hotkeys and start-with-Windows entry")
    print("It keeps your downloads and presets:")
    print(f"  {paths.downloads_dir()}")
    print(f"  {paths.user_presets_file()}")
    if not assume_yes:
        try:
            answer = input("Uninstall crec? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            print("Nothing removed.")
            return 1

    from .tray import cmd_tray, set_startup

    if _tray_running():
        cmd_tray(["--stop"])
    set_startup(False)
    on_path = _remove_from_user_path(target)
    _remove_data(data, keep=paths.user_presets_file())
    _launch_helper(_REMOVE_SCRIPT, Dir=str(target))
    print("crec is uninstalled." + (" (Removed from PATH; open terminals keep the old PATH.)" if on_path else ""))
    return 0
