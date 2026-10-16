"""The command line.

Design rule: the common path takes zero arguments. You copy a link, you type
``crec``, you paste the file. Everything else is opt-in, and anything that can be
expressed as a bundle of primitives is a preset in TOML rather than code here.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, clipboard, deps, download, media, paths, presets, state
from .download import Progress, fetch
from .errors import CrecError
from .options import PRIMITIVES, Options

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SUBCOMMANDS = {"update", "doctor", "presets"}


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #

class _Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30, width=96)


def build_parser(preset_set: presets.PresetSet) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crec",
        formatter_class=_Formatter,
        description=(
            "yt-dlp + ffmpeg with a clipboard.\n\n"
            "Run `crec` with no arguments: it takes the URL from your clipboard,\n"
            "downloads it, and puts the finished file back on your clipboard.\n"
            "Copy a file instead of a link and crec works on that file."
        ),
        epilog=(
            "examples:\n"
            "  crec                        download whatever URL is on the clipboard\n"
            "  crec --discord              same, shrunk to fit Discord\n"
            "  crec -c 0:10-0:40 -s 8      trim to 30s and fit under 8 MB\n"
            "  crec -a                     audio only (reuses the last URL)\n"
            "  crec --gif -c 1:00-1:05     five-second GIF\n"
            "  crec clip.mp4 --discord     shrink a file you already have\n"
            "  crec ./clips --discord      every video in a folder\n"
            "\n"
            "commands:\n"
            "  crec update                 update yt-dlp now\n"
            "  crec doctor [--fix]         check (and repair) the setup\n"
            "  crec presets [--edit]       list presets / open your presets file\n"
        ),
    )

    parser.add_argument(
        "inputs", nargs="*", metavar="URL|FILE",
        help="a URL, or files/folders to work on (default: the clipboard)",
    )

    core = parser.add_argument_group("the ones you'll actually use")
    core.add_argument("-q", "--quality", type=int, metavar="H", help="max height, e.g. 720")
    core.add_argument("-a", "--audio", action="store_true", default=None, help="audio only")
    core.add_argument("-c", "--cut", metavar="A-B", help="trim to a range, e.g. 0:10-0:40")
    core.add_argument(
        "-s", "--size", type=float, metavar="MB",
        help="re-encode to land under this many MB",
    )
    core.add_argument("-o", "--output", metavar="DIR", help="output folder")
    core.add_argument(
        "-n", "--no-copy", action="store_true", default=None,
        help="don't touch the clipboard",
    )

    more = parser.add_argument_group("finer control")
    more.add_argument("--format", metavar="EXT", help="mp4 mkv webm gif mp3 m4a wav")
    more.add_argument("--crf", type=int, metavar="N", help="quality instead of size: 18 great, 28 small")
    more.add_argument("--scale", type=int, metavar="W", help="max width in pixels")
    more.add_argument("--fps", type=int, metavar="N", help="force frame rate")
    more.add_argument("--mute", action="store_true", default=None, help="drop the audio track")
    more.add_argument("--exact", action="store_true", default=None, help="frame-exact cut (slower)")
    more.add_argument("-t", "--thumbnail", action="store_true", default=None, help="also save the thumbnail")
    more.add_argument("-p", "--playlist", action="store_true", default=None, help="download the whole playlist")
    more.add_argument("--open", action="store_true", default=None, help="reveal the file when done")
    more.add_argument("--ffmpeg", metavar="ARGS", help="extra raw ffmpeg arguments")
    more.add_argument("--last", action="store_true", help="reuse the last URL or file, ignoring the clipboard")
    more.add_argument("-v", "--version", action="store_true", help="show version and exit")

    # Ask the parser which option strings are taken rather than maintaining a
    # hand-written reserved list that will silently drift out of date. A user's
    # preset must never be able to crash crec by picking a name we already use.
    taken = {
        option
        for action in parser._actions  # noqa: SLF001 - no public accessor exists
        for option in action.option_strings
    }

    if preset_set.presets:
        group = parser.add_argument_group(
            "presets",
            "From presets.toml. Add your own - see `crec presets --edit`.",
        )
        for name in list(preset_set.names()):
            if f"--{name}" in taken:
                preset_set.warnings.append(
                    f"preset '{name}' is ignored - --{name} is already a built-in flag. "
                    f"Rename it in your presets file."
                )
                del preset_set.presets[name]
                continue
            preset = preset_set.presets[name]
            group.add_argument(
                f"--{name}",
                action="store_true",
                default=None,
                dest=f"preset_{name.replace('-', '_')}",
                help=preset.description or "(no description)",
            )
    return parser


def explicit_options(args: argparse.Namespace) -> dict:
    """Only the flags the user actually typed, mapped onto primitives."""
    mapping = {
        "quality": args.quality,
        "audio_only": args.audio,
        "no_audio": args.mute,
        "cut": args.cut,
        "exact_cut": args.exact,
        "target_mb": args.size,
        "crf": args.crf,
        "format": args.format,
        "fps": args.fps,
        "scale": args.scale,
        "thumbnail": args.thumbnail,
        "playlist": args.playlist,
        "output_dir": args.output,
        "copy": False if args.no_copy else None,
        "open_folder": args.open,
        "ffmpeg_args": _split_ffmpeg(args.ffmpeg),
    }
    return {key: value for key, value in mapping.items() if value is not None}


def _split_ffmpeg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    import shlex

    return shlex.split(raw)


def resolve_options(
    args: argparse.Namespace, preset_set: presets.PresetSet
) -> tuple[Options, list[str]]:
    """defaults < presets (in the order given) < explicit flags."""
    options = Options().merged_with(preset_set.defaults)

    used: list[str] = []
    for name in preset_set.names():
        if getattr(args, f"preset_{name.replace('-', '_')}", None):
            options = options.merged_with(preset_set.presets[name].values)
            used.append(name)

    options = options.merged_with(explicit_options(args))
    if options.wants_audio_container:
        # `--format mp3` means audio. Without this it downloaded the video and
        # left it as mp4, since nothing downstream converts video to audio.
        options.audio_only = True
    return options, used


# --------------------------------------------------------------------------- #
# URL sourcing
# --------------------------------------------------------------------------- #

MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv", ".ts", ".gif",
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac",
}


@dataclass
class Source:
    """What a run works on: a URL to download, or files already on disk."""

    url: str | None = None
    files: list[Path] = field(default_factory=list)
    where: str = ""


def _media_in(folder: Path) -> list[Path]:
    """Media files directly inside *folder* - not recursive, never surprising."""
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
    )


def _from_arguments(given: list[str]) -> Source:
    if len(given) == 1:
        match = _URL.search(given[0])
        if match and not Path(given[0]).exists():
            return Source(url=match.group(0), where="argument")

    files: list[Path] = []
    for raw in given:
        path = Path(raw.strip('"')).expanduser()
        if path.is_dir():
            found = _media_in(path)
            if not found:
                raise CrecError(f"No video or audio files in {path}.")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        elif _URL.search(raw):
            raise CrecError("One URL at a time - for a playlist, pass its URL with -p.")
        else:
            raise CrecError(f"'{raw}' is not a URL, a file, or a folder.")
    return Source(files=files, where="argument")


def _remembered() -> Source | None:
    inputs = state.last_inputs()
    if inputs:
        return Source(files=inputs, where="last run")
    url = state.last_url()
    if url:
        return Source(url=url, where="last run")
    return None


def _same_files(left: list[Path], right: list[Path]) -> bool:
    def key(paths: list[Path]) -> set[str]:
        return {os.path.normcase(str(p.resolve())) for p in paths}

    return bool(left) and key(left) == key(right)


def find_source(given: list[str], *, prefer_last: bool) -> Source:
    """Pick what to work on: arguments > a copied file > a copied URL > last run."""
    if given:
        return _from_arguments(given)

    if not prefer_last:
        copied = [p for p in clipboard.read_files() if p.suffix.lower() in MEDIA_SUFFIXES]
        if copied:
            # Our own output still on the clipboard means "the same thing again":
            # `crec` then `crec -a` should re-fetch the audio, not dig it out of
            # the already-processed file. Fall through to the last run instead.
            if not _same_files(copied, state.last_files()):
                return Source(files=copied, where="clipboard")
        else:
            text = clipboard.read_text() or ""
            match = _URL.search(text)
            if match:
                return Source(url=match.group(0), where="clipboard")
            pasted = [p for p in clipboard.paths_in_text(text) if p.suffix.lower() in MEDIA_SUFFIXES]
            if pasted:
                return Source(files=pasted, where="clipboard")

    remembered = _remembered()
    if remembered:
        return remembered

    raise CrecError(
        "Nothing to work on. Copy a link or a video file and run `crec`, "
        "or pass one: crec <url|file>"
    )


def _has_local_work(options: Options) -> bool:
    """Whether the options ask for anything a file on disk doesn't already have."""
    return any(
        (
            options.cut, options.needs_reencode, options.audio_only, options.no_audio,
            options.quality, options.format,
        )
    )


