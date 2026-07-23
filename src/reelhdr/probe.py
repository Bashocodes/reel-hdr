"""Structured video probing and source-transfer classification.

The module deliberately consumes ffprobe's JSON output instead of scraping its
human-readable text.  It keeps frame rates rational and treats absent optional
metadata as unknown rather than inventing values.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any

from reelhdr.tools import require_tool

PROBE_TIMEOUT_SECONDS = 15.0
_MAX_ERROR_DETAIL_CHARS = 500
_DOLBY_VISION_SAMPLE_ENTRIES = frozenset({"dvav", "dva1", "dvhe", "dvh1"})
_PQ_TRANSFERS = frozenset({"16", "pq", "smpte2084", "smpte-st-2084", "st2084"})
_HLG_TRANSFERS = frozenset({"18", "arib-std-b67", "arib_std_b67", "hlg"})
_HIGH_BIT_DEPTH_PIX_FMT = re.compile(r"p0?(9|10|12|14|16)(?:le|be|$)")
_EIGHT_BIT_PLANAR_PIX_FMT = re.compile(r"^(?:yuvj?|yuva|gbr|gray)\d*p$")


class SourceClass(StrEnum):
    """Transfer-family classification used to select a conversion path."""

    SDR = "sdr"
    HLG = "hlg"
    PQ = "pq"
    DOLBY_VISION = "dolby-vision"


class ProbeError(RuntimeError):
    """Raised when ffprobe fails or returns an unusable response."""


@dataclass(frozen=True, slots=True)
class VideoProbe:
    """Normalized evidence for the first video stream in an input file."""

    path: Path
    source_class: SourceClass
    codec_name: str | None
    codec_profile: str | None
    codec_tag_string: str | None
    width: int | None
    height: int | None
    fps: Fraction | None
    frame_count: int | None
    bit_depth: int | None
    duration: float | None
    has_audio: bool
    pix_fmt: str | None
    color_range: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    dv_profile: int | None = None
    dv_level: int | None = None
    dv_bl_signal_compatibility_id: int | None = None
    has_dolby_vision_rpu: bool = False
    stream_tags: tuple[tuple[str, str], ...] = ()

    @property
    def resolution(self) -> tuple[int, int] | None:
        """Return ``(width, height)`` when both dimensions are known."""

        if self.width is None or self.height is None:
            return None
        return self.width, self.height

    @property
    def fps_float(self) -> float | None:
        """Return a display-friendly floating-point frame rate."""

        return float(self.fps) if self.fps is not None else None

    @property
    def tags(self) -> dict[str, str]:
        """Return a mutable copy of the normalized stream tags."""

        return dict(self.stream_tags)


def probe_video(
    input_path: str | Path,
    *,
    ffprobe_path: str | Path | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> VideoProbe:
    """Run ffprobe without a shell and normalize its first video stream.

    ``ffprobe_path`` is injectable for callers and tests.  When omitted, the
    executable is resolved through the shared external-tools layer.
    """

    source_path = Path(input_path).expanduser()
    executable = str(ffprobe_path) if ffprobe_path is not None else require_tool("ffprobe")
    argv = [
        executable,
        "-v",
        "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        "-i",
        str(source_path),
    ]

    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeError(f"ffprobe timed out after {timeout:g}s") from error
    except OSError as error:
        raise ProbeError(f"ffprobe could not start: {_one_line(str(error))}") from error

    if completed.returncode != 0:
        detail = _one_line(completed.stderr or completed.stdout or "no diagnostic output")
        raise ProbeError(f"ffprobe exited {completed.returncode}: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ProbeError("ffprobe returned invalid JSON") from error

    if not isinstance(payload, Mapping):
        raise ProbeError("ffprobe JSON root must be an object")
    return parse_probe_json(payload, path=source_path)


def parse_probe_json(payload: Mapping[str, Any], *, path: str | Path) -> VideoProbe:
    """Normalize a recorded ffprobe JSON object.

    This parsing entry point is public so conformance tests can use text
    fixtures without shipping media or invoking external tools.
    """

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ProbeError("ffprobe JSON does not contain a streams array")

    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise ProbeError("ffprobe found no video stream")

    format_data = payload.get("format")
    if not isinstance(format_data, Mapping):
        format_data = {}

    codec_tag = _optional_string(video_stream.get("codec_tag_string"))
    codec_profile = _optional_string(video_stream.get("profile"))
    color_transfer = _optional_string(video_stream.get("color_transfer"))
    side_data = _side_data(video_stream)
    dv_profile = _first_side_data_int(side_data, "dv_profile")
    dv_level = _first_side_data_int(side_data, "dv_level")
    dv_compatibility = _first_side_data_int(
        side_data,
        "dv_bl_signal_compatibility_id",
    )
    has_rpu = any(_truthy(item.get("rpu_present_flag")) for item in side_data)
    has_dolby_evidence = _has_dolby_vision_evidence(
        side_data=side_data,
        codec_tag=codec_tag,
        codec_profile=codec_profile,
    )

    tags = video_stream.get("tags")
    normalized_tags = ()
    if isinstance(tags, Mapping):
        normalized_tags = tuple(
            sorted((str(key), str(value)) for key, value in tags.items() if value is not None)
        )

    return VideoProbe(
        path=Path(path),
        source_class=_classify_transfer(
            color_transfer,
            has_dolby_evidence=has_dolby_evidence,
        ),
        codec_name=_optional_string(video_stream.get("codec_name")),
        codec_profile=codec_profile,
        codec_tag_string=codec_tag,
        width=_optional_positive_int(video_stream.get("width")),
        height=_optional_positive_int(video_stream.get("height")),
        fps=_parse_frame_rate(
            video_stream.get("avg_frame_rate"),
            video_stream.get("r_frame_rate"),
        ),
        frame_count=(
            _optional_nonnegative_int(video_stream.get("nb_read_frames"))
            or _optional_nonnegative_int(video_stream.get("nb_frames"))
        ),
        bit_depth=_parse_bit_depth(video_stream),
        duration=_parse_duration(video_stream, format_data),
        has_audio=any(
            isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
            for stream in streams
        ),
        pix_fmt=_optional_string(video_stream.get("pix_fmt")),
        color_range=_optional_string(video_stream.get("color_range")),
        color_space=_optional_string(video_stream.get("color_space")),
        color_transfer=color_transfer,
        color_primaries=_optional_string(video_stream.get("color_primaries")),
        dv_profile=dv_profile,
        dv_level=dv_level,
        dv_bl_signal_compatibility_id=dv_compatibility,
        has_dolby_vision_rpu=has_rpu,
        stream_tags=normalized_tags,
    )


def _classify_transfer(
    color_transfer: str | None,
    *,
    has_dolby_evidence: bool,
) -> SourceClass:
    if has_dolby_evidence:
        return SourceClass.DOLBY_VISION

    normalized = color_transfer.casefold().strip() if color_transfer else ""
    if normalized in _PQ_TRANSFERS:
        return SourceClass.PQ
    if normalized in _HLG_TRANSFERS:
        return SourceClass.HLG
    return SourceClass.SDR


def _has_dolby_vision_evidence(
    *,
    side_data: tuple[Mapping[str, Any], ...],
    codec_tag: str | None,
    codec_profile: str | None,
) -> bool:
    if codec_tag and codec_tag.casefold() in _DOLBY_VISION_SAMPLE_ENTRIES:
        return True
    if codec_profile and "dolby vision" in codec_profile.casefold():
        return True

    for item in side_data:
        side_data_type = _optional_string(item.get("side_data_type"))
        if side_data_type:
            normalized_type = side_data_type.casefold()
            if "dovi" in normalized_type or "dolby vision" in normalized_type:
                return True
        if any(
            key in item
            for key in (
                "dv_profile",
                "dv_version_major",
                "dv_bl_signal_compatibility_id",
                "rpu_present_flag",
            )
        ):
            return True
    return False


def _side_data(stream: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = stream.get("side_data_list")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _first_side_data_int(
    side_data: tuple[Mapping[str, Any], ...],
    key: str,
) -> int | None:
    for item in side_data:
        value = _optional_nonnegative_int(item.get(key))
        if value is not None:
            return value
    return None


def _parse_frame_rate(*candidates: Any) -> Fraction | None:
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = Fraction(str(candidate))
        except (ValueError, ZeroDivisionError):
            continue
        if value > 0:
            return value
    return None


def _parse_bit_depth(stream: Mapping[str, Any]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = _optional_positive_int(stream.get(key))
        if value is not None:
            return value

    pix_fmt = _optional_string(stream.get("pix_fmt"))
    if pix_fmt:
        match = _HIGH_BIT_DEPTH_PIX_FMT.search(pix_fmt.casefold())
        if match:
            return int(match.group(1))
        if _EIGHT_BIT_PLANAR_PIX_FMT.fullmatch(pix_fmt.casefold()):
            return 8
        if pix_fmt.casefold() in {"bgr24", "bgra", "gray", "nv12", "nv21", "rgb24", "rgba"}:
            return 8

    profile = _optional_string(stream.get("profile"))
    if profile:
        match = re.search(r"\b(?:main\s*)?(10|12)\b", profile.casefold())
        if match:
            return int(match.group(1))
    return None


def _parse_duration(
    stream: Mapping[str, Any],
    format_data: Mapping[str, Any],
) -> float | None:
    duration = _optional_nonnegative_float(stream.get("duration"))
    if duration is not None:
        return duration

    duration_ts = _optional_nonnegative_int(stream.get("duration_ts"))
    time_base = _parse_frame_rate(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return float(duration_ts * time_base)

    return _optional_nonnegative_float(format_data.get("duration"))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_positive_int(value: Any) -> int | None:
    parsed = _optional_nonnegative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _optional_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return bool(value)


def _one_line(value: str) -> str:
    detail = " ".join(value.split()) or "no diagnostic output"
    if len(detail) > _MAX_ERROR_DETAIL_CHARS:
        return f"{detail[:_MAX_ERROR_DETAIL_CHARS]}…"
    return detail
