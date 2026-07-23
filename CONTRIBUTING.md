# Contributing to Reel-HDR

Thank you for helping make HDR conversion and verification more inspectable.
Small, focused changes with synthetic tests are the easiest to review.

## Development setup

You will need:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Git
- Optional external tools for integration tests: FFmpeg/ffprobe with
  `libx265`, MP4Box, and `dovi_tool`

Clone your fork and prepare the environment:

```bash
uv sync --dev
uv run reelhdr doctor
make verify
```

`make verify` is the required gate. It runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Integration tests generate synthetic clips at runtime. They skip automatically
when required external tools are unavailable. Never add private or
copyright-restricted media to make a test pass.

## Adding a verification check

Verification checks live in `src/reelhdr/verify.py`.

1. Choose a stable, descriptive check ID such as `video.chroma_subsampling`.
2. Base the result on existing structured probe/container evidence when
   possible.
3. Return `ok`, `warn`, or `fail` through the shared report helpers.
4. Make every fail explanation state what was found, what was expected, and
   the likely cause.
5. Add focused unit fixtures for passing, failing, missing, and malformed
   evidence.
6. If the check depends on a selected output contract, test both
   `instagram-dv84` and `hlg`.
7. Update the verification documentation.

The verifier must remain read-only and bounds-safe. A missing tool or malformed
file should become a structured finding, not an uncaught exception.

## Adding a preset

Preset definitions and override resolution live in
`src/reelhdr/presets.py`; execution remains in the shared pipeline.

1. Add a stable CLI name and a concise output-contract description.
2. Reuse the shared encode/mux steps instead of forking conversion logic.
3. Define only neutral, explainable defaults.
4. Preserve explicit flag precedence for `--max-pq`, `--crf`/`--bitrate`,
   and `--fps`.
5. Add resolution tests, dry-run plan assertions, and a synthetic integration
   test when the preset changes the output contract.
6. Teach automatic verification what the preset expects.
7. Update `reelhdr presets`, README, and troubleshooting guidance.

Do not claim that a platform will always preserve an output. Describe the
produced signalling and the evidence the verifier can prove.

## Pull request expectations

- Keep the change bounded and explain the user-visible result.
- Link an issue when one exists.
- Include tests or explain why tests are unnecessary.
- Preserve the neutral-conversion and read-only-verification boundaries.
- Do not add media, creative look constants, personal paths, credentials, or
  private source identifiers.
- Update documentation for public CLI or report-schema changes.
- Confirm `make verify` passes.

Before opening a pull request, inspect the staged file list:

```bash
git diff --cached --check
git diff --cached --stat
```

## Security and conduct

Do not report vulnerabilities in a public issue. Follow
[SECURITY.md](SECURITY.md). Participation is governed by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
