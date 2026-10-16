"""Tests for the logic that decides things, which is where the bugs live.

Deliberately no network and no ffmpeg: these cover parsing, precedence and the
size-targeting maths. The encode paths are verified end-to-end by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crec import media, presets  # noqa: E402
from crec.cli import build_parser, resolve_options  # noqa: E402
from crec.errors import CrecError, ImpossibleTarget  # noqa: E402
from crec.options import Options, validate_preset_values  # noqa: E402


# --------------------------------------------------------------------------- #
# timestamps and cut specs
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("text", "expected"),
    [("90", 90.0), ("1:30", 90.0), ("0:10", 10.0), ("1:02:03", 3723.0), ("1:00:00.5", 3600.5)],
)
def test_parse_timestamp(text: str, expected: float) -> None:
    assert media.parse_timestamp(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "-5", "1:aa"])
def test_parse_timestamp_rejects_junk(text: str) -> None:
    with pytest.raises(ValueError):
        media.parse_timestamp(text)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("0:10-0:40", (10.0, 40.0)),
        ("90-150", (90.0, 150.0)),
        ("0:10-", (10.0, None)),
        ("-0:40", (0.0, 40.0)),
        ("1:00..1:05", (60.0, 65.0)),
    ],
)
def test_parse_cut(spec: str, expected: tuple[float, float | None]) -> None:
    assert media.parse_cut(spec) == expected


def test_parse_cut_rejects_backwards_range() -> None:
    with pytest.raises(CrecError, match="must be after"):
        media.parse_cut("0:40-0:10")


def test_parse_cut_rejects_missing_separator() -> None:
    with pytest.raises(CrecError, match="start-end"):
        media.parse_cut("0:40")


def test_ytdlp_sections_uses_inf_for_open_end() -> None:
    assert media.ytdlp_sections("0:10-0:40") == "*10-40"
    assert media.ytdlp_sections("0:10-") == "*10-inf"


# --------------------------------------------------------------------------- #
# size targeting
# --------------------------------------------------------------------------- #

def _probe(
    duration: float, height: int = 1080, has_audio: bool = True, fps: float = 30.0
) -> media.Probe:
    return media.Probe(
        duration=duration,
        width=int(height * 16 / 9),
        height=height,
        fps=fps,
        has_audio=has_audio,
        has_video=True,
        size_bytes=999_000_000,
        audio_sample_rate=48_000,
    )


@pytest.mark.parametrize(
    ("duration", "target_mb", "fps"),
    [
        (10, 2, 30.0),      # verified end-to-end at 1.9965 MB - must not regress
        (15, 10, 30.0),     # verified end-to-end at 9.8 MB - must not regress
        (60, 10, 30.0),
        (634.6, 60, 30.0),
        (634.6, 60, 60.0),
        (300, 25, 60.0),
        (120, 8, 24.0),
    ],
)
def test_predicted_size_stays_under_target(duration, target_mb, fps) -> None:
    """The whole promise. Includes the 10-minute/10 MB case that shipped at
    11.3 MB, and the two short cases that already worked - a change to the
    overhead model must not regress those."""
    info = _probe(duration=duration, fps=fps)
    plan = media.plan_encode(info, target_mb=target_mb, keep_audio=True, fps=fps)

    # What the encoders will really emit, plus what the container costs.
    audio_real = plan.audio_kbps * 1.06
    streams = (plan.video_kbps + audio_real) * 1000 * duration / 8
    predicted = streams + media.container_overhead_bytes(info, fps=fps)
    assert predicted <= target_mb * 1_048_576, (
        f"{predicted / 1_048_576:.2f} MB predicted for a {target_mb} MB target"
    )


def test_overhead_scales_with_frame_count() -> None:
    """A flat percentage was the bug: 60 fps costs twice the video sample-table
    entries of 30 fps. (Not 2x overall - audio packets and moov don't change.)"""
    info = _probe(duration=600)
    at30 = media.container_overhead_bytes(info, fps=30.0)
    at60 = media.container_overhead_bytes(info, fps=60.0)
    assert at60 > at30 * 1.3


def test_ten_minutes_under_ten_mb_is_refused_not_faked() -> None:
    """The case that shipped an 11.3 MB file for a 10 MB target.

    It is genuinely impossible at watchable quality, so the honest outcome is a
    fast refusal that says what *would* work - not a slow encode that overshoots.
    """
    with pytest.raises(ImpossibleTarget) as caught:
        media.plan_encode(_probe(duration=634.6), target_mb=10, keep_audio=True, fps=30.0)

    message = str(caught.value)
    assert "cannot hold" in message
    assert "-c" in message, "must point at trimming as the way out"


def test_overhead_model_matches_the_measured_file() -> None:
    """Measured on a real 634.6s / 30 fps / 48 kHz encode: 673,276 bytes."""
    info = _probe(duration=634.624)
    estimate = media.container_overhead_bytes(info, fps=30.0)
    assert 673_276 <= estimate <= 673_276 * 1.35, f"got {estimate}"


def test_plan_encode_drops_resolution_when_bitrate_is_tight() -> None:
    # 8 MB over 3 minutes leaves ~233 kbps for video - watchable at 240p, mush at 1080p.
    plan = media.plan_encode(_probe(duration=180), target_mb=8, keep_audio=True)
    assert plan.height is not None and plan.height < 1080
    assert plan.note


def test_plan_encode_keeps_resolution_when_budget_is_generous() -> None:
    plan = media.plan_encode(_probe(duration=10), target_mb=50, keep_audio=True)
    assert plan.height is None
    assert plan.note is None


def test_plan_encode_refuses_impossible_target() -> None:
    """Rather than silently producing a slideshow."""
    with pytest.raises(ImpossibleTarget, match="cannot hold"):
        media.plan_encode(_probe(duration=7200), target_mb=1, keep_audio=True)


def test_plan_encode_sacrifices_audio_before_giving_up() -> None:
    """Steps 128 -> 96 -> 64 -> 48, taking the first that leaves usable video."""
    plan = media.plan_encode(_probe(duration=361), target_mb=8, keep_audio=True)
    assert plan.audio_kbps < 128
    assert plan.video_kbps >= 80


def test_low_audio_budgets_go_mono() -> None:
    """Measured: AAC at 48k stereo overshoots by 30% because it cannot comply;
    the same rate in mono lands within 4%."""
    plan = media.plan_encode(_probe(duration=361), target_mb=8, keep_audio=True)
    assert plan.audio_kbps <= 64
    assert plan.audio_mono is True


def test_generous_budgets_stay_stereo() -> None:
    plan = media.plan_encode(_probe(duration=30), target_mb=50, keep_audio=True)
    assert plan.audio_mono is False
    assert plan.audio_kbps == 128


def test_plan_encode_ignores_audio_when_muted() -> None:
    plan = media.plan_encode(_probe(duration=60), target_mb=10, keep_audio=False)
    assert plan.audio_kbps == 0


def test_plan_encode_needs_a_duration() -> None:
    with pytest.raises(CrecError, match="duration"):
        media.plan_encode(_probe(duration=0), target_mb=10, keep_audio=True)


# --------------------------------------------------------------------------- #
# preset validation
# --------------------------------------------------------------------------- #

def test_validate_accepts_known_keys() -> None:
    assert validate_preset_values("x", {"target_mb": 10, "quality": 720}) == []


def test_validate_accepts_int_for_float_primitive() -> None:
    assert validate_preset_values("x", {"target_mb": 10}) == []


def test_validate_flags_unknown_key_with_suggestion() -> None:
    problems = validate_preset_values("x", {"target_md": 10})
    assert len(problems) == 1
    assert "target_mb" in problems[0]


def test_validate_flags_wrong_type() -> None:
    problems = validate_preset_values("x", {"quality": "720"})
    assert problems and "should be int" in problems[0]


def test_validate_rejects_bool_for_numeric_primitive() -> None:
    problems = validate_preset_values("x", {"quality": True})
    assert problems


def test_builtin_presets_are_all_valid() -> None:
    """A shipped preset with a typo would be embarrassing."""
    loaded = presets.load()
    assert loaded.warnings == []
    assert "discord" in loaded.presets


# --------------------------------------------------------------------------- #
# precedence: defaults < presets < explicit flags
# --------------------------------------------------------------------------- #

def _resolve(argv: list[str]) -> tuple[Options, list[str]]:
    preset_set = presets.load()
    parser = build_parser(preset_set)
    return resolve_options(parser.parse_args(argv), preset_set)


def test_preset_applies() -> None:
    options, used = _resolve(["--discord"])
    assert options.target_mb == 10
    assert used == ["discord"]


def test_explicit_flag_beats_preset() -> None:
    options, _ = _resolve(["--discord", "-s", "8"])
    assert options.target_mb == 8


def test_presets_compose() -> None:
    options, used = _resolve(["--small", "--discord"])
    assert (options.quality, options.target_mb) == (720, 10)
    assert set(used) == {"small", "discord"}


def test_copy_is_on_by_default_and_off_with_flag() -> None:
    assert _resolve([])[0].copy is True
    assert _resolve(["-n"])[0].copy is False


def test_gif_preset_sets_the_whole_bundle() -> None:
    options, _ = _resolve(["--gif"])
    assert (options.format, options.fps, options.scale, options.no_audio) == ("gif", 15, 480, True)


def test_user_preset_cannot_shadow_a_builtin_flag(monkeypatch, tmp_path) -> None:
    """A bad user preset must warn, not crash the tool."""
    user_file = tmp_path / "presets.toml"
    user_file.write_text('[mute]\ndescription = "clash"\nno_audio = true\n', encoding="utf-8")
    monkeypatch.setattr(presets.paths, "user_presets_file", lambda: user_file)

    preset_set = presets.load()
    build_parser(preset_set)  # must not raise
    assert any("already a built-in flag" in w for w in preset_set.warnings)
    assert "mute" not in preset_set.presets


def test_user_preset_overrides_a_builtin_preset(monkeypatch, tmp_path) -> None:
    """The reason presets are data: platform limits change without a release."""
    user_file = tmp_path / "presets.toml"
    user_file.write_text('[discord]\ntarget_mb = 25\n', encoding="utf-8")
    monkeypatch.setattr(presets.paths, "user_presets_file", lambda: user_file)

    preset_set = presets.load()
    assert preset_set.presets["discord"].values["target_mb"] == 25
    assert preset_set.presets["discord"].source == "user"


def test_broken_user_preset_is_dropped_not_fatal(monkeypatch, tmp_path) -> None:
    user_file = tmp_path / "presets.toml"
    user_file.write_text('[oops]\nnonsense_key = 1\n', encoding="utf-8")
    monkeypatch.setattr(presets.paths, "user_presets_file", lambda: user_file)

    preset_set = presets.load()
    assert "oops" not in preset_set.presets
    assert preset_set.warnings


def test_defaults_table_applies_to_every_run(monkeypatch, tmp_path) -> None:
    user_file = tmp_path / "presets.toml"
    user_file.write_text('[defaults]\nquality = 480\n', encoding="utf-8")
    monkeypatch.setattr(presets.paths, "user_presets_file", lambda: user_file)

    preset_set = presets.load()
    parser = build_parser(preset_set)
    options, _ = resolve_options(parser.parse_args([]), preset_set)
    assert options.quality == 480

    options, _ = resolve_options(parser.parse_args(["-q", "1080"]), preset_set)
    assert options.quality == 1080, "explicit flag must still win over defaults"


# --------------------------------------------------------------------------- #
# derived behaviour
# --------------------------------------------------------------------------- #

def test_needs_reencode_is_false_for_a_plain_download() -> None:
    assert Options().needs_reencode is False


@pytest.mark.parametrize(
    "kwargs",
    [{"target_mb": 8}, {"crf": 20}, {"fps": 30}, {"scale": 640}, {"format": "gif"}],
)
def test_needs_reencode_detects_work(kwargs: dict) -> None:
    assert Options(**kwargs).needs_reencode is True


def test_exact_cut_alone_does_not_force_a_reencode() -> None:
    """The trim does the exact cut; re-encoding afterwards was a wasted remux."""
    assert Options(cut="0:10-0:20", exact_cut=True).needs_reencode is False


def test_audio_format_implies_audio_only() -> None:
    """`--format mp3` used to download the video and leave it as mp4."""
    options, _ = _resolve(["--format", "mp3"])
    assert options.audio_only is True
    assert _resolve(["--format", "mp4"])[0].audio_only is False


def test_plain_cut_alone_does_not_force_a_reencode() -> None:
    """A keyframe-aligned trim is a stream copy; re-encoding it would be waste."""
    assert Options(cut="0:10-0:20").needs_reencode is False


def test_version_normalisation_treats_padded_versions_as_equal() -> None:
    """2026.07.04 and 2026.7.4 are the same release."""
    from crec.deps import _normalise

    assert _normalise("2026.07.04") == _normalise("2026.7.4")


# --------------------------------------------------------------------------- #
# ffmpeg argument construction (no ffmpeg required)
# --------------------------------------------------------------------------- #

@pytest.fixture
def captured_args(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(media.deps, "ffmpeg_path", lambda **_: Path("ffmpeg"))
    monkeypatch.setattr(media, "_run", lambda args, *, what: calls.append(args))
    return calls


def test_fast_trim_uses_duration_not_absolute_end(captured_args) -> None:
    """Regression: before -i, -to is absolute on the source timeline while -t
    counts from the seek point. Using -to with (end - start) silently produced a
    clip half the requested length."""
    media.trim(Path("in.mp4"), Path("out.mp4"), "0:05-0:15")
    args = captured_args[0]

    assert "-to" not in args, "-to before -i is absolute; it must not be used here"
    assert args[args.index("-t") + 1] == "10", "duration must be end - start"
    assert args.index("-ss") < args.index("-i"), "-ss must precede -i for a fast seek"
    assert args.index("-t") < args.index("-i")
    assert "copy" in args, "the fast path must stream-copy"


def test_fast_trim_with_open_end_sets_no_duration(captured_args) -> None:
    media.trim(Path("in.mp4"), Path("out.mp4"), "0:20-")
    args = captured_args[0]
    assert "-t" not in args and "-to" not in args
    assert args[args.index("-ss") + 1] == "20"


def test_exact_trim_of_audio_does_not_reach_for_libx264(captured_args, monkeypatch) -> None:
    """libx264 on a file with no video stream just fails."""
    monkeypatch.setattr(
        media, "probe",
        lambda _p: media.Probe(30.0, 0, 0, 0.0, has_audio=True, has_video=False, size_bytes=1),
    )
    media.trim(Path("in.mp3"), Path("out.mp3"), "0:05-0:15", exact=True)
    args = captured_args[0]

    assert "libx264" not in args
    assert "libmp3lame" in args
    # After -i these are output options, where -to IS the absolute end. Correct here.
    assert args.index("-i") < args.index("-ss")
    assert args[args.index("-to") + 1] == "15"


# --------------------------------------------------------------------------- #
# detecting a range the site ignored
# --------------------------------------------------------------------------- #

def _verdict(spec: str, actual: float, source: float | None):
    from crec import cli

    original = media.probe

    def fake(_path):
        return media.Probe(actual, 1920, 1080, 30.0, True, True, 1)

    media.probe = fake
    try:
        return cli._trim_verdict(Path("x.mp4"), spec, source)
    finally:
        media.probe = original


def test_verdict_accepts_an_honoured_closed_range() -> None:
    needs, warning = _verdict("0:30-0:40", actual=10.0, source=635.0)
    assert needs is False and warning is None


def test_verdict_tolerates_keyframe_overshoot() -> None:
    needs, _ = _verdict("0:30-0:40", actual=11.5, source=635.0)
    assert needs is False


def test_verdict_catches_an_ignored_closed_range() -> None:
    needs, _ = _verdict("0:30-0:40", actual=635.0, source=635.0)
    assert needs is True


def test_verdict_catches_an_ignored_open_range() -> None:
    """The case that used to slip through: an open-ended cut on a site that
    ignores download-sections handed back the whole video, silently."""
    needs, _ = _verdict("1:30-", actual=600.0, source=600.0)
    assert needs is True


def test_verdict_accepts_an_honoured_open_range() -> None:
    needs, warning = _verdict("1:30-", actual=510.0, source=600.0)
    assert needs is False and warning is None


def test_verdict_warns_when_it_cannot_tell() -> None:
    needs, warning = _verdict("1:30-", actual=600.0, source=None)
    assert needs is False
    assert warning and "could not confirm" in warning


# --------------------------------------------------------------------------- #
# codecs follow the container
# --------------------------------------------------------------------------- #

def test_audio_reencode_into_mp3_uses_an_mp3_encoder(captured_args) -> None:
    """Regression: `crec -a -s 5` asked for AAC inside an .mp3, which ffmpeg refuses."""
    media.convert(Path("in.mp3"), Path("out.mp3"), audio_kbps=96)
    args = captured_args[0]
    assert args[args.index("-c:a") + 1] == "libmp3lame"


def test_webm_reencode_does_not_use_h264(captured_args) -> None:
    media.convert(Path("in.webm"), Path("out.webm"), crf=30)
    args = captured_args[0]
    assert "libx264" not in args
    assert args[args.index("-c:v") + 1] == "libvpx-vp9"


def test_lossless_audio_is_compressed_to_mp3() -> None:
    assert media.lossless_to_lossy(Path("a.wav")) == ".mp3"
    assert media.lossless_to_lossy(Path("a.m4a")) == ".m4a"


def test_probe_ignores_cover_art(monkeypatch, tmp_path) -> None:
    """An mp3's embedded thumbnail is a 'video' stream to ffprobe. Treating it
    as video sent `--music -s 10` through the x264 encoder."""
    import json
    import subprocess

    payload = {
        "format": {"duration": "200.0"},
        "streams": [
            {"codec_type": "audio", "sample_rate": "44100"},
            {"codec_type": "video", "width": 600, "height": 600,
             "disposition": {"attached_pic": 1}},
        ],
    }
    monkeypatch.setattr(media.deps, "ffprobe_path", lambda **_: Path("ffprobe"))
    monkeypatch.setattr(
        media.deps, "run_quiet",
        lambda _args: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    info = media.probe(tmp_path / "song.mp3")
    assert info.has_audio and not info.has_video


# --------------------------------------------------------------------------- #
# fitting audio and gifs to a size
# --------------------------------------------------------------------------- #

def test_fit_audio_picks_a_bitrate_that_fits(monkeypatch, tmp_path) -> None:
    requested: list[int] = []

    def fake_convert(_src, dst, *, audio_kbps):
        requested.append(audio_kbps)
        dst.write_bytes(b"x" * int(audio_kbps * 1000 / 8 * 600))  # CBR, 600 s

    monkeypatch.setattr(media, "convert", fake_convert)
    info = media.Probe(600.0, 0, 0, 0.0, True, False, 50 * 1_048_576)
    media.fit_audio(Path("in.mp3"), tmp_path / "out.mp3", info, 5)

    assert len(requested) == 1
    assert (tmp_path / "out.mp3").stat().st_size <= 5 * 1_048_576


def test_fit_audio_retries_when_extra_streams_overshoot(monkeypatch, tmp_path) -> None:
    """Cover art rides along uncounted; the second attempt must correct for it."""
    calls: list[int] = []

    def fake_convert(_src, dst, *, audio_kbps):
        calls.append(audio_kbps)
        dst.write_bytes(b"x" * (int(audio_kbps * 1000 / 8 * 600) + 300_000))

    monkeypatch.setattr(media, "convert", fake_convert)
    info = media.Probe(600.0, 0, 0, 0.0, True, False, 50 * 1_048_576)
    media.fit_audio(Path("in.mp3"), tmp_path / "out.mp3", info, 5)

    assert len(calls) == 2 and calls[1] < calls[0]
    assert (tmp_path / "out.mp3").stat().st_size <= 5 * 1_048_576


def test_fit_audio_refuses_rather_than_making_noise() -> None:
    info = media.Probe(3 * 3600.0, 0, 0, 0.0, True, False, 1)
    with pytest.raises(ImpossibleTarget):
        media.fit_audio(Path("in.mp3"), Path("out.mp3"), info, 5)


def _fake_gif(monkeypatch, bytes_per_px2_fps: float):
    calls: list[tuple[int, int]] = []

    def fake(_src, dst, *, fps, width):
        calls.append((width, fps))
        dst.write_bytes(b"x" * int(width * width * fps * bytes_per_px2_fps))

    monkeypatch.setattr(media, "to_gif", fake)
    monkeypatch.setattr(
        media, "probe", lambda _p: media.Probe(5.0, 1920, 1080, 30.0, True, True, 1)
    )
    return calls


def test_fit_gif_shrinks_until_it_fits(monkeypatch, tmp_path) -> None:
    """Regression: --gif used to ignore -s entirely."""
    # Sizes scaled down 100x so the fakes stay quick: 480px@15 ~ 0.2 MB vs 0.1 MB.
    calls = _fake_gif(monkeypatch, bytes_per_px2_fps=0.06)
    out = tmp_path / "out.gif"
    media.fit_gif(Path("in.mp4"), out, 0.1, fps=15, width=480)

    assert len(calls) > 1
    assert out.stat().st_size <= 0.1 * 1_048_576


def test_fit_gif_leaves_a_small_gif_alone(monkeypatch, tmp_path) -> None:
    calls = _fake_gif(monkeypatch, bytes_per_px2_fps=0.01)
    media.fit_gif(Path("in.mp4"), tmp_path / "out.gif", 0.1, fps=15, width=480)
    assert calls == [(480, 15)]


def test_fit_gif_gives_up_honestly(monkeypatch, tmp_path) -> None:
    _fake_gif(monkeypatch, bytes_per_px2_fps=1.0)  # even 160px@8 is ~0.2 MB
    with pytest.raises(ImpossibleTarget):
        media.fit_gif(Path("in.mp4"), tmp_path / "out.gif", 0.01, fps=15, width=480)


# --------------------------------------------------------------------------- #
# clipboard
# --------------------------------------------------------------------------- #

def test_applescript_quoting_survives_quotes_in_titles() -> None:
    from crec.clipboard import _applescript_string

    assert _applescript_string('a "b" \\c') == '"a \\"b\\" \\\\c"'


# --------------------------------------------------------------------------- #
# local files: where the input comes from
# --------------------------------------------------------------------------- #

@pytest.fixture
def desk(monkeypatch, tmp_path):
    """A fake clipboard and a private state file."""
    from crec import cli, clipboard, state

    board = {"files": [], "text": ""}
    monkeypatch.setattr(clipboard, "read_files", lambda: list(board["files"]))
    monkeypatch.setattr(clipboard, "read_text", lambda: board["text"])
    monkeypatch.setattr(state.paths, "state_file", lambda: tmp_path / "state.json")
    monkeypatch.setattr(state.paths, "data_dir", lambda: tmp_path)
    return board, cli, state, tmp_path


def _video(folder: Path, name: str = "clip.mp4") -> Path:
    path = folder / name
    path.write_bytes(b"not really a video")
    return path


def test_copied_file_beats_last_url(desk) -> None:
    board, cli, state, tmp = desk
    state.remember("https://example.com/old")
    board["files"] = [_video(tmp)]

    source = cli.find_source([], prefer_last=False)
    assert source.url is None and source.files == [tmp / "clip.mp4"]


def test_copied_url_still_works(desk) -> None:
    board, cli, *_ = desk
    board["text"] = "look at this https://example.com/v/1 lol"
    assert cli.find_source([], prefer_last=False).url == "https://example.com/v/1"


def test_copy_as_path_text_is_a_file(desk) -> None:
    """Explorer's "Copy as path" puts the path in quotes, as text."""
    board, cli, _, tmp = desk
    clip = _video(tmp)
    board["text"] = f'"{clip}"'
    assert cli.find_source([], prefer_last=False).files == [clip]


def test_non_media_copied_files_are_ignored(desk) -> None:
    board, cli, _, tmp = desk
    notes = tmp / "notes.docx"
    notes.write_bytes(b"x")
    board["files"] = [notes]
    board["text"] = "https://example.com/v/2"
    assert cli.find_source([], prefer_last=False).url == "https://example.com/v/2"


def test_own_output_on_clipboard_means_the_same_url_again(desk) -> None:
    """`crec` then `crec -a` must re-fetch the source, not process crec's output."""
    board, cli, state, tmp = desk
    output = _video(tmp, "downloaded.mp4")
    state.remember("https://example.com/v/3", [output])
    board["files"] = [output]

    source = cli.find_source([], prefer_last=False)
    assert source.url == "https://example.com/v/3"


def test_own_output_on_clipboard_means_the_same_file_again(desk) -> None:
    """`crec clip.mp4 --discord` then `crec --gif` works from the original."""
    board, cli, state, tmp = desk
    original, output = _video(tmp), _video(tmp, "clip-crec.mp4")
    state.remember(None, [output], inputs=[original])
    board["files"] = [output]

    assert cli.find_source([], prefer_last=False).files == [original]


def test_last_flag_ignores_the_clipboard(desk) -> None:
    board, cli, state, tmp = desk
    state.remember("https://example.com/v/4")
    board["files"] = [_video(tmp)]
    assert cli.find_source([], prefer_last=True).url == "https://example.com/v/4"


def test_state_remembers_one_kind_of_source(desk) -> None:
    _, _, state, tmp = desk
    state.remember("https://example.com/v/5")
    state.remember(None, inputs=[_video(tmp)])
    assert state.last_url() is None and state.last_inputs() == [tmp / "clip.mp4"]


def test_folder_argument_takes_its_media_only(desk) -> None:
    _, cli, _, tmp = desk
    folder = tmp / "clips"
    folder.mkdir()
    for name in ("b.mp4", "a.mkv", "readme.txt"):
        (folder / name).write_bytes(b"x")
    (folder / "nested").mkdir()

    assert [p.name for p in cli.find_source([str(folder)], prefer_last=False).files] == [
        "a.mkv", "b.mp4",
    ]


def test_bad_argument_says_what_it_expected(desk) -> None:
    _, cli, *_ = desk
    with pytest.raises(CrecError, match="not a URL, a file, or a folder"):
        cli.find_source(["nope.mp4"], prefer_last=False)


def test_nothing_to_do_with_a_bare_local_file() -> None:
    from crec import cli

    assert not cli._has_local_work(Options())
    assert cli._has_local_work(Options(target_mb=10))
    assert cli._has_local_work(Options(cut="0:01-0:02"))
    assert cli._has_local_work(Options(audio_only=True))


def test_staging_never_endangers_the_original(tmp_path) -> None:
    """Post-processing replaces files by name; the user's file must survive it."""
    from crec import cli

    original = _video(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    staged = cli._stage(original, out)
    assert staged != original

    # What media.replace does after an encode: unlink the name, move a new file in.
    produced = out / "new.mp4"
    produced.write_bytes(b"encoded")
    final = media.replace(produced, staged)

    assert original.read_bytes() == b"not really a video"
    assert final.read_bytes() == b"encoded"


def test_paths_in_text_handles_multiple_quoted_lines(tmp_path) -> None:
    from crec import clipboard

    a, b = _video(tmp_path, "a.mp4"), _video(tmp_path, "b.mp4")
    text = f'"{a}"\r\n"{b}"\r\n"{tmp_path / "missing.mp4"}"'
    assert clipboard.paths_in_text(text) == [a, b]


# --------------------------------------------------------------------------- #
# cookies
# --------------------------------------------------------------------------- #

def test_cookies_from_a_browser_and_profile() -> None:
    from crec.download import cookie_opts

    assert cookie_opts("Firefox") == {"cookiesfrombrowser": ("firefox", None, None, None)}
    assert cookie_opts("chrome:Profile 1")["cookiesfrombrowser"][:2] == ("chrome", "Profile 1")
    assert cookie_opts(None) == {}


def test_cookies_from_a_file(tmp_path) -> None:
    from crec.download import cookie_opts

    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert cookie_opts(str(jar)) == {"cookiefile": str(jar)}


def test_missing_cookie_file_is_not_mistaken_for_a_browser() -> None:
    """Regression: "C:/x/cookies.txt" split on ':' gave browser "c"."""
    from crec.download import cookie_opts

    with pytest.raises(CrecError, match="no cookies file"):
        cookie_opts("C:/nowhere/cookies.txt")


def test_unknown_browser_lists_the_real_ones() -> None:
    from crec.download import cookie_opts

    with pytest.raises(CrecError, match="firefox"):
        cookie_opts("netscape")


def test_cookies_is_a_preset_key() -> None:
    assert validate_preset_values("defaults", {"cookies": "firefox"}) == []
    assert _resolve(["--cookies", "edge"])[0].cookies == "edge"


@pytest.mark.parametrize(
    "message",
    [
        # yt-dlp's own login errors end with its cookie hint
        "ERROR: [Instagram] abc: Requested content is not available, rate-limit reached "
        "or login required. Use --cookies, --cookies-from-browser, --username and --password",
        "ERROR: [youtube] xyz: Sign in to confirm you're not a bot. Use --cookies-from-browser",
        "ERROR: [youtube] xyz: Sign in to confirm your age. This video may be inappropriate",
        "ERROR: [TikTok] 123: This account is private",
    ],
)
def test_login_errors_are_recognised(message: str) -> None:
    from crec.download import _looks_stale, _needs_login

    assert _needs_login(message)
    # Updating yt-dlp never fixes a login wall, so it must not trigger that retry.
    assert not (_looks_stale(message) and not _needs_login(message))


def test_ordinary_breakage_is_not_a_login_problem() -> None:
    from crec.download import _needs_login

    assert not _needs_login("ERROR: [TikTok] 123: Unable to extract webpage video data")


def test_login_help_names_the_site_and_the_fix() -> None:
    from crec.download import _login_help

    first = _login_help("https://www.tiktok.com/@a/video/1", None)
    assert "tiktok.com" in first and "crec cookies add" in first and "--cookies firefox" in first

    again = _login_help("https://www.tiktok.com/@a/video/1", "firefox")
    assert "even with cookies" in again

    expired = _login_help("https://vm.tiktok.com/x", None, saved_site="tiktok.com")
    assert "saved tiktok.com login has probably expired" in expired


def test_cookie_read_help_only_blames_locking_when_it_is_locking(monkeypatch) -> None:
    from crec import download

    monkeypatch.setattr(download.sys, "platform", "win32")
    locked = download._cookie_read_help("chrome", "Could not copy Chrome cookie database")
    assert "Close chrome" in locked

    missing = download._cookie_read_help("chrome:Work", "could not find chrome cookies database")
    assert "Could not find a 'Work' profile in chrome" in missing


def test_cookie_failure_is_found_inside_downloaderror() -> None:
    """yt-dlp reports CookieLoadError, which raises DownloadError, then re-raises."""
    import yt_dlp
    from crec.download import _caused_by_cookie_load

    try:
        try:
            raise yt_dlp.cookies.CookieLoadError("could not find chrome cookies database")
        except yt_dlp.cookies.CookieLoadError:
            raise yt_dlp.utils.DownloadError("ERROR: could not find...") from None
    except yt_dlp.utils.DownloadError as wrapped:
        wrapped.__context__ = yt_dlp.cookies.CookieLoadError("x")
        assert _caused_by_cookie_load(wrapped, yt_dlp)

    assert not _caused_by_cookie_load(yt_dlp.utils.DownloadError("other"), yt_dlp)


def test_long_output_folder_is_not_trimmed_into_the_filename(tmp_path, monkeypatch) -> None:
    """Regression: trim_file_name applied to the whole path, so a deep -o folder
    sent "Me at the zoo.webm" to <a parent folder>/c.webm."""
    import yt_dlp
    from crec import download
    from crec.download import Progress, build_ydl_opts

    # The suite stays ffmpeg-free: CI's Linux and macOS runners don't have it.
    monkeypatch.setattr(download.deps, "ffmpeg_path", lambda **_: Path("ffmpeg"))

    deep = tmp_path.joinpath(*["a-fairly-long-folder-name"] * 6)
    opts = build_ydl_opts(Options(), deep, Progress(enabled=False))
    with yt_dlp.YoutubeDL(opts) as ydl:
        name = ydl.prepare_filename({"id": "x", "title": "Me at the zoo", "ext": "webm"})
    assert Path(name) == deep / "Me at the zoo.webm"


# --------------------------------------------------------------------------- #
# tray hotkeys (the pure parts - the Win32 side is exercised by hand)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("combo", "expected"),
    [
        ("ctrl+shift+alt+d", (0x2 | 0x4 | 0x1, ord("D"))),
        ("Win+Alt+9", (0x8 | 0x1, ord("9"))),
        ("control + f12", (0x2, 0x7B)),
    ],
)
def test_parse_combo(combo: str, expected: tuple[int, int]) -> None:
    from crec.tray import parse_combo

    assert parse_combo(combo) == expected


@pytest.mark.parametrize(
    ("combo", "complaint"),
    [("d", "at least one"), ("hyper+d", "not a modifier"), ("ctrl+space", "not a key"), ("", "empty")],
)
def test_parse_combo_rejects(combo: str, complaint: str) -> None:
    from crec.tray import parse_combo

    with pytest.raises(ValueError, match=complaint):
        parse_combo(combo)


def test_describe_orders_modifiers_conventionally() -> None:
    from crec.tray import describe

    assert describe("alt+shift+ctrl+d") == "Ctrl+Shift+Alt+D"
    assert describe("win+control+f5") == "Ctrl+Win+F5"


def test_builtin_hotkeys_are_valid_and_not_presets() -> None:
    from crec.tray import load_hotkeys

    hotkeys, warnings = load_hotkeys()
    assert warnings == []
    assert {h.flags for h in hotkeys} == {"", "--discord"}
    assert "hotkeys" not in presets.load().presets, "[hotkeys] must not become --hotkeys"


def test_user_hotkeys_replace_the_builtin_table(monkeypatch, tmp_path) -> None:
    from crec.tray import load_hotkeys

    user_file = tmp_path / "presets.toml"
    user_file.write_text(
        '[hotkeys]\n'
        '"ctrl+alt+g" = "--gif"\n'
        '"ctrl+alt+x" = "--no-such-flag"\n'
        '"ctrl+alt+v" = "clip.mp4 --discord"\n'
        '"alt+ctrl+g" = "-a"\n'
        '"nonsense" = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(presets.paths, "user_presets_file", lambda: user_file)

    hotkeys, warnings = load_hotkeys()
    assert [(h.combo, h.flags) for h in hotkeys] == [("ctrl+alt+g", "--gif")]
    joined = " | ".join(warnings)
    assert "not valid crec flags" in joined
    assert "work on the clipboard" in joined
    assert "listed twice" in joined
    assert "at least one" in joined


def test_hotkey_does_not_silently_redo_the_last_run(desk) -> None:
    """Pressing the hotkey with nothing new copied must not re-download the
    previous video - the CLI's fallback would be a surprise from a hotkey."""
    board, cli, state, tmp = desk
    state.remember("https://example.com/v/9")
    board["text"] = "just some text"

    assert cli.find_source([], prefer_last=False).url == "https://example.com/v/9"
    with pytest.raises(CrecError, match="Nothing to work on"):
        cli.find_source([], prefer_last=False, fall_back_to_last=False)


def test_hotkey_still_treats_own_output_as_same_again(desk) -> None:
    board, cli, state, tmp = desk
    output = _video(tmp, "out.mp4")
    state.remember("https://example.com/v/10", [output])
    board["files"] = [output]
    source = cli.find_source([], prefer_last=False, fall_back_to_last=False)
    assert source.url == "https://example.com/v/10"


def test_progress_listener_hears_stages_without_a_terminal() -> None:
    from crec.download import Progress

    heard: list[str] = []
    progress = Progress(enabled=False, listener=heard.append)
    progress.stage("compressing")
    progress.hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200})
    assert heard == ["compressing", "downloading 25%"]


def test_icons_ship_with_the_package() -> None:
    from crec.tray import _asset

    for name in ("crec.ico", "crec-busy.ico", "crec-error.ico"):
        data = _asset(name).read_bytes()
        assert data[:4] == b"\0\0\1\0", f"{name} is not an .ico"


# --------------------------------------------------------------------------- #
# self-update
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [("2.1.0", "2.0.0", True), ("v2.10.0", "2.9.9", True), ("2.0.0", "2.0.0", False), ("1.9", "2.0.0", False)],
)
def test_newer_compares_versions_as_numbers(candidate: str, current: str, expected: bool) -> None:
    from crec.selfupdate import newer

    assert newer(candidate, current) is expected


def test_pip_installs_are_told_to_use_pip() -> None:
    """Only the frozen Windows app replaces itself; pip owns everything else."""
    from crec import selfupdate

    started, message = selfupdate.update_crec()
    assert started is False and "pip" in message


def test_update_refuses_a_release_without_a_checksum(monkeypatch) -> None:
    from crec import selfupdate

    monkeypatch.setattr(selfupdate, "is_installed_app", lambda: True)
    monkeypatch.setattr(
        selfupdate, "latest_release",
        lambda: selfupdate.Release("99.0.0", "https://example.invalid/x.zip", None),
    )
    started, message = selfupdate.update_crec()
    assert started is False and "checksum" in message


def test_update_refuses_a_download_that_fails_its_checksum(monkeypatch, tmp_path) -> None:
    from crec import selfupdate

    source = tmp_path / "release"
    source.mkdir()
    (source / "version.txt").write_text("99.0.0", encoding="utf-8")
    (source / selfupdate.ASSET).write_bytes(b"not the real zip")
    (source / f"{selfupdate.ASSET}.sha256").write_text("0" * 64 + "  x\n", encoding="utf-8")
    monkeypatch.setenv("CREC_UPDATE_SOURCE", str(source))
    monkeypatch.setattr(selfupdate, "is_installed_app", lambda: True)
    monkeypatch.setattr(selfupdate.paths, "data_dir", lambda: tmp_path / "data")
    launched: list = []
    monkeypatch.setattr(selfupdate, "_launch_helper", lambda *a, **k: launched.append(a))

    with pytest.raises(CrecError, match="checksum"):
        selfupdate.update_crec()
    assert launched == [], "nothing may be swapped in after a failed checksum"


def test_uninstall_and_update_are_reserved_names() -> None:
    assert {"uninstall", "update", "tray"} <= presets.RESERVED


def test_uninstall_keeps_presets_even_inside_the_data_folder(tmp_path) -> None:
    """The prompt promises to keep presets; that must hold even when the config
    folder and data folder are the same place."""
    from crec.selfupdate import _remove_data

    data = tmp_path / "crec"
    (data / "bin").mkdir(parents=True)
    (data / "bin" / "ffmpeg.exe").write_bytes(b"x")
    (data / "state.json").write_text("{}", encoding="utf-8")
    presets_file = data / "presets.toml"
    presets_file.write_text("[mine]\n", encoding="utf-8")

    _remove_data(data, keep=presets_file)
    assert [p.name for p in data.iterdir()] == ["presets.toml"]

    separate = tmp_path / "other"
    separate.mkdir()
    _remove_data(separate, keep=presets_file)
    assert not separate.exists() and presets_file.exists()


def test_download_progress_is_one_line_when_not_a_terminal(monkeypatch, tmp_path, capsys) -> None:
    from crec import deps

    source = tmp_path / "blob.bin"
    source.write_bytes(b"x" * 600_000)
    deps._download(source.as_uri(), tmp_path / "copy.bin", label="blob", quiet=False)
    assert capsys.readouterr().err == "fetching blob...\n"
    assert (tmp_path / "copy.bin").read_bytes() == source.read_bytes()


# --------------------------------------------------------------------------- #
# the saved-login store
# --------------------------------------------------------------------------- #

FUTURE = 4_102_444_800  # 2100-01-01


def _export(*rows: tuple[str, str, str, int]) -> str:
    """A Netscape cookies.txt, as the browser extension writes it."""
    lines = ["# Netscape HTTP Cookie File", "# exported by an extension", ""]
    for domain, name, value, expires in rows:
        prefix = "#HttpOnly_" if name == "sessionid" else ""
        lines.append(f"{prefix}{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


@pytest.fixture
def jar_store(monkeypatch, tmp_path):
    from crec import cookies

    monkeypatch.setattr(cookies.paths, "data_dir", lambda: tmp_path)
    return cookies


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("www.tiktok.com", "tiktok.com"), (".tiktok.com", "tiktok.com"), ("vm.tiktok.com", "tiktok.com"),
        ("twitter.com", "x.com"), ("youtu.be", "youtube.com"), ("www.bbc.co.uk", "bbc.co.uk"),
    ],
)
def test_site_of(host: str, site: str) -> None:
    from crec.cookies import site_of

    assert site_of(host) == site


