from fractions import Fraction

import pytest

from reelhdr.presets import (
    DEFAULT_PRESET,
    PresetName,
    format_preset_table,
    parse_fps,
    resolve_options,
)


def test_default_preset_resolves_to_instagram_dv84() -> None:
    options = resolve_options()

    assert options.preset.name is DEFAULT_PRESET
    assert options.preset.dolby_vision is True
    assert options.preset.amve is True
    assert options.max_pq == 2500
    assert options.crf == 18
    assert options.bitrate is None
    assert options.fps == "passthrough"


def test_hlg_preset_omits_dolby_vision_and_amve() -> None:
    options = resolve_options(PresetName.HLG)

    assert options.preset.dolby_vision is False
    assert options.preset.amve is False


def test_explicit_flags_take_precedence_over_preset_defaults() -> None:
    options = resolve_options(
        "instagram-dv84",
        max_pq=2200,
        crf=14,
        fps="30000/1001",
    )

    assert options.max_pq == 2200
    assert options.crf == 14
    assert options.fps == "30000/1001"


def test_bitrate_replaces_the_default_crf() -> None:
    options = resolve_options(bitrate="12M")

    assert options.crf is None
    assert options.bitrate == "12M"


def test_invalid_or_conflicting_overrides_are_rejected() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_options(crf=18, bitrate="12M")
    with pytest.raises(ValueError, match="positive FFmpeg rate"):
        resolve_options(bitrate="quick")
    with pytest.raises(ValueError, match="0 through 51"):
        resolve_options(crf=52)


def test_fps_accepts_passthrough_decimal_and_rational_values() -> None:
    assert parse_fps("passthrough") is None
    assert parse_fps("29.97") == Fraction(2997, 100)
    assert parse_fps("30000/1001") == Fraction(30000, 1001)


def test_preset_table_marks_the_default_and_describes_both_outputs() -> None:
    table = format_preset_table()

    assert "instagram-dv84" in table
    assert "HLG-only" in table
    assert "DV8.4 compatible signalling + amve" in table
