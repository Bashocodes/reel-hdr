from __future__ import annotations

import pytest

from reelhdr.amve import IsoBmffError, read_iso_bmff_evidence


def _box(box_type: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _full_box(box_type: bytes, payload: bytes = b"") -> bytes:
    return _box(box_type, b"\0\0\0\0" + payload)


def _fixture(
    *,
    config_type: bytes | None = b"dvvC",
    config_payload: bytes = bytes((1, 0, 8 << 1, 0b00000101, 4 << 4)),
    raw_config_box: bytes | None = None,
    include_amve: bool = True,
    moov_after_mdat: bool = False,
) -> bytes:
    children = _box(b"hvcC", b"decoder-configuration")
    if raw_config_box is not None:
        children += raw_config_box
    elif config_type is not None:
        children += _box(config_type, config_payload)
    if include_amve:
        children += _box(b"amve", b"\0\0\0\1\0\2\0\3")

    sample_entry = _box(b"dvh1", bytes(78) + children)
    stsd = _full_box(b"stsd", (1).to_bytes(4, "big") + sample_entry)
    stbl = _box(b"stbl", stsd)
    minf = _box(b"minf", stbl)
    hdlr = _full_box(b"hdlr", b"\0\0\0\0vide" + bytes(12))
    mdia = _box(b"mdia", hdlr + minf)
    moov = _box(b"moov", _box(b"trak", mdia))

    ftyp = _box(b"ftyp", b"isom\0\0\0\0isom")
    mdat = _box(b"mdat", b"media payload")
    return ftyp + (mdat + moov if moov_after_mdat else moov + mdat)


@pytest.mark.parametrize("config_type", [b"dvcC", b"dvvC"])
def test_reader_extracts_dolby_vision_and_container_evidence(
    config_type: bytes,
) -> None:
    source = _fixture(config_type=config_type)

    evidence = read_iso_bmff_evidence(source)

    assert evidence.top_level_box_order == ("ftyp", "moov", "mdat")
    assert tuple((box.offset, box.size) for box in evidence.top_level_boxes) == (
        (0, 20),
        (20, len(source) - 20 - 21),
        (len(source) - 21, 21),
    )
    assert evidence.moov_offset == 20
    assert evidence.mdat_offset == len(source) - 21
    assert evidence.fast_start is True
    assert evidence.first_video_sample_entry_type == "dvh1"
    assert evidence.amve_present is True

    config = evidence.dolby_vision_config
    assert config is not None
    assert config.box_type == config_type.decode()
    assert config.offset > evidence.moov_offset
    assert config.payload == bytes((1, 0, 16, 5, 64))
    assert config.profile == 8
    assert config.bl_signal_compatibility_id == 4


def test_reader_reports_missing_config_without_scanning_media_payload() -> None:
    source = _fixture(config_type=None, include_amve=False) + _box(
        b"free", b"dvcC and dvvC are not child boxes here"
    )

    evidence = read_iso_bmff_evidence(source)

    assert evidence.dolby_vision_config is None
    assert evidence.amve_present is False


def test_reader_returns_wrong_profile_and_compatibility_as_evidence() -> None:
    payload = bytes((1, 0, 9 << 1, 0, 2 << 4))

    config = read_iso_bmff_evidence(_fixture(config_payload=payload)).dolby_vision_config

    assert config is not None
    assert config.profile == 9
    assert config.bl_signal_compatibility_id == 2


def test_reader_reports_non_fast_start_top_level_order() -> None:
    evidence = read_iso_bmff_evidence(_fixture(moov_after_mdat=True))

    assert evidence.top_level_box_order == ("ftyp", "mdat", "moov")
    assert evidence.fast_start is False
    assert evidence.mdat_offset is not None
    assert evidence.moov_offset is not None
    assert evidence.mdat_offset < evidence.moov_offset


def test_reader_keeps_short_config_fields_unknown() -> None:
    config = read_iso_bmff_evidence(_fixture(config_payload=b"\1\0")).dolby_vision_config

    assert config is not None
    assert config.payload == b"\1\0"
    assert config.profile is None
    assert config.bl_signal_compatibility_id is None


def test_reader_rejects_truncated_config_with_controlled_error() -> None:
    complete = _box(b"dvvC", bytes((1, 0, 16, 5, 64)))
    source = _fixture(raw_config_box=complete[:-1], include_amve=False)

    with pytest.raises(IsoBmffError, match="truncated 'dvvC' box"):
        read_iso_bmff_evidence(source)


def test_reader_rejects_absurd_declared_size_with_controlled_error() -> None:
    source = (1 << 31).to_bytes(4, "big") + b"moov"

    with pytest.raises(IsoBmffError, match="truncated 'moov' box"):
        read_iso_bmff_evidence(source)


def test_reader_enforces_global_box_count_budget() -> None:
    source = _box(b"free") * 10_001

    with pytest.raises(IsoBmffError, match="box count exceeds 10000"):
        read_iso_bmff_evidence(source)
