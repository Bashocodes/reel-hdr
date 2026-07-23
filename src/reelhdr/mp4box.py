"""Read-only MP4Box inspection with bounded, typed evidence.

GPAC's human-readable output is useful corroborating evidence for a verifier,
but it is not a stable machine protocol.  This module consequently treats
missing and conflicting text as uncertainty instead of guessing.  Direct box
parsing remains the authoritative way to establish that a configuration atom
exists.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from reelhdr.tools import require_tool

MP4BOX_INFO_TIMEOUT_SECONDS = 15.0
MP4BOX_INFO_MAX_OUTPUT_BYTES = 1_000_000
_ERROR_DETAIL_CHARS = 500

_ANSI_ESCAPE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
_TRACK_HEADER = re.compile(
    r"^#\s*Track\s+\d+\s+Info(?:\s*-\s*ID\s+(?P<id>\d+))?",
    re.IGNORECASE,
)
_MEDIA_TYPE = re.compile(
    r"^\s*Media\s+Type\s*:\s*(?P<kind>[A-Za-z0-9 ]+)"
    r"(?:\s*:\s*(?P<entry>[A-Za-z0-9._-]+))?",
    re.IGNORECASE,
)
_DOLBY_MARKER = re.compile(r"\bDolby\s*Vision\b|\b(?:dvcC|dvvC)\b", re.IGNORECASE)
_DV_PROFILE = re.compile(r"\bprofile\s*[:=]?\s*(\d+)\b", re.IGNORECASE)
_DV_COMPATIBILITY = re.compile(
    r"\b(?:BL\s+)?compatibility(?:\s+(?:ID|identifier))?\s*[:=]?\s*(\d+)\b",
    re.IGNORECASE,
)
_MOVIE_DURATION = re.compile(r"^Duration\s+(?P<duration>\d+:\d+:\d+(?:\.\d+)?)\s*$")
_MEDIA_SAMPLES = re.compile(
    r"^\s*Media\s+Samples\s*:\s*(?P<count>\d+)"
    r"(?:\s*-\s*CFR\s+(?P<fps>\d+(?:\.\d+)?)/sec)?",
    re.IGNORECASE,
)
_SAMPLE_ENTRY_INFO = re.compile(
    r"^\s*(?:Visual|Audio)\s+Sample\s+Entry\s+Info\s*:\s*(?P<text>.+)$",
    re.IGNORECASE,
)
_CODEC_SUMMARY = re.compile(
    r"^\s*(?:HEVC\s+(?:Video\s*-\s*)?|HEVC\s+Info\s*:|"
    r"MPEG-\d+\s+Audio\b|AVC\s+Video\b)(?P<text>.*)$",
    re.IGNORECASE,
)


class EvidenceState(StrEnum):
    """How conclusively MP4Box text establishes an observation."""

    PRESENT = "present"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"


class MP4BoxInfoError(RuntimeError):
    """Structured failure raised when ``MP4Box -info`` cannot complete."""

    def __init__(
        self,
        message: str,
        *,
        argv: tuple[str, ...],
        returncode: int | None = None,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(message)
        self.argv = argv
        self.returncode = returncode
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class MP4BoxTrackInfo:
    """Evidence parsed from one ``# Track ...`` section."""

    track_id: int | None
    media_type: str | None
    sample_entry: str | None
    sample_entry_text: str | None
    codec_summary: str | None
    sample_count: int | None
    constant_frame_rate: float | None

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def is_audio(self) -> bool:
        return self.media_type == "audio"


