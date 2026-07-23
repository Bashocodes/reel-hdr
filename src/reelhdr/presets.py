"""Typed conversion presets and explicit command-line override resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

DEFAULT_MAX_PQ = 2500
DEFAULT_CRF = 18
DEFAULT_FPS_POLICY = "passthrough"
_BITRATE = re.compile(r"^[1-9][0-9]*(?:[kKmMgG])?$")


class PresetName(StrEnum):
    """Stable names accepted by the public CLI."""

    INSTAGRAM_DV84 = "instagram-dv84"
    HLG = "hlg"


@dataclass(frozen=True, slots=True)
class Preset:
    """One user-facing pipeline configuration."""

    name: PresetName
    description: str
    dolby_vision: bool
    amve: bool
    max_pq: int = DEFAULT_MAX_PQ
    crf: int = DEFAULT_CRF
    fps: str = DEFAULT_FPS_POLICY


@dataclass(frozen=True, slots=True)
class ResolvedOptions:
    """A preset after command-line values have taken precedence."""

    preset: Preset
    max_pq: int
    crf: int | None
    bitrate: str | None
    fps: str


PRESETS: tuple[Preset, ...] = (
    Preset(
        name=PresetName.INSTAGRAM_DV84,
        description="HEVC Main 10 HLG + Dolby Vision 8.4 compatible signalling + amve",
        dolby_vision=True,
        amve=True,
    ),
    Preset(
        name=PresetName.HLG,
        description="Clean HEVC Main 10 HLG MP4; no Dolby Vision RPU/config or amve",
        dolby_vision=False,
        amve=False,
    ),
)
DEFAULT_PRESET = PresetName.INSTAGRAM_DV84
_BY_NAME = {preset.name: preset for preset in PRESETS}


def get_preset(name: str | PresetName) -> Preset:
    """Return one preset or raise a concise user-facing error."""

    try:
        normalized = PresetName(name)
        return _BY_NAME[normalized]
    except (ValueError, KeyError) as error:
        choices = ", ".join(preset.name.value for preset in PRESETS)
        raise ValueError(f"unknown preset {name!r}; choose one of: {choices}") from error


def resolve_options(
    preset: str | PresetName = DEFAULT_PRESET,
    *,
    max_pq: int | None = None,
    crf: int | None = None,
    bitrate: str | None = None,
    fps: str | None = None,
) -> ResolvedOptions:
    """Resolve defaults, with every explicit flag taking precedence."""

    definition = get_preset(preset)
    if crf is not None and bitrate is not None:
        raise ValueError("--crf and --bitrate are mutually exclusive")

    resolved_crf = definition.crf if crf is None and bitrate is None else crf
    if resolved_crf is not None and not 0 <= resolved_crf <= 51:
        raise ValueError("--crf must be an integer from 0 through 51")

    if bitrate is not None and _BITRATE.fullmatch(bitrate) is None:
        raise ValueError("--bitrate must be a positive FFmpeg rate such as 12M or 8000k")

    resolved_fps = definition.fps if fps is None else fps.strip().casefold()
    parse_fps(resolved_fps)

    resolved_max_pq = definition.max_pq if max_pq is None else max_pq
    if not 0 <= resolved_max_pq <= 4095:
        raise ValueError("--max-pq must be an integer from 0 through 4095")

    return ResolvedOptions(
        preset=definition,
        max_pq=resolved_max_pq,
        crf=resolved_crf,
        bitrate=bitrate,
        fps=resolved_fps,
    )


def parse_fps(value: str) -> Fraction | None:
    """Parse ``passthrough``, a decimal, or a rational frame rate."""

    normalized = value.strip().casefold()
    if normalized == DEFAULT_FPS_POLICY:
        return None
    try:
        rate = Fraction(normalized)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(
            "--fps must be passthrough, a number, or a fraction such as 30000/1001"
        ) from error
    if rate <= 0 or rate > 240:
        raise ValueError("--fps must be greater than zero and no more than 240")
    return rate


def format_preset_table() -> str:
    """Return a stable table for ``reelhdr presets``."""

    headers = ("PRESET", "DEFAULT", "VIDEO", "DV / AMVE")
    rows = [
        (
            preset.name.value,
            "yes" if preset.name is DEFAULT_PRESET else "",
            f"HEVC Main 10 HLG, CRF {preset.crf}, fps {preset.fps}",
            "DV8.4 compatible signalling + amve" if preset.dolby_vision else "none (HLG-only)",
        )
        for preset in PRESETS
    ]
    widths = tuple(
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    )

    def line(values: tuple[str, ...]) -> str:
        return "  ".join(
            value.ljust(width) for value, width in zip(values, widths, strict=True)
        ).rstrip()

    return "\n".join(
        (
            line(headers),
            line(tuple("-" * width for width in widths)),
            *(line(row) for row in rows),
        )
    )


__all__ = [
    "DEFAULT_CRF",
    "DEFAULT_FPS_POLICY",
    "DEFAULT_MAX_PQ",
    "DEFAULT_PRESET",
    "PRESETS",
    "Preset",
    "PresetName",
    "ResolvedOptions",
    "format_preset_table",
    "get_preset",
    "parse_fps",
    "resolve_options",
]
