# Add a chroma-subsampling verification check

**Suggested labels:** `good first issue`, `verification`, `tests`

## Goal

Add a stable `video.chroma_subsampling` finding that confirms the delivered
video uses 4:2:0 sampling, complementing the existing Main 10 and bit-depth
checks.

## Scope

- Derive the value from the normalized ffprobe pixel format.
- Report `ok` for recognized 10-bit 4:2:0 formats.
- Report `fail` for recognized 4:2:2/4:4:4 formats.
- Report `warn` when the pixel format is missing or cannot be classified.
- Keep the explanation in the existing found/expected/likely-cause style.
- Add passing, failing, and unknown unit cases to `tests/test_verify.py`.
- Update the verification list in README.

## Acceptance criteria

- No new runtime dependency.
- No media fixture; use existing JSON/text evidence.
- `make verify` passes.

**Estimated difficulty:** Easy, 2–4 hours.