def _stage(source: Path, output_dir: Path) -> Path:
    """Give post-processing its own name for *source*, leaving the original alone.

    Post-processing swaps files into place by name, so it must never run on the
    user's file itself. A hard link is instant and costs no space; the original
    survives because every step writes a new file rather than editing in place.
    Copy when linking is impossible (another drive, FAT32).
    """
    staged = output_dir / f"{source.stem}-crec{source.suffix}"
    staged.unlink(missing_ok=True)
    try:
        os.link(source, staged)
    except OSError:
        shutil.copy2(source, staged)
    return staged


# --------------------------------------------------------------------------- #
# post-processing
# --------------------------------------------------------------------------- #

def _temp_beside(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}.crec-tmp{suffix}")


def _plan_download_quality(url: str, options: Options) -> tuple[int | None, str | None]:
    """Cap the download resolution to what the size target can actually use.

    Without this, ``crec --discord`` on a long video pulls the full 4K stream and
    then squashes it to 144p - minutes of downloading for pixels that get thrown
    away. Returns ``(height_cap, message)``; both None means download normally.

    Also surfaces an impossible target *before* the download rather than after.
    """
    peeked = download.peek(url)
    if peeked is None or peeked.duration is None:
        return None, None  # live stream, or the site won't say - don't guess

    duration = peeked.duration
    if options.cut:
        start, end = media.parse_cut(options.cut)
        duration = (end - start) if end is not None else max(duration - start, 1.0)

    source_height = peeked.height or 1080
    probe = media.Probe(
        duration=duration,
        width=int(source_height * 16 / 9),
        height=source_height,
        fps=30.0,
        has_audio=not options.no_audio,
        has_video=True,
        size_bytes=0,
    )

    # Let ImpossibleTarget propagate: far better to hear it now than after a
    # several-hundred-megabyte download.
    plan = media.plan_encode(
        probe,
        options.target_mb,
        keep_audio=not options.no_audio,
        audio_kbps=options.audio_quality,
    )

    planned = plan.height or source_height
    cap = media.rung_above(planned)
    if cap >= source_height:
        return None, None

    saved = f"{source_height}p" if peeked.height else "the largest stream"
    return cap, (
        f"targeting {options.target_mb:g} MB means ~{planned}p, "
        f"so downloading {cap}p instead of {saved}"
    )