def test_parse_reads_httponly_lines_and_skips_comments() -> None:
    from crec.cookies import parse

    jar = parse(_export((".tiktok.com", "sessionid", "abc", FUTURE), (".tiktok.com", "tt_csrf", "x", 0)))
    assert [(c.name, c.http_only) for c in jar] == [("sessionid", True), ("tt_csrf", False)]


def test_parse_rejects_something_that_is_not_an_export() -> None:
    from crec.cookies import parse

    with pytest.raises(CrecError, match="Get cookies.txt"):
        parse("https://www.tiktok.com/@someone/video/123")


def test_add_keeps_one_site_and_encrypts_it(jar_store) -> None:
    saved = jar_store.add(_export((".tiktok.com", "sessionid", "SECRET-SESSION", FUTURE)))
    assert [(s.site, s.count) for s in saved] == [("tiktok.com", 1)]

    on_disk = (jar_store.store_dir() / "tiktok.com.json").read_text(encoding="utf-8")
    if sys.platform == "win32":
        assert "SECRET" not in on_disk and "U0VDUkVU" not in on_disk  # not plain, not base64-plain
    assert jar_store.has_login_for("https://www.tiktok.com/@a/video/1") == "tiktok.com"
    assert jar_store.has_login_for("https://www.youtube.com/watch?v=x") is None


