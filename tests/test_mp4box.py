from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reelhdr.mp4box import (
    EvidenceState,
    MP4BoxInfoError,
    inspect_mp4box,
    parse_mp4box_info,
)

PASSING_INFO = """\
# Movie Info - 2 tracks - TimeScale 600
Duration 00:00:01.000
Fragmented: no
Progressive (moov before mdat)

# Track 1 Info - ID 1 - TimeScale 10
Media Duration 00:00:01.000
Media Samples: 10 - CFR 10/sec
Media Type: vide:hvc1
\tVisual Sample Entry Info: width=64 height=64 (depth=24 bits)
\tDolbyVision version 1.0 profile 8 level 1 \
(RPU: 1 Base Layer: 1 Enhancement Layer: 0 Compatibility: 4)
\tHEVC Info: Profile Main 10 @ Level 1 - Chroma Format YUV 4:2:0

# Track 2 Info - ID 2 - TimeScale 48000
Media Samples: 48
Media Type: soun:mp4a
\tMPEG-4 Audio AAC LC (AOT=2 implicit) - 1 Channel(s) - SampleRate 48000
"""


def test_parse_mp4box_info_extracts_tracks_and_dolby_evidence() -> None:
    result = parse_mp4box_info(PASSING_INFO, path="passing.mp4")

    assert result.path == Path("passing.mp4")
    assert result.movie_duration == 1.0
    assert result.moov_before_mdat is True
    assert len(result.video_tracks) == 1
    assert len(result.audio_tracks) == 1

    video = result.video_tracks[0]
    assert video.track_id == 1
    assert video.sample_entry == "hvc1"
    assert video.sample_entry_text == "width=64 height=64 (depth=24 bits)"
    assert video.codec_summary == ("HEVC Info: Profile Main 10 @ Level 1 - Chroma Format YUV 4:2:0")
    assert video.sample_count == 10
    assert video.constant_frame_rate == 10.0

    audio = result.audio_tracks[0]
    assert audio.track_id == 2
    assert audio.sample_entry == "mp4a"
    assert "AAC LC" in (audio.codec_summary or "")

    dolby = result.dolby_vision
    assert dolby.state is EvidenceState.PRESENT
    assert dolby.profile == 8
    assert dolby.compatibility_id == 4
    assert dolby.track_id == 1
    assert dolby.issues == ()


def test_parse_absent_dolby_evidence_is_explicit() -> None:
    output = """\
# Movie Info - 1 tracks - TimeScale 1000
Duration 00:00:02.000
Not Progressive
# Track 1 Info - ID 7
Media Type: vide:hvc1
\tHEVC Info: Profile Main 10 @ Level 4
"""

    result = parse_mp4box_info(output, path="plain.mp4")

    assert result.moov_before_mdat is False
    assert result.dolby_vision.state is EvidenceState.ABSENT
    assert result.dolby_vision.profile is None
    assert result.dolby_vision.compatibility_id is None
    assert result.dolby_vision.source_lines == ()


def test_conflicting_dolby_text_is_ambiguous() -> None:
    output = """\
# Track 1 Info - ID 1
Media Type: vide:dvh1
DolbyVision version 1.0 profile 8 level 1 (Compatibility: 4)
DolbyVision version 1.0 profile 5 level 1 (Compatibility: 1)
"""

    result = parse_mp4box_info(output, path="conflict.mp4")

    assert result.dolby_vision.state is EvidenceState.AMBIGUOUS
    assert result.dolby_vision.profile is None
    assert result.dolby_vision.compatibility_id is None
    assert "conflicting Dolby Vision profiles" in result.dolby_vision.issues[0]


@pytest.mark.parametrize(
    "output",
    [
        "# Track 1 Info - ID 1\nMedia Type: vide:dvh1\nDolbyVision profile 8\n",
        "# Track 1 Info - ID 1\nMedia Type: vide:dvh1\nFound dvvC configuration\n",
    ],
)
def test_partial_dolby_evidence_is_ambiguous(output: str) -> None:
    result = parse_mp4box_info(output, path="partial.mp4")

    assert result.dolby_vision.state is EvidenceState.AMBIGUOUS
    assert result.dolby_vision.issues


def test_truncated_output_cannot_prove_dolby_absent() -> None:
    result = parse_mp4box_info(
        "# Movie Info - 1 tracks\n",
        path="truncated.mp4",
        output_truncated=True,
    )

    assert result.output_truncated is True
    assert result.dolby_vision.state is EvidenceState.AMBIGUOUS
    assert result.dolby_vision.issues == ("MP4Box output was truncated",)


def test_inspect_invokes_injected_mp4box_without_shell_and_bounds_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        argv: tuple[str, ...],
        *,
        check: bool,
        stdout: object,
        stderr: int,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update(
            argv=argv,
            check=check,
            stderr=stderr,
            timeout=timeout,
        )
        stdout.write(PASSING_INFO.encode() + b"x" * 200)  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("reelhdr.mp4box.subprocess.run", fake_run)

    result = inspect_mp4box(
        "clip.mp4",
        mp4box_path="/tools/MP4Box",
        timeout=3,
        max_output_bytes=len(PASSING_INFO.encode()),
    )

    assert observed == {
        "argv": ("/tools/MP4Box", "-info", "clip.mp4"),
        "check": False,
        "stderr": subprocess.STDOUT,
        "timeout": 3,
    }
    assert len(result.output.encode()) <= len(PASSING_INFO.encode())
    assert result.output_truncated is True
    # The retained text contains a valid line, but truncation remains explicit.
    assert result.dolby_vision.state is EvidenceState.AMBIGUOUS


def test_inspect_uses_shared_tool_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("reelhdr.mp4box.require_tool", lambda key: "/resolved/MP4Box")
    observed: list[tuple[str, ...]] = []

    def fake_run(
        argv: tuple[str, ...],
        *,
        check: bool,
        stdout: object,
        stderr: int,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, stderr, timeout
        observed.append(argv)
        stdout.write(PASSING_INFO.encode())  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("reelhdr.mp4box.subprocess.run", fake_run)

    inspect_mp4box("clip.mp4")

    assert observed == [("/resolved/MP4Box", "-info", "clip.mp4")]


def test_nonzero_exit_raises_structured_bounded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: tuple[str, ...],
        *,
        check: bool,
        stdout: object,
        stderr: int,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, stderr, timeout
        stdout.write(b"cannot open input\n" + b"x" * 1_000)  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(argv, 2)

    monkeypatch.setattr("reelhdr.mp4box.subprocess.run", fake_run)

    with pytest.raises(MP4BoxInfoError) as caught:
        inspect_mp4box(
            "missing.mp4",
            mp4box_path="/tools/MP4Box",
            max_output_bytes=64,
        )

    error = caught.value
    assert error.argv == ("/tools/MP4Box", "-info", "missing.mp4")
    assert error.returncode == 2
    assert error.diagnostic is not None
    assert len(error.diagnostic) <= 64


def test_timeout_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise subprocess.TimeoutExpired(("/tools/MP4Box", "-info"), 2)

    monkeypatch.setattr("reelhdr.mp4box.subprocess.run", fake_run)

    with pytest.raises(MP4BoxInfoError) as caught:
        inspect_mp4box("slow.mp4", mp4box_path="/tools/MP4Box", timeout=2)

    assert caught.value.returncode is None
    assert "timed out after 2s" in str(caught.value)