def _trim_verdict(
    path: Path, spec: str, source_duration: float | None
) -> tuple[bool, str | None]:
    """Did the site honour our requested range?

    Rather than maintaining a list of which extractors support
    ``--download-sections``, measure the result. Returns
    ``(needs_local_trim, warning)``.
    """
    start, end = media.parse_cut(spec)

    if end is not None:
        expected = end - start
    elif source_duration is not None:
        # Open-ended cut: a honoured range leaves source_duration - start.
        expected = max(source_duration - start, 0.0)
    else:
        return False, (
            f"could not confirm the {spec} range was applied - "
            f"the site did not report a duration"
        )

    try:
        actual = media.probe(path).duration
    except CrecError:
        return False, None

    # 10% slack (min 2s) absorbs keyframe-aligned cuts overshooting slightly,
    # while still catching a range that was ignored outright.
    if actual <= expected + max(2.0, expected * 0.10):
        return False, None
    return True, None


def post_process(
    path: Path,
    options: Options,
    say,
    *,
    source_duration: float | None = None,
    local: bool = False,
) -> Path:
    """Apply cut/size/format work to a file. Returns the final path.

    ``local`` means the file did not come from yt-dlp, so nothing upstream
    trimmed it, extracted its audio or picked its container - do that here.
    """
    current = path

    def note(text: str) -> None:
        say(None)
        print(f"  {text}", file=sys.stderr)

    if options.cut:
        if local:
            needs_trim, warning = True, None
        else:
            needs_trim, warning = _trim_verdict(current, options.cut, source_duration)
        if warning:
            note(f"note: {warning}")
        if needs_trim:
            say("trimming" if local else "trimming (the site ignored our range request)")
            temporary = _temp_beside(current, current.suffix)
            media.trim(current, temporary, options.cut, exact=options.exact_cut)
            current = media.replace(temporary, current)

    if local:
        current = _local_conversions(current, options, say)

    if (options.format or "") == "gif":
        say("converting to gif")
        temporary = _temp_beside(current, ".gif")
        fps, width = options.fps or 15, options.scale or 480
        if options.target_mb is not None:
            media.fit_gif(
                current, temporary, options.target_mb, fps=fps, width=width, on_note=note
            )
        else:
            media.to_gif(current, temporary, fps=fps, width=width)
        gif = current.with_suffix(".gif")
        gif.unlink(missing_ok=True)
        temporary.rename(gif)
        current.unlink(missing_ok=True)
        return gif

    if options.target_mb is not None:
        info = media.probe(current)
        if info.size_mb <= options.target_mb:
            say(None)
            print(
                f"  already {info.size_mb:.1f} MB - under your {options.target_mb:g} MB "
                f"target, left untouched",
                file=sys.stderr,
            )
        elif not info.has_video:
            say("compressing audio")
            temporary = _temp_beside(current, media.lossless_to_lossy(current))
            media.fit_audio(current, temporary, info, options.target_mb)
            current = media.replace(temporary, current)
        else:
            def pass_label(pass_number: int, attempt: int) -> None:
                suffix = "" if attempt == 1 else f", retry {attempt - 1}"
                say(
                    f"compressing to fit {options.target_mb:g} MB "
                    f"(pass {pass_number}/2{suffix})"
                )

            temporary = _temp_beside(current, ".mp4")
            media.fit_to_size(
                current, temporary, info, options.target_mb,
                keep_audio=not options.no_audio,
                audio_kbps=options.audio_quality,
                width=options.scale,
                fps=options.fps,
                extra_args=options.ffmpeg_args,
                on_pass=pass_label,
                on_note=note,
            )
            current = media.replace(temporary, current)

    elif options.needs_reencode or (local and options.no_audio and media.probe(current).has_audio):
        say("re-encoding" if options.needs_reencode else "removing the audio")
        suffix = f".{options.format}" if options.format else current.suffix
        temporary = _temp_beside(current, suffix)
        media.convert(
            current, temporary,
            crf=options.crf,
            width=options.scale,
            height=options.quality if local else None,
            fps=options.fps,
            drop_audio=options.no_audio,
            audio_kbps=options.audio_quality,
            extra_args=options.ffmpeg_args,
        )
        current = media.replace(temporary, current)

    return current


