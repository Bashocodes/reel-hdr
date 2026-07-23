from __future__ import annotations

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from reelhdr import verify
from reelhdr.amve import (
    DolbyVisionConfigEvidence,
    IsoBmffEvidence,
    TopLevelBoxEvidence,
)
from reelhdr.mp4box import parse_mp4box_info
from reelhdr.probe import SourceClass, VideoProbe, parse_probe_json
from reelhdr.verify import (
    CheckStatus,
    VerificationEvidence,
    VerifyExpectations,
    VerifyVerdict,
    evaluate_verification,
    format_human_report,
    format_json_report,
    verify_file,
)

_VALID_MP4BOX = """\
Duration 00:00:01.001
Progressive (moov before mdat)
# Track 1 Info - ID 1
Media Type: vide:hvc1
Media Samples: 30 - CFR 29.970/sec
DolbyVision version 1.0 profile 8 level 1 (Compatibility: 4)
"""
_FIXTURES = Path(__file__).parent / "fixtures"


def _valid_probe() -> VideoProbe:
    return parse_probe_json(
        json.loads((_FIXTURES / "ffprobe_dolby_vision.json").read_text()),
        path="valid.mp4",
    )


def _valid_container() -> IsoBmffEvidence:
    return IsoBmffEvidence(
        top_level_boxes=(
            TopLevelBoxEvidence("ftyp", 0, 24),
            TopLevelBoxEvidence("moov", 24, 900),
            TopLevelBoxEvidence("mdat", 924, 10_000),
        ),
        moov_offset=24,
        mdat_offset=924,
        first_video_sample_entry_type="hvc1",
        dolby_vision_config=DolbyVisionConfigEvidence(
            box_type="dvcC",
            offset=400,
            payload=bytes([1, 0, 16, 9, 64]),
            profile=8,
            bl_signal_compatibility_id=4,
        ),
        amve_present=True,
    )


def _valid_evidence() -> VerificationEvidence:
    return VerificationEvidence(
        probe=_valid_probe(),
        container=_valid_container(),
        mp4box=parse_mp4box_info(_VALID_MP4BOX, path="valid.mp4"),
    )


def _by_id(report, check_id: str):
    return next(check for check in report.checks if check.check_id == check_id)


def test_valid_evidence_passes_every_declared_check() -> None:
    report = evaluate_verification(
        "valid.mp4",
        _valid_evidence(),
        expectations=VerifyExpectations(expect_audio=True, audio_codec="aac"),
    )

    assert report.verdict is VerifyVerdict.PASS
    assert report.fail_count == 0
    assert {check.check_id for check in report.checks} == {
        "video.codec",
        "video.profile",
        "video.bit_depth",
        "color.transfer",
        "color.primaries",
        "color.matrix",
        "video.resolution",
        "video.fps",
        "timing.frame_count",
        "timing.duration_consistency",
        "dv.ffprobe_signal",
        "audio.presence",
        "audio.codec",
        "container.video_sample_entry",
        "dv.config_box",
        "dv.profile",
        "dv.bl_compatibility_id",
        "container.amve",
        "container.fast_start",
        "dv.mp4box_signal",
        "timing.tool_frame_count",
    }
    assert all(check.status is CheckStatus.OK for check in report.checks)


def test_clean_hlg_evidence_passes_the_hlg_preset_contract() -> None:
    evidence = _valid_evidence()
    assert evidence.probe is not None
    assert evidence.container is not None
    clean_probe = replace(
        evidence.probe,
        source_class=SourceClass.HLG,
        dv_profile=None,
        dv_level=None,
        dv_bl_signal_compatibility_id=None,
        has_dolby_vision_rpu=False,
    )
    clean_container = replace(
        evidence.container,
        dolby_vision_config=None,
        amve_present=False,
    )
    clean_mp4box = parse_mp4box_info(
        _VALID_MP4BOX.replace(
            "DolbyVision version 1.0 profile 8 level 1 (Compatibility: 4)\n",
            "",
        ),
        path="clean-hlg.mp4",
    )

    report = evaluate_verification(
        "clean-hlg.mp4",
        replace(
            evidence,
            probe=clean_probe,
            container=clean_container,
            mp4box=clean_mp4box,
        ),
        expectations=VerifyExpectations(
            expect_audio=True,
            audio_codec="aac",
            expect_dolby_vision=False,
            expect_amve=False,
        ),
    )

    assert report.verdict is VerifyVerdict.PASS
    assert report.fail_count == 0
    assert _by_id(report, "dv.config_box").value_found == "missing"
    assert _by_id(report, "container.amve").value_found == "missing"


