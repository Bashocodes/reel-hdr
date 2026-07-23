# How Reel-HDR works

Reel-HDR makes one narrow promise: perform a neutral, inspectable transfer and
container conversion, then verify the produced file. It does not invent a
look.

## Source classification

ffprobe JSON supplies transfer characteristics, primaries, matrix, pixel
format, resolution, frame rate, and Dolby Vision side data. The first video
stream is classified as:

- **SDR:** no PQ, HLG, or Dolby Vision evidence.
- **HLG:** transfer characteristic `arib-std-b67` / code 18.
- **PQ:** transfer characteristic `smpte2084` / code 16.
- **Dolby Vision:** Dolby Vision sample-entry or side-data evidence. This class
  is inspected but not accepted as a conversion input.

## BT.2100 transfer math

The default PQ path performs two real transfer operations. It never changes
only the tag.

### PQ EOTF

For normalized PQ code value \(N\), ITU-R BT.2100 Table 4 defines absolute
luminance:

```text
Y = N^(1 / m2)
L = 10000 × (max(Y - c1, 0) / (c2 - c3 × Y))^(1 / m1)

m1 = 2610 / 16384
m2 = 2523 / 32
c1 = 3424 / 4096
c2 = 2413 / 128
c3 = 2392 / 128
```

`L` is in cd/m². Reel-HDR divides it by the configurable nominal peak
(`--pq-peak-nits`, default 1000) to obtain relative scene light and clips
values outside the HLG reference domain. This is a neutral ceiling, not a
creative highlight roll-off.

### HLG OETF

For relative scene light \(E\), ITU-R BT.2100 Table 5 defines:

```text
E' = sqrt(3 × E)                         for 0 ≤ E ≤ 1/12
E' = a × ln(12 × E - b) + c             otherwise

a = 0.17883277
b = 1 - 4a
c = 0.5 - a × ln(4a)
```

The full-fidelity path decodes planar 16-bit RGB frames through an FFmpeg pipe,
applies these equations in bounded NumPy chunks, and pipes them to the Main 10
encoder. `--fast` uses FFmpeg's transfer-aware scale path as an approximation.

Reference: [ITU-R BT.2100](https://www.itu.int/rec/r-rec-bt.2100).

## Why PQ retagging fails

PQ code values represent absolute display luminance under the ST 2084 curve.
HLG code values represent relative scene light under a different piecewise
curve. Changing `color_trc=smpte2084` to `color_trc=arib-std-b67` without
changing samples makes a decoder interpret PQ numbers with HLG math. The
metadata and pixels then contradict each other, which can produce visibly
incorrect brightness on iOS and after a social-platform transcode.

Reel-HDR therefore requires `PQ EOTF → relative scene light → HLG OETF` before
HLG encoding. Re-labelling is not an optimization of that operation.

## SDR and HLG

- SDR is transcoded to 10-bit HEVC and receives HLG/BT.2020 signalling without
  a creative tone curve.
- HLG samples already have the target transfer. Main 10 HEVC can be copied
  while its bitstream/container tags are normalized. Other HLG codecs are
  transcoded without a transfer transform.

## Preset tails

Both presets produce an HEVC Main 10, limited-range, BT.2020 base with
`arib-std-b67` transfer:

```text
instagram-dv84:
  base HEVC
    → static L1 configuration (max_pq configurable)
    → dovi_tool RPU generation and injection
    → MP4Box profile 8 / HLG-compatibility signalling
    → amve insertion
    → atomic publish
    → verification

hlg:
  base HEVC
    → MP4Box HLG color signalling
    → atomic publish
    → HLG-specific verification
```

The first path produces Dolby Vision profile 8.4 compatible signalling. Static
L1 values describe the configured delivery ceiling; they are not a
frame-by-frame content measurement.

## The `amve` box

`amve` is an ISO-BMFF ambient-viewing-environment box inside the video sample
entry. Its fields carry ambient illuminance and display-white chromaticity
codes corresponding to the HEVC ambient-viewing-environment message.
Reel-HDR inserts a small neutral D65 reference payload after muxing and verifies
its exact structural location.

The box is one part of a coherent delivery: HLG base samples, BT.2020 tags,
Profile 8.4-compatible RPU/configuration, and ambient-viewing metadata agree.
The tested Instagram path preserves the HDR presentation more reliably when
that set survives its re-encode. `amve` alone cannot turn SDR into HDR or repair
incorrect transfer math, and platform behavior can change.

## Verification boundary

The verifier never opens its input for writing. It compares three independent
evidence sources:

1. ffprobe JSON for decoded stream and timing facts.
2. `MP4Box -info` for external track/container corroboration.
3. A bounded direct ISO-BMFF parser for physical box order, Dolby Vision
   configuration, compatibility ID, and `amve`.

Malformed boxes, excessive nesting, truncated sizes, and missing tools become
typed findings rather than uncaught parser errors.
