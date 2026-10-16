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