@pytest.mark.parametrize(
    ("field", "value", "check_id"),
    [
        ("codec_name", "h264", "video.codec"),
        ("codec_profile", "Main", "video.profile"),
        ("bit_depth", 8, "video.bit_depth"),
        ("color_transfer", "smpte2084", "color.transfer"),
        ("color_primaries", "bt709", "color.primaries"),
        ("color_space", "bt709", "color.matrix"),
        ("width", 1079, "video.resolution"),
        ("fps", Fraction(0), "video.fps"),
        ("frame_count", 0, "timing.frame_count"),
        ("duration", 9.0, "timing.duration_consistency"),
        ("dv_profile", 7, "dv.ffprobe_signal"),
    ],
)
def test_each_ffprobe_conformance_failure_is_reported(
    field: str,
    value: object,
    check_id: str,
) -> None:
    evidence = _valid_evidence()
    assert evidence.probe is not None
    evidence = replace(evidence, probe=replace(evidence.probe, **{field: value}))

    check = _by_id(evaluate_verification("bad.mp4", evidence), check_id)

    assert check.status is CheckStatus.FAIL
    assert "Found " in check.explanation
    assert "expected " in check.explanation
    assert "likely cause:" in check.explanation


def test_wrong_transfer_explains_that_pq_is_not_hlg() -> None:
    evidence = _valid_evidence()
    assert evidence.probe is not None
    evidence = replace(
        evidence,
        probe=replace(evidence.probe, color_transfer="smpte2084"),
    )

    check = _by_id(evaluate_verification("pq.mp4", evidence), "color.transfer")

    assert check.status is CheckStatus.FAIL
    assert "this file is PQ, not HLG" in check.explanation
    assert "run reelhdr convert" in check.explanation


@pytest.mark.parametrize(
    ("mutation", "check_id"),
    [
        ({"first_video_sample_entry_type": "avc1"}, "container.video_sample_entry"),
        ({"amve_present": False}, "container.amve"),
    ],
)
def test_container_contract_failures_are_reported(
    mutation: dict[str, object],
    check_id: str,
) -> None:
    evidence = _valid_evidence()
    assert evidence.container is not None
    evidence = replace(evidence, container=replace(evidence.container, **mutation))

    check = _by_id(evaluate_verification("bad.mp4", evidence), check_id)

    assert check.status is CheckStatus.FAIL


def test_missing_dolby_config_is_a_single_precise_failure() -> None:
    evidence = _valid_evidence()
    assert evidence.container is not None
    evidence = replace(
        evidence,
        container=replace(evidence.container, dolby_vision_config=None),
    )

    report = evaluate_verification("missing-dv.mp4", evidence)
    check = _by_id(report, "dv.config_box")

    assert check.status is CheckStatus.FAIL
    assert check.value_found == "missing"
    assert "omitted or stripped during muxing" in check.explanation
    assert "dv.profile" not in {item.check_id for item in report.checks}


@pytest.mark.parametrize(
    ("field", "value", "check_id"),
    [
        ("profile", 7, "dv.profile"),
        ("bl_signal_compatibility_id", 1, "dv.bl_compatibility_id"),
    ],
)
def test_wrong_direct_dolby_fields_fail(
    field: str,
    value: int,
    check_id: str,
) -> None:
    evidence = _valid_evidence()
    assert evidence.container is not None
    config = evidence.container.dolby_vision_config
    assert config is not None
    evidence = replace(
        evidence,
        container=replace(
            evidence.container,
            dolby_vision_config=replace(config, **{field: value}),
        ),
    )

    assert _by_id(evaluate_verification("bad.mp4", evidence), check_id).status is CheckStatus.FAIL