_AUDIO_FORMATS = {"mp3", "m4a", "wav", "opus", "flac"}


def _local_conversions(current: Path, options: Options, say) -> Path:
    """What yt-dlp's format selection does for downloads, done for a local file."""
    info = media.probe(current)

    if options.audio_only and info.has_video:
        say("extracting the audio")
        extension = options.format if options.format in _AUDIO_FORMATS else "mp3"
        temporary = _temp_beside(current, f".{extension}")
        media.convert(current, temporary, drop_video=True, audio_kbps=options.audio_quality)
        return media.replace(temporary, current)

    wanted = f".{options.format}" if options.format and options.format != "gif" else None
    if wanted and wanted != current.suffix.lower() and not options.needs_reencode:
        say(f"converting to {options.format}")
        temporary = _temp_beside(current, wanted)
        try:
            media.convert(current, temporary)  # remux: instant when the codecs fit
        except CrecError:
            media.convert(current, temporary, crf=options.crf)  # they didn't; re-encode
        current = media.replace(temporary, current)

    # -q means "download at most this tall". For a file we already have, honour
    # it by scaling down - unless a later re-encode or size target will anyway.
    if options.quality and info.height > options.quality and not options.needs_reencode:
        say(f"scaling to {options.quality}p")
        temporary = _temp_beside(current, current.suffix)
        media.convert(
            current, temporary, height=options.quality, drop_audio=options.no_audio
        )
        current = media.replace(temporary, current)

    return current


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #

