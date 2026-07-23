# Publish a JSON Schema for verification reports

**Suggested labels:** `good first issue`, `verification`, `documentation`

## Goal

Add a checked-in JSON Schema describing `reelhdr verify --json` so other tools
can validate reports without importing Python.

## Scope

- Add a Draft 2020-12 schema under `docs/schema/`.
- Describe report path, verdict, summary counts, and check objects.
- Restrict status and verdict to their current enum values.
- Add a test that validates the serializer's documented keys and enums without
  adding a runtime schema dependency.
- Link the schema from README.

## Acceptance criteria

- The schema matches the current typed report exactly.
- No unstable timestamp or machine-specific metadata is introduced.
- `make verify` passes.

**Estimated difficulty:** Easy, 2–4 hours.
