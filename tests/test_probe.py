from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from reelhdr.probe import (
    ProbeError,
    SourceClass,
    parse_probe_json,
    probe_video,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("ffprobe_sdr.json", SourceClass.SDR),
        ("ffprobe_hlg.json", SourceClass.HLG),
        ("ffprobe_pq.json", SourceClass.PQ),
        ("ffprobe_dolby_vision.json", SourceClass.DOLBY_VISION),
    ],
)
def test_classifies_recorded_ffprobe_json(
    fixture_name: str,
    expected: SourceClass,
) -> None:
    probe = parse_probe_json(_fixture(fixture_name), path="input.mov")

    assert probe.source_class is expected


def test_normalizes_geometry_timing_tags_and_audio() -> None:
    probe = parse_probe_json(_fixture("ffprobe_sdr.json"), path="input.mp4")

    assert probe.codec_name == "h264"
    assert probe.codec_profile == "High"
    assert probe.codec_tag_string == "avc1"
    assert probe.resolution == (1920, 1080)
    assert probe.fps == Fraction(30000, 1001)
    assert probe.fps_float == pytest.approx(29.97003)
    assert probe.frame_count == 30
    assert probe.bit_depth == 8
    assert probe.duration == pytest.approx(1.001)
    assert probe.has_audio is True
    assert probe.audio_codec_name == "aac"
    assert probe.audio_sample_rate == 48000
    assert probe.audio_channels == 2
    assert probe.pix_fmt == "yuv420p"
    assert probe.color_range == "tv"
    assert probe.color_space == "bt709"
    assert probe.color_transfer == "bt709"
    assert probe.color_primaries == "bt709"
    assert probe.tags == {"handler_name": "VideoHandler", "language": "und"}


def test_normalizes_hdr_and_dolby_vision_evidence() -> None:
    hlg = parse_probe_json(_fixture("ffprobe_hlg.json"), path="hlg.mov")
    pq = parse_probe_json(_fixture("ffprobe_pq.json"), path="pq.mov")
    dolby = parse_probe_json(
        _fixture("ffprobe_dolby_vision.json"),
        path="dolby.mov",
    )

    assert hlg.bit_depth == 10
    assert hlg.frame_count == 50
    assert hlg.has_audio is False
    assert hlg.audio_codec_name is None
    assert hlg.audio_sample_rate is None
    assert hlg.audio_channels is None
    assert pq.bit_depth == 10  # Inferred from yuv420p10le.
    assert pq.fps == Fraction(24000, 1001)
    assert dolby.dv_profile == 8
    assert dolby.dv_level == 6
    assert dolby.dv_bl_signal_compatibility_id == 4
    assert dolby.has_dolby_vision_rpu is True


def test_dolby_vision_sample_entry_takes_precedence_over_transfer() -> None:
    payload = _fixture("ffprobe_hlg.json")
    payload["streams"][0]["codec_tag_string"] = "dvh1"

    probe = parse_probe_json(payload, path="input.mp4")

    assert probe.source_class is SourceClass.DOLBY_VISION
    assert probe.dv_profile is None


def test_optional_fields_remain_unknown_and_invalid_rates_are_ignored() -> None:
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "0/0",
                "r_frame_rate": "N/A",
                "nb_frames": "N/A",
                "duration": "N/A",
            }
        ]
    }

    probe = parse_probe_json(payload, path="minimal.mp4")

    assert probe.source_class is SourceClass.SDR
    assert probe.resolution is None
    assert probe.fps is None
    assert probe.frame_count is None
    assert probe.bit_depth is None
    assert probe.duration is None
    assert probe.has_audio is False
    assert probe.audio_codec_name is None
    assert probe.audio_sample_rate is None
    assert probe.audio_channels is None


def test_first_audio_stream_is_normalized_and_invalid_values_are_unknown() -> None:
    payload = _fixture("ffprobe_sdr.json")
    payload["streams"][1].update(
        {
            "codec_name": "  aac  ",
            "sample_rate": "not-a-rate",
            "channels": 0,
        }
    )
    payload["streams"].append(
        {
            "codec_type": "audio",
            "codec_name": "flac",
            "sample_rate": "96000",
            "channels": 6,
        }
    )

    probe = parse_probe_json(payload, path="input.mp4")

    assert probe.has_audio is True
    assert probe.audio_codec_name == "aac"
    assert probe.audio_sample_rate is None
    assert probe.audio_channels is None


def test_counted_frames_take_precedence_over_container_estimate() -> None:
    payload = _fixture("ffprobe_sdr.json")
    payload["streams"][0]["nb_frames"] = "29"
    payload["streams"][0]["nb_read_frames"] = "30"

    probe = parse_probe_json(payload, path="input.mp4")

    assert probe.frame_count == 30


def test_parse_rejects_missing_video_stream() -> None:
    with pytest.raises(ProbeError, match="no video stream"):
        parse_probe_json(
            {"streams": [{"codec_type": "audio", "codec_name": "aac"}]},
            path="audio-only.m4a",
        )


def test_probe_invokes_injected_ffprobe_as_argv_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fixture("ffprobe_hlg.json")
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    probe = probe_video(
        "example input.mov",
        ffprobe_path="/opt/test tools/ffprobe",
        timeout=3.5,
    )

    assert probe.source_class is SourceClass.HLG
    assert observed["argv"] == [
        "/opt/test tools/ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        "-i",
        "example input.mov",
    ]
    assert observed["kwargs"] == {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 3.5,
    }


def test_probe_reports_concise_process_and_json_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="invalid\ncontainer",
        )

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(ProbeError, match=r"ffprobe exited 1: invalid container") as raised:
        probe_video("broken.mov", ffprobe_path="/test/ffprobe")
    assert raised.value.command[0] == "/test/ffprobe"
    assert raised.value.command[-1] == "broken.mov"
    assert raised.value.output_lines == ("invalid", "container")

    def invalid_json_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="{", stderr="")

    monkeypatch.setattr(subprocess, "run", invalid_json_run)
    with pytest.raises(ProbeError, match="returned invalid JSON"):
        probe_video("broken.mov", ffprobe_path="/test/ffprobe")


def test_probe_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 2)

    monkeypatch.setattr(subprocess, "run", timed_out)

    with pytest.raises(ProbeError, match=r"timed out after 2s"):
        probe_video("slow.mov", ffprobe_path="/test/ffprobe", timeout=2)