def test_add_refuses_a_whole_browser_jar(jar_store) -> None:
    """The point is to keep only what a download needs."""
    everything = _export(
        (".tiktok.com", "sessionid", "a", FUTURE),
        (".google.com", "SID", "b", FUTURE),
        (".bank.example", "auth", "c", FUTURE),
    )
    with pytest.raises(CrecError, match="3 sites"):
        jar_store.add(everything)
    assert jar_store.saved() == []

    picked = jar_store.add(everything, site="www.tiktok.com")
    assert [s.site for s in picked] == ["tiktok.com"]
    assert jar_store.has_login_for("https://accounts.google.com") is None


def test_add_drops_expired_cookies(jar_store) -> None:
    saved = jar_store.add(_export((".x.com", "auth_token", "a", FUTURE), (".x.com", "old", "b", 1000)))
    assert saved[0].count == 1
    with pytest.raises(CrecError, match="expired"):
        jar_store.add(_export((".x.com", "old", "b", 1000)))


def test_saved_login_is_lent_then_refreshed_then_removed(jar_store) -> None:
    """yt-dlp rewrites the jar on exit; keep this site's refreshed values only."""
    jar_store.add(_export((".tiktok.com", "sessionid", "old-value", FUTURE)))

    with jar_store.lent_to_ytdlp("https://www.tiktok.com/@a/video/1") as path:
        assert "old-value" in path.read_text(encoding="utf-8")
        # What yt-dlp's save_cookies() leaves behind: a refreshed session and a
        # cookie some CDN set along the way.
        path.write_text(_export(
            (".tiktok.com", "sessionid", "new-value", FUTURE),
            (".tiktokcdn.com", "tracker", "zzz", FUTURE),
        ), encoding="utf-8")
        handed = path

    assert not handed.exists(), "the plaintext hand-off file must not outlive the download"
    with jar_store.lent_to_ytdlp("https://tiktok.com/x") as path:
        text = path.read_text(encoding="utf-8")
    assert "new-value" in text and "old-value" not in text and "tracker" not in text


