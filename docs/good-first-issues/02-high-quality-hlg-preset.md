# Add a neutral `high-quality-hlg` preset

**Suggested labels:** `good first issue`, `preset`, `tests`

## Goal

Add a generic HLG-only preset for users who want a lower default CRF while
retaining the same neutral transfer and container contract.

## Scope

- Add `high-quality-hlg` to the typed preset registry.
- Use the shared HLG pipeline; do not fork conversion code.
- Select a documented lower CRF default without changing transfer math,
  metadata, or FPS policy.
- Preserve explicit `--crf`, `--bitrate`, and `--fps` precedence.
- Verify against the clean HLG contract with no Dolby Vision configuration or
  `amve`.
- Add preset-resolution, plan-parity, and table-output tests.
- Document that the preset changes encoder quality only and makes no platform
  guarantee.

## Acceptance criteria

- Existing preset output behavior is unchanged.
- No creative grading values or media files are added.
- `make verify` passes.

**Estimated difficulty:** Easy, 3–5 hours.
