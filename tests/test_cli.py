from pathlib import Path
from types import SimpleNamespace

from reelhdr import cli
from reelhdr.pipeline import PlanError, ProgressEvent
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


def test_presets_prints_the_table(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "format_preset_table", lambda: "preset table")

    assert cli.main(["presets"]) == 0
    assert capsys.readouterr().out.strip() == "preset table"


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


def test_verify_hlg_selects_the_clean_hlg_contract(monkeypatch) -> None:
    observed = {}

    def fake_verify(_path, *, expectations):
        observed["expectations"] = expectations
        return _report()

    monkeypatch.setattr(cli, "verify_file", fake_verify)

    assert cli.main(["verify", "delivery.mp4", "--preset", "hlg"]) == 0
    assert observed["expectations"].expect_dolby_vision is False
    assert observed["expectations"].expect_amve is False


def test_convert_dry_run_probes_and_prints_plan(monkeypatch, capsys) -> None:
    source = object()
    tools = object()
    plan = object()
    monkeypatch.setattr(cli, "probe_video", lambda path: source)
    monkeypatch.setattr(cli, "resolve_toolchain", lambda _preset: tools)

    def fake_build(observed_source, output, **options):
        assert observed_source is source
        assert output == Path("out.mp4")
        assert options == {
            "toolchain": tools,
            "preset": "instagram-dv84",
            "fast": True,
            "max_pq": 2400,
            "nominal_peak_nits": 1200.0,
            "crf": 18,
            "bitrate": None,
            "fps": "passthrough",
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
    monkeypatch.setattr(cli, "resolve_toolchain", lambda _preset: object())
    monkeypatch.setattr(
        cli,
        "build_conversion_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PlanError("bad input")),
    )

    assert cli.main(["convert", "input.mov", "-o", "out.mp4"]) == 2
    error = capsys.readouterr().err
    assert "conversion failed at plan: bad input" in error
    assert "command: internal action plan" in error
    assert "likely fix:" in error


def test_convert_runs_verification_after_publishing(monkeypatch, capsys) -> None:
    source = SimpleNamespace(
        has_audio=True,
        audio_codec_name="aac",
        source_class=SimpleNamespace(value="sdr"),
    )
    monkeypatch.setattr(cli, "probe_video", lambda _path: source)
    monkeypatch.setattr(cli, "resolve_toolchain", lambda _preset: object())
    monkeypatch.setattr(cli, "build_conversion_plan", lambda *_args, **_kwargs: object())

    def fake_execute(_plan, *, force, progress):
        assert force is False
        progress(ProgressEvent("encode", 1.25))
        progress(ProgressEvent("rpu", 0.25))
        progress(ProgressEvent("mux", 0.1))
        progress(ProgressEvent("amve", 0.01))
        return Path("out.mp4")

    monkeypatch.setattr(cli, "execute_conversion", fake_execute)
    observed = {}

    def fake_verify(path, *, expectations):
        observed["path"] = path
        observed["expectations"] = expectations
        return _report()

    monkeypatch.setattr(cli, "verify_file", fake_verify)

    assert cli.main(["convert", "input.mov", "-o", "out.mp4"]) == 0

    output = capsys.readouterr().out
    assert "[ok] probe" in output
    assert "[ok] plan" in output
    assert "[ok] encode" in output
    assert "[ok] rpu" in output
    assert "[ok] mux" in output
    assert "[ok] amve" in output
    assert "[ok] verify" in output
    assert "wrote out.mp4" in output
    assert "VERDICT: PASS" in output
    assert observed["path"] == Path("out.mp4")
    assert observed["expectations"].expect_audio is True
    assert observed["expectations"].audio_codec == "aac"
    assert observed["expectations"].expect_dolby_vision is True
    assert observed["expectations"].expect_amve is True


def test_convert_flag_overrides_reach_the_plan(monkeypatch, capsys) -> None:
    source = object()
    observed = {}
    monkeypatch.setattr(cli, "probe_video", lambda _path: source)
    monkeypatch.setattr(cli, "resolve_toolchain", lambda preset: ("tools", preset))

    def fake_build(_source, _output, **options):
        observed.update(options)
        return object()

    monkeypatch.setattr(cli, "build_conversion_plan", fake_build)
    monkeypatch.setattr(cli, "format_plan", lambda _plan: "plan")

    result = cli.main(
        [
            "convert",
            "input.mov",
            "-o",
            "out.mp4",
            "--preset",
            "hlg",
            "--bitrate",
            "15M",
            "--fps",
            "24",
            "--max-pq",
            "2100",
            "--dry-run",
        ]
    )

    assert result == 0
    assert observed["preset"] == "hlg"
    assert observed["crf"] is None
    assert observed["bitrate"] == "15M"
    assert observed["fps"] == "24"
    assert observed["max_pq"] == 2100
    assert capsys.readouterr().out.strip() == "plan"
