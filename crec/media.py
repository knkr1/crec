"""ffmpeg work: trimming, size targeting, GIFs, format conversion.

The interesting piece is :func:`plan_encode`. "Compression level 0-9" tells a
user nothing - nobody knows what 5 means. What people actually want is "make it
fit under Discord's limit", so crec solves for the bitrate that lands on a byte
budget, and *drops the resolution* when that budget is too small to justify the
pixels. Encoding 1080p at 300 kbps produces mush; 480p at 300 kbps is watchable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import deps
from .errors import CrecError, ImpossibleTarget

# Minimum bitrate (kbps) at which each height still looks acceptable. Used to
# pick a resolution that suits the budget rather than fighting it.
_HEIGHT_LADDER: tuple[tuple[int, int], ...] = (
    (2160, 12000),
    (1440, 6000),
    (1080, 2500),
    (720, 1200),
    (480, 600),
    (360, 350),
    (240, 180),
    (144, 90),
)

_MIN_VIDEO_KBPS = 80

# MP4 spends roughly this many bytes of sample-table entry (stsz/stco/stsc/ctts)
# per media sample. Measured at 13.8 B/sample over a 10-minute encode; rounded up
# because undershooting the reservation is what pushes a file over a hard limit.
_BYTES_PER_SAMPLE = 15
_MOOV_ALLOWANCE = 65_536

# ffmpeg's native AAC encoder does not hit -b:a exactly. Measured overshoot:
# +30% at 48k stereo (it simply cannot comply), +4% at 48k mono, +1.5% at 128k.
# Reserve accordingly rather than discovering it in the output size.
# At or below this, use mono. 48k stereo was measured at +30%; 64k stereo was not
# measured and is not worth the risk when mono at the same rate sounds better anyway.
_AAC_STEREO_FLOOR_KBPS = 64
_AAC_COMPLIANCE = 1.06

_NULL = "NUL" if sys.platform == "win32" else "/dev/null"


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    has_video: bool
    size_bytes: int
    audio_sample_rate: int = 48_000

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1_048_576


def probe(path: Path) -> Probe:
    ffprobe = deps.ffprobe_path()
    result = deps.run_quiet(
        [
            str(ffprobe), "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise CrecError(f"ffprobe could not read {path.name}: {result.stderr.strip()[:200]}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for candidate in (data.get("format", {}).get("duration"), (video or {}).get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    try:
        sample_rate = int((audio or {}).get("sample_rate") or 48_000)
    except (TypeError, ValueError):
        sample_rate = 48_000

    return Probe(
        duration=duration,
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        fps=_parse_fps((video or {}).get("r_frame_rate")),
        has_audio=audio is not None,
        has_video=video is not None,
        size_bytes=path.stat().st_size if path.exists() else 0,
        audio_sample_rate=sample_rate,
    )


def _parse_fps(raw: str | None) -> float:
    if not raw:
        return 0.0
    if "/" in raw:
        numerator, _, denominator = raw.partition("/")
        try:
            return float(numerator) / float(denominator) if float(denominator) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# cut specs
# --------------------------------------------------------------------------- #

def parse_timestamp(text: str) -> float:
    """``90``, ``1:30``, ``1:02:03.5`` -> seconds."""
    text = text.strip()
    if not text:
        raise ValueError("empty timestamp")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"'{text}' has too many ':' segments")
    total = 0.0
    for part in parts:
        if not re.fullmatch(r"\d+(\.\d+)?", part.strip()):
            raise ValueError(f"'{text}' is not a timestamp")
        total = total * 60 + float(part)
    return total


def parse_cut(spec: str) -> tuple[float, float | None]:
    """``0:10-0:40`` -> ``(10.0, 40.0)``. Open ends allowed: ``0:10-``, ``-0:40``."""
    raw = spec.strip()
    # Split on the range dash, but not on a dash inside a negative-free timestamp.
    if ".." in raw:
        left, _, right = raw.partition("..")
    elif "-" in raw:
        left, _, right = raw.partition("-")
    else:
        raise CrecError(
            f"Could not read '{spec}' as a range. Use start-end, e.g. -c 0:10-0:40"
        )

    try:
        start = parse_timestamp(left) if left.strip() else 0.0
        end = parse_timestamp(right) if right.strip() else None
    except ValueError as exc:
        raise CrecError(f"Bad --cut value '{spec}': {exc}") from exc

    if end is not None and end <= start:
        raise CrecError(f"--cut end ({end:g}s) must be after the start ({start:g}s).")
    return start, end


def ytdlp_sections(spec: str) -> str:
    """Render a cut spec as a yt-dlp ``--download-sections`` value."""
    start, end = parse_cut(spec)
    return f"*{start:g}-{'inf' if end is None else format(end, 'g')}"


# --------------------------------------------------------------------------- #
# size targeting
# --------------------------------------------------------------------------- #

@dataclass
class EncodePlan:
    video_kbps: int
    audio_kbps: int
    height: int | None
    note: str | None
    audio_mono: bool = False
    quality_warning: str | None = None


def container_overhead_bytes(info: Probe, *, fps: float | None = None) -> int:
    """Bytes MP4 spends on sample tables, which are not available for media.

    Modelled per *sample* rather than as a flat percentage: overhead scales with
    frame count, so a 60 fps ten-minute video pays far more than a flat 3% ever
    reserves. That underestimate is what pushed an earlier 10 MB target to
    11.3 MB.
    """
    rate = fps or info.fps or 30.0
    video_samples = info.duration * rate
    # An AAC frame is 1024 samples, so the packet count follows the sample rate.
    audio_samples = (
        info.duration * (info.audio_sample_rate or 48_000) / 1024 if info.has_audio else 0.0
    )
    return int((video_samples + audio_samples) * _BYTES_PER_SAMPLE) + _MOOV_ALLOWANCE


def plan_encode(
    info: Probe,
    target_mb: float,
    *,
    keep_audio: bool,
    audio_kbps: int | None = None,
    fps: float | None = None,
    correction: float = 1.0,
) -> EncodePlan:
    """Solve for the bitrate that lands under *target_mb*.

    *correction* scales the video budget, so a verified retry can compensate for
    an encoder that missed. Raises :class:`ImpossibleTarget` rather than silently
    emitting a slideshow.
    """
    if info.duration <= 0:
        raise CrecError("Cannot target a file size without knowing the duration.")

    target_bytes = target_mb * 1_048_576
    payload_bytes = target_bytes - container_overhead_bytes(info, fps=fps)
    if payload_bytes <= 0:
        needed = container_overhead_bytes(info, fps=fps) / 1_048_576
        raise ImpossibleTarget(
            f"{target_mb:g} MB cannot hold {_format_duration(info.duration)} of video - "
            f"the container structure alone needs {needed:.1f} MB. "
            f"Trim it with -c, or raise the target."
        )

    budget_kbps = payload_bytes * 8 / info.duration / 1000

    wants_audio = keep_audio and info.has_audio
    audio = (audio_kbps or 128) if wants_audio else 0
    mono = False

    def video_for(audio_request: int) -> int:
        # Reserve what AAC will actually produce, not what we asked it for.
        reserved = audio_request * _AAC_COMPLIANCE if audio_request else 0
        return int(budget_kbps * correction - reserved)

    video_kbps = video_for(audio)

    if video_kbps < _MIN_VIDEO_KBPS and wants_audio:
        # Reclaim from audio before giving up. Mono first: stereo AAC below
        # ~64 kbps overshoots by ~30% because it cannot comply at all.
        for candidate in (96, 64, 48):
            if candidate >= audio:
                continue
            audio = candidate
            mono = candidate <= _AAC_STEREO_FLOOR_KBPS
            video_kbps = video_for(audio)
            if video_kbps >= _MIN_VIDEO_KBPS:
                break

    if video_kbps < _MIN_VIDEO_KBPS:
        overhead_mb = container_overhead_bytes(info, fps=fps) / 1_048_576
        needed = (
            (_MIN_VIDEO_KBPS + audio * _AAC_COMPLIANCE) * info.duration * 1000 / 8 / 1_048_576
            + overhead_mb
        )
        raise ImpossibleTarget(
            f"{target_mb:g} MB cannot hold {_format_duration(info.duration)} of video. "
            f"Try at least {needed:.0f} MB, or trim it with -c "
            f"(about {_format_duration(_affordable_seconds(target_mb, info))} would fit)."
        )

    height, note = _height_for_bitrate(info.height, video_kbps)
    return EncodePlan(
        video_kbps=video_kbps,
        audio_kbps=audio,
        height=height,
        note=note,
        audio_mono=mono,
        quality_warning=_quality_warning(info, video_kbps, height),
    )


def _affordable_seconds(target_mb: float, info: Probe) -> float:
    """Roughly how much of this video would fit in the target at decent quality."""
    decent_kbps = 700.0
    return max(target_mb * 1_048_576 * 8 / 1000 / decent_kbps, 1.0)


def _quality_warning(info: Probe, video_kbps: int, height: int | None) -> str | None:
    """Flag budgets that are technically achievable but genuinely bad.

    Landing exactly on the floor is not a success worth staying quiet about -
    the user waits through a long encode and gets something unwatchable.
    """
    result_height = height or info.height
    if video_kbps <= 150 or (result_height and result_height <= 240):
        return (
            f"this is only {video_kbps} kbps at {result_height}p over "
            f"{_format_duration(info.duration)} - it will look rough. "
            f"Trimming with -c, or a bigger target, would look far better."
        )
    return None


def _height_for_bitrate(source_height: int, video_kbps: int) -> tuple[int | None, str | None]:
    if source_height <= 0:
        return None, None
    for height, minimum in _HEIGHT_LADDER:
        if height > source_height:
            continue
        if video_kbps >= minimum:
            if height < source_height:
                return height, f"dropped to {height}p so {video_kbps} kbps looks decent"
            return None, None
    smallest = _HEIGHT_LADDER[-1][0]
    if smallest < source_height:
        return smallest, f"dropped to {smallest}p - {video_kbps} kbps is very tight"
    return None, None


def rung_above(height: int) -> int:
    """The next standard resolution up from *height*.

    Used to cap downloads: downscaling from one rung above gives a visibly
    better result than encoding a native source at the same height, and the
    extra bandwidth at these sizes is trivial.
    """
    taller = [rung for rung, _ in reversed(_HEIGHT_LADDER) if rung > height]
    return taller[0] if taller else height


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


# --------------------------------------------------------------------------- #
# running ffmpeg
# --------------------------------------------------------------------------- #

def _run(args: list[str], *, what: str) -> None:
    result = deps.run_quiet(args)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-4:])
        raise CrecError(f"{what} failed:\n{tail}")


def _scale_filter(height: int | None, width: int | None, fps: int | None) -> str | None:
    chain: list[str] = []
    if fps:
        chain.append(f"fps={fps}")
    if width:
        # -2 keeps the other dimension even, which h264 requires.
        chain.append(f"scale='min({width},iw)':-2:flags=lanczos")
    elif height:
        chain.append(f"scale=-2:'min({height},ih)':flags=lanczos")
    return ",".join(chain) if chain else None


def encode_to_size(
    source: Path,
    destination: Path,
    plan: EncodePlan,
    *,
    width: int | None = None,
    fps: int | None = None,
    extra_args: list[str] | None = None,
    on_pass: object = None,
) -> None:
    """Two-pass x264 encode aimed at a byte budget.

    Two passes rather than one because single-pass ``-b:v`` overshoots, and
    overshooting a hard upload limit means the file is useless.
    """
    ffmpeg = deps.ffmpeg_path()
    video_filter = _scale_filter(plan.height, width, fps)

    with tempfile.TemporaryDirectory(prefix="crec-") as workdir:
        passlog = str(Path(workdir) / "passlog")
        common = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
        if video_filter:
            common += ["-vf", video_filter]

        if callable(on_pass):
            on_pass(1)
        _run(
            common
            + [
                "-c:v", "libx264", "-b:v", f"{plan.video_kbps}k",
                "-pass", "1", "-passlogfile", passlog,
                "-an", "-f", "mp4", _NULL,
            ],
            what="ffmpeg (analysis pass)",
        )

        if callable(on_pass):
            on_pass(2)
        second = common + [
            "-c:v", "libx264", "-b:v", f"{plan.video_kbps}k",
            "-pass", "2", "-passlogfile", passlog,
            "-preset", "medium", "-movflags", "+faststart",
        ]
        if plan.audio_kbps:
            second += ["-c:a", "aac", "-b:a", f"{plan.audio_kbps}k"]
            if plan.audio_mono:
                second += ["-ac", "1"]
        else:
            second += ["-an"]
        second += list(extra_args or [])
        second.append(str(destination))
        _run(second, what="ffmpeg (encode pass)")


def fit_to_size(
    source: Path,
    destination: Path,
    info: Probe,
    target_mb: float,
    *,
    keep_audio: bool,
    audio_kbps: int | None = None,
    width: int | None = None,
    fps: int | None = None,
    extra_args: list[str] | None = None,
    on_pass: object = None,
    on_note: object = None,
) -> Path:
    """Encode *source* so it genuinely lands under *target_mb*.

    Predicting the output size is not enough - encoders miss. So this measures
    the result and, if it overshot, re-encodes once with the budget scaled by how
    far off it was. Landing over a hard upload limit makes the file worthless,
    which is worth one extra encode to avoid.
    """
    limit = target_mb * 1_048_576
    correction = 1.0

    for attempt in (1, 2):
        plan = plan_encode(
            info, target_mb,
            keep_audio=keep_audio,
            audio_kbps=audio_kbps,
            fps=float(fps) if fps else None,
            correction=correction,
        )
        if attempt == 1 and callable(on_note):
            if plan.note:
                on_note(plan.note)
            if plan.quality_warning:
                on_note(plan.quality_warning)

        def report(pass_number: int, _attempt: int = attempt) -> None:
            if callable(on_pass):
                on_pass(pass_number, _attempt)

        encode_to_size(
            source, destination, plan,
            width=width, fps=fps, extra_args=extra_args, on_pass=report,
        )

        actual = destination.stat().st_size
        if actual <= limit:
            return destination

        if attempt == 2:
            # Two honest attempts. Better to say so than to hand over a file
            # that will be rejected on upload.
            raise ImpossibleTarget(
                f"Got it to {actual / 1_048_576:.1f} MB but could not reach "
                f"{target_mb:g} MB. Trim it with -c, or allow a bit more room."
            )

        # Scale the next attempt by how far the first missed.
        correction *= limit / actual * 0.98
        if callable(on_note):
            on_note(
                f"landed at {actual / 1_048_576:.1f} MB - retrying "
                f"{correction * 100:.0f}% of the budget to get under {target_mb:g} MB"
            )

    return destination


def trim(
    source: Path,
    destination: Path,
    spec: str,
    *,
    exact: bool = False,
) -> None:
    """Trim a local file.

    Fast path seeks to a keyframe and stream-copies - instant, but the cut lands
    on the nearest keyframe. ``exact`` re-encodes for frame accuracy.
    """
    ffmpeg = deps.ffmpeg_path()
    start, end = parse_cut(spec)
    args = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error"]

    if exact and not probe(source).has_video:
        # "Frame-exact" is meaningless without frames, and libx264 on an audio
        # file just fails. Re-encode the audio instead.
        args += ["-i", str(source), "-ss", f"{start:g}"]
        if end is not None:
            args += ["-to", f"{end:g}"]
        args += ["-c:a", "libmp3lame" if source.suffix == ".mp3" else "aac", "-q:a", "2"]
    elif exact:
        args += ["-i", str(source), "-ss", f"{start:g}"]
        if end is not None:
            args += ["-to", f"{end:g}"]
        args += ["-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart"]
    else:
        # -ss before -i is the fast seek; stream copy keeps it lossless.
        #
        # Use -t (duration), not -to. Before -i these are *input* options, and
        # -to is absolute on the source timeline while -t counts from the seek
        # point - so "-ss 5 -to 10" yields 5 seconds, not 10.
        args += ["-ss", f"{start:g}"]
        if end is not None:
            args += ["-t", f"{end - start:g}"]
        args += ["-i", str(source), "-c", "copy", "-avoid_negative_ts", "make_zero"]

    args.append(str(destination))
    _run(args, what="ffmpeg (trim)")


def to_gif(
    source: Path,
    destination: Path,
    *,
    fps: int = 15,
    width: int = 480,
) -> None:
    """Palette-generated GIF. A shared palette is the difference between a
    reasonable GIF and a 40 MB dithered mess."""
    ffmpeg = deps.ffmpeg_path()
    chain = (
        f"fps={fps},scale='min({width},iw)':-1:flags=lanczos,"
        "split[a][b];[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    _run(
        [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-vf", chain, "-loop", "0", str(destination),
        ],
        what="ffmpeg (gif)",
    )


def convert(
    source: Path,
    destination: Path,
    *,
    crf: int | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    drop_audio: bool = False,
    audio_kbps: int | None = None,
    extra_args: list[str] | None = None,
) -> None:
    """Quality-based (CRF) re-encode, or a remux when nothing needs re-encoding."""
    ffmpeg = deps.ffmpeg_path()
    args = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]

    video_filter = _scale_filter(height, width, fps)
    if video_filter:
        args += ["-vf", video_filter]

    if crf is not None or video_filter:
        args += ["-c:v", "libx264", "-crf", str(crf if crf is not None else 21), "-preset", "medium"]
        args += ["-movflags", "+faststart"]
    else:
        args += ["-c", "copy"]

    if drop_audio:
        args += ["-an"]
    elif audio_kbps:
        args += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"]

    args += list(extra_args or [])
    args.append(str(destination))
    _run(args, what="ffmpeg (convert)")


def replace(temporary: Path, target: Path) -> Path:
    """Swap a freshly produced file into place, keeping the new extension."""
    final = target.with_suffix(temporary.suffix)
    if final.resolve() != temporary.resolve():
        final.unlink(missing_ok=True)
        shutil.move(str(temporary), str(final))
    if target.exists() and target.resolve() != final.resolve():
        target.unlink(missing_ok=True)
    return final