def test_non_fast_start_is_a_warning_and_does_not_fail_the_report() -> None:
    evidence = _valid_evidence()
    assert evidence.container is not None
    slow = replace(
        evidence.container,
        top_level_boxes=(
            TopLevelBoxEvidence("ftyp", 0, 24),
            TopLevelBoxEvidence("mdat", 24, 10_000),
            TopLevelBoxEvidence("moov", 10_024, 900),
        ),
        moov_offset=10_024,
        mdat_offset=24,
    )
    evidence = replace(evidence, container=slow)

    report = evaluate_verification("slow.mp4", evidence)

    assert _by_id(report, "container.fast_start").status is CheckStatus.WARN
    assert report.verdict is VerifyVerdict.PASS


@pytest.mark.parametrize(
    ("text", "check_id"),
    [
        (
            "# Track 1 Info - ID 1\nMedia Type: vide:hvc1\nMedia Samples: 30\n",
            "dv.mp4box_signal",
        ),
        (
            _VALID_MP4BOX.replace("profile 8", "profile 7"),
            "dv.mp4box_signal",
        ),
        (
            _VALID_MP4BOX.replace("Media Samples: 30", "Media Samples: 29"),
            "timing.tool_frame_count",
        ),
    ],
)
def test_mp4box_disagreements_fail(text: str, check_id: str) -> None:
    evidence = replace(
        _valid_evidence(),
        mp4box=parse_mp4box_info(text, path="bad.mp4"),
    )

    assert _by_id(evaluate_verification("bad.mp4", evidence), check_id).status is CheckStatus.FAIL


def test_expected_audio_presence_and_codec_are_enforced() -> None:
    evidence = _valid_evidence()
    assert evidence.probe is not None
    no_audio = replace(
        evidence.probe,
        has_audio=False,
        audio_codec_name=None,
        audio_sample_rate=None,
        audio_channels=None,
    )

    report = evaluate_verification(
        "silent.mp4",
        replace(evidence, probe=no_audio),
        expectations=VerifyExpectations(expect_audio=True, audio_codec="aac"),
    )

    assert _by_id(report, "audio.presence").status is CheckStatus.FAIL
    assert "audio.codec" not in {check.check_id for check in report.checks}

    wrong_codec = replace(evidence.probe, audio_codec_name="opus")
    report = evaluate_verification(
        "opus.mp4",
        replace(evidence, probe=wrong_codec),
        expectations=VerifyExpectations(expect_audio=True, audio_codec="aac"),
    )
    assert _by_id(report, "audio.codec").status is CheckStatus.FAIL


def test_truncated_or_unreadable_evidence_becomes_fail_checks_without_throwing() -> None:
    report = evaluate_verification(
        "truncated.mp4",
        VerificationEvidence(
            probe_error="ffprobe exited 1: invalid data",
            container_error="truncated 'moov' box at byte 24",
            mp4box_error="MP4Box -info exited 1: invalid file",
        ),
    )

    assert report.verdict is VerifyVerdict.FAIL
    assert [check.check_id for check in report.checks] == [
        "evidence.ffprobe",
        "container.parse",
        "evidence.mp4box",
    ]
    assert all(check.status is CheckStatus.FAIL for check in report.checks)


def test_human_and_json_reports_have_stable_verdicts_and_fields() -> None:
    report = evaluate_verification("valid.mp4", _valid_evidence())

    human = format_human_report(report)
    machine = json.loads(format_json_report(report))

    assert "STATUS  CHECK" in human
    assert "VERDICT: PASS" in human
    assert machine["verdict"] == "pass"
    assert machine["summary"]["fail"] == 0
    assert machine["checks"][0] == report.checks[0].to_dict()


def test_verify_file_is_read_only_even_when_all_evidence_sources_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "truncated.mp4"
    original = b"\0\0\0\x20moovshort"
    path.write_bytes(original)

    def fail_probe(*_args, **_kwargs):
        raise OSError("synthetic ffprobe failure")

    def fail_mp4box(*_args, **_kwargs):
        raise OSError("synthetic MP4Box failure")

    monkeypatch.setattr(verify, "probe_video", fail_probe)
    monkeypatch.setattr(verify, "inspect_mp4box", fail_mp4box)

    report = verify_file(path)

    assert report.verdict is VerifyVerdict.FAIL
    assert path.read_bytes() == original
