from pathlib import Path

from reelhdr import cli
from reelhdr.pipeline import PlanError
from reelhdr.tools import TOOL_SPECS, ToolStatus


def test_doctor_prints_detection_report_and_exits_zero(monkeypatch, capsys) -> None:
    statuses = tuple(
        ToolStatus(
            spec=spec,
            path=f"/test-bin/{spec.executable}",
            version=f"{spec.executable} test-version",
        )
        for spec in TOOL_SPECS
    )
    monkeypatch.setattr(cli, "detect_all_tools", lambda: statuses)

    assert cli.main(["doctor"]) == 0

    output = capsys.readouterr().out
    assert "External tools:" in output
    assert "[ok] ffmpeg: /test-bin/ffmpeg" in output
    assert "[ok] MP4Box: /test-bin/MP4Box" in output


def test_verify_remains_an_explicit_stub(capsys) -> None:
    assert cli.main(["verify"]) == 0
    assert capsys.readouterr().out.strip() == "coming in the next phase"


def test_convert_dry_run_probes_and_prints_plan(monkeypatch, capsys) -> None:
    source = object()
    tools = object()
    plan = object()
    monkeypatch.setattr(cli, "probe_video", lambda path: source)
    monkeypatch.setattr(cli, "resolve_toolchain", lambda: tools)

    def fake_build(observed_source, output, **options):
        assert observed_source is source
        assert output == Path("out.mp4")
        assert options == {
            "toolchain": tools,
            "fast": True,
            "max_pq": 2400,
            "nominal_peak_nits": 1200.0,
        }
        return plan

    monkeypatch.setattr(cli, "build_conversion_plan", fake_build)
    monkeypatch.setattr(cli, "format_plan", lambda value: "ordered plan" if value is plan else "")

    result = cli.main(
        [
            "convert",
            "input.mov",
            "-o",
            "out.mp4",
            "--dry-run",
            "--fast",
            "--max-pq",
            "2400",
            "--pq-peak-nits",
            "1200",
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.strip() == "ordered plan"


def test_convert_reports_plan_errors_without_traceback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "probe_video", lambda _path: object())
    monkeypatch.setattr(cli, "resolve_toolchain", lambda: object())
    monkeypatch.setattr(
        cli,
        "build_conversion_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PlanError("bad input")),
    )

    assert cli.main(["convert", "input.mov", "-o", "out.mp4"]) == 2
    assert capsys.readouterr().err.strip() == "error: bad input"