def cmd_update() -> int:
    changed, message = deps.update_ytdlp()
    print(message)
    return 0 if changed or "already current" in message else 1


def cmd_presets(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="crec presets")
    parser.add_argument("--edit", action="store_true", help="open your presets file")
    parser.add_argument("--keys", action="store_true", help="list the available keys")
    args = parser.parse_args(argv)

    if args.keys:
        print("Keys you can use in a preset:\n")
        for primitive in PRIMITIVES:
            print(f"  {primitive.name:<14} {primitive.type.__name__:<5} {primitive.help}")
        return 0

    target = paths.user_presets_file()
    if args.edit:
        if presets.write_starter_file(target):
            print(f"created {target}")
        _reveal(target, select=False)
        print(f"opening {target}")
        return 0

    preset_set = presets.load()
    for warning in preset_set.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if preset_set.defaults:
        print("[defaults] applied to every run:")
        for key, value in preset_set.defaults.items():
            print(f"  {key} = {value!r}")
        print()

    print("Presets (use as --name):\n")
    for name in preset_set.names():
        preset = preset_set.presets[name]
        marker = "*" if preset.source == "user" else " "
        settings = " ".join(f"{k}={v}" for k, v in preset.values.items())
        print(f" {marker}--{name:<16} {preset.description}")
        if settings:
            print(f"    {' ' * 16} {settings}")
    print(f"\n * = yours. Edit them with: crec presets --edit\n   ({target})")
    return 0


