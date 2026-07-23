from pathlib import Path
from types import SimpleNamespace

from reelhdr import cli
from reelhdr.pipeline import PlanError
from reelhdr.tools import TOOL_SPECS, ToolStatus
from reelhdr.verify import CheckStatus, VerifyCheck, VerifyReport


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


def _report(*, failing: bool = False) -> VerifyReport:
    return VerifyReport(
        path=Path("delivery.mp4"),
        checks=(
            VerifyCheck(
                check_id="video.codec",
                value_found="h264" if failing else "hevc",
                expectation="hevc",
                status=CheckStatus.FAIL if failing else CheckStatus.OK,
                explanation="test finding",
            ),
        ),
    )


def test_verify_json_passes_audio_expectations_and_exits_zero(
    monkeypatch,
    capsys,
) -> None:
    observed = {}

    def fake_verify(path, *, expectations):
        observed["path"] = path
        observed["expectations"] = expectations
        return _report()

    monkeypatch.setattr(cli, "verify_file", fake_verify)

    assert (
        cli.main(
            [
                "verify",
                "delivery.mp4",
                "--json",
                "--expect-audio",
                "aac",
            ]
        )
        == 0
    )

    assert observed["path"] == Path("delivery.mp4")
    assert observed["expectations"].expect_audio is True
    assert observed["expectations"].audio_codec == "aac"
    assert '"verdict": "pass"' in capsys.readouterr().out


def test_verify_human_report_exits_one_on_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "verify_file", lambda *_args, **_kwargs: _report(failing=True))

    assert cli.main(["verify", "delivery.mp4"]) == 1
    assert "VERDICT: FAIL" in capsys.readouterr().out


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


def test_convert_runs_verification_after_publishing(monkeypatch, capsys) -> None:
    source = SimpleNamespace(has_audio=True, audio_codec_name="aac")
    monkeypatch.setattr(cli, "probe_video", lambda _path: source)
    monkeypatch.setattr(cli, "resolve_toolchain", lambda: object())
    monkeypatch.setattr(cli, "build_conversion_plan", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "execute_conversion", lambda *_args, **_kwargs: Path("out.mp4"))
    observed = {}

    def fake_verify(path, *, expectations):
        observed["path"] = path
        observed["expectations"] = expectations
        return _report()

    monkeypatch.setattr(cli, "verify_file", fake_verify)

    assert cli.main(["convert", "input.mov", "-o", "out.mp4"]) == 0

    output = capsys.readouterr().out
    assert "wrote out.mp4" in output
    assert "VERDICT: PASS" in output
    assert observed["path"] == Path("out.mp4")
    assert observed["expectations"].expect_audio is True
    assert observed["expectations"].audio_codec == "aac"
