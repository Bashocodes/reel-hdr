"""Small, bounded ISO-BMFF helpers used by the conversion pipeline.

The ambient-viewing environment (``amve``) box belongs to the visual sample
entry, not at the file or track root.  For HEVC this means:

``moov/trak/mdia/minf/stbl/stsd/{hvc1,hev1,dvh1,dvhe}/amve``.

This module deliberately treats the eight-byte ``amve`` payload as opaque.
Container mutation and ambient-viewing policy are separate concerns: callers
must supply metadata encoded for their intended standard and environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_AMVE_PAYLOAD_SIZE: Final = 8
_MAX_BOX_COUNT: Final = 10_000
_MAX_DEPTH: Final = 12
_UINT32_MAX: Final = (1 << 32) - 1
_UINT64_MAX: Final = (1 << 64) - 1
_HEVC_SAMPLE_ENTRIES: Final = frozenset({b"hvc1", b"hev1", b"dvh1", b"dvhe"})
_VISUAL_SAMPLE_ENTRY_PREFIX_SIZE: Final = 78


class IsoBmffError(ValueError):
    """Raised when an MP4 is malformed or cannot be mutated safely."""


def encode_amve_payload(
    ambient_illuminance: int,
    ambient_light_x: int,
    ambient_light_y: int,
) -> bytes:
    """Encode already-quantized H.265 ambient-viewing-environment fields.

    The box payload is one unsigned 32-bit ambient-illuminance code followed by
    unsigned 16-bit x and y chromaticity codes.  This helper intentionally does
    not choose physical values or convert units. ISO/IEC 14496-12 defines the
    box as equivalent to the ambient-viewing-environment SEI in ITU-T H.265 /
    ISO/IEC 23008-2; value policy belongs to the caller.
    """

    fields = (
        ("ambient_illuminance", ambient_illuminance, _UINT32_MAX, 4),
        ("ambient_light_x", ambient_light_x, (1 << 16) - 1, 2),
        ("ambient_light_y", ambient_light_y, (1 << 16) - 1, 2),
    )
    encoded = bytearray()
    for name, value, maximum, width in fields:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if not 0 <= value <= maximum:
            raise ValueError(f"{name} must be between 0 and {maximum}")
        encoded.extend(value.to_bytes(width, "big"))
    return bytes(encoded)


@dataclass(frozen=True, slots=True)
class _Box:
    start: int
    size: int
    type: bytes
    header_size: int
    size_encoding: int

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(slots=True)
class _ParseBudget:
    boxes_left: int = _MAX_BOX_COUNT

    def consume(self) -> None:
        if self.boxes_left == 0:
            raise IsoBmffError(f"ISO-BMFF box count exceeds {_MAX_BOX_COUNT}")
        self.boxes_left -= 1


@dataclass(frozen=True, slots=True)
class _Target:
    sample_entry: _Box
    ancestors: tuple[_Box, ...]
    children: tuple[_Box, ...]


def _u32(data: memoryview, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _u64(data: memoryview, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big")


def _parse_boxes(
    data: memoryview,
    start: int,
    end: int,
    *,
    depth: int,
    budget: _ParseBudget,
) -> tuple[_Box, ...]:
    if depth > _MAX_DEPTH:
        raise IsoBmffError(f"ISO-BMFF nesting exceeds {_MAX_DEPTH} levels")
    if start < 0 or end < start or end > len(data):
        raise IsoBmffError("ISO-BMFF child bounds exceed the input")

    boxes: list[_Box] = []
    cursor = start
    while cursor < end:
        budget.consume()
        if end - cursor < 8:
            raise IsoBmffError(f"truncated ISO-BMFF box header at byte {cursor}")

        size32 = _u32(data, cursor)
        box_type = bytes(data[cursor + 4 : cursor + 8])
        header_size = 8
        size_encoding = 32

        if size32 == 1:
            if end - cursor < 16:
                raise IsoBmffError(f"truncated extended ISO-BMFF header at byte {cursor}")
            size = _u64(data, cursor + 8)
            header_size = 16
            size_encoding = 64
        elif size32 == 0:
            size = end - cursor
            size_encoding = 0
        else:
            size = size32

        if box_type == b"uuid":
            header_size += 16
        if size < header_size:
            name = box_type.decode("ascii", errors="replace")
            raise IsoBmffError(f"invalid {name!r} box size {size} at byte {cursor}")
        if size > end - cursor:
            name = box_type.decode("ascii", errors="replace")
            raise IsoBmffError(f"truncated {name!r} box at byte {cursor}")

        boxes.append(
            _Box(
                start=cursor,
                size=size,
                type=box_type,
                header_size=header_size,
                size_encoding=size_encoding,
            )
        )
        cursor += size

    return tuple(boxes)


def _children(
    data: memoryview,
    box: _Box,
    *,
    depth: int,
    budget: _ParseBudget,
    prefix_size: int = 0,
) -> tuple[_Box, ...]:
    start = box.payload_start + prefix_size
    if start > box.end:
        name = box.type.decode("ascii", errors="replace")
        raise IsoBmffError(f"truncated {name!r} box payload")
    return _parse_boxes(data, start, box.end, depth=depth, budget=budget)


def _only(boxes: tuple[_Box, ...], box_type: bytes, parent_name: str) -> _Box:
    matches = tuple(box for box in boxes if box.type == box_type)
    name = box_type.decode("ascii", errors="replace")
    if not matches:
        raise IsoBmffError(f"{parent_name} has no {name!r} child")
    if len(matches) > 1:
        raise IsoBmffError(f"{parent_name} has multiple {name!r} children")
    return matches[0]


def _handler_type(data: memoryview, hdlr: _Box) -> bytes:
    # FullBox version/flags (4) + pre_defined (4) precede handler_type (4).
    if hdlr.size - hdlr.header_size < 12:
        raise IsoBmffError("truncated 'hdlr' box payload")
    return bytes(data[hdlr.payload_start + 8 : hdlr.payload_start + 12])


def _sample_entries(
    data: memoryview,
    stsd: _Box,
    *,
    depth: int,
    budget: _ParseBudget,
) -> tuple[_Box, ...]:
    # stsd is a FullBox followed by entry_count, then ordinary sample-entry boxes.
    if stsd.size - stsd.header_size < 8:
        raise IsoBmffError("truncated 'stsd' box payload")
    entry_count = _u32(data, stsd.payload_start + 4)
    entries = _children(data, stsd, depth=depth, budget=budget, prefix_size=8)
    if len(entries) != entry_count:
        raise IsoBmffError(
            f"'stsd' declares {entry_count} sample entries but contains {len(entries)}"
        )
    return entries


def _locate_target(
    data: memoryview,
    *,
    video_track_index: int,
    budget: _ParseBudget,
) -> tuple[_Target, _Box, tuple[_Box, ...]]:
    if video_track_index < 0:
        raise ValueError("video_track_index must be non-negative")

    top_level = _parse_boxes(data, 0, len(data), depth=0, budget=budget)
    moov = _only(top_level, b"moov", "file")
    moov_children = _children(data, moov, depth=1, budget=budget)
    tracks = tuple(box for box in moov_children if box.type == b"trak")

    video_targets: list[_Target] = []
    for trak in tracks:
        trak_children = _children(data, trak, depth=2, budget=budget)
        mdia = _only(trak_children, b"mdia", "'trak'")
        mdia_children = _children(data, mdia, depth=3, budget=budget)
        hdlr = _only(mdia_children, b"hdlr", "'mdia'")
        if _handler_type(data, hdlr) != b"vide":
            continue

        minf = _only(mdia_children, b"minf", "'mdia'")
        minf_children = _children(data, minf, depth=4, budget=budget)
        stbl = _only(minf_children, b"stbl", "'minf'")
        stbl_children = _children(data, stbl, depth=5, budget=budget)
        stsd = _only(stbl_children, b"stsd", "'stbl'")
        entries = _sample_entries(data, stsd, depth=6, budget=budget)
        hevc_entries = tuple(entry for entry in entries if entry.type in _HEVC_SAMPLE_ENTRIES)
        if not hevc_entries:
            continue
        if len(hevc_entries) > 1:
            raise IsoBmffError("video track has multiple HEVC sample entries")

        sample_entry = hevc_entries[0]
        sample_children = _children(
            data,
            sample_entry,
            depth=7,
            budget=budget,
            prefix_size=_VISUAL_SAMPLE_ENTRY_PREFIX_SIZE,
        )
        video_targets.append(
            _Target(
                sample_entry=sample_entry,
                ancestors=(sample_entry, stsd, stbl, minf, mdia, trak, moov),
                children=sample_children,
            )
        )

    if video_track_index >= len(video_targets):
        raise IsoBmffError(
            f"file has {len(video_targets)} HEVC video track(s); "
            f"cannot select index {video_track_index}"
        )
    return video_targets[video_track_index], moov, tracks


def _chunk_offset_boxes(
    data: memoryview,
    tracks: tuple[_Box, ...],
    *,
    budget: _ParseBudget,
) -> tuple[_Box, ...]:
    offsets: list[_Box] = []
    for trak in tracks:
        trak_children = _children(data, trak, depth=2, budget=budget)
        mdia = _only(trak_children, b"mdia", "'trak'")
        mdia_children = _children(data, mdia, depth=3, budget=budget)
        minf = _only(mdia_children, b"minf", "'mdia'")
        minf_children = _children(data, minf, depth=4, budget=budget)
        stbl = _only(minf_children, b"stbl", "'minf'")
        stbl_children = _children(data, stbl, depth=5, budget=budget)
        offsets.extend(box for box in stbl_children if box.type in {b"stco", b"co64"})
    return tuple(offsets)


def _adjust_chunk_offsets(
    output: bytearray,
    boxes: tuple[_Box, ...],
    *,
    insertion_at: int,
    delta: int,
) -> None:
    view = memoryview(output)
    for box in boxes:
        width = 4 if box.type == b"stco" else 8
        payload_size = box.size - box.header_size
        if payload_size < 8:
            raise IsoBmffError(f"truncated {box.type.decode()!r} box payload")
        entry_count = _u32(view, box.payload_start + 4)
        required_size = 8 + entry_count * width
        if required_size != payload_size:
            raise IsoBmffError(f"invalid {box.type.decode()!r} entry table size")

        cursor = box.payload_start + 8
        maximum = _UINT32_MAX if width == 4 else _UINT64_MAX
        for _ in range(entry_count):
            offset = int.from_bytes(view[cursor : cursor + width], "big")
            if offset >= insertion_at:
                adjusted = offset + delta
                if adjusted > maximum:
                    raise IsoBmffError(
                        f"{box.type.decode()!r} offset overflows after amve insertion"
                    )
                output[cursor : cursor + width] = adjusted.to_bytes(width, "big")
            cursor += width
    view.release()


def _write_expanded_size(output: bytearray, box: _Box, delta: int) -> None:
    expanded = box.size + delta
    if box.size_encoding == 64:
        if expanded > _UINT64_MAX:
            raise IsoBmffError("extended ISO-BMFF box size overflow")
        output[box.start + 8 : box.start + 16] = expanded.to_bytes(8, "big")
        return
    if expanded > _UINT32_MAX:
        raise IsoBmffError("32-bit ISO-BMFF box size overflow")
    # A zero size means "to parent end". Normalizing it to an explicit size makes
    # the mutation self-contained and avoids silently consuming a later sibling.
    output[box.start : box.start + 4] = expanded.to_bytes(4, "big")


def insert_amve(
    mp4: bytes | bytearray | memoryview,
    payload: bytes | bytearray | memoryview,
    *,
    video_track_index: int = 0,
) -> bytes:
    """Insert one ambient-viewing box into an HEVC visual sample entry.

    ``payload`` must be the eight-byte body of an ``amve`` box.  The function
    intentionally does not choose ambient-light values or perform a physical
    units conversion.

    The parser is truncation-safe and capped at twelve levels and 10,000 boxes.
    Existing identical metadata makes the operation idempotent.  Existing
    different metadata is rejected rather than silently replaced or duplicated.
    Chunk offsets are adjusted when the inserted bytes move referenced media.
    """

    source = memoryview(mp4).cast("B")
    payload_bytes = bytes(memoryview(payload).cast("B"))
    if len(payload_bytes) != _AMVE_PAYLOAD_SIZE:
        raise ValueError(f"amve payload must be exactly {_AMVE_PAYLOAD_SIZE} bytes")

    budget = _ParseBudget()
    target, _moov, tracks = _locate_target(
        source, video_track_index=video_track_index, budget=budget
    )
    existing = tuple(box for box in target.children if box.type == b"amve")
    if len(existing) > 1:
        raise IsoBmffError("HEVC sample entry already contains duplicate 'amve' boxes")
    if existing:
        box = existing[0]
        existing_payload = bytes(source[box.payload_start : box.end])
        if existing_payload != payload_bytes:
            raise IsoBmffError("HEVC sample entry already contains different 'amve' metadata")
        return bytes(source)

    amve = (8 + len(payload_bytes)).to_bytes(4, "big") + b"amve" + payload_bytes
    insertion_at = target.sample_entry.end
    output = bytearray(source)
    chunk_offset_boxes = _chunk_offset_boxes(source, tracks, budget=budget)
    _adjust_chunk_offsets(
        output,
        chunk_offset_boxes,
        insertion_at=insertion_at,
        delta=len(amve),
    )

    for ancestor in target.ancestors:
        _write_expanded_size(output, ancestor, len(amve))
    output[insertion_at:insertion_at] = amve
    return bytes(output)


__all__ = ["IsoBmffError", "encode_amve_payload", "insert_amve"]
