# Security Policy

## Supported versions

Reel-HDR is pre-1.0. Security fixes are applied to the current development
branch and latest tagged alpha.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| `0.1.0a0` / `v0.1.0-alpha` | Yes |
| Earlier snapshots | No |

## Report a vulnerability privately

Use GitHub private vulnerability reporting on this repository (Security tab → Report a vulnerability).
If that is unavailable, write to hello@kalailabs.org. Do not open a public issue, discussion, or pull request.

Please include:

- A description of the issue and its impact
- Reproduction steps using synthetic media when possible
- Affected Reel-HDR, operating-system, and external-tool versions
- The relevant `reelhdr doctor` output
- Whether the issue involves command execution, temporary files, path
  handling, malformed-container parsing, or report integrity

Do not include real credentials, private media, or personal filesystem paths.

## Response expectations

Once a private reporting contact is configured, maintainers aim to acknowledge
complete reports within five business days. Validation, remediation, and
disclosure timing depend on severity and complexity.

Please allow maintainers a reasonable opportunity to investigate and release a
fix before public disclosure.

## Security boundaries

- Reel-HDR does not make network calls.
- External programs are invoked with argv arrays, never shell command strings.
- Conversion uses disposable temporary workspaces and atomic publication.
- Verification opens input files read-only and bounds direct ISO-BMFF parsing.
- FFmpeg, ffprobe, MP4Box, and `dovi_tool` are user-installed programs with
  their own update and security lifecycles.
