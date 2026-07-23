# Changelog

All notable changes to Reel-HDR are documented here. Versions follow Python's
PEP 440 in package metadata and use readable release tags.

## [Unreleased]

No changes yet.

## [0.1.0a0] - 2026-07-23

### Clean-room bootstrap

- Created the Python 3.12 `uv` package and `reelhdr` CLI.
- Added generic PATH discovery and version reporting for FFmpeg, ffprobe,
  MP4Box, and `dovi_tool`.
- Established a media-denying `.gitignore` and clean-room extraction ledger.

### Neutral conversion engine

- Implemented tested BT.2100 PQ and HLG transfer functions.
- Added structured SDR, HLG, and PQ probe classification.
- Built inspectable, dry-runnable conversion plans with disposable workspaces
  and atomic output publication.
- Added full-fidelity PQ-to-HLG frame math plus an explicitly labelled fast
  approximation.
- Added Profile 8.4-compatible RPU generation/injection, MP4 muxing, and
  bounded `amve` insertion.

### Conformance verification

- Added typed pass/warn/fail reports from ffprobe, MP4Box, and direct bounded
  ISO-BMFF parsing.
- Checks cover Main 10 HEVC, HLG/BT.2020 signalling, Dolby Vision profile and
  compatibility evidence, timing, frames, audio expectations, `amve`, and
  fast-start layout.
- Added human and JSON output, meaningful exit codes, and automatic
  post-conversion verification.

### CLI and documentation

- Added `instagram-dv84` and clean `hlg` presets with explicit override
  precedence.
- Added timed progress and bounded, actionable failure diagnostics.
- Added public architecture, licensing, troubleshooting, contribution,
  security, conduct, and CI documentation.
- Added synthetic integration coverage and publication sweeps.

[Unreleased]: https://github.com/Bashocodes/reel-hdr/compare/v0.1.0-alpha...HEAD
[0.1.0a0]: https://github.com/Bashocodes/reel-hdr/releases/tag/v0.1.0-alpha