def _reveal(path: Path, *, select: bool = True) -> None:
    """Show *path* in the file manager, or open it if ``select`` is false."""
    try:
        if sys.platform == "win32":
            if select:
                # explorer returns non-zero even on success; check=False is right.
                subprocess.run(["explorer", f"/select,{path}"], check=False)
            else:
                os.startfile(str(path))  # noqa: S606 - intended shell association
        elif sys.platform == "darwin":
            subprocess.run(["open"] + (["-R"] if select else []) + [str(path)], check=False)
        elif select:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def run(argv: list[str]) -> int:
    if argv and argv[0] in SUBCOMMANDS:
        command, rest = argv[0], argv[1:]
        if command == "update":
            return cmd_update()
        if command == "presets":
            return cmd_presets(rest)
        if command == "doctor":
            return deps.doctor(fix="--fix" in rest)

    preset_set = presets.load()
    parser = build_parser(preset_set)
    args = parser.parse_args(argv)

    if args.version:
        print(f"crec {__version__}  (yt-dlp {deps.ytdlp_version()})")
        return 0

    for warning in preset_set.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    options, used_presets = resolve_options(args, preset_set)
    source = find_source(args.inputs, prefer_last=args.last)
    local = source.url is None

    if local and not _has_local_work(options):
        noun = "that file" if len(source.files) == 1 else "those files"
        raise CrecError(
            f"Nothing to do with {noun} - say what you want, e.g. "
            f"crec --discord, crec -c 0:10-0:20, crec --gif, crec -a"
        )

    output_dir = paths.ensure(
        Path(options.output_dir).expanduser() if options.output_dir else paths.downloads_dir()
    )

    update_check = _UpdateCheck() if not local else None
    progress = Progress()
    label = f" ({', '.join('--' + p for p in used_presets)})" if used_presets else ""
    if local:
        shown = source.files[0].name if len(source.files) == 1 else f"{len(source.files)} files"
        print(f"crec {shown}{label}", file=sys.stderr)
    else:
        print(f"crec {source.url}{label}", file=sys.stderr)
    if source.where == "last run":
        what = "file" if local else "URL"
        print(f"  (reusing the last {what} - nothing new on the clipboard)", file=sys.stderr)

    # Video-only maths: for audio it would refuse targets that audio easily fits.
    if (
        not local
        and options.target_mb is not None
        and options.quality is None
        and not options.audio_only
        and options.format != "gif"
    ):
        progress.stage("checking what we need")
        cap, message = _plan_download_quality(source.url, options)
        progress.done()
        if cap is not None:
            options.quality = cap
            print(f"  {message}", file=sys.stderr)

    def say(stage: str | None) -> None:
        progress.done() if stage is None else progress.stage(stage)

    try:
        if local:
            items = [download.Downloaded(path=_stage(f, output_dir)) for f in source.files]
        else:
            items = fetch(source.url, options, output_dir, progress)
        if len(items) > 1 and options.needs_reencode:
            progress.done()
            print(
                f"  {len(items)} files to re-encode - this will take a while",
                file=sys.stderr,
            )
        finals = [
            post_process(
                item.path, options, say, source_duration=item.source_duration, local=local
            )
            for item in items
        ]
    finally:
        progress.done()

    state.remember(source.url, finals, inputs=source.files)

    for path in finals:
        size = path.stat().st_size / 1_048_576
        print(f"  {path}  ({size:.1f} MB)")
    # Paths go to stdout so they can be piped; status goes to stderr. Flush here
    # or block-buffered stdout lands after the stderr lines when redirected.
    sys.stdout.flush()

    if options.copy:
        try:
            clipboard.copy_files(finals)
            noun = "file" if len(finals) == 1 else f"{len(finals)} files"
            print(f"copied to clipboard - paste anywhere ({noun})", file=sys.stderr)
        except clipboard.ClipboardUnsupported as exc:
            print(f"note: {exc}", file=sys.stderr)
        except OSError as exc:
            print(f"warning: could not copy to clipboard: {exc}", file=sys.stderr)

    if options.open_folder and finals:
        _reveal(finals[0])

    if update_check:
        update_check.report()
    return 0


class _UpdateCheck:
    """Asks PyPI for the latest yt-dlp while the download runs.

    It used to run after the download, which added up to a 10s network
    timeout to the end of a run once a day. Now it overlaps the download, and
    if it still hasn't answered by the end we skip the notice rather than wait.
    """

    def __init__(self) -> None:
        import threading

        self.latest: str | None = None
        self._thread: threading.Thread | None = None
        if deps.should_check_update():
            deps.record_update_check()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        self.latest = deps.latest_ytdlp_version()

    def report(self) -> None:
        if self._thread is None:
            return
        self._thread.join(timeout=0.5)
        if not self.latest:
            return
        current = deps.ytdlp_version()
        if deps._normalise(current) != deps._normalise(self.latest):
            print(
                f"\nyt-dlp {self.latest} is out (you have {current}). Run: crec update",
                file=sys.stderr,
            )


def main() -> int:
    deps.activate_ytdlp()
    try:
        return run(sys.argv[1:])
    except CrecError as exc:
        print(f"crec: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
