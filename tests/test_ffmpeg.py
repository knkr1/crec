"""The encode paths, run through a real ffmpeg.

test_core.py fakes ffmpeg on purpose, which is exactly why it could not catch
the bugs that mattered most: crec handing ffmpeg arguments it rejects (AAC in an
.mp3, h264 in a webm, cover art treated as video). These generate small clips
and check what actually comes out.

Skipped when ffmpeg is not installed. CI runs them in a job that installs it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crec import cli, deps, media  # noqa: E402
from crec.options import Options  # noqa: E402

FFMPEG = deps.find_ffmpeg("ffmpeg")
pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(
        FFMPEG is None or deps.find_ffmpeg("ffprobe") is None, reason="needs ffmpeg"
    ),
]


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True, capture_output=True,
    )


def _codecs(path: Path) -> list[str]:
    result = subprocess.run(
        [str(deps.find_ffmpeg("ffprobe")), "-v", "error", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _say(_stage) -> None:
    pass


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("media")
    # 12 s of busy 720p video at a high bitrate, so size targets have work to do.
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
        "-c:v", "libx264", "-b:v", "6M", "-c:a", "aac", "-shortest",
        str(folder / "clip.mp4"),
    )
    # A 3-minute 320k mp3 with embedded cover art (~7 MB).
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=300x300:rate=1", "-frames:v", "1",
            str(folder / "cover.png"))
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=330:duration=180", "-i", str(folder / "cover.png"),
        "-map", "0", "-map", "1", "-c:a", "libmp3lame", "-b:a", "320k",
        "-c:v", "png", "-disposition:v", "attached_pic", str(folder / "song.mp3"),
    )
    return folder


@pytest.fixture
def work(media_dir, tmp_path):
    """Copy a fixture into this test's own folder, since post-processing edits in place."""
    def take(name: str) -> Path:
        target = tmp_path / name
        shutil.copy(media_dir / name, target)
        return target
    return take


def test_video_lands_under_its_target(work) -> None:
    source = work("clip.mp4")
    out = cli.post_process(source, Options(target_mb=2), _say)
    assert out.stat().st_size <= 2 * 1_048_576
    assert _codecs(out) == ["h264", "aac"]


def test_audio_with_cover_art_shrinks_as_audio(work) -> None:
    source = work("song.mp3")
    assert not media.probe(source).has_video, "cover art must not count as video"

    out = cli.post_process(source, Options(audio_only=True, target_mb=2), _say)
    assert out.suffix == ".mp3"
    assert out.stat().st_size <= 2 * 1_048_576
    assert _codecs(out)[0] == "mp3"


def test_gif_lands_under_its_target(work) -> None:
    source = work("clip.mp4")
    out = cli.post_process(source, Options(format="gif", fps=15, scale=480, target_mb=1), _say)
    assert out.suffix == ".gif"
    assert out.stat().st_size <= 1 * 1_048_576


def test_webm_reencode_uses_webm_codecs(work, tmp_path) -> None:
    source = work("clip.mp4")
    out = tmp_path / "out.webm"
    media.convert(source, out, crf=40)
    assert _codecs(out) == ["vp9", "opus"]


def test_mkv_reencode_keeps_the_audio_as_is(work, tmp_path) -> None:
    source = work("clip.mp4")
    out = tmp_path / "out.mkv"
    media.convert(source, out, height=360)
    assert _codecs(out) == ["h264", "aac"]


@pytest.mark.parametrize("exact", [False, True])
def test_trim_produces_the_requested_length(work, tmp_path, exact: bool) -> None:
    source = work("clip.mp4")
    out = tmp_path / "trimmed.mp4"
    media.trim(source, out, "0:02-0:06", exact=exact)
    # Stream copy snaps to keyframes, so allow a little slack there.
    assert abs(media.probe(out).duration - 4.0) < (0.2 if exact else 2.1)


def test_local_file_audio_extraction(work) -> None:
    source = work("clip.mp4")
    out = cli.post_process(source, Options(audio_only=True), _say, local=True)
    assert out.suffix == ".mp3" and _codecs(out) == ["mp3"]


def test_local_file_container_change(work) -> None:
    source = work("clip.mp4")
    out = cli.post_process(source, Options(format="mkv"), _say, local=True)
    assert out.suffix == ".mkv" and _codecs(out) == ["h264", "aac"]


def test_local_file_mute(work) -> None:
    source = work("clip.mp4")
    out = cli.post_process(source, Options(no_audio=True), _say, local=True)
    assert _codecs(out) == ["h264"]
