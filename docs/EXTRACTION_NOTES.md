# Clean-room extraction notes

This ledger records a read-only study of private implementation code. The
study was restricted to standards-oriented behavior and tool interaction. No
media, metadata payload, fingerprint, filename, project identifier, personal
path, tuned look value, or generated artifact was copied.

Source labels are intentionally generic. They describe only the category of
implementation that was inspected.

## Standards and infrastructure ledger

| Observed behavior | Generic source category | Clean-room decision |
| --- | --- | --- |
| Structured process execution and JSON parsing | Transfer experiments | Reimplement with argv arrays, timeouts, bounded output, and structured errors. |
| Video probing for geometry, rational frame rate, frame count, codec, pixel format, colorimetry, duration, and side data | Transfer and verification utilities | Reimplement around ffprobe JSON with explicit unknown fields and no private path fallbacks. |
| BT.709 signal-to-linear conversion | Transfer experiments | Defer; implement only from the published recommendation if a future neutral gamut path needs it. |
| SMPTE ST 2084 / BT.2100 PQ encode and decode | Transfer experiments | Reimplement from the published equations with endpoint, anchor, monotonicity, and round-trip tests. |
| BT.2100 HLG OETF and inverse | Transfer and rendering experiments | Reimplement once from the published piecewise equations with branch-domain tests. |
| PQ luminance to relative scene light to HLG | Transfer experiments | Reimplement as an explicit neutral policy with one documented peak-normalization option. Never substitute transfer retagging. |
| RGB-primary conversion through a shared white point | Transfer experiments | Defer; if needed, derive from published chromaticities and make gamut/clipping policy explicit. |
| Highlight shaping, tone mapping, black shaping, saturation, and preview transforms | Creative rendering experiments | Exclude. These are look decisions, not format conformance. |
| Main 10 HLG encode orchestration | Transfer and rendering experiments | Rebuild as inspectable plan data with user-selected rate/FPS options and standards tags. Do not inherit tuning. |
| Dolby Vision RPU generation and elementary-stream injection | Delivery utilities | Rebuild around user-installed `dovi_tool`; retain no wrapper path or fixed private metadata. |
| ISO-BMFF mux and track inspection | Delivery and verification utilities | Rebuild around user-installed MP4Box with explicit profile/compatibility expectations. |
| Ambient-viewing box handling | Delivery utilities | Reimplement from the public container/HEVC field layout with a documented neutral policy and structural tests. |
| HEVC Main 10, HLG, BT.2020, range, timing, audio, and track checks | Verification routines | Reimplement as typed field-level findings derived from the selected output contract. |
| Dolby Vision configuration-box checks | Verification routines | Reimplement with bounded ISO-BMFF walking; never treat an arbitrary byte substring as proof. |
| Frame count and duration consistency | Rendering and verification routines | Reimplement from probe evidence with rational rates and VFR-aware warnings. |
| Full-decode integrity checking | Verification routines | Defer as an optional external-tool check; metadata checks remain independently necessary. |
| Opening/closing frame comparison for loops | Rendering and verification routines | Defer as an opt-in metric with an explicit tolerance and decoded-output evidence. |
| Media fingerprint pinning | Verification routines | Exclude entirely. Asset identity is not format conformance. |
| Application job state, private dependency fallbacks, and wrapper discovery | Application infrastructure | Exclude. Reel-HDR uses generic PATH discovery and local CLI results only. |

## Explicitly excluded material

The following categories must never enter the clean implementation:

- Video, image, audio, editing-project, output, or reference metadata files.
- Digests, asset identity assertions, filenames, private directories, or
  personal filesystem paths.
- Brand, subject, character, campaign, timeline, logo, or compositor
  identifiers.
- Creative grade, tone-map, preview, black, saturation, highlight, gamut, or
  look curves and their tuned values.
- Mastering, content-light, nominal-peak, or average-light values copied from
  a specific delivery.
- Encoder, bitrate, GOP, resolution, padding, frame-rate, duration, audio, or
  size choices copied from a specific render.
- Masks, effects, random seeds, protected regions, placement rules, or editing
  application presets.
- Wrapper paths, application fallbacks, endpoints, credentials, key files,
  job state, or output-directory conventions.
- Platform observations used as a substitute for standards evidence.

## Clean implementation rules

1. Transfer equations are derived from named public standards and tested at
   breakpoints, endpoints, anchors, and round trips.
2. Conversion policy remains explicit and separate from conformance checks.
3. External programs are resolved on PATH and invoked with argv arrays, never
   shell strings.
4. Tool output and parser work are bounded; malformed evidence becomes a typed
   failure.
5. Verification is read-only and combines structured probe, external tool,
   and direct container evidence.
6. Audio and preset-specific checks are conditional on declared expectations.
7. Tests generate synthetic media at runtime; no media fixture is committed.
8. Documentation describes compatible signalling and measured evidence
   without making platform guarantees.

## Redaction audit

This document intentionally contains:

- No private source-directory or project names.
- No original private wrapper or application names.
- No personal paths, media names, fingerprints, or endpoints.
- No private numeric constants or creative tuning values.
