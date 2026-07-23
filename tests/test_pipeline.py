from __future__ import annotations

import io
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from reelhdr import pipeline
from reelhdr.pipeline import (
    ConversionError,
    ConversionPlan,
    PlanError,
    PlanStep,
    StepAction,
    Toolchain,
    build_conversion_plan,
    execute_conversion,
    format_plan,
)
from reelhdr.probe import SourceClass, VideoProbe

TOOLS = Toolchain(
    ffmpeg="/tools/ffmpeg",
    dovi_tool="/tools/dovi_tool",
    mp4box="/tools/MP4Box",
)


def test_hlg_toolchain_does_not_require_dovi_tool(monkeypatch) -> None:
    requested = []

    def fake_require(key):
        requested.append(key)
        return f"/tools/{key}"

    monkeypatch.setattr(pipeline, "require_tool", fake_require)

    tools = pipeline.resolve_toolchain("hlg")

    assert requested == ["ffmpeg", "mp4box"]
    assert tools.dovi_tool is None


def _probe(
    source_class: SourceClass,
    *,
    path: Path = Path("/input/source.mov"),
    codec_name: str = "h264",
    bit_depth: int = 8,
    has_audio: bool = True,
) -> VideoProbe:
    return VideoProbe(
        path=path,
        source_class=source_class,
        codec_name=codec_name,
        codec_profile=None,
        codec_tag_string=None,
        width=1080,
        height=1920,
        fps=Fraction(30000, 1001),
        frame_count=300,
        bit_depth=bit_depth,
        duration=10.01,
        has_audio=has_audio,
        pix_fmt="yuv420p10le" if bit_depth == 10 else "yuv420p",
        color_range="tv",
        color_space="bt2020nc" if source_class is not SourceClass.SDR else "bt709",
        color_transfer={
            SourceClass.SDR: "bt709",
            SourceClass.HLG: "arib-std-b67",
            SourceClass.PQ: "smpte2084",
            SourceClass.DOLBY_VISION: "arib-std-b67",
        }[source_class],
        color_primaries="bt2020" if source_class is not SourceClass.SDR else "bt709",
    )


@pytest.mark.parametrize(
    ("source_class", "first_key", "first_action"),
    [
        (SourceClass.SDR, "encode-sdr-base", StepAction.COMMAND),
        (SourceClass.HLG, "encode-hlg-base", StepAction.COMMAND),
        (SourceClass.PQ, "pq-to-hlg", StepAction.PQ_PIPE),
    ],
)
def test_each_source_class_builds_the_same_ordered_delivery_tail(
    source_class: SourceClass,
    first_key: str,
    first_action: StepAction,
) -> None:
    plan = build_conversion_plan(
        _probe(source_class),
        "/output/delivery.mp4",
        toolchain=TOOLS,
    )

    assert [step.key for step in plan.steps] == [
        first_key,
        "write-rpu-config",
        "generate-rpu",
        "inject-rpu",
        "mux",
        "insert-amve",
        "publish",
    ]
    assert plan.steps[0].action is first_action
    assert plan.max_pq == 2500
    assert "dvp=8.hlg2100" in plan.steps[4].commands[0].argv[3]
    assert "colr=nclx,9,18,9,no" in plan.steps[4].commands[0].argv[3]
    assert plan.steps[4].commands[0].argv[-1] == "{work}/muxed.mp4"


def test_sdr_is_tag_only_and_hlg_hevc_main10_is_stream_copied() -> None:
    sdr = build_conversion_plan(_probe(SourceClass.SDR), "out.mp4", toolchain=TOOLS)
    sdr_encode = sdr.steps[0]
    assert "tag-only" in (sdr_encode.note or "")
    assert (
        sdr_encode.commands[0].argv[sdr_encode.commands[0].argv.index("-vf") + 1]
        == "format=yuv420p10le"
    )

    hlg = build_conversion_plan(
        _probe(SourceClass.HLG, codec_name="hevc", bit_depth=10),
        "out.mp4",
        toolchain=TOOLS,
    )
    hlg_argv = hlg.steps[0].commands[0].argv
    assert hlg.steps[0].key == "normalize-hlg"
    assert hlg_argv[hlg_argv.index("-c:v") + 1] == "copy"
    bitstream_filters = hlg_argv[hlg_argv.index("-bsf:v") + 1]
    assert bitstream_filters.startswith("hevc_mp4toannexb,hevc_metadata=")
    assert "transfer_characteristics=18" in bitstream_filters