@dataclass(frozen=True, slots=True)
class DolbyVisionTextEvidence:
    """Dolby Vision evidence inferred from MP4Box's descriptive text."""

    state: EvidenceState
    profile: int | None
    compatibility_id: int | None
    track_id: int | None
    source_lines: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MP4BoxInfo:
    """Bounded normalized result from a read-only MP4Box inspection."""

    path: Path
    tracks: tuple[MP4BoxTrackInfo, ...]
    movie_duration: float | None
    moov_before_mdat: bool | None
    dolby_vision: DolbyVisionTextEvidence
    output: str
    output_truncated: bool

    @property
    def video_tracks(self) -> tuple[MP4BoxTrackInfo, ...]:
        return tuple(track for track in self.tracks if track.is_video)

    @property
    def audio_tracks(self) -> tuple[MP4BoxTrackInfo, ...]:
        return tuple(track for track in self.tracks if track.is_audio)


@dataclass(slots=True)
class _TrackBuilder:
    track_id: int | None
    media_type: str | None = None
    sample_entry: str | None = None
    sample_entry_text: str | None = None
    codec_summary: str | None = None
    sample_count: int | None = None
    constant_frame_rate: float | None = None

    def freeze(self) -> MP4BoxTrackInfo:
        return MP4BoxTrackInfo(
            track_id=self.track_id,
            media_type=self.media_type,
            sample_entry=self.sample_entry,
            sample_entry_text=self.sample_entry_text,
            codec_summary=self.codec_summary,
            sample_count=self.sample_count,
            constant_frame_rate=self.constant_frame_rate,
        )


def inspect_mp4box(
    input_path: str | Path,
    *,
    mp4box_path: str | Path | None = None,
    timeout: float = MP4BOX_INFO_TIMEOUT_SECONDS,
    max_output_bytes: int = MP4BOX_INFO_MAX_OUTPUT_BYTES,
) -> MP4BoxInfo:
    """Run ``MP4Box -info`` without a shell and parse its bounded output.

    Standard output and error are merged because GPAC versions differ in which
    stream receives informational text.  A temporary file prevents an
    unexpectedly verbose process from consuming proportional Python memory;
    only ``max_output_bytes`` are retained.
    """

    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be at least 1")

    path = Path(input_path).expanduser()
    executable = str(mp4box_path) if mp4box_path is not None else require_tool("mp4box")
    argv = (executable, "-info", str(path))

    with tempfile.TemporaryFile(mode="w+b") as output_file:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise MP4BoxInfoError(
                f"MP4Box -info timed out after {timeout:g}s",
                argv=argv,
            ) from error
        except OSError as error:
            diagnostic = _one_line(str(error))
            raise MP4BoxInfoError(
                f"MP4Box -info could not start: {diagnostic}",
                argv=argv,
                diagnostic=diagnostic,
            ) from error

        output_file.seek(0)
        raw = output_file.read(max_output_bytes + 1)

    truncated = len(raw) > max_output_bytes
    retained = raw[:max_output_bytes]
    text = _normalize_output(retained)
    if completed.returncode != 0:
        diagnostic = _one_line(text or "no diagnostic output")
        raise MP4BoxInfoError(
            f"MP4Box -info exited {completed.returncode}: {diagnostic}",
            argv=argv,
            returncode=completed.returncode,
            diagnostic=diagnostic,
        )
    return parse_mp4box_info(text, path=path, output_truncated=truncated)


