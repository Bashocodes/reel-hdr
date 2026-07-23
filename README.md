# Reel-HDR

Reel-HDR is an open-source Python CLI for local HDR video conversion and
conformance verification.

The conversion engine accepts SDR, HLG, and PQ inputs and produces a local
HEVC Main 10, BT.2020/HLG base layer with Dolby Vision Profile 8.4 metadata.
It adds the ISO-BMFF ambient-viewing (`amve`) box to the video sample entry.
This project does not claim Dolby certification.

Conversion is intentionally neutral:

- PQ uses the BT.2100 PQ EOTF, a configurable relative-scene normalization,
  and the BT.2100 HLG OETF. `--fast` is an explicitly labeled FFmpeg
  approximation; the default processes every decoded sample with NumPy.
- SDR receives a 10-bit HLG/BT.2020 signaling transcode without a tone curve.
- HLG receives no transfer transform. Existing 10-bit HEVC samples are copied;
  other codecs are transcoded without changing the signal transfer.

Creative grading, look curves, and platform-success guarantees are out of
scope.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- External video tools as reported by `reelhdr doctor`

Reel-HDR does not bundle FFmpeg, GPAC, or Dolby Vision tooling.

## Development

```bash
uv sync --dev
uv run reelhdr doctor
uv run reelhdr convert input.mov -o output.mp4 --dry-run
uv run reelhdr convert input.mov -o output.mp4
make verify
```

The verification gate runs Ruff linting, Ruff formatting checks, and pytest.

The converter operates in a temporary workspace and atomically publishes the
completed output. FFmpeg, ffprobe, dovi_tool, and MP4Box are discovered on
`PATH`; no external binary is bundled.