def test_pq_default_and_fast_modes_are_unambiguously_labeled() -> None:
    source = _probe(SourceClass.PQ)
    exact = build_conversion_plan(source, "out.mp4", toolchain=TOOLS)
    fast = build_conversion_plan(source, "out.mp4", toolchain=TOOLS, fast=True)

    assert exact.steps[0].action is StepAction.PQ_PIPE
    assert len(exact.steps[0].commands) == 2
    assert "full-fidelity" in (exact.steps[0].note or "")
    assert fast.steps[0].action is StepAction.COMMAND
    assert "approximation" in (fast.steps[0].note or "")
    assert (
        "in_transfer=smpte2084"
        in fast.steps[0].commands[0].argv[fast.steps[0].commands[0].argv.index("-vf") + 1]
    )


def test_dry_run_formats_every_external_argv_without_executing() -> None:
    plan = build_conversion_plan(_probe(SourceClass.PQ), "out.mp4", toolchain=TOOLS)

    output = format_plan(plan)

    assert "Reel-HDR conversion plan (dry run)" in output
    assert "preset: instagram-dv84" in output
    assert "source: pq" in output
    assert "decoder: /tools/ffmpeg" in output
    assert "dovi_tool: /tools/dovi_tool generate" in output
    assert "MP4Box: /tools/MP4Box -new -add" in output
    assert "$WORK/dv84.hevc" in output
    assert "internal: insert-amve" in output


def test_hlg_preset_has_no_rpu_or_amve_steps() -> None:
    plan = build_conversion_plan(
        _probe(SourceClass.SDR),
        "out.mp4",
        toolchain=Toolchain(
            ffmpeg="/tools/ffmpeg",
            dovi_tool=None,
            mp4box="/tools/MP4Box",
        ),
        preset="hlg",
    )

    assert [step.key for step in plan.steps] == [
        "encode-sdr-base",
        "mux",
        "publish",
    ]
    mux_spec = plan.steps[1].commands[0].argv[3]
    assert "dvp=" not in mux_spec
    assert "colr=nclx,9,18,9,no" in mux_spec


def test_numeric_fps_and_bitrate_override_passthrough_and_crf() -> None:
    plan = build_conversion_plan(
        _probe(SourceClass.SDR),
        "out.mp4",
        toolchain=TOOLS,
        fps="24",
        crf=None,
        bitrate="12M",
    )

    encode = plan.steps[0].commands[0].argv
    assert plan.output_fps == Fraction(24, 1)
    assert plan.frame_count == 240
    assert encode[encode.index("-fps_mode") + 1] == "cfr"
    assert "fps=24/1" in encode[encode.index("-vf") + 1]
    assert encode[encode.index("-b:v") + 1] == "12M"
    assert "-crf" not in encode


def test_rpu_config_applies_the_configurable_static_l1_ceiling() -> None:
    plan = build_conversion_plan(
        _probe(SourceClass.SDR),
        "out.mp4",
        toolchain=TOOLS,
        max_pq=2400,
    )

    config = pipeline._rpu_config(plan)

    assert config["length"] == 300
    assert config["default_metadata_blocks"] == [
        {"Level1": {"min_pq": 0, "max_pq": 2400, "avg_pq": 1200}}
    ]


def test_plan_rejects_dolby_input_invalid_metadata_and_odd_geometry() -> None:
    with pytest.raises(PlanError, match="already Dolby Vision"):
        build_conversion_plan(
            _probe(SourceClass.DOLBY_VISION),
            "out.mp4",
            toolchain=TOOLS,
        )
    with pytest.raises(PlanError, match="0 through 4095"):
        build_conversion_plan(
            _probe(SourceClass.SDR),
            "out.mp4",
            toolchain=TOOLS,
            max_pq=4096,
        )

    odd = replace(_probe(SourceClass.SDR), width=1079)
    with pytest.raises(PlanError, match="even width"):
        build_conversion_plan(odd, "out.mp4", toolchain=TOOLS)


