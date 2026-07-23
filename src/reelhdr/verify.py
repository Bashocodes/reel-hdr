"""Typed, read-only conformance verification for Reel-HDR deliverables.

A successful encoder process only proves that a process exited.  This module
combines three independent evidence sources:

* ffprobe JSON for decoded stream properties and timing;
* MP4Box ``-info`` text as an external container-level corroboration;
* a bounded ISO-BMFF reader for physical box placement and Dolby Vision config.

Verification never opens the input for writing and never invokes a mutating
tool command.
"""

from __future__ import annotations

import json
import mmap
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any

from reelhdr.amve import IsoBmffError, IsoBmffEvidence, read_iso_bmff_evidence
from reelhdr.mp4box import (
    EvidenceState,
    MP4BoxInfo,
    MP4BoxInfoError,
    inspect_mp4box,
)
from reelhdr.probe import ProbeError, VideoProbe, probe_video
from reelhdr.tools import ToolUnavailableError

_MAX_SANE_DIMENSION = 16_384
_MAX_SANE_FPS = 240.0
_MATRIX_BT2020 = frozenset({"9", "bt2020", "bt2020nc", "bt2020ncl"})
_PRIMARIES_BT2020 = frozenset({"9", "bt2020"})
_TRANSFER_HLG = frozenset({"18", "arib-std-b67", "arib_std_b67", "hlg"})


