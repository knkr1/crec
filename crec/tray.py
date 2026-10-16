"""`crec tray`: global hotkeys and a tray icon, so crec runs without a terminal.

Copy a link or a file, press a hotkey, paste the result. Everything else here
exists to make that safe and visible: the icon shows what is happening, a
notification says when the file is ready (or why it isn't), and the menu is
how you pause, configure or quit it.

Windows only for now, written against raw Win32 through ctypes. The hotkeys
(RegisterHotKey) need a window and a message loop anyway, and the tray icon is
a few more calls on that same loop - so this costs no dependencies, which keeps
the frozen exe small and import-safe. A library like pystray would pull in
Pillow for no gain.

Jobs run on a worker thread through :func:`crec.cli.job`, exactly as a
command-line run would; results are posted back to the window's thread, which
alone touches the icon.
"""

from __future__ import annotations

import io
import shlex
import subprocess
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from . import paths, presets
from .errors import CrecError

CLASS_NAME = "crec.tray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "crec"

_MODIFIERS = {"ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "alt": 0x0001, "win": 0x0008}
_MODIFIER_ORDER = (("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt"), ("win", "Win"))
_MOD_NOREPEAT = 0x4000


# --------------------------------------------------------------------------- #
# hotkey specs (pure, so testable anywhere)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Hotkey:
    combo: str  # as written in presets.toml
    modifiers: int
    key: int  # virtual-key code
    flags: str  # crec arguments, "" for plain `crec`

    @property
    def label(self) -> str:
        return describe(self.combo)

    @property
    def action(self) -> str:
        return f"crec {self.flags}".strip()


def parse_combo(combo: str) -> tuple[int, int]:
    """``"ctrl+shift+alt+d"`` -> ``(modifier mask, virtual-key code)``."""
    parts = [part.strip().lower() for part in combo.split("+") if part.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts

    mask = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise ValueError(f"'{mod}' is not a modifier (use ctrl, shift, alt, win)")
        mask |= _MODIFIERS[mod]
    if not mask:
        # A bare key would swallow that letter everywhere while crec runs.
        raise ValueError("needs at least one of ctrl, shift, alt, win")

    if len(key) == 1 and key.isascii() and key.isalnum():
        return mask, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return mask, 0x6F + int(key[1:])
    raise ValueError(f"'{key}' is not a key crec can bind (use a-z, 0-9 or f1-f24)")


def describe(combo: str) -> str:
    """``"alt+ctrl+d"`` -> ``"Ctrl+Alt+D"``: modifiers in the usual order."""
    parts = [part.strip().lower() for part in combo.split("+") if part.strip()]
    mods = {("ctrl" if p == "control" else p) for p in parts[:-1]}
    shown = [name for key, name in _MODIFIER_ORDER if key in mods]
    return "+".join(shown + [parts[-1].upper()]) if parts else combo


def load_hotkeys() -> tuple[list[Hotkey], list[str]]:
    """Parse and validate the configured hotkeys. Bad ones become warnings."""
    from .cli import build_parser

    table, warnings = presets.load_hotkeys()
    preset_set = presets.load()
    parser = build_parser(preset_set)

    hotkeys: list[Hotkey] = []
    taken: set[tuple[int, int]] = set()
    for combo, flags in table.items():
        try:
            modifiers, key = parse_combo(combo)
        except ValueError as exc:
            warnings.append(f"hotkey '{combo}': {exc}")
            continue
        if (modifiers, key) in taken:
            warnings.append(f"hotkey '{combo}' is listed twice")
            continue
        try:
            argv = shlex.split(flags)
            with redirect_stderr(io.StringIO()):
                args = parser.parse_args(argv)
        except (ValueError, SystemExit):
            warnings.append(f"hotkey '{combo}': '{flags}' is not valid crec flags")
            continue
        if args.inputs:
            warnings.append(f"hotkey '{combo}': hotkeys work on the clipboard, drop '{args.inputs[0]}'")
            continue
        taken.add((modifiers, key))
        hotkeys.append(Hotkey(combo, modifiers, key, flags))
    return hotkeys, warnings


# --------------------------------------------------------------------------- #
# running a job
# --------------------------------------------------------------------------- #

def _log_file() -> Path:
    return paths.data_dir() / "tray.log"


def log(text: str) -> None:
    """Append to the tray log - the only place a windowless process can talk."""
    try:
        target = _log_file()
        paths.ensure(target.parent)
        if target.exists() and target.stat().st_size > 1_000_000:
            target.unlink()  # crude, but nobody needs megabytes of history
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text.rstrip()}\n")
    except OSError:
        pass


def run_job(flags: str, on_status) -> list[Path]:
    """Run crec with *flags* on whatever is on the clipboard. Raises CrecError."""
    from .cli import build_parser, job
    from .download import Progress

    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            preset_set = presets.load()
            args = build_parser(preset_set).parse_args(shlex.split(flags))
            return job(
                args, preset_set,
                progress=Progress(enabled=False, listener=on_status),
                fall_back_to_last=False,
            )
    except SystemExit as exc:  # argparse rejected the flags
        raise CrecError(f"'{flags}' is not valid crec flags") from exc
    finally:
        log(f"$ crec {flags}\n{captured.getvalue()}")


# --------------------------------------------------------------------------- #
# launching, stopping, start-with-Windows
# --------------------------------------------------------------------------- #

def _self_command(*args: str, windowed: bool = False) -> list[str]:
    """A command line that reaches this same crec from any working directory.

    ``windowed`` picks the console-less twin (crecw.exe, or pythonw.exe from
    source) so a background tray never shows a console window, not even at login.
    """
    executable = Path(sys.executable)
    if windowed:
        twin = executable.with_name("crecw.exe" if getattr(sys, "frozen", False) else "pythonw.exe")
        if twin.is_file():
            executable = twin
    if getattr(sys, "frozen", False):
        return [str(executable), *args]
    # Not `-m crec`: that resolves against the working directory first, and the
    # user's home holds ~/crec - the downloads folder - which Python happily
    # imports as an empty namespace package. Pin the real package's location.
    root = str(Path(__file__).resolve().parents[1])
    bootstrap = (
        f"import sys; sys.path.insert(0, {root!r}); "
        "from crec.cli import main; sys.exit(main())"
    )
    return [str(executable), "-c", bootstrap, *args]


def _hide_child_consoles() -> None:
    """Make every subprocess this process starts windowless.

    The tray runs without a console, so each console program it starts -
    ffmpeg, and the ffmpeg yt-dlp starts for merging - would otherwise pop up a
    console window of its own. crec's own calls already pass CREATE_NO_WINDOW;
    yt-dlp's do not, and it is not ours to patch, so set it at the source.
    """
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


def _find_window() -> int:
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    return user32.FindWindowW(CLASS_NAME, None) or 0


def startup_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            command = subprocess.list2cmdline(_self_command("tray", "--run", windowed=True))
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def cmd_tray(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="crec tray",
        description="Run crec in the background with global hotkeys and a tray icon.",
    )
    parser.add_argument("--stop", action="store_true", help="quit the running tray")
    parser.add_argument(
        "--startup", choices=("on", "off"), help="start the tray when you log in to Windows"
    )
    parser.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise CrecError("The tray and hotkeys are Windows-only for now.")

    if args.startup:
        set_startup(args.startup == "on")
        print(f"start with Windows: {args.startup}")
        return 0

    if args.stop:
        window = _find_window()
        if not window:
            print("crec tray is not running.")
            return 0
        import ctypes

        user32 = ctypes.WinDLL("user32")
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
        print("crec tray stopped.")
        return 0

    if args.run:
        if _find_window():
            return 0  # already running - e.g. started at login and again by hand
        _hide_child_consoles()
        return TrayApp().run()

    if _find_window():
        print("crec tray is already running - look for its icon by the clock.")
        return 0

    hotkeys, warnings = load_hotkeys()
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    # Relaunch in the background with no console, detached from this terminal.
    subprocess.Popen(
        _self_command("tray", "--run", windowed=True),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path.home()), close_fds=True,
    )
    for _ in range(50):
        if _find_window():
            break
        time.sleep(0.1)
    else:
        print(f"crec tray did not start. Details may be in {_log_file()}", file=sys.stderr)
        return 1

    print("crec is in your tray. Copy a link or a file, then press:")
    for hotkey in hotkeys:
        print(f"  {hotkey.label:<20} {hotkey.action}")
    if not startup_enabled():
        print("\nTo start it when you log in: crec tray --startup on")
    print("Quit from the tray icon's menu, or: crec tray --stop")
    return 0


