# Add machine-readable output to `reelhdr doctor`

**Suggested labels:** `good first issue`, `cli`, `doctor`

## Goal

Support `reelhdr doctor --json` so bug reports and automation can consume tool
availability without scraping the human table.

## Scope

- Add a stable JSON shape containing tool key, executable, resolved path,
  availability, version, version error, and install hint.
- Keep the current human output as the default.
- Preserve copy-pasteable install guidance in human mode.
- Add CLI and serialization tests with mocked PATH/tool results.
- Document the new flag in README and CONTRIBUTING.

## Acceptance criteria

- No tool command is run through a shell.
- JSON output contains no environment dump or unrelated filesystem paths.
- `make verify` passes.

**Estimated difficulty:** Easy, 2–4 hours.