def test_nothing_is_lent_to_other_sites(jar_store) -> None:
    jar_store.add(_export((".tiktok.com", "sessionid", "a", FUTURE)))
    with jar_store.lent_to_ytdlp("https://www.youtube.com/watch?v=x") as path:
        assert path is None


def test_fetch_uses_the_saved_login_unless_cookies_is_given(jar_store, monkeypatch, tmp_path) -> None:
    from crec import download

    jar_store.add(_export((".tiktok.com", "sessionid", "a", FUTURE)))
    seen: list = []
    monkeypatch.setattr(
        download, "_fetch",
        lambda url, options, out, progress, retry, *, saved_site, jar=None: seen.append((saved_site, jar)) or [],
    )
    progress = download.Progress(enabled=False)
    download.fetch("https://www.tiktok.com/@a/video/1", Options(), tmp_path, progress)
    download.fetch("https://www.tiktok.com/@a/video/1", Options(cookies="firefox"), tmp_path, progress)
    download.fetch("https://www.youtube.com/watch?v=x", Options(), tmp_path, progress)

    assert seen[0][0] == "tiktok.com" and seen[0][1] is not None
    assert seen[1] == (None, None), "an explicit --cookies wins over the saved login"
    assert seen[2] == (None, None)


# --------------------------------------------------------------------------- #
# output robustness
# --------------------------------------------------------------------------- #

def test_tidy_strips_terminal_colours() -> None:
    """In a real terminal yt-dlp colours ERROR:, which leaked as "crec: ERROR: ..."."""
    from crec.download import _tidy

    coloured = "\x1b[0;31mERROR:\x1b[0m [TikTok] 123: Unexpected response from webpage request; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues"
    assert _tidy(coloured) == "[TikTok] 123: Unexpected response from webpage request"


def test_emoji_output_does_not_crash_when_piped(tmp_path) -> None:
    """Regression: printing a finished TikTok path ("... 🐧 ...mp4") to a pipe on
    Windows raised UnicodeEncodeError after the download had succeeded."""
    import os
    import subprocess

    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "APPDATA": str(tmp_path),
           "XDG_DATA_HOME": str(tmp_path), "CREC_NO_UPDATE_CHECK": "1"}
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv = ['crec', 'cookies', 'remove', 'linux\U0001f427.example']; "
         "from crec.cli import main; sys.exit(main())"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"nothing saved for" in result.stdout


def test_doctor_reports_impersonation() -> None:
    from crec.deps import _impersonation

    assert _impersonation().startswith(("available", "MISSING"))
