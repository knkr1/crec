"""Saved logins, one site at a time.

`--cookies chrome` hands yt-dlp the browser's whole cookie jar, and on Windows
it mostly cannot even do that any more: Chrome encrypts its cookies so only
Chrome can read them. So crec also keeps its own small store instead:

* You export one site's cookies from the browser (the "Get cookies.txt
  LOCALLY" extension does this) and run ``crec cookies add``.
* crec keeps **only that site's cookies** - anything else in the export is
  dropped - encrypted with your Windows login (DPAPI), one file per site.
* Downloads from that site use them automatically; no other site ever sees
  them. When yt-dlp finishes it writes the jar back, and crec keeps the
  refreshed values, so a login lasts as long as it would in the browser.

A cookie *is* the login: whoever sends a site's session cookie is you, to that
site. Hence the scoping, the encryption, and never sending them anywhere but
the site they came from.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import paths
from .errors import CrecError

# Second-level labels that are not registrable on their own: example.co.uk is
# a site, co.uk is not. Not the full public suffix list - just what video
# sites actually sit under.
_SHARED_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.jp", "ne.jp", "com.br",
    "co.nz", "co.in", "co.kr", "com.tr", "com.mx", "com.ar", "co.za", "com.cn",
}
# Sites that answer under another name, so one saved login covers both.
_ALIASES = {"twitter.com": "x.com", "youtu.be": "youtube.com", "fb.watch": "facebook.com"}


def site_of(host: str) -> str:
    """``www.tiktok.com`` / ``.tiktok.com`` / ``vm.tiktok.com`` -> ``tiktok.com``."""
    labels = [label for label in host.lower().strip(".").split(".") if label]
    if len(labels) <= 2:
        site = ".".join(labels)
    elif ".".join(labels[-2:]) in _SHARED_SUFFIXES:
        site = ".".join(labels[-3:])
    else:
        site = ".".join(labels[-2:])
    return _ALIASES.get(site, site)


def site_of_url(url: str) -> str | None:
    host = urlparse(url).hostname
    return site_of(host) if host else None


# --------------------------------------------------------------------------- #
# Netscape cookies.txt
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Cookie:
    domain: str
    subdomains: bool
    path: str
    secure: bool
    expires: int  # 0 = session cookie
    name: str
    value: str
    http_only: bool = False

    @property
    def site(self) -> str:
        return site_of(self.domain)

    def line(self) -> str:
        prefix = "#HttpOnly_" if self.http_only else ""
        return "\t".join((
            prefix + self.domain, "TRUE" if self.subdomains else "FALSE", self.path,
            "TRUE" if self.secure else "FALSE", str(self.expires), self.name, self.value,
        ))


HEADER = "# Netscape HTTP Cookie File\n"


def parse(text: str) -> list[Cookie]:
    """Read a Netscape-format cookies.txt. Raises CrecError if it isn't one."""
    cookies: list[Cookie] = []
    for raw in text.splitlines():
        line = raw.strip("\r\n")
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, subdomains, path, secure, expires, name, value = fields
        try:
            expiry = int(float(expires or 0))
        except ValueError:
            continue
        cookies.append(Cookie(
            domain=domain, subdomains=subdomains.upper() == "TRUE", path=path or "/",
            secure=secure.upper() == "TRUE", expires=max(expiry, 0), name=name,
            value=value, http_only=http_only,
        ))
    if not cookies:
        raise CrecError(
            "That doesn't look like a cookies.txt export. Use the \"Get cookies.txt "
            "LOCALLY\" browser extension on the site, and export (or copy) it."
        )
    return cookies


def render(cookies: list[Cookie]) -> str:
    return HEADER + "".join(cookie.line() + "\n" for cookie in cookies)


# --------------------------------------------------------------------------- #
# encryption at rest
# --------------------------------------------------------------------------- #

