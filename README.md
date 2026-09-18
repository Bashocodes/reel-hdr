# Reel-HDR

HDR reels that survive Instagram. A local Python command: SDR, HLG or PQ video goes in, and a Dolby Vision profile 8.4
file on an HLG base comes out. Every output is checked (HEVC Main 10, BT.2020/HLG tags, Dolby Vision signalling) before
it reports success. It does not grade your footage; it fixes transfer, encoding and metadata.

```text
$ reelhdr doctor
External tools:
[ok] ffmpeg: /opt/homebrew/bin/ffmpeg — ffmpeg version 8.1.1
[ok] ffprobe: /opt/homebrew/bin/ffprobe — ffprobe version 8.1.1
[ok] dovi_tool: /opt/homebrew/bin/dovi_tool — dovi_tool 2.3.2
[ok] MP4Box: /opt/homebrew/bin/MP4Box — MP4Box - GPAC version 26.02
```

## Run it

macOS with Homebrew and uv.

```bash
brew install ffmpeg mp4box dovi_tool
```

```bash
git clone https://github.com/Bashocodes/reel-hdr.git
cd reel-hdr
uv tool install .
```

```bash
reelhdr doctor
```

```bash
reelhdr convert input.mov -o reel-hdr.mp4
```

Try the plan without writing a file: add `--dry-run`. To check a file you already have:

```bash
reelhdr verify reel-hdr.mp4
```

More: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) · [CHANGELOG.md](CHANGELOG.md)

## How it works

Reel-HDR probes the first video stream, classifies its transfer, and takes one
neutral path:

```text
                              ┌─ SDR ─→ 10-bit HLG-tagged transcode ─┐
INPUT ─→ ffprobe ─→ classify ─┼─ HLG ─→ pass/normalize HLG values ──┼─→ HEVC Main 10
                              └─ PQ  ─→ PQ EOTF → scene → HLG OETF ─┘
                                      │
                                      ├─ instagram-dv84
                                      │   RPU → inject → MP4Box → amve
                                      │
                                      └─ hlg
                                          MP4Box only; no DV or amve
                                      │
                                      └─→ read-only conformance verify
```

- **PQ:** the default path applies the ITU-R BT.2100 PQ EOTF, normalizes
  absolute luminance to relative scene light, and applies the HLG OETF for
  every decoded sample. `--fast` selects a clearly labelled FFmpeg
  approximation.
- **HLG:** pixel values pass through. Existing 10-bit HEVC can be stream-copied;
  other inputs are normalized to Main 10 HEVC and correct container tags.
- **SDR:** signal values receive a neutral 10-bit, HLG-tagged transcode. No
  creative tone curve is introduced.

See [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) for formulas and container
details.

## Verification

`convert` verifies every completed output automatically. You can also inspect a
file without modifying it:

```bash
reelhdr verify reel-hdr.mp4
reelhdr verify reel-hdr.mp4 --json
reelhdr verify clean-hlg.mp4 --preset hlg
```

The report combines ffprobe, `MP4Box -info`, and bounded direct ISO-BMFF
parsing. It checks HEVC Main 10, HLG/BT.2020 color tags, frame and duration
consistency, Dolby Vision profile/compatibility evidence when expected,
`amve`, audio expectations, and fast-start layout. Warnings are advisory; any
fail-level finding gives exit code 1.

## Presets

Print the installed table with `reelhdr presets`.

| Preset | Default | Output |
| --- | --- | --- |
| `instagram-dv84` | Yes | HEVC Main 10 HLG, Dolby Vision 8.4 compatible signalling, `amve`, verification |
| `hlg` | No | Clean HEVC Main 10 HLG MP4, without Dolby Vision RPU/config or `amve` |

Common overrides:

```bash
# Different static L1 ceiling and encoder quality
reelhdr convert input.mov -o output.mp4 --max-pq 2200 --crf 16

# Target bitrate instead of CRF
reelhdr convert input.mov -o output.mp4 --bitrate 15M

# Preserve source timestamps (default) or request constant 24 fps
reelhdr convert input.mov -o output.mp4 --fps passthrough
reelhdr convert input.mov -o output.mp4 --fps 24
```

`--crf` and `--bitrate` are mutually exclusive. Explicit flags always override
preset defaults.

## Progress and failures

A live conversion reports timed stages:

```text
[ok] probe     0.08s — pq
[ok] plan      0.03s — instagram-dv84
[ok] encode   12.41s
[ok] rpu       0.18s
[ok] mux       0.09s
[ok] amve      0.01s
[ok] verify    0.15s — pass
```

On failure, Reel-HDR prints the failing stage, exact argv, the last 15 tool
output lines, and one likely fix. Its temporary workspace is removed on both
success and failure; publication is atomic.

## Limitations

- No creative grading, shot matching, gamut mapping, or look curves.
- Produces Dolby Vision profile 8.4 compatible signalling; output acceptance
  still depends on the receiving device and platform.
- MP4 video inputs only; image sequences and editing timelines are out of scope.
- Static L1 metadata is not content analysis.
- Requires user-installed FFmpeg with `libx265`, ffprobe, MP4Box, and
  `dovi_tool`.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `tool was not found` | External binary is missing from `PATH` | Run `reelhdr doctor` and paste its install block |
| PQ output looks wrong on iPhone | Source was retagged instead of converted | Use the default conversion path; do not replace it with tag-only FFmpeg arguments |
| `output already exists` | Safe overwrite protection | Choose another `-o` path or pass `--force` |
| RPU step fails | Missing/outdated `dovi_tool` | Update it with Homebrew or `cargo install dovi_tool` |
| Mux step fails | MP4Box missing or source audio unreadable | Run `reelhdr doctor`; test the input with ffprobe |
| Frame-count verification fails | Variable/malformed timestamps or forced FPS | Retry with `--fps passthrough` and inspect the source |
| Fast-start warning | `mdat` precedes `moov` | File remains usable, but remux before delivery if the platform requires progressive layout |

## Development and licensing

```bash
uv sync --dev
make verify
```

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) or a
bounded task under [docs/good-first-issues](docs/good-first-issues/). Release
history is recorded in [CHANGELOG.md](CHANGELOG.md), and suspected
vulnerabilities should follow [SECURITY.md](SECURITY.md).

Reel-HDR is MIT-licensed. Its external media tools are not bundled; their
licenses and the process boundary are documented in
[docs/LICENSING_TOOLS.md](docs/LICENSING_TOOLS.md).

Made by cyberyogi (Sharan Ramakrishna). Everything I make: https://inkoji.com/cyberyogi
