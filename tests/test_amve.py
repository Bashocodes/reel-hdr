from __future__ import annotations

from collections.abc import Iterator

import pytest

from reelhdr.amve import IsoBmffError, encode_amve_payload, insert_amve


def _box(box_type: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _full_box(box_type: bytes, payload: bytes = b"") -> bytes:
    return _box(box_type, b"\0\0\0\0" + payload)


def _video_track(chunk_offset: int, *, amve_payloads: tuple[bytes, ...] = ()) -> bytes:
    visual_prefix = bytes(range(78))
    sample_children = _box(b"hvcC", b"configuration")
    sample_children += b"".join(_box(b"amve", payload) for payload in amve_payloads)
    sample_entry = _box(b"hvc1", visual_prefix + sample_children)
    stsd = _full_box(b"stsd", (1).to_bytes(4, "big") + sample_entry)
    stco = _full_box(b"stco", (1).to_bytes(4, "big") + chunk_offset.to_bytes(4, "big"))
    stbl = _box(b"stbl", stsd + stco)
    minf = _box(b"minf", _box(b"vmhd", b"keep-me") + stbl)
    hdlr = _full_box(b"hdlr", b"\0\0\0\0vide" + b"\0" * 12)
    mdia = _box(b"mdia", hdlr + minf)
    return _box(b"trak", _box(b"free", b"track-padding") + mdia)


def _fixture(*, amve_payloads: tuple[bytes, ...] = ()) -> bytes:
    ftyp = _box(b"ftyp", b"isom\0\0\0\0isom")
    mdat = _box(b"mdat", b"synthetic-media-bytes")
    placeholder_moov = _box(
        b"moov", _box(b"mvhd", b"movie-header") + _video_track(0, amve_payloads=amve_payloads)
    )
    chunk_offset = len(ftyp) + len(placeholder_moov) + 8
    moov = _box(
        b"moov",
        _box(b"mvhd", b"movie-header") + _video_track(chunk_offset, amve_payloads=amve_payloads),
    )
    return ftyp + moov + mdat


def _boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[int, bytes, int]]:
    limit = len(data) if end is None else end
    cursor = start
    while cursor < limit:
        size = int.from_bytes(data[cursor : cursor + 4], "big")
        yield cursor, data[cursor + 4 : cursor + 8], size
        cursor += size
    assert cursor == limit


def _child(
    data: bytes,
    parent: tuple[int, bytes, int],
    box_type: bytes,
    *,
    prefix: int = 0,
) -> tuple[int, bytes, int]:
    start, _, size = parent
    return next(box for box in _boxes(data, start + 8 + prefix, start + size) if box[1] == box_type)


def _path(data: bytes) -> dict[bytes, tuple[int, bytes, int]]:
    top = {box_type: box for box in _boxes(data) for box_type in [box[1]]}
    moov = top[b"moov"]
    trak = _child(data, moov, b"trak")
    mdia = _child(data, trak, b"mdia")
    minf = _child(data, mdia, b"minf")
    stbl = _child(data, minf, b"stbl")
    stsd = _child(data, stbl, b"stsd")
    hvc1 = _child(data, stsd, b"hvc1", prefix=8)
    return {
        b"moov": moov,
        b"trak": trak,
        b"mdia": mdia,
        b"minf": minf,
        b"stbl": stbl,
        b"stsd": stsd,
        b"hvc1": hvc1,
    }


def _chunk_offset(data: bytes) -> int:
    path = _path(data)
    stco = _child(data, path[b"stbl"], b"stco")
    start = stco[0]
    return int.from_bytes(data[start + 16 : start + 20], "big")


def test_insert_amve_inside_hevc_sample_entry_and_update_sizes() -> None:
    original = _fixture()
    original_path = _path(original)
    original_chunk_offset = _chunk_offset(original)
    payload = b"\x00\x00\x12\x34\x56\x78\x9a\xbc"

    result = insert_amve(original, payload)

    assert len(result) == len(original) + 16
    result_path = _path(result)
    for box_type, original_box in original_path.items():
        assert result_path[box_type][2] == original_box[2] + 16

    hvc1 = result_path[b"hvc1"]
    sample_children = tuple(_boxes(result, hvc1[0] + 8 + 78, hvc1[0] + hvc1[2]))
    assert [box[1] for box in sample_children] == [b"hvcC", b"amve"]
    amve = sample_children[-1]
    assert result[amve[0] + 8 : amve[0] + amve[2]] == payload

    assert _chunk_offset(result) == original_chunk_offset + 16
    assert b"track-padding" in result
    assert result[result.index(b"synthetic-media-bytes") :] == b"synthetic-media-bytes"


def test_insert_amve_is_idempotent_and_rejects_conflicting_metadata() -> None:
    payload = b"12345678"
    with_amve = insert_amve(_fixture(), payload)

    assert insert_amve(with_amve, payload) == with_amve
    with pytest.raises(IsoBmffError, match="different 'amve' metadata"):
        insert_amve(with_amve, b"abcdefgh")


def test_insert_amve_rejects_preexisting_duplicates() -> None:
    duplicate = _fixture(amve_payloads=(b"12345678", b"12345678"))

    with pytest.raises(IsoBmffError, match="duplicate 'amve'"):
        insert_amve(duplicate, b"12345678")


@pytest.mark.parametrize(
    ("malformed", "message"),
    [
        (b"\0\0\0\x20moovshort", "truncated 'moov'"),
        (_fixture()[:-3], "truncated 'mdat'"),
        (_box(b"ftyp") + _box(b"moov", b"\0\0\0"), "truncated ISO-BMFF box header"),
    ],
)
def test_insert_amve_rejects_malformed_or_truncated_input(malformed: bytes, message: str) -> None:
    with pytest.raises(IsoBmffError, match=message):
        insert_amve(malformed, b"12345678")


def test_insert_amve_requires_the_standard_payload_width() -> None:
    with pytest.raises(ValueError, match="exactly 8 bytes"):
        insert_amve(_fixture(), b"short")


def test_encode_amve_payload_uses_declared_integer_widths() -> None:
    assert encode_amve_payload(0x01020304, 0x0506, 0x0708) == bytes.fromhex("0102030405060708")


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ((-1, 0, 0), ValueError),
        (((1 << 32), 0, 0), ValueError),
        ((0, (1 << 16), 0), ValueError),
        ((0, 0, 1.5), TypeError),
        ((True, 0, 0), TypeError),
    ],
)
def test_encode_amve_payload_rejects_out_of_range_or_non_integer_fields(
    values: tuple[object, object, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        encode_amve_payload(*values)  # type: ignore[arg-type]
