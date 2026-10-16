"""Draw crec's tray and exe icons. Pure Python, so no Pillow in the build.

    python tools/make_icons.py            # writes crec/assets/*.ico
    python tools/make_icons.py --preview DIR   # also writes 256px PNGs to look at

The mark is an arrow dropping onto a line - "it lands on your clipboard" -
on a rounded square. It has to survive 16x16 in the tray, so it is bold and
has no detail worth losing. State is carried by colour (idle indigo, busy
amber) plus a red badge for errors, since shape changes vanish at that size.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "crec" / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
SUPERSAMPLE = 4

INDIGO = (91, 91, 214)
AMBER = (232, 150, 20)
RED = (229, 72, 77)
WHITE = (255, 255, 255)


# --------------------------------------------------------------------------- #
# shapes in unit coordinates (0..1, y down)
# --------------------------------------------------------------------------- #

def _in_rounded_rect(x, y, left, top, right, bottom, radius):
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and top <= y <= bottom


def _in_triangle(x, y, a, b, c):
    def side(p, q):
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])

    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _in_circle(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _glyph(x, y):
    # Edges sit on multiples of 1/16 so the 16px tray icon gets whole pixels:
    # a 2px shaft and a 2px baseline instead of blurry 1.5px ones.
    shaft = 7 / 16 <= x <= 9 / 16 and 3 / 16 <= y <= 8 / 16
    head = _in_triangle(x, y, (3 / 16, 7 / 16), (13 / 16, 7 / 16), (0.5, 11.5 / 16))
    base = _in_rounded_rect(x, y, 4 / 16, 12 / 16, 12 / 16, 14 / 16, 0.03)
    return shaft or head or base


def _colour_at(x, y, background, badge):
    """RGBA of the topmost shape at (x, y), or None for transparent."""
    if badge:
        # Error badge, bottom-right, with a white ring so it reads on any taskbar.
        if _in_circle(x, y, 0.76, 0.76, 0.20):
            return RED + (255,)
        if _in_circle(x, y, 0.76, 0.76, 0.255):
            return WHITE + (255,)
    if not _in_rounded_rect(x, y, 0.03, 0.03, 0.97, 0.97, 0.22):
        return None
    return (WHITE if _glyph(x, y) else background) + (255,)


def render(size: int, background, badge: bool) -> bytes:
    """Return straight (non-premultiplied) RGBA rows, top to bottom."""
    n = SUPERSAMPLE
    out = bytearray()
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(n):
                for sx in range(n):
                    rgba = _colour_at((px + (sx + 0.5) / n) / size, (py + (sy + 0.5) / n) / size,
                                      background, badge)
                    if rgba:
                        r += rgba[0]
                        g += rgba[1]
                        b += rgba[2]
                        a += 255
            count = n * n
            if a:
                covered = a // 255
                out += bytes((r // covered, g // covered, b // covered, a // count))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


# --------------------------------------------------------------------------- #
# file formats
# --------------------------------------------------------------------------- #

def png(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    stride = size * 4
    raw = b"".join(b"\0" + rgba[row * stride:(row + 1) * stride] for row in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico whose entries are PNGs - supported by Windows since Vista."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    return header + directory + blobs


VARIANTS = {
    "crec": (INDIGO, False),
    "crec-busy": (AMBER, False),
    "crec-error": (INDIGO, True),
}


def main(argv: list[str]) -> int:
    preview = Path(argv[argv.index("--preview") + 1]) if "--preview" in argv else None
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, (background, badge) in VARIANTS.items():
        images = [(size, png(size, render(size, background, badge))) for size in SIZES]
        (ASSETS / f"{name}.ico").write_bytes(ico(images))
        if preview:
            preview.mkdir(parents=True, exist_ok=True)
            (preview / f"{name}.png").write_bytes(images[-1][1])
            (preview / f"{name}-16.png").write_bytes(images[0][1])
        print(f"wrote {name}.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