def test_temp_workspace_is_removed_when_a_step_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "input.bin"
    source_path.write_bytes(b"input")
    source = _probe(SourceClass.SDR, path=source_path)
    observed_workspaces: list[Path] = []

    def fail_step(_step, *, plan, workspace, force):
        del plan, force
        observed_workspaces.append(workspace)
        (workspace / "partial").write_bytes(b"partial")
        raise ConversionError("planned failure")

    monkeypatch.setattr(pipeline, "_execute_step", fail_step)
    plan = ConversionPlan(
        input_path=source_path,
        output_path=tmp_path / "output.mp4",
        source=source,
        fast=False,
        max_pq=2500,
        nominal_peak_nits=1000.0,
        ambient_illuminance=3_140_000,
        ambient_light_x=15_635,
        ambient_light_y=16_450,
        frame_count=300,
        steps=(
            PlanStep(
                key="fail",
                description="fail",
                action=StepAction.COMMAND,
            ),
        ),
    )

    with pytest.raises(ConversionError, match="planned failure"):
        execute_conversion(plan)

    assert observed_workspaces
    assert not observed_workspaces[0].exists()
    assert not plan.output_path.exists()


def test_progress_reports_canonical_stages_and_hlg_skips(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "input.bin"
    source_path.write_bytes(b"input")
    plan = build_conversion_plan(
        _probe(SourceClass.SDR, path=source_path),
        tmp_path / "out.mp4",
        toolchain=Toolchain(
            ffmpeg="/tools/ffmpeg",
            dovi_tool=None,
            mp4box="/tools/MP4Box",
        ),
        preset="hlg",
    )
    monkeypatch.setattr(pipeline, "_execute_step", lambda *_args, **_kwargs: None)
    events = []

    execute_conversion(plan, progress=events.append)

    assert [(event.stage, event.skipped) for event in events] == [
        ("encode", False),
        ("rpu", True),
        ("mux", False),
        ("amve", True),
    ]
    assert all(event.elapsed_seconds >= 0 for event in events)


def test_frame_pipe_applies_the_shared_pq_to_hlg_math() -> None:
    input_samples = np.array([0, round(0.5080784215 * 65535), 65535], dtype="<u2")
    output = io.BytesIO()

    pipeline._convert_frame(input_samples.tobytes(), output, nominal_peak_nits=1000.0)

    converted = np.frombuffer(output.getvalue(), dtype="<u2") / 65535.0
    assert converted[0] == 0.0
    assert converted[1] == pytest.approx(0.544, abs=0.002)
    assert converted[2] == 1.0


def test_failed_tool_output_is_returned_as_a_bounded_diagnostic(
    monkeypatch,
) -> None:
    def fail(argv, **kwargs):
        kwargs["stdout"].write(b"synthetic failure detail\n")
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(ConversionError, match=r"ffmpeg exited 7") as raised:
        pipeline._run_command(("/tools/ffmpeg", "-version"))

    assert raised.value.step == "command"
    assert raised.value.commands == (("/tools/ffmpeg", "-version"),)
    assert raised.value.output_lines == ("synthetic failure detail",)
    diagnostic = pipeline.format_conversion_failure(raised.value)
    assert "command:" in diagnostic
    assert "/tools/ffmpeg -version" in diagnostic
    assert "synthetic failure detail" in diagnostic
    assert "likely fix:" in diagnostic


def test_failed_tool_diagnostic_keeps_only_the_last_15_lines(monkeypatch) -> None:
    def fail(argv, **kwargs):
        kwargs["stdout"].write("\n".join(f"line {index}" for index in range(20)).encode() + b"\n")
        return subprocess.CompletedProcess(argv, 9)

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(ConversionError) as raised:
        pipeline._run_command(("/tools/ffmpeg", "-i", "input.mov"), step="encode-sdr-base")

    assert len(raised.value.output_lines) == 15
    assert raised.value.output_lines[0] == "line 5"
    assert raised.value.output_lines[-1] == "line 19"
