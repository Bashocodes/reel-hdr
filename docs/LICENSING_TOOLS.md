# Licensing and external tools

Reel-HDR's Python source is distributed under the repository's
[MIT License](../LICENSE). The media programs it invokes have their own
licenses.

## The boundary

Reel-HDR does not contain, vendor, download, or link against FFmpeg, ffprobe,
GPAC/MP4Box, `dovi_tool`, or their libraries. The user installs each program
separately. Reel-HDR discovers executables on `PATH` and launches them as
ordinary subprocesses with explicit argument arrays.

That boundary keeps the Python package and external programs independently
replaceable and independently distributed. It does not change or waive any
external tool's terms. Anyone who redistributes those binaries must inspect
the exact build and comply with its applicable license.

## Tool licenses

| Tool | Upstream license | How Reel-HDR uses it |
| --- | --- | --- |
| FFmpeg / ffprobe | FFmpeg is primarily LGPL 2.1-or-later; optional components make a build GPL. The `libx265` combination used by Reel-HDR requires a GPL-enabled FFmpeg build. | Decode/probe inputs and encode the HEVC Main 10 HLG base stream. |
| MP4Box (GPAC) | GPAC reports LGPL 2.1-or-later. | Mux HEVC and optional audio into ISO-BMFF/MP4 and inspect container evidence. |
| `dovi_tool` | MIT. | Generate and inject Profile 8.4-compatible RPU metadata for `instagram-dv84`. |

Authoritative references:

- [FFmpeg license](https://ffmpeg.org/legal.html)
- [FFmpeg external-library license effects](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)
- [GPAC licensing](https://gpac.io/licensing/)
- [`dovi_tool` license](https://github.com/quietvoid/dovi_tool/blob/main/LICENSE)

## Why Reel-HDR does not bundle binaries

- Users choose builds appropriate to their platform and legal requirements.
- `reelhdr doctor` can show the exact paths and version strings in use.
- External security and codec updates do not require republishing this Python
  package.
- The source distribution remains small and contains no third-party executable
  payloads.

This document describes the architecture and upstream notices; it is not legal
advice.