def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData: only this Windows user can read it."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    buffer = ctypes.create_string_buffer(data, len(data))
    source = BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = BLOB()
    if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):  # UI_FORBIDDEN
        raise CrecError(f"Could not {'encrypt' if protect else 'decrypt'} saved cookies "
                        f"(error {ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _seal(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        return base64.b64encode(_dpapi(raw, protect=True)).decode("ascii"), True
    return base64.b64encode(raw).decode("ascii"), False  # file mode 0600 protects it


def _unseal(blob: str, protected: bool) -> str:
    raw = base64.b64decode(blob)
    return (_dpapi(raw, protect=False) if protected else raw).decode("utf-8")


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #

def store_dir() -> Path:
    return paths.data_dir() / "cookies"


def _entry_file(site: str) -> Path:
    return store_dir() / f"{site}.json"


@dataclass
class Saved:
    site: str
    count: int
    added: float
    # Last time a download used (and refreshed) it. Expiry dates are no guide
    # to whether a login still works: the soonest one is usually a 30-minute
    # tracking cookie, and the session cookie's real lifetime is server-side.
    used: float | None = None


def _write(site: str, cookies: list[Cookie], *, added: float | None = None, used: bool = False) -> Saved:
    folder = paths.ensure(store_dir())
    blob, protected = _seal(render(cookies))
    entry = {
        "site": site, "count": len(cookies), "added": added or time.time(),
        "used": time.time() if used else None, "protected": protected, "cookies": blob,
    }
    target = _entry_file(site)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(entry), encoding="utf-8")
    if sys.platform != "win32":
        temporary.chmod(0o600)
    os.replace(temporary, target)
    if sys.platform != "win32":
        folder.chmod(0o700)
    return Saved(site, entry["count"], entry["added"], entry["used"])


def _read(site: str) -> tuple[dict, list[Cookie]] | None:
    try:
        entry = json.loads(_entry_file(site).read_text(encoding="utf-8"))
        return entry, parse(_unseal(entry["cookies"], entry.get("protected", False)))
    except (OSError, ValueError, KeyError, CrecError):
        return None


def add(text: str, *, site: str | None = None, now: float | None = None) -> list[Saved]:
    """Save the cookies in *text*. Returns what was stored, per site.

    An export holding several sites is refused unless *site* picks one: the
    point is to keep only what a download needs, not a browser's whole jar.
    """
    now = now or time.time()
    cookies = parse(text)
    live = [c for c in cookies if not c.expires or c.expires > now]
    if not live:
        raise CrecError("Every cookie in that export has already expired. Log in again and re-export.")

    by_site: dict[str, list[Cookie]] = {}
    for cookie in live:
        by_site.setdefault(cookie.site, []).append(cookie)

    if site:
        wanted = site_of(site)
        if wanted not in by_site:
            raise CrecError(f"That export has no cookies for {wanted} (it has: {', '.join(sorted(by_site))}).")
        by_site = {wanted: by_site[wanted]}
    elif len(by_site) > 1:
        ranked = sorted(by_site, key=lambda s: -len(by_site[s]))
        more = "..." if len(ranked) > 8 else ""
        raise CrecError(
            f"That export holds cookies for {len(by_site)} sites ({', '.join(ranked[:8])}{more}). "
            f"crec only keeps one site's - export just the site you need, or pick one: "
            f"crec cookies add --site {ranked[0]}"
        )
    return [_write(name, jar) for name, jar in by_site.items()]


def saved() -> list[Saved]:
    found: list[Saved] = []
    for file in sorted(store_dir().glob("*.json")) if store_dir().is_dir() else []:
        try:
            entry = json.loads(file.read_text(encoding="utf-8"))
            found.append(Saved(entry["site"], entry["count"], entry["added"], entry.get("used")))
        except (OSError, ValueError, KeyError):
            continue
    return found


def remove(site: str) -> bool:
    target = _entry_file(site_of(site))
    if target.exists():
        target.unlink()
        return True
    return False


def has_login_for(url: str) -> str | None:
    """The site name if crec has saved cookies that would be used for *url*."""
    site = site_of_url(url)
    return site if site and _entry_file(site).exists() else None


@contextmanager
def lent_to_ytdlp(url: str):
    """Yield a cookies.txt path for *url*'s site, or None if nothing is saved.

    The file exists only for the duration of the download. yt-dlp rewrites it
    on exit with whatever the site refreshed; those values for this site are
    folded back into the store, and the file is deleted.
    """
    site = site_of_url(url)
    loaded = _read(site) if site else None
    if not loaded:
        yield None
        return

    entry, jar = loaded
    handoff = paths.ensure(paths.data_dir()) / f"cookies-{os.getpid()}.txt"
    handoff.write_text(render(jar), encoding="utf-8")
    if sys.platform != "win32":
        handoff.chmod(0o600)
    try:
        yield handoff
        try:
            refreshed = [c for c in parse(handoff.read_text(encoding="utf-8")) if c.site == site]
        except (OSError, CrecError):
            refreshed = []
        if refreshed:
            _write(site, refreshed, added=entry.get("added"), used=True)
    finally:
        handoff.unlink(missing_ok=True)
