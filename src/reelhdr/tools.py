"""Discovery and version reporting for external command-line tools."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

VERSION_TIMEOUT_SECONDS = 5.0
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Static configuration for one external executable."""

    key: str
    executable: str
    version_args: tuple[str, ...]
    install_hint: str


@dataclass(frozen=True, slots=True)
class ToolStatus:
    """Observed state for one external executable."""

    spec: ToolSpec
    path: str | None
    version: str | None
    version_error: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None


class ToolUnavailableError(RuntimeError):
    """Raised when a requested feature lacks its external executable."""


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="ffmpeg",
        executable="ffmpeg",
        version_args=("-version",),
        install_hint="brew install ffmpeg",
    ),
    ToolSpec(
        key="ffprobe",
        executable="ffprobe",
        version_args=("-version",),
        install_hint="brew install ffmpeg",
    ),
    ToolSpec(
        key="dovi_tool",
        executable="dovi_tool",
        version_args=("--version",),
        install_hint="brew install dovi_tool or cargo install dovi_tool",
    ),
    ToolSpec(
        key="mp4box",
        executable="MP4Box",
        version_args=("-version",),
        install_hint="brew install mp4box",
    ),
)

_SPECS_BY_KEY = {spec.key: spec for spec in TOOL_SPECS}


def _first_nonempty_line(outputs: Sequence[str]) -> str | None:
    lines: list[str] = []
    for output in outputs:
        for line in output.splitlines():
            stripped = _ANSI_ESCAPE.sub("", line).strip()
            if stripped:
                lines.append(stripped)

    return next(
        (line for line in lines if "version" in line.casefold()),
        lines[0] if lines else None,
    )


def detect_tool(
    spec: ToolSpec,
    *,
    path: str | None = None,
    timeout: float = VERSION_TIMEOUT_SECONDS,
) -> ToolStatus:
    """Locate a tool and read one concise version line without invoking a shell."""

    executable_path = shutil.which(spec.executable, path=path)
    if executable_path is None:
        return ToolStatus(spec=spec, path=None, version=None)

    try:
        completed = subprocess.run(
            [executable_path, *spec.version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolStatus(
            spec=spec,
            path=executable_path,
            version=None,
            version_error=f"version check timed out after {timeout:g}s",
        )
    except OSError as error:
        return ToolStatus(
            spec=spec,
            path=executable_path,
            version=None,
            version_error=f"version check failed: {error}",
        )

    version = _first_nonempty_line((completed.stdout, completed.stderr))
    version_error = None
    if completed.returncode != 0:
        version_error = f"version check exited {completed.returncode}"
    elif version is None:
        version_error = "version output was empty"

    return ToolStatus(
        spec=spec,
        path=executable_path,
        version=version,
        version_error=version_error,
    )


def detect_all_tools(*, path: str | None = None) -> tuple[ToolStatus, ...]:
    """Return statuses in a stable, user-facing order."""

    return tuple(detect_tool(spec, path=path) for spec in TOOL_SPECS)


def require_tool(key: str, *, path: str | None = None) -> str:
    """Return an executable path or raise a one-line actionable error."""

    try:
        spec = _SPECS_BY_KEY[key]
    except KeyError as error:
        raise ValueError(f"Unknown external tool: {key}") from error

    status = detect_tool(spec, path=path)
    if status.path is None:
        raise ToolUnavailableError(
            f"{spec.executable} is required but was not found. Install it with: {spec.install_hint}"
        )
    return status.path


def format_tool_report(statuses: Sequence[ToolStatus]) -> str:
    """Format a deterministic doctor report with copy-pasteable fixes."""

    lines = ["External tools:"]
    for status in statuses:
        spec = status.spec
        if not status.available:
            lines.append(
                f"[missing] {spec.executable}: not found — install with: {spec.install_hint}"
            )
            continue

        detail = status.version or status.version_error or "version unknown"
        if status.version and status.version_error:
            detail = f"{status.version} ({status.version_error})"
        lines.append(f"[ok] {spec.executable}: {status.path} — {detail}")
    missing_keys = {status.spec.key for status in statuses if not status.available}
    brew_packages: list[str] = []
    if missing_keys & {"ffmpeg", "ffprobe"}:
        brew_packages.append("ffmpeg")
    if "mp4box" in missing_keys:
        brew_packages.append("mp4box")
    if "dovi_tool" in missing_keys:
        brew_packages.append("dovi_tool")
    if brew_packages:
        lines.extend(("", "Install missing tools:", f"brew install {' '.join(brew_packages)}"))
        if "dovi_tool" in missing_keys:
            lines.extend(
                (
                    "# If dovi_tool is unavailable from your Homebrew setup:",
                    "cargo install dovi_tool",
                )
            )
    return "\n".join(lines)
