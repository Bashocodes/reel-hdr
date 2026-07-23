# Clean-room extraction notes

This ledger records a read-only study of private implementation code. The study
was limited to Python and shell source needed to identify standards-oriented
behavior. No media, reference metadata payload, fingerprint, or generated
artifact was opened or copied.

The source labels below are intentionally generic. They identify the kind of
reference and, where safe, an unbranded source-file basename without preserving
private project names or personal paths.

Decision meanings:

- **Reimplement now** — generic infrastructure needed by the bootstrap phase.
- **Reimplement later** — useful standards behavior, but conversion or
  conformance logic belongs in a later phase and must be written independently.
- **Split** — retain only a standards primitive; reject adjacent creative policy.
- **Skip** — private, creative, application-specific, heuristic, or unnecessary.

## Standards and infrastructure ledger

| Function or behavior found | Sanitized source file | What it does | Decision |
| --- | --- | --- | --- |
| `run_json_command` | Transfer experiments, Python modules | Executes an argv command and parses JSON output. | **Reimplement later.** Use the detected executable path, no shell, a timeout, bounded output, return-code checks, and structured errors. |
| `probe_video` | Transfer experiments, Python modules | Reads dimensions, rational frame rate, and optional frame count with ffprobe. | **Reimplement later.** Preserve rational rates and expand to codec, pixel format, colorimetry, range, duration, and side-data fields. |
| `rec709_signal_to_linear` | Transfer experiments, `sdr_to_hdr10.py` | Applies the piecewise inverse BT.709 transfer function to normalized signal values. | **Reimplement later** from ITU-R BT.709, with breakpoint, endpoint, and domain tests. |
| `pq_encode_luminance` | Transfer experiments, `sdr_to_hdr10.py` | Maps absolute luminance in cd/m² to normalized SMPTE ST 2084 code values. | **Reimplement later** from ST 2084. Use an unambiguous name, explicit domain handling, monotonicity tests, and encode/decode round trips. |
| `pq_decode_luminance` | Transfer experiments, PQ-to-HLG module | Maps normalized ST 2084 code values to absolute luminance. | **Reimplement later** from ST 2084. Define scalar/array, clipping, non-finite, and denominator behavior explicitly. |
| `hlg_encode_scene_linear` | Transfer experiments, PQ-to-HLG module | Applies the piecewise BT.2100/ARIB STD-B67 HLG OETF to relative scene light. | **Reimplement later** from BT.2100. Evaluate branches with masks so the logarithmic branch is never evaluated outside its domain. |
| RGB-primary conversion | Transfer experiments, `sdr_to_hdr10.py` | Converts linear BT.709 RGB through D65/XYZ into linear BT.2020 RGB. | **Reimplement later** from published chromaticities or independently verified matrices. Make the gamut and clipping policy explicit. |
| `smoothstep` | Transfer experiments, `sdr_to_hdr10.py` | Generic interpolation used by a private look transform. | **Skip.** Add an independent helper only if later generic code actually needs it. |
| `soft_knee` | Transfer experiments, `sdr_to_hdr10.py` | Applies a tuned highlight roll-off. | **Skip.** This is creative tone-mapping policy, not format conformance. |
| `hdr_grade` | Transfer experiments, `sdr_to_hdr10.py` | Combines black shaping, shadow, saturation, highlight, gamut, and PQ operations into a private SDR-to-HDR look. | **Skip wholesale.** A future neutral mapping policy must be documented and designed independently. |
| `make_sdr_preview` | Transfer experiments, `sdr_to_hdr10.py` | Decodes PQ and applies a custom preview tone map and display gamma. | **Split.** Reuse only the independently implemented PQ decoder; skip the preview/look curve. |
| SDR-to-HDR experiment orchestration | Transfer experiments, `sdr_to_hdr10.py` | Decodes frames, transforms transfer/gamut, encodes Main10 HEVC, and writes HDR signaling. | **Reimplement later** from the delivery contract. Keep only explicit standards signaling, Main10, `hvc1`, and fast-start concepts. |
| PQ-to-HLG experiment orchestration | Transfer experiments, PQ-to-HLG module | Decodes PQ luminance, normalizes scene light, applies HLG, and encodes Main10 HEVC with HLG signaling. | **Reimplement later.** Resolution, timing, bitrate, audio, canvas, and peak assumptions must become validated policy or options. |
| `run_checked` | Renderer study A/B, main Python modules | Runs a subprocess and fails on a nonzero status. | **Reimplement later.** Preserve argv boundaries, redact sensitive values, impose timeouts, and return concise structured diagnostics. |
| Structured media probe | Renderer study A, verification routine | Uses ffprobe JSON with frame counting to inspect video/audio streams, codec/profile/sample entry, geometry, pixel format, timing, HDR tags, and audio properties. | **Reimplement later.** Compare to caller-derived expectations; treat audio as optional. |
| HEVC and HDR field assertions | Renderer study A, verification routine | Checks HEVC Main 10, `hvc1`, 10-bit 4:2:0, limited range, BT.2020 primaries/matrix, and HLG transfer signaling. | **Reimplement later** as field-level pass/warn/fail evidence without fixed dimensions or timing. |
| MP4Box Dolby Vision inspection | Renderer study A, verification routine | Reads track information and identifies Dolby Vision profile 8 with HLG compatibility identifier 4. | **Reimplement later.** Parse robustly and fail clearly when profile or compatibility evidence is absent or malformed. |
| MP4 configuration-box inspection | Renderer study A/B, verification routines | Looks for HEVC/Dolby configuration, color, and optional ambient-viewing boxes. | **Reimplement later** with bounded ISO-BMFF box walking or trusted tool evidence, not raw prefix substring searches. |
| Full-decode integrity check | Renderer study A, verification routine | Decodes the complete output to a null sink and checks for decoder errors. | **Reimplement later** as an optional verification stage; it complements but does not replace metadata checks. |
| Source-frame loop equality | Renderer study A, render and verification routines | Compares authored opening and closing frames for an exact loop boundary. | **Reimplement later only as an optional loop check.** Prefer decoded output and report a metric/tolerance instead of mutating frames. |
| Periodic endpoint error | Renderer study B, render module | Evaluates a generated cycle at start/end and computes mean absolute error. | **Reimplement later only for generator-level tests.** It does not prove encoded loop quality. |
| Encoded first/last comparison | Renderer study A, verification routine | Uses decoded-frame similarity metrics to assess the delivered loop boundary. | **Reimplement later** as an optional check with an explicit user-selected metric and tolerance. |
| Timeline/frame accounting | Renderer study A/B, render and verification routines | Verifies authored segment totals, decoded frame count, frame rate, and duration. | **Reimplement later.** Derive expectations from requested timing and handle rational rates and VFR explicitly. |
| Audio stream validation | Renderer study A, verification routine | Checks codec, channel count, and sample rate. | **Reimplement later** as optional policy-driven verification. No-audio must remain valid. |
| Verification report generation | Renderer study A/B, verification routines | Records probe evidence, expected/actual fields, profile/box state, loop result, and verdict. | **Reimplement later** as sanitized structured data plus concise human output. |
| HLG OETF | Renderer study B, render module | Applies the standards HLG transfer function. | **Reimplement later** from BT.2100; this duplicates the transfer-study candidate and must have one canonical implementation. |
| Main10 HLG encode plan | Renderer study A/B, Python and shell helpers | Produces 10-bit 4:2:0 HEVC with HLG/BT.2020 signaling, limited range, `hvc1`, fast-start, and optional audio. | **Reimplement later** as a parameterized plan after probing input colorimetry. Do not inherit encoder tuning. |
| Elementary-stream mux | Renderer study B, render module | Stream-copies HEVC into MP4 while requesting `hvc1` and fast-start layout. | **Reimplement later** only if the clean conversion architecture needs a separate mux stage. |
| Private Dolby Vision wrapper invocation | Renderer study B, render module | Calls a personal wrapper with fixed metadata. | **Skip and cleanly rebuild later** around detected `dovi_tool` and MP4Box commands with documented inputs. |
| Preview/tone-map commands | Renderer study A/B, helper artifacts | Creates SDR review images or videos. | **Skip for bootstrap.** Any future preview transform must be independently specified and clearly labeled as non-conformance output. |
| SHA-256 media identity helper | Renderer study A, verification routine | Pins a specific private asset by digest. | **Skip entirely.** Asset identity is not format conformance. |
| `probe` | Verification utility, `taskdeck.py` | Uses ffprobe key/value output for the first video stream and returns codec, pixel format, transfer, matrix, dimensions, and frame count. | **Reimplement later** with JSON, timeouts, explicit errors, and the shared probe model; do not copy silent-failure behavior. |
| `verify_dv84` | Verification utility, `taskdeck.py` | Heuristically scans for `hvc1`, Dolby configuration, color, and optional ambient-viewing markers, then surfaces MP4Box profile lines. | **Reimplement later**, but not the raw byte-prefix scan. Verify actual profile/compatibility ID, bit depth, tags, frames, timing, and malformed containers. |
| `preflight` | Verification utility, `taskdeck.py` | Locates application dependencies with PATH and private fallbacks. | **Reimplement now only as generic PATH discovery.** `src/reelhdr/tools.py` contains no application fallback or bundled binary. |
| Job `log` / `update` helpers | Verification utility, `taskdeck.py` | Attach text and status to a private application job. | **Skip.** Reel-HDR will use its own structured result model. |

