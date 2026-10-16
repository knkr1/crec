"""Putting a *file* on the clipboard, and reading a URL back off it.

This is the feature. yt-dlp does everything else; this is the part that makes
crec worth installing.

Platform reality, which is why this is scoped rather than uniform:

* **Windows** - ``CF_HDROP``. The shell copies the data, so it survives our exit.
  Fully supported.
* **macOS** - ``NSPasteboard`` file URLs, driven through ``osascript`` so we stay
  dependency-free. Also persists.
* **Linux** - X11 and Wayland make the *live process* the clipboard owner, so a
  download-and-exit CLI physically cannot leave a file on the clipboard without
  lingering as a daemon. We print the path and say why instead of pretending.

Only ``CF_HDROP`` is set on Windows, deliberately - not text as well. Apps that
see both formats often prefer the text, which would paste a path string into
Discord instead of uploading the file.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


class ClipboardUnsupported(RuntimeError):
    """This platform cannot hold a file reference after we exit."""


def supports_files() -> bool:
    return sys.platform in {"win32", "darwin"}


def describe() -> str:
    if sys.platform == "win32":
        return "CF_HDROP (files supported)"
    if sys.platform == "darwin":
        return "NSPasteboard (files supported)"
    return "text only - X11/Wayland cannot hold a file after crec exits"


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #

def _win_user32():
    """user32 with every prototype declared.

    The argtypes are not optional: without them ctypes marshals handles as C
    ``int``, and on 64-bit Windows a real HANDLE overflows that. Declaring them
    is the difference between working and "int too long to convert".
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    return user32


def _win_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return kernel32


def _win_session(retries: int = 10):
    """Open the clipboard, retrying briefly.

    Another process can legitimately hold the clipboard open for a few
    milliseconds; failing the whole download over that would be silly.
    """
    user32 = _win_user32()
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            return user32
        time.sleep(0.05 * (attempt + 1))
    raise OSError("Could not open the Windows clipboard - another app is holding it.")


def _win_copy_files(files: list[Path]) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = _win_kernel32()

    class DROPFILES(ctypes.Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt_x", wintypes.LONG),
            ("pt_y", wintypes.LONG),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    # Double-null-terminated list of wide paths, as CF_HDROP requires.
    payload = "".join(f"{path}\0" for path in files) + "\0"
    encoded = payload.encode("utf-16-le")
    header = DROPFILES(
        pFiles=ctypes.sizeof(DROPFILES), pt_x=0, pt_y=0, fNC=False, fWide=True
    )
    total = ctypes.sizeof(DROPFILES) + len(encoded)

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, total)
    if not handle:
        raise OSError("GlobalAlloc failed while building the clipboard payload.")

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock failed while building the clipboard payload.")
    try:
        ctypes.memmove(locked, ctypes.byref(header), ctypes.sizeof(DROPFILES))
        ctypes.memmove(locked + ctypes.sizeof(DROPFILES), encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)

    user32 = _win_session()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_HDROP, handle):
            kernel32.GlobalFree(handle)
            raise OSError(f"SetClipboardData failed (error {ctypes.get_last_error()}).")
        # Success transfers ownership of `handle` to the system - do not free it.
    finally:
        user32.CloseClipboard()


def _win_read_files() -> list[Path]:
    """Files copied in Explorer (Ctrl+C on a file) arrive as CF_HDROP."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    user32 = _win_session()
    try:
        if not user32.IsClipboardFormatAvailable(CF_HDROP):
            return []
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        files: list[Path] = []
        for index in range(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buffer, length + 1)
            files.append(Path(buffer.value))
        return files
    finally:
        user32.CloseClipboard()


def _win_read_text() -> str:
    import ctypes
    from ctypes import wintypes

    user32 = _win_session()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        kernel32 = _win_kernel32()
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #

def _applescript_string(text: str) -> str:
    """Quote *text* as an AppleScript literal; a raw `"` in a title breaks the script."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mac_copy_files(files: list[Path]) -> None:
    items = ", ".join(f"(POSIX file {_applescript_string(str(path))})" for path in files)
    script = f"set the clipboard to {{{items}}}" if len(files) > 1 else f"set the clipboard to {items}"
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise OSError(f"osascript failed: {result.stderr.strip()}")


def _mac_read_files() -> list[Path]:
    result = subprocess.run(
        ["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"],
        capture_output=True, text=True, check=False,
    )
    path = result.stdout.strip()
    return [Path(path)] if result.returncode == 0 and path else []


def _linux_read_files() -> list[Path]:
    from urllib.parse import unquote, urlparse

    for command in (
        ["wl-paste", "--no-newline", "--type", "text/uri-list"],
        ["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"],
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError:
            continue
        if result.returncode == 0:
            return [
                Path(unquote(urlparse(line.strip()).path))
                for line in result.stdout.splitlines()
                if line.strip().startswith("file://")
            ]
    return []


def _mac_read_text() -> str:
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #

def copy_files(files: list[Path]) -> None:
    """Place *files* on the clipboard as a file reference.

    Raises :class:`ClipboardUnsupported` where that is impossible.
    """
    real = [path.resolve() for path in files if path.exists()]
    if not real:
        raise OSError("Nothing to copy - the file does not exist.")
    if sys.platform == "win32":
        _win_copy_files(real)
    elif sys.platform == "darwin":
        _mac_copy_files(real)
    else:
        raise ClipboardUnsupported(describe())


def read_files() -> list[Path]:
    """Files on the clipboard, copied as files (not as text). Never raises."""
    try:
        if sys.platform == "win32":
            found = _win_read_files()
        elif sys.platform == "darwin":
            found = _mac_read_files()
        else:
            found = _linux_read_files()
    except OSError:
        return []
    return [path for path in found if path.is_file()]


def paths_in_text(text: str) -> list[Path]:
    """Existing files named in clipboard *text*, one per line.

    Explorer's "Copy as path" wraps each path in double quotes, so strip them.
    """
    found: list[Path] = []
    for line in text.splitlines():
        candidate = line.strip().strip('"').strip()
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
            if path.is_file():
                found.append(path)
        except (OSError, ValueError):
            continue
    return found


def read_text() -> str:
    try:
        if sys.platform == "win32":
            return _win_read_text().strip()
        if sys.platform == "darwin":
            return _mac_read_text().strip()
        for command in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return result.stdout.strip()
    except OSError:
        return ""
    return ""