class CheckStatus(StrEnum):
    """Severity and outcome of one conformance check."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class VerifyVerdict(StrEnum):
    """Overall report verdict."""

    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class VerifyCheck:
    """One stable, machine-readable conformance finding."""

    check_id: str
    value_found: str
    expectation: str
    status: CheckStatus
    explanation: str

    def to_dict(self) -> dict[str, str]:
        """Return the public JSON shape."""

        return {
            "id": self.check_id,
            "value_found": self.value_found,
            "expectation": self.expectation,
            "status": self.status.value,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Complete verification result for one input path."""

    path: Path
    checks: tuple[VerifyCheck, ...]

    @property
    def fail_count(self) -> int:
        return sum(check.status is CheckStatus.FAIL for check in self.checks)

    @property
    def warn_count(self) -> int:
        return sum(check.status is CheckStatus.WARN for check in self.checks)

    @property
    def ok_count(self) -> int:
        return sum(check.status is CheckStatus.OK for check in self.checks)

    @property
    def verdict(self) -> VerifyVerdict:
        return VerifyVerdict.FAIL if self.fail_count else VerifyVerdict.PASS

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report without unstable timestamps."""

        return {
            "path": str(self.path),
            "verdict": self.verdict.value,
            "summary": {
                "ok": self.ok_count,
                "warn": self.warn_count,
                "fail": self.fail_count,
            },
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class VerifyExpectations:
    """Caller-supplied expectations not inferable from a standalone file."""

    expect_audio: bool = False
    audio_codec: str | None = None
    expect_dolby_vision: bool = True
    expect_amve: bool = True


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """All gathered evidence, including controlled source failures."""

    probe: VideoProbe | None = None
    container: IsoBmffEvidence | None = None
    mp4box: MP4BoxInfo | None = None
    probe_error: str | None = None
    container_error: str | None = None
    mp4box_error: str | None = None


def verify_file(
    input_path: str | Path,
    *,
    expectations: VerifyExpectations | None = None,
    ffprobe_path: str | Path | None = None,
    mp4box_path: str | Path | None = None,
) -> VerifyReport:
    """Gather independent evidence and verify a file without modifying it."""

    path = Path(input_path).expanduser().resolve()
    probe: VideoProbe | None = None
    container: IsoBmffEvidence | None = None
    mp4box: MP4BoxInfo | None = None
    probe_error: str | None = None
    container_error: str | None = None
    mp4box_error: str | None = None

    try:
        probe = probe_video(path, ffprobe_path=ffprobe_path)
    except (ProbeError, ToolUnavailableError, OSError) as error:
        probe_error = _one_line(str(error))

    try:
        container = _read_container_evidence(path)
    except (IsoBmffError, OSError, ValueError) as error:
        container_error = _one_line(str(error))

    try:
        mp4box = inspect_mp4box(path, mp4box_path=mp4box_path)
    except (MP4BoxInfoError, ToolUnavailableError, OSError) as error:
        mp4box_error = _one_line(str(error))

    return evaluate_verification(
        path,
        VerificationEvidence(
            probe=probe,
            container=container,
            mp4box=mp4box,
            probe_error=probe_error,
            container_error=container_error,
            mp4box_error=mp4box_error,
        ),
        expectations=expectations,
    )


def evaluate_verification(
    path: str | Path,
    evidence: VerificationEvidence,
    *,
    expectations: VerifyExpectations | None = None,
) -> VerifyReport:
    """Pure check engine for gathered or fixture-backed evidence."""

    expected = expectations or VerifyExpectations()
    checks: list[VerifyCheck] = []

    if evidence.probe is None:
        checks.append(
            _fail(
                "evidence.ffprobe",
                evidence.probe_error or "no ffprobe evidence",
                "a readable video stream",
                "the input is missing, malformed, unsupported, or ffprobe is unavailable",
            )
        )
    else:
        checks.extend(_probe_checks(evidence.probe, expected))

    if evidence.container is None:
        checks.append(
            _fail(
                "container.parse",
                evidence.container_error or "no ISO-BMFF evidence",
                "a bounds-valid MP4 container",
                "the file is truncated, malformed, or is not an ISO-BMFF/MP4 file",
            )
        )
    else:
        checks.extend(_container_checks(evidence.container, expected))

    if evidence.mp4box is None:
        checks.append(
            _fail(
                "evidence.mp4box",
                evidence.mp4box_error or "no MP4Box evidence",
                "successful read-only MP4Box -info inspection",
                "the container is malformed or MP4Box is unavailable",
            )
        )
    else:
        checks.extend(_mp4box_checks(evidence.mp4box, evidence.probe, expected))

    return VerifyReport(path=Path(path), checks=tuple(checks))


def format_human_report(report: VerifyReport) -> str:
    """Format a compact table followed by teaching explanations and verdict."""

    rows = [
        (
            check.status.value.upper(),
            check.check_id,
            _clip(check.value_found, 34),
            _clip(check.expectation, 34),
        )
        for check in report.checks
    ]
    headers = ("STATUS", "CHECK", "FOUND", "EXPECTED")
    widths = tuple(
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    )

    lines = [
        f"Reel-HDR verification: {report.path}",
        _table_line(headers, widths),
        _table_line(tuple("-" * width for width in widths), widths),
    ]
    for row, check in zip(rows, report.checks, strict=True):
        lines.append(_table_line(row, widths))
        lines.append(f"       {check.explanation}")

    verdict_detail = (
        f"{report.ok_count} ok, {report.warn_count} warning(s), {report.fail_count} failure(s)"
    )
    lines.append(f"VERDICT: {report.verdict.value.upper()} — {verdict_detail}")
    return "\n".join(lines)


def format_json_report(report: VerifyReport) -> str:
    """Serialize the stable machine report."""

    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def _probe_checks(
    probe: VideoProbe,
    expectations: VerifyExpectations,
) -> list[VerifyCheck]:
    checks: list[VerifyCheck] = []

    codec = _found(probe.codec_name)
    checks.append(
        _ok("video.codec", codec, "hevc", "The video stream is HEVC.")
        if probe.codec_name and probe.codec_name.casefold() in {"hevc", "h265"}
        else _fail(
            "video.codec",
            codec,
            "hevc",
            "the file was encoded with the wrong video codec instead of the HEVC target",
        )
    )

    profile = _found(probe.codec_profile)
    profile_ok = bool(probe.codec_profile and "main 10" in probe.codec_profile.casefold())
    checks.append(
        _ok("video.profile", profile, "Main 10", "The HEVC profile is Main 10.")
        if profile_ok
        else _fail(
            "video.profile",
            profile,
            "Main 10",
            "the encoder selected an 8-bit or unsupported HEVC profile",
        )
    )

    bit_depth = _found(probe.bit_depth)
    checks.append(
        _ok("video.bit_depth", bit_depth, "10", "The decoded pixel format is 10-bit.")
        if probe.bit_depth == 10
        else _fail(
            "video.bit_depth",
            bit_depth,
            "10",
            "the source or encoder pixel format was not normalized to 10-bit",
        )
    )

    transfer = _found(probe.color_transfer)
    transfer_ok = _normalized(probe.color_transfer) in _TRANSFER_HLG
    transfer_cause = (
        "this file is PQ, not HLG; run reelhdr convert before delivery"
        if _normalized(probe.color_transfer) in {"16", "pq", "smpte2084", "st2084"}
        else "the HLG transfer tag was omitted or replaced during encoding or muxing"
    )
    checks.append(
        _ok(
            "color.transfer",
            transfer,
            "arib-std-b67 (HLG)",
            "The video transfer characteristic is HLG.",
        )
        if transfer_ok
        else _fail(
            "color.transfer",
            transfer,
            "arib-std-b67 (HLG)",
            transfer_cause,
        )
    )

    primaries = _found(probe.color_primaries)
    checks.append(
        _ok(
            "color.primaries",
            primaries,
            "bt2020",
            "The color primaries are BT.2020.",
        )
        if _normalized(probe.color_primaries) in _PRIMARIES_BT2020
        else _fail(
            "color.primaries",
            primaries,
            "bt2020",
            "the BT.2020 primaries tag was omitted or replaced",
        )
    )

    matrix = _found(probe.color_space)
    checks.append(
        _ok(
            "color.matrix",
            matrix,
            "bt2020nc",
            "The YCbCr matrix is BT.2020 non-constant luminance.",
        )
        if _normalized(probe.color_space) in _MATRIX_BT2020
        else _fail(
            "color.matrix",
            matrix,
            "bt2020nc",
            "the encoder or muxer retained an SDR matrix instead of BT.2020",
        )
    )

    resolution = (
        f"{probe.width}x{probe.height}"
        if probe.width is not None and probe.height is not None
        else "missing"
    )
    resolution_ok = bool(
        probe.width
        and probe.height
        and probe.width <= _MAX_SANE_DIMENSION
        and probe.height <= _MAX_SANE_DIMENSION
        and probe.width % 2 == 0
        and probe.height % 2 == 0
    )
    checks.append(
        _ok(
            "video.resolution",
            resolution,
            "positive, even dimensions ≤16384",
            "The resolution is valid for 4:2:0 Main 10 delivery.",
        )
        if resolution_ok
        else _fail(
            "video.resolution",
            resolution,
            "positive, even dimensions ≤16384",
            "the dimensions are missing, implausible, or incompatible with 4:2:0 video",
        )
    )

    fps_text = _fps_text(probe.fps)
    fps_ok = probe.fps is not None and 0 < float(probe.fps) <= _MAX_SANE_FPS
    checks.append(
        _ok(
            "video.fps",
            fps_text,
            "0 < fps ≤ 240",
            "The declared frame rate is positive and plausible.",
        )
        if fps_ok
        else _fail(
            "video.fps",
            fps_text,
            "0 < fps ≤ 240",
            "the stream has missing, zero, or implausible timing metadata",
        )
    )

    frames = _found(probe.frame_count)
    checks.append(
        _ok(
            "timing.frame_count",
            frames,
            "a positive decoded frame count",
            "ffprobe decoded a nonzero frame count.",
        )
        if probe.frame_count is not None and probe.frame_count > 0
        else _fail(
            "timing.frame_count",
            frames,
            "a positive decoded frame count",
            "the video is empty, truncated, or lacks usable sample timing",
        )
    )
    checks.append(_duration_consistency_check(probe))
    checks.append(_ffprobe_dolby_check(probe, expectations))

    if expectations.expect_audio:
        audio_found = (
            f"{probe.audio_codec_name or 'unknown codec'}, "
            f"{probe.audio_sample_rate or 'unknown rate'} Hz, "
            f"{probe.audio_channels or 'unknown channels'} ch"
            if probe.has_audio
            else "no audio track"
        )
        checks.append(
            _ok(
                "audio.presence",
                audio_found,
                "an audio track",
                "The expected audio track is present.",
            )
            if probe.has_audio
            else _fail(
                "audio.presence",
                audio_found,
                "an audio track",
                "audio was dropped while extracting or muxing the video",
            )
        )
        if probe.has_audio and expectations.audio_codec:
            expected_codec = expectations.audio_codec.casefold()
            found_codec = _found(probe.audio_codec_name)
            checks.append(
                _ok(
                    "audio.codec",
                    found_codec,
                    expected_codec,
                    "The preserved audio codec matches the caller expectation.",
                )
                if _normalized(probe.audio_codec_name) == expected_codec
                else _fail(
                    "audio.codec",
                    found_codec,
                    expected_codec,
                    "the audio stream was transcoded to or muxed as a different codec",
                )
            )

    return checks


def _duration_consistency_check(probe: VideoProbe) -> VerifyCheck:
    if (
        probe.duration is None
        or probe.duration <= 0
        or probe.frame_count is None
        or probe.frame_count <= 0
        or probe.fps is None
        or probe.fps <= 0
    ):
        return _fail(
            "timing.duration_consistency",
            (
                f"duration={_found(probe.duration)}, frames={_found(probe.frame_count)}, "
                f"fps={_fps_text(probe.fps)}"
            ),
            "duration ≈ frame_count ÷ fps",
            "one or more required timing fields are missing or invalid",
        )

    computed = probe.frame_count / float(probe.fps)
    difference = abs(probe.duration - computed)
    tolerance = max(0.05, 1.0 / float(probe.fps))
    found = f"declared={probe.duration:.6f}s, computed={computed:.6f}s, delta={difference:.6f}s"
    expected = f"delta ≤ {tolerance:.6f}s"
    if difference <= tolerance:
        return _ok(
            "timing.duration_consistency",
            found,
            expected,
            "Declared duration agrees with frame count and frame rate.",
        )
    return _fail(
        "timing.duration_consistency",
        found,
        expected,
        "frames were dropped, duplicated, or assigned inconsistent timestamps",
    )


def _ffprobe_dolby_check(
    probe: VideoProbe,
    expectations: VerifyExpectations,
) -> VerifyCheck:
    found = (
        f"profile={_found(probe.dv_profile)}, "
        f"compatibility={_found(probe.dv_bl_signal_compatibility_id)}, "
        f"rpu={'present' if probe.has_dolby_vision_rpu else 'not reported'}"
    )
    if not expectations.expect_dolby_vision:
        has_dolby = bool(
            probe.dv_profile is not None
            or probe.dv_bl_signal_compatibility_id is not None
            or probe.has_dolby_vision_rpu
        )
        return (
            _fail(
                "dv.ffprobe_signal",
                found,
                "no Dolby Vision signalling",
                "the file contains Dolby Vision metadata but the HLG preset requires a clean base",
            )
            if has_dolby
            else _ok(
                "dv.ffprobe_signal",
                found,
                "no Dolby Vision signalling",
                "ffprobe sees a clean HLG stream without Dolby Vision metadata.",
            )
        )

    expected = "profile=8, compatibility=4, RPU present"
    if probe.dv_profile not in {None, 8} or probe.dv_bl_signal_compatibility_id not in {
        None,
        4,
    }:
        return _fail(
            "dv.ffprobe_signal",
            found,
            expected,
            "the muxed Dolby Vision record targets a different profile or base-layer system",
        )
    if (
        probe.dv_profile == 8
        and probe.dv_bl_signal_compatibility_id == 4
        and probe.has_dolby_vision_rpu
    ):
        return _ok(
            "dv.ffprobe_signal",
            found,
            expected,
            "ffprobe sees Profile 8, HLG compatibility, and RPU signaling.",
        )
    return _warn(
        "dv.ffprobe_signal",
        found,
        expected,
        (
            "this ffprobe build did not expose complete Dolby Vision side data; "
            "box checks remain authoritative"
        ),
    )


def _container_checks(
    container: IsoBmffEvidence,
    expectations: VerifyExpectations,
) -> list[VerifyCheck]:
    checks: list[VerifyCheck] = []
    sample_entry = container.first_video_sample_entry_type
    checks.append(
        _ok(
            "container.video_sample_entry",
            sample_entry,
            "hvc1, hev1, dvh1, or dvhe",
            "The visual sample entry is HEVC/Dolby Vision compatible.",
        )
        if sample_entry in {"hvc1", "hev1", "dvh1", "dvhe"}
        else _fail(
            "container.video_sample_entry",
            sample_entry,
            "hvc1, hev1, dvh1, or dvhe",
            "the MP4 sample description advertises a non-HEVC codec",
        )
    )

    config = container.dolby_vision_config
    if not expectations.expect_dolby_vision:
        checks.append(
            _ok(
                "dv.config_box",
                "missing",
                "no dvcC or dvvC configuration box",
                "The HLG-only sample entry has no Dolby Vision configuration.",
            )
            if config is None
            else _fail(
                "dv.config_box",
                config.box_type,
                "no dvcC or dvvC configuration box",
                "the HLG-only output was muxed with Dolby Vision configuration metadata",
            )
        )
    elif config is None:
        checks.append(
            _fail(
                "dv.config_box",
                "missing",
                "dvcC or dvvC in the video sample entry",
                "the Dolby Vision configuration atom was omitted or stripped during muxing",
            )
        )
    else:
        checks.append(
            _ok(
                "dv.config_box",
                config.box_type,
                "dvcC or dvvC in the video sample entry",
                "A Dolby Vision decoder-configuration atom is present.",
            )
        )
        checks.append(
            _ok(
                "dv.profile",
                _found(config.profile),
                "8",
                "The configuration atom declares Dolby Vision Profile 8.",
            )
            if config.profile == 8
            else _fail(
                "dv.profile",
                _found(config.profile),
                "8",
                (
                    "the RPU/configuration was generated for the wrong Dolby Vision "
                    "profile or is truncated"
                ),
            )
        )
        checks.append(
            _ok(
                "dv.bl_compatibility_id",
                _found(config.bl_signal_compatibility_id),
                "4 (HLG)",
                "The base layer is declared HLG-compatible.",
            )
            if config.bl_signal_compatibility_id == 4
            else _fail(
                "dv.bl_compatibility_id",
                _found(config.bl_signal_compatibility_id),
                "4 (HLG)",
                "the Dolby Vision base layer targets a non-HLG compatibility mode or is truncated",
            )
        )

    if expectations.expect_amve:
        checks.append(
            _ok(
                "container.amve",
                "present",
                "amve in the video sample entry",
                "Ambient-viewing metadata is attached to the video sample description.",
            )
            if container.amve_present
            else _fail(
                "container.amve",
                "missing",
                "amve in the video sample entry",
                "the file bypassed Reel-HDR's final container step or metadata was stripped",
            )
        )
    else:
        checks.append(
            _fail(
                "container.amve",
                "present",
                "no amve box",
                "the HLG-only preset should not carry the DV delivery path's ambient-viewing box",
            )
            if container.amve_present
            else _ok(
                "container.amve",
                "missing",
                "no amve box",
                "The clean HLG sample entry has no ambient-viewing extension.",
            )
        )

    order = " → ".join(container.top_level_box_order) or "no top-level boxes"
    if container.fast_start is True:
        checks.append(
            _ok(
                "container.fast_start",
                order,
                "moov before mdat",
                "The metadata precedes media payload for progressive download.",
            )
        )
    else:
        checks.append(
            _warn(
                "container.fast_start",
                order,
                "moov before mdat",
                (
                    "the file remains playable, but social and streaming pipelines "
                    "generally prefer fast-start layout"
                ),
            )
        )
    return checks


def _mp4box_checks(
    info: MP4BoxInfo,
    probe: VideoProbe | None,
    expectations: VerifyExpectations,
) -> list[VerifyCheck]:
    checks: list[VerifyCheck] = []
    dolby = info.dolby_vision
    found = (
        f"state={dolby.state.value}, profile={_found(dolby.profile)}, "
        f"compatibility={_found(dolby.compatibility_id)}"
    )
    expected = "profile=8, compatibility=4"
    if not expectations.expect_dolby_vision:
        expected = "no Dolby Vision signalling"
        if dolby.state is EvidenceState.ABSENT:
            checks.append(
                _ok(
                    "dv.mp4box_signal",
                    found,
                    expected,
                    "MP4Box confirms a clean HLG-only sample entry.",
                )
            )
        elif dolby.state is EvidenceState.PRESENT:
            checks.append(
                _fail(
                    "dv.mp4box_signal",
                    found,
                    expected,
                    "MP4Box sees Dolby Vision metadata in an HLG-only output",
                )
            )
        else:
            checks.append(
                _warn(
                    "dv.mp4box_signal",
                    found,
                    expected,
                    "MP4Box output was ambiguous, so absence could not be independently confirmed",
                )
            )
    elif dolby.profile not in {None, 8} or dolby.compatibility_id not in {None, 4}:
        checks.append(
            _fail(
                "dv.mp4box_signal",
                found,
                expected,
                "MP4Box reports a different Dolby Vision profile or base-layer compatibility mode",
            )
        )
    elif (
        dolby.state is EvidenceState.PRESENT and dolby.profile == 8 and dolby.compatibility_id == 4
    ):
        checks.append(
            _ok(
                "dv.mp4box_signal",
                found,
                expected,
                "MP4Box independently confirms Profile 8 with HLG compatibility.",
            )
        )
    elif dolby.state is EvidenceState.ABSENT:
        checks.append(
            _fail(
                "dv.mp4box_signal",
                found,
                expected,
                "MP4Box does not see Dolby Vision signaling in the selected video track",
            )
        )
    else:
        checks.append(
            _warn(
                "dv.mp4box_signal",
                found,
                expected,
                (
                    "MP4Box output was incomplete or ambiguous; direct "
                    "configuration-box checks remain authoritative"
                ),
            )
        )

    video_track = info.video_tracks[0] if info.video_tracks else None
    mp4box_frames = video_track.sample_count if video_track else None
    probe_frames = probe.frame_count if probe else None
    frame_evidence = f"MP4Box={_found(mp4box_frames)}, ffprobe={_found(probe_frames)}"
    if mp4box_frames is None or probe_frames is None:
        checks.append(
            _warn(
                "timing.tool_frame_count",
                frame_evidence,
                "MP4Box sample count = ffprobe frame count",
                "one tool did not expose a count, so cross-tool frame accounting is incomplete",
            )
        )
    elif mp4box_frames == probe_frames:
        checks.append(
            _ok(
                "timing.tool_frame_count",
                frame_evidence,
                "MP4Box sample count = ffprobe frame count",
                "Independent tools agree on the number of video samples.",
            )
        )
    else:
        checks.append(
            _fail(
                "timing.tool_frame_count",
                frame_evidence,
                "MP4Box sample count = ffprobe frame count",
                (
                    "the container sample table and decoded stream disagree, "
                    "suggesting truncation or timing corruption"
                ),
            )
        )
    return checks


def _read_container_evidence(path: Path) -> IsoBmffEvidence:
    with path.open("rb") as input_file:
        if input_file.seek(0, 2) == 0:
            raise IsoBmffError("empty file")
        input_file.seek(0)
        with mmap.mmap(input_file.fileno(), length=0, access=mmap.ACCESS_READ) as mapped:
            return read_iso_bmff_evidence(mapped)


def _ok(
    check_id: str,
    value_found: object,
    expectation: str,
    explanation: str,
) -> VerifyCheck:
    return VerifyCheck(
        check_id=check_id,
        value_found=str(value_found),
        expectation=expectation,
        status=CheckStatus.OK,
        explanation=explanation,
    )


def _warn(
    check_id: str,
    value_found: object,
    expectation: str,
    cause: str,
) -> VerifyCheck:
    return VerifyCheck(
        check_id=check_id,
        value_found=str(value_found),
        expectation=expectation,
        status=CheckStatus.WARN,
        explanation=(f"Found {value_found}; expected {expectation}; note: {cause}."),
    )


def _fail(
    check_id: str,
    value_found: object,
    expectation: str,
    cause: str,
) -> VerifyCheck:
    return VerifyCheck(
        check_id=check_id,
        value_found=str(value_found),
        expectation=expectation,
        status=CheckStatus.FAIL,
        explanation=(f"Found {value_found}; expected {expectation}; likely cause: {cause}."),
    )


def _found(value: object | None) -> str:
    return "missing" if value is None else str(value)


def _normalized(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def _fps_text(value: Fraction | None) -> str:
    if value is None:
        return "missing"
    return f"{value.numerator}/{value.denominator} ({float(value):.6f})"


def _one_line(value: str) -> str:
    return " ".join(value.split()) or "unknown error"


def _clip(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return f"{value[: maximum - 1]}…"


def _table_line(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return "  ".join(value.ljust(width) for value, width in zip(values, widths, strict=True))


__all__ = [
    "CheckStatus",
    "VerificationEvidence",
    "VerifyCheck",
    "VerifyExpectations",
    "VerifyReport",
    "VerifyVerdict",
    "evaluate_verification",
    "format_human_report",
    "format_json_report",
    "verify_file",
]