def parse_mp4box_info(
    output: str,
    *,
    path: str | Path,
    output_truncated: bool = False,
) -> MP4BoxInfo:
    """Parse recorded ``MP4Box -info`` text without invoking external tools."""

    normalized = _normalize_output(output.encode())
    tracks: list[_TrackBuilder] = []
    current: _TrackBuilder | None = None
    movie_duration: float | None = None
    moov_before_mdat: bool | None = None
    dv_mentions: list[tuple[int | None, str]] = []

    for line in normalized.splitlines():
        header = _TRACK_HEADER.match(line)
        if header:
            current = _TrackBuilder(track_id=_optional_int(header.group("id")))
            tracks.append(current)
            continue

        if movie_duration is None:
            duration_match = _MOVIE_DURATION.match(line)
            if duration_match:
                movie_duration = _parse_timestamp(duration_match.group("duration"))

        folded = line.casefold()
        if "progressive (moov before mdat)" in folded:
            moov_before_mdat = True
        elif "moov after mdat" in folded or folded.startswith("not progressive"):
            moov_before_mdat = False

        if _DOLBY_MARKER.search(line):
            dv_mentions.append((current.track_id if current else None, line.strip()))

        if current is None:
            continue

        media_match = _MEDIA_TYPE.match(line)
        if media_match:
            kind = media_match.group("kind").strip().casefold()
            current.media_type = _normalize_media_type(kind)
            entry = media_match.group("entry")
            current.sample_entry = entry.casefold() if entry else None
            continue

        entry_match = _SAMPLE_ENTRY_INFO.match(line)
        if entry_match:
            current.sample_entry_text = entry_match.group("text").strip()
            continue

        samples_match = _MEDIA_SAMPLES.match(line)
        if samples_match:
            current.sample_count = int(samples_match.group("count"))
            fps = samples_match.group("fps")
            current.constant_frame_rate = float(fps) if fps else None
            continue

        codec_match = _CODEC_SUMMARY.match(line)
        if codec_match:
            current.codec_summary = line.strip()

    frozen_tracks = tuple(track.freeze() for track in tracks)
    return MP4BoxInfo(
        path=Path(path),
        tracks=frozen_tracks,
        movie_duration=movie_duration,
        moov_before_mdat=moov_before_mdat,
        dolby_vision=_parse_dolby_evidence(dv_mentions, output_truncated),
        output=normalized,
        output_truncated=output_truncated,
    )


def _parse_dolby_evidence(
    mentions: list[tuple[int | None, str]],
    output_truncated: bool,
) -> DolbyVisionTextEvidence:
    profiles: set[int] = set()
    compatibilities: set[int] = set()
    track_ids: set[int] = set()
    incomplete = False

    for track_id, line in mentions:
        if track_id is not None:
            track_ids.add(track_id)
        profile_match = _DV_PROFILE.search(line)
        compatibility_match = _DV_COMPATIBILITY.search(line)
        if profile_match:
            profiles.add(int(profile_match.group(1)))
        if compatibility_match:
            compatibilities.add(int(compatibility_match.group(1)))
        if profile_match is None or compatibility_match is None:
            incomplete = True

    issues: list[str] = []
    if len(profiles) > 1:
        issues.append("MP4Box reported conflicting Dolby Vision profiles")
    if len(compatibilities) > 1:
        issues.append("MP4Box reported conflicting base-layer compatibility identifiers")
    if len(track_ids) > 1:
        issues.append("Dolby Vision evidence spans multiple tracks")
    if incomplete:
        issues.append("a Dolby Vision/configuration line lacked profile or compatibility data")
    if output_truncated:
        issues.append("MP4Box output was truncated")

    profile = next(iter(profiles)) if len(profiles) == 1 else None
    compatibility = next(iter(compatibilities)) if len(compatibilities) == 1 else None
    track_id = next(iter(track_ids)) if len(track_ids) == 1 else None

    if not mentions and not output_truncated:
        state = EvidenceState.ABSENT
    elif issues or not mentions:
        state = EvidenceState.AMBIGUOUS
    else:
        state = EvidenceState.PRESENT

    return DolbyVisionTextEvidence(
        state=state,
        profile=profile,
        compatibility_id=compatibility,
        track_id=track_id,
        source_lines=tuple(line for _, line in mentions),
        issues=tuple(issues),
    )


def _normalize_output(raw: bytes) -> str:
    return _ANSI_ESCAPE.sub(b"", raw).decode("utf-8", errors="replace").replace("\r\n", "\n")


def _normalize_media_type(value: str) -> str:
    if value in {"vide", "video"}:
        return "video"
    if value in {"soun", "audio", "sound"}:
        return "audio"
    return value


def _parse_timestamp(value: str) -> float | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _one_line(value: str) -> str:
    return " ".join(value.split())[:_ERROR_DETAIL_CHARS]