## Explicitly excluded material

The following categories were observed or adjacent to the studied code and must
not be ported:

- Any video, image, audio, project file, generated output, or reference dynamic
  metadata payload.
- SHA-256 values, media identity assertions, asset filenames, private folder
  names, or absolute/personal paths.
- Subject, brand, character, campaign, timeline, shot, logo, or effect-specific
  identifiers and rules.
- Creative grading, tone-mapping, preview, black, saturation, highlight, or
  gamut-look curves and their tuned defaults.
- Fixed mastering-display, content-light, nominal-peak, or average-light
  metadata values taken from a particular render.
- Fixed CRF, bitrate, preset, GOP, resolution, padding, frame rate, duration,
  frame total, canvas, audio, or file-size choices.
- Masks, camera/effect algorithms, random seeds, protected regions, logo
  placement, or compositor project/preset names.
- Personal wrapper paths, PATH fallbacks, private service endpoints,
  credentials, key-file configuration, job state, or output-directory
  conventions.
- Platform-success claims or delivery observations as a substitute for
  standards conformance.

## Clean implementation rules

1. Transfer equations and colorimetry will be re-derived from the named
   standards, with independent tests at breakpoints, endpoints, and round trips.
2. Conversion policy will be explicit and separable from conformance checks.
   Content-specific “look” transforms are outside the clean core.
3. External commands will use resolved executable paths and argv arrays, never
   a shell string, and will have timeouts, bounded output, and checked statuses.
4. Verification will use structured probe/tool evidence and bounded container
   parsing. A marker substring alone will never prove Profile 8.4.
5. Audio and loop verification will be conditional on declared expectations.
6. This bootstrap phase intentionally contains no conversion or conformance
   implementation; `convert` and `verify` remain explicit stubs.
