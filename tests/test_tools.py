from pathlib import Path

import pytest

from reelhdr.tools import (
    TOOL_SPECS,
    ToolUnavailableError,
    detect_all_tools,
    format_tool_report,
    require_tool,
)


def _write_fake_tool(directory: Path, executable: str) -> None:
    tool = directory / executable
    if executable == "MP4Box":
        tool.write_text(
            "#!/bin/sh\n"
            "printf '\\033[31m%s\\033[0m\\n' 'platform diagnostic' >&2\n"
            "printf '%s\\n' 'MP4Box - GPAC version test' >&2\n"
        )
    else:
        tool.write_text(f"#!/bin/sh\nprintf '%s\\n' '{executable} test-version'\n")
    tool.chmod(0o755)


def test_detection_uses_the_supplied_path_and_reports_versions(tmp_path: Path) -> None:
    for spec in TOOL_SPECS:
        _write_fake_tool(tmp_path, spec.executable)

    statuses = detect_all_tools(path=str(tmp_path))

    assert [status.spec.key for status in statuses] == [
        "ffmpeg",
        "ffprobe",
        "dovi_tool",
        "mp4box",
    ]
    assert all(status.available for status in statuses)
    assert all(
        status.path is not None and status.path.startswith(str(tmp_path)) for status in statuses
    )
    assert [status.version for status in statuses] == [
        "ffmpeg test-version",
        "ffprobe test-version",
        "dovi_tool test-version",
        "MP4Box - GPAC version test",
    ]


def test_missing_tools_include_one_line_install_hints(tmp_path: Path) -> None:
    statuses = detect_all_tools(path=str(tmp_path))
    report = format_tool_report(statuses)

    assert all(not status.available for status in statuses)
    assert "[missing] ffmpeg: not found — install with: brew install ffmpeg" in report
    assert "[missing] MP4Box: not found — install with: brew install mp4box" in report
    assert "cargo install dovi_tool" in report

    with pytest.raises(ToolUnavailableError) as raised:
        require_tool("mp4box", path=str(tmp_path))

    message = str(raised.value)
    assert "\n" not in message
    assert message == "MP4Box is required but was not found. Install it with: brew install mp4box"