# --------------------------------------------------------------------------- #
# the Win32 side
# --------------------------------------------------------------------------- #

def _asset(name: str) -> Path:
    beside = Path(__file__).with_name("assets") / name
    if beside.is_file():
        return beside
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return base / "crec" / "assets" / name


class TrayApp:
    WM_TRAY = 0x8000 + 1  # WM_APP + 1: icon events
    WM_JOB_DONE = 0x8000 + 2
    WM_STATUS = 0x8000 + 3
    WM_UPDATE_FOUND = 0x8000 + 4

    # menu command ids; hotkey items use HOTKEY_BASE + index
    CMD_OPEN, CMD_EDIT, CMD_RELOAD, CMD_STARTUP, CMD_PAUSE, CMD_QUIT, CMD_LOG, CMD_UPDATE, CMD_COOKIES = range(1, 10)
    HOTKEY_BASE = 100

    def __init__(self) -> None:
        self._setup_win32()
        self.hotkeys: list[Hotkey] = []
        self.paused = False
        self.busy = False
        self.status = "ready"
        self.result: tuple[str, object] | None = None
        self.last_files: list[Path] = []
        self.balloon_action = None  # what clicking the current notification does
        self.update_found: str | None = None
        self.update_announced = False  # an explicit check reports "up to date" too
        self.update_quiet = False  # after a job: don't replace the "Ready" notification
        self.hwnd = None
        # Windows calls the window procedure during CreateWindowExW, before this
        # is known. -1 matches no message (0 would match WM_NULL).
        self.taskbar_created = -1

    # --- ctypes plumbing ----------------------------------------------------

    def _setup_win32(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.ctypes = ctypes
        LRESULT = w.LPARAM
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", w.UINT), ("lpfnWndProc", self.WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", w.BYTE * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", w.HICON),
            ]

        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW

        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        s = ctypes.WinDLL("shell32", use_last_error=True)
        sigs = {
            (u, "DefWindowProcW"): ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], LRESULT),
            (u, "RegisterClassW"): ([ctypes.POINTER(WNDCLASSW)], w.ATOM),
            (u, "CreateWindowExW"): (
                [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                 ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID], w.HWND),
            (u, "DestroyWindow"): ([w.HWND], w.BOOL),
            (u, "PostMessageW"): ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
            (u, "PostQuitMessage"): ([ctypes.c_int], None),
            (u, "GetMessageW"): ([ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], w.BOOL),
            (u, "TranslateMessage"): ([ctypes.POINTER(w.MSG)], w.BOOL),
            (u, "DispatchMessageW"): ([ctypes.POINTER(w.MSG)], LRESULT),
            (u, "RegisterHotKey"): ([w.HWND, ctypes.c_int, w.UINT, w.UINT], w.BOOL),
            (u, "UnregisterHotKey"): ([w.HWND, ctypes.c_int], w.BOOL),
            (u, "LoadImageW"): (
                [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT], w.HANDLE),
            (u, "GetSystemMetrics"): ([ctypes.c_int], ctypes.c_int),
            (u, "CreatePopupMenu"): ([], w.HMENU),
            (u, "AppendMenuW"): ([w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR], w.BOOL),
            (u, "TrackPopupMenu"): (
                [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID],
                ctypes.c_int),
            (u, "DestroyMenu"): ([w.HMENU], w.BOOL),
            (u, "SetForegroundWindow"): ([w.HWND], w.BOOL),
            (u, "GetCursorPos"): ([ctypes.POINTER(w.POINT)], w.BOOL),
            (u, "RegisterWindowMessageW"): ([w.LPCWSTR], w.UINT),
            (k, "GetModuleHandleW"): ([w.LPCWSTR], w.HMODULE),
            (s, "Shell_NotifyIconW"): ([w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)], w.BOOL),
        }
        for (dll, name), (argtypes, restype) in sigs.items():
            function = getattr(dll, name)
            function.argtypes, function.restype = argtypes, restype
        self.user32, self.kernel32, self.shell32 = u, k, s

    def _load_icon(self, name: str, large: bool = False):
        size = self.user32.GetSystemMetrics(11 if large else 49)  # SM_CXICON / SM_CXSMICON
        return self.user32.LoadImageW(None, str(_asset(name)), 1, size, size, 0x10)  # LR_LOADFROMFILE

    # --- lifecycle ----------------------------------------------------------

    def run(self) -> int:
        log("tray starting")
        try:
            self._create_window()
            self.icons = {
                "idle": self._load_icon("crec.ico"),
                "busy": self._load_icon("crec-busy.ico"),
                "error": self._load_icon("crec-error.ico"),
                "large": self._load_icon("crec.ico", large=True),
            }
            self._add_icon()
            self._register_hotkeys(announce=True)
            self._check_for_update()
        except Exception:  # noqa: BLE001 - nowhere to show it but the log
            log("tray failed to start:\n" + traceback.format_exc())
            return 1

        message = self.ctypes.wintypes.MSG()
        while self.user32.GetMessageW(self.ctypes.byref(message), None, 0, 0) > 0:
            self.user32.TranslateMessage(self.ctypes.byref(message))
            self.user32.DispatchMessageW(self.ctypes.byref(message))
        log("tray stopped")
        return 0

    def _create_window(self) -> None:
        instance = self.kernel32.GetModuleHandleW(None)
        self._wndproc = self.WNDPROC(self._on_message)  # keep a reference: ctypes won't
        window_class = self.WNDCLASSW(
            lpfnWndProc=self._wndproc, hInstance=instance, lpszClassName=CLASS_NAME
        )
        if not self.user32.RegisterClassW(self.ctypes.byref(window_class)):
            raise OSError(f"RegisterClassW failed ({self.ctypes.get_last_error()})")
        # A real (never shown) top-level window, not a message-only one: only
        # top-level windows hear "TaskbarCreated" when Explorer restarts.
        self.hwnd = self.user32.CreateWindowExW(
            0, CLASS_NAME, "crec", 0, 0, 0, 0, 0, None, None, instance, None
        )
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed ({self.ctypes.get_last_error()})")
        self.taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")

    def _quit(self) -> None:
        for index in range(len(self.hotkeys)):
            self.user32.UnregisterHotKey(self.hwnd, index + 1)
        self._notify(2)  # NIM_DELETE
        self.user32.DestroyWindow(self.hwnd)

    # --- the icon -------------------------------------------------------------

    def _icon_data(self, flags: int):
        data = self.NOTIFYICONDATAW()
        data.cbSize = self.ctypes.sizeof(self.NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        return data

    def _notify(self, action: int, data=None) -> None:
        self.shell32.Shell_NotifyIconW(action, self.ctypes.byref(data or self._icon_data(0)))

    def _add_icon(self) -> None:
        data = self._icon_data(0x01 | 0x02 | 0x04 | 0x80)  # MESSAGE | ICON | TIP | SHOWTIP
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self.icons["idle"]
        data.szTip = self._tooltip()
        self._notify(0, data)  # NIM_ADD
        data.uVersion = 4  # NOTIFYICON_VERSION_4: richer, saner click events
        self._notify(4, data)  # NIM_SETVERSION

    def _tooltip(self) -> str:
        state = "paused" if self.paused else self.status
        extra = f" - crec {self.update_found} available" if self.update_found else ""
        return f"crec - {state}{extra}"[:127]

    def _refresh(self, icon: str | None = None) -> None:
        data = self._icon_data(0x02 | 0x04 | 0x80)
        data.hIcon = self.icons[icon or ("busy" if self.busy else "idle")]
        data.szTip = self._tooltip()
        self._notify(1, data)  # NIM_MODIFY

    def _balloon(
        self, title: str, text: str, *, error: bool = False,
        target: Path | None = None, action=None,
    ) -> None:
        from .cli import _reveal

        self.balloon_action = action or ((lambda: _reveal(target)) if target else None)
        data = self._icon_data(0x10)  # NIF_INFO
        data.szInfoTitle = title[:63]
        data.szInfo = (text[:252] + "...") if len(text) > 255 else text
        if error:
            data.dwInfoFlags = 0x03  # NIIF_ERROR
        else:
            data.dwInfoFlags = 0x04 | 0x20  # NIIF_USER | NIIF_LARGE_ICON: our own icon
            data.hBalloonIcon = self.icons["large"]
        self._notify(1, data)

    # --- hotkeys ------------------------------------------------------------

    def _register_hotkeys(self, *, announce: bool) -> None:
        for index in range(len(self.hotkeys)):
            self.user32.UnregisterHotKey(self.hwnd, index + 1)
        self.hotkeys, problems = load_hotkeys()
        failed = []
        if not self.paused:
            for index, hotkey in enumerate(self.hotkeys):
                ok = self.user32.RegisterHotKey(
                    self.hwnd, index + 1, hotkey.modifiers | _MOD_NOREPEAT, hotkey.key
                )
                if not ok:
                    failed.append(hotkey.label)
        for problem in problems:
            log(f"warning: {problem}")
        if failed or problems:
            detail = "; ".join(
                [f"{', '.join(failed)} taken by another app" if failed else ""] + problems
            ).strip("; ")
            self._balloon("Some hotkeys are not active", detail + "\nFix them: Edit presets and hotkeys.", error=True)
        elif announce:
            keys = ", ".join(hotkey.label for hotkey in self.hotkeys) or "none configured"
            self._balloon("crec is running", f"Copy a link or a file, then press {keys}.")

    # --- jobs ---------------------------------------------------------------

    def _start(self, hotkey: Hotkey) -> None:
        if self.busy:
            self._balloon("Still busy", "crec is still working on the last one.")
            return
        self.busy = True
        self.status = "starting"
        self._refresh()
        threading.Thread(target=self._work, args=(hotkey,), daemon=True).start()

    def _work(self, hotkey: Hotkey) -> None:
        try:
            self.result = ("ok", run_job(hotkey.flags, self._on_status))
        except CrecError as exc:
            log(f"failed: {exc}")
            self.result = ("error", str(exc))
        except Exception as exc:  # noqa: BLE001 - must never kill the tray
            log("job crashed:\n" + traceback.format_exc())
            self.result = ("error", f"crec crashed: {exc}. Details in {_log_file()}")
        self.user32.PostMessageW(self.hwnd, self.WM_JOB_DONE, 0, 0)

    def _on_status(self, text: str) -> None:
        self.status = text
        self.user32.PostMessageW(self.hwnd, self.WM_STATUS, 0, 0)

    def _import_cookies(self) -> None:
        """The extension's Copy button, then this: no terminal needed."""
        from . import clipboard, cookies

        try:
            copied = clipboard.read_files()
            text = copied[0].read_text(encoding="utf-8", errors="replace") if copied else clipboard.read_text()
            stored = cookies.add(text or "")
        except (CrecError, OSError) as exc:
            self._balloon("No cookies imported", str(exc), error=True)
            return
        sites = ", ".join(entry.site for entry in stored)
        self._balloon(
            f"Saved your {sites} login",
            f"crec will use it for {sites} downloads, and only for {sites}.",
        )

    # --- crec's own updates -------------------------------------------------

    def _check_for_update(self, *, force: bool = False, quiet: bool = False) -> None:
        """Look for a newer crec at most daily, off the UI thread.

        Tray users never see the terminal's "crec X is out" line, so without
        this they would stay on whatever version they first installed.
        """
        from . import selfupdate

        if not selfupdate.is_installed_app():
            if force:
                self._balloon("Updates", "This crec came from pip - update it with: pipx upgrade crec")
            return
        stamp = paths.data_dir() / "last-crec-update-check"
        try:
            if not force and time.time() - stamp.stat().st_mtime < 24 * 3600:
                return
        except OSError:
            pass
        try:
            paths.ensure(stamp.parent)
            stamp.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        self.update_announced = force
        self.update_quiet = quiet

        def look() -> None:
            release = selfupdate.latest_release()
            self.update_found = (
                release.version if release and selfupdate.newer(release.version) else None
            )
            self.user32.PostMessageW(self.hwnd, self.WM_UPDATE_FOUND, 0, 0)

        threading.Thread(target=look, daemon=True).start()

    def _offer_update(self) -> None:
        from . import __version__

        self._refresh()
        if self.update_found and not self.update_quiet:
            self._balloon(
                f"crec {self.update_found} is available",
                f"You have {__version__}. Click here to update - it takes a few seconds.",
                action=self._run_update,
            )
        elif self.update_announced:
            self._balloon("crec is up to date", f"You have the latest version ({__version__}).")

    def _run_update(self) -> None:
        # `crec update` stops this tray and its swap script starts the new one.
        subprocess.Popen(
            _self_command("update"),
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _finish(self) -> None:
        self.busy = False
        self.status = "ready"
        kind, payload = self.result or ("error", "no result")
        if kind == "ok":
            files: list[Path] = payload  # type: ignore[assignment]
            self.last_files = files
            self._refresh("idle")
            if len(files) == 1:
                size = files[0].stat().st_size / 1_048_576 if files[0].exists() else 0
                self._balloon("Ready - paste it anywhere", f"{files[0].name}  ({size:.1f} MB)", target=files[0])
            else:
                self._balloon("Ready - paste them anywhere", f"{len(files)} files", target=files[0] if files else None)
            self._check_for_update(quiet=True)
        else:
            self.status = "last run failed"
            self._refresh("error")
            self._balloon("crec could not do that", str(payload), error=True)

    # --- menu ---------------------------------------------------------------

    def _show_menu(self) -> None:
        u = self.user32
        menu = u.CreatePopupMenu()
        MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR = 0x0, 0x1, 0x8, 0x800
        u.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, self._tooltip())
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for index, hotkey in enumerate(self.hotkeys):
            u.AppendMenuW(
                menu, MF_STRING | (MF_GRAYED if self.busy else 0),
                self.HOTKEY_BASE + index, f"{hotkey.action}\t{hotkey.label}",
            )
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, self.CMD_OPEN, "Open crec folder")
        u.AppendMenuW(menu, MF_STRING, self.CMD_EDIT, "Edit presets and hotkeys")
        u.AppendMenuW(menu, MF_STRING, self.CMD_RELOAD, "Reload hotkeys")
        u.AppendMenuW(menu, MF_STRING, self.CMD_COOKIES, "Import cookies from clipboard")
        u.AppendMenuW(menu, MF_STRING, self.CMD_LOG, "Show log")
        if self.update_found:
            u.AppendMenuW(menu, MF_STRING, self.CMD_UPDATE, f"Update to crec {self.update_found}")
        else:
            u.AppendMenuW(menu, MF_STRING, self.CMD_UPDATE, "Check for updates")
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if startup_enabled() else 0), self.CMD_STARTUP, "Start with Windows")
        u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.paused else 0), self.CMD_PAUSE, "Pause hotkeys")
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, self.CMD_QUIT, "Quit")

        point = self.ctypes.wintypes.POINT()
        u.GetCursorPos(self.ctypes.byref(point))
        u.SetForegroundWindow(self.hwnd)  # or the menu won't close on an outside click
        chosen = u.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
        u.PostMessageW(self.hwnd, 0, 0, 0)  # WM_NULL, per the TrackPopupMenu docs
        u.DestroyMenu(menu)
        if chosen:
            self._on_command(chosen)

    def _on_command(self, command: int) -> None:
        from .cli import _reveal

        if command >= self.HOTKEY_BASE:
            index = command - self.HOTKEY_BASE
            if index < len(self.hotkeys):
                self._start(self.hotkeys[index])
        elif command == self.CMD_OPEN:
            folder = paths.ensure(paths.downloads_dir())
            _reveal(self.last_files[0] if self.last_files and self.last_files[0].exists() else folder,
                    select=bool(self.last_files))
        elif command == self.CMD_EDIT:
            target = paths.user_presets_file()
            presets.write_starter_file(target)
            _reveal(target, select=False)
        elif command == self.CMD_RELOAD:
            self._register_hotkeys(announce=True)
        elif command == self.CMD_COOKIES:
            self._import_cookies()
        elif command == self.CMD_UPDATE:
            if self.update_found:
                self._run_update()
            else:
                self._check_for_update(force=True)
        elif command == self.CMD_LOG:
            if _log_file().exists():
                _reveal(_log_file(), select=False)
        elif command == self.CMD_STARTUP:
            set_startup(not startup_enabled())
        elif command == self.CMD_PAUSE:
            self.paused = not self.paused
            self._register_hotkeys(announce=False)
            self._refresh()
        elif command == self.CMD_QUIT:
            self._quit()

    # --- window procedure ---------------------------------------------------

    def _on_message(self, hwnd, message, wparam, lparam):
        try:
            if message == 0x0312:  # WM_HOTKEY
                index = wparam - 1
                if 0 <= index < len(self.hotkeys) and not self.paused:
                    self._start(self.hotkeys[index])
                return 0
            if message == self.WM_TRAY:
                event = lparam & 0xFFFF
                if event in (0x007B, 0x0400, 0x0401):  # WM_CONTEXTMENU, NIN_SELECT, NIN_KEYSELECT
                    self._show_menu()
                elif event == 0x0405 and self.balloon_action:  # NIN_BALLOONUSERCLICK
                    self.balloon_action()
                return 0
            if message == self.WM_JOB_DONE:
                self._finish()
                return 0
            if message == self.WM_UPDATE_FOUND:
                self._offer_update()
                return 0
            if message == self.WM_STATUS:
                if self.busy:
                    self._refresh()
                return 0
            if message == self.taskbar_created:
                self._add_icon()  # Explorer restarted and forgot us
                return 0
            if message == 0x0010:  # WM_CLOSE (from `crec tray --stop`)
                self._quit()
                return 0
            if message == 0x0002:  # WM_DESTROY
                self.user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - an exception here would kill the loop
            log("window procedure error:\n" + traceback.format_exc())
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)


