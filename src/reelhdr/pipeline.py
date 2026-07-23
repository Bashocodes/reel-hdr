"""Neutral SDR/HLG/PQ to Dolby Vision Profile 8.4 conversion plans.

Planning and execution are deliberately separate.  A plan is inspectable data:
every external process is represented as an argv tuple and no shell is used.
The only content transform in the default pipeline is the standards-defined
PQ EOTF -> relative scene light -> HLG OETF conversion.

Reel-HDR produces Profile 8.4-compatible signalling and metadata.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO

import numpy as np

from reelhdr.amve import encode_amve_payload, insert_amve
from reelhdr.presets import (
    DEFAULT_CRF,
    DEFAULT_MAX_PQ,
    DEFAULT_PRESET,
    PresetName,
    get_preset,
    parse_fps,
)
from reelhdr.probe import SourceClass, VideoProbe
from reelhdr.tools import require_tool
from reelhdr.transfer import pq_to_hlg_scene_light

WORK_TOKEN = "{work}"
DEFAULT_PQ_NOMINAL_PEAK_NITS = 1000.0

# ISO/IEC 23008-2 ambient-viewing fields for a neutral D65, 314-lux indoor
# reference environment.  The integer scales are 1/10000 lux and 1/50000 for
# each chromaticity coordinate.
DEFAULT_AMBIENT_ILLUMINANCE = 3_140_000
DEFAULT_AMBIENT_LIGHT_X = 15_635
DEFAULT_AMBIENT_LIGHT_Y = 16_450

_LOG_TAIL_BYTES = 64 * 1024
_SAMPLE_CHUNK = 1_048_576


class PlanError(ValueError):
    """Raised when probe evidence cannot support a deterministic plan."""


class ConversionError(RuntimeError):
    """Raised with bounded, actionable details when a conversion step fails."""

    def __init__(
        self,
        message: str,
        *,
        step: str | None = None,
        commands: tuple[tuple[str, ...], ...] = (),
        output_lines: tuple[str, ...] = (),
        likely_fix: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.commands = commands
        self.output_lines = output_lines[-15:]
        self.likely_fix = likely_fix
        self.elapsed_seconds = elapsed_seconds


class StepAction(StrEnum):
    """Executor operation for an inspectable plan step."""

    COMMAND = "command"
    PQ_PIPE = "pq-pipe"
    WRITE_RPU_CONFIG = "write-rpu-config"
    INSERT_AMVE = "insert-amve"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class Toolchain:
    """Resolved external executables used by conversion."""

    ffmpeg: str
    dovi_tool: str | None
    mp4box: str


@dataclass(frozen=True, slots=True)
class Command:
    """One external process invocation."""

    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One ordered conversion operation."""

    key: str
    description: str
    action: StepAction
    commands: tuple[Command, ...] = ()
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ConversionPlan:
    """Complete, immutable conversion plan."""

    input_path: Path
    output_path: Path
    source: VideoProbe
    fast: bool
    max_pq: int
    nominal_peak_nits: float
    ambient_illuminance: int
    ambient_light_x: int
    ambient_light_y: int
    frame_count: int
    steps: tuple[PlanStep, ...]
    preset: PresetName = DEFAULT_PRESET
    crf: int | None = DEFAULT_CRF
    bitrate: str | None = None
    fps_passthrough: bool = True
    output_fps: Fraction | None = None


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One completed or intentionally skipped public conversion stage."""

    stage: str
    elapsed_seconds: float
    skipped: bool = False
    detail: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]


def resolve_toolchain(
    preset: str | PresetName = DEFAULT_PRESET,
) -> Toolchain:
    """Resolve only the executables required by the selected preset."""

    definition = get_preset(preset)
    return Toolchain(
        ffmpeg=require_tool("ffmpeg"),
        dovi_tool=require_tool("dovi_tool") if definition.dolby_vision else None,
        mp4box=require_tool("mp4box"),
    )


def build_conversion_plan(
    source: VideoProbe,
    output_path: str | Path,
    *,
    toolchain: Toolchain | None = None,
    preset: str | PresetName = DEFAULT_PRESET,
    fast: bool = False,
    max_pq: int = DEFAULT_MAX_PQ,
    nominal_peak_nits: float = DEFAULT_PQ_NOMINAL_PEAK_NITS,
    crf: int | None = DEFAULT_CRF,
    bitrate: str | None = None,
    fps: str = "passthrough",
    ambient_illuminance: int = DEFAULT_AMBIENT_ILLUMINANCE,
    ambient_light_x: int = DEFAULT_AMBIENT_LIGHT_X,
    ambient_light_y: int = DEFAULT_AMBIENT_LIGHT_Y,
) -> ConversionPlan:
    """Build a source-aware HLG or DV 8.4-compatible plan without running it."""

    definition = get_preset(preset)
    tools = toolchain or resolve_toolchain(definition.name)
    width, height = _required_geometry(source)
    source_fps = _required_fps(source)
    fps_override = parse_fps(fps)
    output_fps = source_fps if fps_override is None else fps_override
    fps_passthrough = fps_override is None
    frame_count = _required_frame_count(
        source,
        output_fps,
        passthrough=fps_passthrough,
    )
    _validate_metadata(
        max_pq=max_pq,
        nominal_peak_nits=nominal_peak_nits,
        ambient_illuminance=ambient_illuminance,
        ambient_light_x=ambient_light_x,
        ambient_light_y=ambient_light_y,
    )
    _validate_encoding(crf=crf, bitrate=bitrate)

    if source.source_class is SourceClass.DOLBY_VISION:
        raise PlanError("input is already Dolby Vision; conversion accepts SDR, HLG, or PQ sources")

    input_path = source.path.expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    base_hevc = f"{WORK_TOKEN}/base-hlg.hevc"
    rpu_config = f"{WORK_TOKEN}/rpu-config.json"
    rpu = f"{WORK_TOKEN}/metadata.rpu"
    injected = f"{WORK_TOKEN}/dv84.hevc"
    muxed = f"{WORK_TOKEN}/muxed.mp4"

    encode = _build_encode_step(
        source,
        input_path=input_path,
        base_hevc=base_hevc,
        tools=tools,
        width=width,
        height=height,
        fps=output_fps,
        fps_passthrough=fps_passthrough,
        fast=fast,
        crf=crf,
        bitrate=bitrate,
    )
    steps = [encode]
    if definition.dolby_vision:
        if tools.dovi_tool is None:
            raise PlanError("the instagram-dv84 preset requires dovi_tool")
        steps.extend(
            (
                PlanStep(
                    key="write-rpu-config",
                    description=(
                        f"Write static Profile 8.4 generator metadata for {frame_count} frames "
                        f"with L1 max_pq={max_pq}."
                    ),
                    action=StepAction.WRITE_RPU_CONFIG,
                ),
                PlanStep(
                    key="generate-rpu",
                    description="Generate a Dolby Vision Profile 8.4-compatible RPU stream.",
                    action=StepAction.COMMAND,
                    commands=(
                        Command(
                            "dovi_tool",
                            (
                                tools.dovi_tool,
                                "generate",
                                "--json",
                                rpu_config,
                                "--rpu-out",
                                rpu,
                                "--profile",
                                "8.4",
                            ),
                        ),
                    ),
                ),
                PlanStep(
                    key="inject-rpu",
                    description="Inject one Profile 8.4 RPU NAL unit per HEVC frame.",
                    action=StepAction.COMMAND,
                    commands=(
                        Command(
                            "dovi_tool",
                            (
                                tools.dovi_tool,
                                "inject-rpu",
                                "--input",
                                base_hevc,
                                "--rpu-in",
                                rpu,
                                "--output",
                                injected,
                            ),
                        ),
                    ),
                ),
            )
        )

    mux_input = injected if definition.dolby_vision else base_hevc
    steps.append(
        PlanStep(
            key="mux",
            description=(
                "Mux the HLG-compatible video and optional source audio into an MP4 container."
            ),
            action=StepAction.COMMAND,
            commands=(
                Command(
                    "MP4Box",
                    _mp4box_argv(
                        tools.mp4box,
                        video_path=mux_input,
                        input_path=input_path,
                        output_path=muxed,
                        fps=output_fps,
                        has_audio=source.has_audio,
                        dolby_vision=definition.dolby_vision,
                    ),
                ),
            ),
        )
    )
    if definition.amve:
        steps.append(
            PlanStep(
                key="insert-amve",
                description="Insert the ambient-viewing box into the video VisualSampleEntry.",
                action=StepAction.INSERT_AMVE,
            )
        )
    steps.append(
        PlanStep(
            key="publish",
            description="Atomically publish the completed MP4 at the requested path.",
            action=StepAction.PUBLISH,
        )
    )

    return ConversionPlan(
        input_path=input_path,
        output_path=destination,
        source=source,
        fast=fast,
        max_pq=max_pq,
        nominal_peak_nits=nominal_peak_nits,
        ambient_illuminance=ambient_illuminance,
        ambient_light_x=ambient_light_x,
        ambient_light_y=ambient_light_y,
        frame_count=frame_count,
        steps=tuple(steps),
        preset=definition.name,
        crf=crf,
        bitrate=bitrate,
        fps_passthrough=fps_passthrough,
        output_fps=output_fps,
    )


def format_plan(plan: ConversionPlan) -> str:
    """Render a stable, shell-readable dry-run without executing anything."""

    mode = "fast approximation" if plan.fast else "default"
    lines = [
        "Reel-HDR conversion plan (dry run)",
        f"preset: {plan.preset.value}",
        f"source: {plan.source.source_class.value}",
        f"input: {plan.input_path}",
        f"output: {plan.output_path}",
        f"frames: {plan.frame_count}",
        (
            "fps: passthrough"
            if plan.fps_passthrough
            else f"fps: {_fraction_text(plan.output_fps or _required_fps(plan.source))}"
        ),
        f"rate control: {_rate_control_text(plan.crf, plan.bitrate)}",
        f"mode: {mode}",
        f"workspace: {WORK_TOKEN} (temporary at execution)",
    ]
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. {step.key}: {step.description}")
        if step.note:
            lines.append(f"   note: {step.note}")
        for command in step.commands:
            argv = tuple(_display_arg(argument) for argument in command.argv)
            lines.append(f"   {command.label}: {shlex.join(argv)}")
        if not step.commands:
            lines.append(f"   internal: {step.action.value}")
    return "\n".join(lines)


def execute_conversion(
    plan: ConversionPlan,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> Path:
    """Execute a plan in a disposable workspace and atomically publish it."""

    if not plan.input_path.is_file():
        raise ConversionError(f"input file does not exist: {plan.input_path}")
    if plan.input_path == plan.output_path:
        raise ConversionError("input and output paths must be different")
    if plan.output_path.exists() and not force:
        raise ConversionError(
            f"output already exists: {plan.output_path} (pass --force to replace it)"
        )

    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reelhdr-") as temporary:
        workspace = Path(temporary)
        staged_steps = {
            stage: tuple(step for step in plan.steps if _progress_stage(step, plan) == stage)
            for stage in ("encode", "rpu", "mux", "amve")
        }
        known_steps = {step for steps in staged_steps.values() for step in steps}
        for stage in ("encode", "rpu", "mux", "amve"):
            steps = staged_steps[stage]
            if not steps:
                if progress is not None:
                    progress(
                        ProgressEvent(
                            stage=stage,
                            elapsed_seconds=0.0,
                            skipped=True,
                            detail=f"not used by preset {plan.preset.value}",
                        )
                    )
                continue
            started = time.monotonic()
            for step in steps:
                _execute_step_with_context(
                    step,
                    plan=plan,
                    workspace=workspace,
                    force=force,
                    stage=stage,
                    started=started,
                )
            if progress is not None:
                progress(
                    ProgressEvent(
                        stage=stage,
                        elapsed_seconds=time.monotonic() - started,
                    )
                )

        for step in plan.steps:
            if step in known_steps:
                continue
            started = time.monotonic()
            _execute_step_with_context(
                step,
                plan=plan,
                workspace=workspace,
                force=force,
                stage=step.key,
                started=started,
            )
            if progress is not None:
                progress(
                    ProgressEvent(
                        stage=step.key,
                        elapsed_seconds=time.monotonic() - started,
                    )
                )
    return plan.output_path


def _execute_step_with_context(
    step: PlanStep,
    *,
    plan: ConversionPlan,
    workspace: Path,
    force: bool,
    stage: str,
    started: float,
) -> None:
    try:
        _execute_step(step, plan=plan, workspace=workspace, force=force)
    except ConversionError as error:
        if error.step is not None:
            error.elapsed_seconds = time.monotonic() - started
            raise
        raise ConversionError(
            str(error),
            step=stage,
            commands=error.commands or _resolved_commands(step, workspace),
            output_lines=error.output_lines,
            likely_fix=error.likely_fix or _likely_fix(stage),
            elapsed_seconds=time.monotonic() - started,
        ) from error
    except (OSError, ValueError) as error:
        raise ConversionError(
            f"{step.key} failed: {error}",
            step=stage,
            commands=_resolved_commands(step, workspace),
            likely_fix=_likely_fix(stage),
            elapsed_seconds=time.monotonic() - started,
        ) from error


def _build_encode_step(
    source: VideoProbe,
    *,
    input_path: Path,
    base_hevc: str,
    tools: Toolchain,
    width: int,
    height: int,
    fps: Fraction,
    fps_passthrough: bool,
    fast: bool,
    crf: int | None,
    bitrate: str | None,
) -> PlanStep:
    if source.source_class is SourceClass.PQ and not fast:
        decoder = (
            tools.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-an",
            "-fps_mode",
            "passthrough" if fps_passthrough else "cfr",
            *(() if fps_passthrough else ("-vf", f"fps={_fraction_text(fps)}")),
            "-pix_fmt",
            "gbrp16le",
            "-f",
            "rawvideo",
            "pipe:1",
        )
        encoder = _raw_rgb_encoder_argv(
            tools.ffmpeg,
            output_path=base_hevc,
            width=width,
            height=height,
            fps=fps,
            crf=crf,
            bitrate=bitrate,
        )
        return PlanStep(
            key="pq-to-hlg",
            description=(
                "Decode PQ frames, apply PQ EOTF -> relative scene light -> HLG OETF "
                "with NumPy, then encode Main 10 HEVC."
            ),
            action=StepAction.PQ_PIPE,
            commands=(
                Command("decoder", decoder),
                Command("encoder", encoder),
            ),
            note="full-fidelity frame-math path; no retag-only shortcut",
        )

    if source.source_class is SourceClass.PQ:
        video_filter = (
            "scale="
            "in_color_matrix=bt2020:"
            "out_color_matrix=bt2020:"
            "in_transfer=smpte2084:"
            "out_transfer=arib-std-b67:"
            "in_primaries=bt2020:"
            "out_primaries=bt2020:"
            "in_range=tv:"
            "out_range=tv:"
            "flags=accurate_rnd+full_chroma_int,"
            "format=yuv420p10le"
        )
        return PlanStep(
            key="pq-to-hlg-fast",
            description=(
                "Approximate PQ-to-HLG conversion with FFmpeg swscale and encode Main 10 HEVC."
            ),
            action=StepAction.COMMAND,
            commands=(
                Command(
                    "ffmpeg",
                    _file_encoder_argv(
                        tools.ffmpeg,
                        input_path=input_path,
                        output_path=base_hevc,
                        video_filter=video_filter,
                        fps=fps,
                        fps_passthrough=fps_passthrough,
                        crf=crf,
                        bitrate=bitrate,
                    ),
                ),
            ),
            note=(
                "--fast is an FFmpeg-filter approximation; the default NumPy path "
                "implements the BT.2100 transfer equations directly"
            ),
        )

    if (
        source.source_class is SourceClass.HLG
        and source.codec_name in {"hevc", "h265"}
        and source.bit_depth == 10
        and fps_passthrough
    ):
        return PlanStep(
            key="normalize-hlg",
            description=(
                "Pass through the existing Main 10 HEVC pixel values and normalize "
                "HLG/BT.2020 bitstream signaling."
            ),
            action=StepAction.COMMAND,
            commands=(
                Command(
                    "ffmpeg",
                    (
                        tools.ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-y",
                        "-i",
                        str(input_path),
                        "-map",
                        "0:v:0",
                        "-an",
                        "-c:v",
                        "copy",
                        "-bsf:v",
                        (
                            "hevc_mp4toannexb,"
                            "hevc_metadata="
                            "colour_primaries=9:"
                            "transfer_characteristics=18:"
                            "matrix_coefficients=9:"
                            "video_full_range_flag=0"
                        ),
                        "-f",
                        "hevc",
                        base_hevc,
                    ),
                ),
            ),
            note="compressed HEVC samples are copied; no transfer or look transform",
        )

    source_label = "HLG" if source.source_class is SourceClass.HLG else "SDR"
    note = (
        "signal values are preserved through a 10-bit transcode; no transfer curve is applied"
        if source.source_class is SourceClass.HLG
        else "tag-only neutral brightness mapping; no tone curve or creative grade"
    )
    return PlanStep(
        key=f"encode-{source.source_class.value}-base",
        description=(f"Encode the {source_label} signal as HLG-tagged, BT.2020 Main 10 HEVC."),
        action=StepAction.COMMAND,
        commands=(
            Command(
                "ffmpeg",
                _file_encoder_argv(
                    tools.ffmpeg,
                    input_path=input_path,
                    output_path=base_hevc,
                    video_filter="format=yuv420p10le",
                    fps=fps,
                    fps_passthrough=fps_passthrough,
                    crf=crf,
                    bitrate=bitrate,
                ),
            ),
        ),
        note=note,
    )


def _file_encoder_argv(
    ffmpeg: str,
    *,
    input_path: Path,
    output_path: str,
    video_filter: str,
    fps: Fraction,
    fps_passthrough: bool,
    crf: int | None,
    bitrate: str | None,
) -> tuple[str, ...]:
    filters = video_filter
    timing_args: tuple[str, ...] = ("-fps_mode", "passthrough")
    if not fps_passthrough:
        filters = f"{filters},fps={_fraction_text(fps)}"
        timing_args = ("-fps_mode", "cfr")
    return (
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        filters,
        *timing_args,
        *_x265_output_args(crf=crf, bitrate=bitrate),
        output_path,
    )


def _raw_rgb_encoder_argv(
    ffmpeg: str,
    *,
    output_path: str,
    width: int,
    height: int,
    fps: Fraction,
    crf: int | None,
    bitrate: str | None,
) -> tuple[str, ...]:
    return (
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "gbrp16le",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        _fraction_text(fps),
        "-i",
        "pipe:0",
        "-an",
        "-vf",
        (
            "scale="
            "in_range=full:"
            "out_range=tv:"
            "out_color_matrix=bt2020:"
            "flags=accurate_rnd+full_chroma_int,"
            "format=yuv420p10le"
        ),
        *_x265_output_args(crf=crf, bitrate=bitrate),
        output_path,
    )


def _x265_output_args(
    *,
    crf: int | None,
    bitrate: str | None,
) -> tuple[str, ...]:
    rate_control = ("-b:v", bitrate) if bitrate is not None else ("-crf", str(crf))
    return (
        "-c:v",
        "libx265",
        "-pix_fmt",
        "yuv420p10le",
        "-color_range",
        "tv",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "arib-std-b67",
        "-colorspace",
        "bt2020nc",
        "-x265-params",
        ("repeat-headers=1:aud=1:colorprim=9:transfer=18:colormatrix=9:range=limited"),
        *rate_control,
        "-f",
        "hevc",
    )


def _mp4box_argv(
    mp4box: str,
    *,
    video_path: str,
    input_path: Path,
    output_path: str,
    fps: Fraction,
    has_audio: bool,
    dolby_vision: bool,
) -> tuple[str, ...]:
    video_spec = f"{video_path}:fps={_fraction_text(fps)}"
    if dolby_vision:
        video_spec += ":dvp=8.hlg2100"
    video_spec += ":colr=nclx,9,18,9,no"
    argv = [mp4box, "-new", "-add", video_spec]
    if has_audio:
        argv.extend(("-add", f"{input_path}#audio"))
    argv.append(output_path)
    return tuple(argv)


def _execute_step(
    step: PlanStep,
    *,
    plan: ConversionPlan,
    workspace: Path,
    force: bool,
) -> None:
    if step.action is StepAction.COMMAND:
        for command in step.commands:
            _run_command(
                _resolve_argv(command.argv, workspace),
                step=step.key,
            )
        return
    if step.action is StepAction.PQ_PIPE:
        decoder, encoder = step.commands
        _run_pq_pipe(
            _resolve_argv(decoder.argv, workspace),
            _resolve_argv(encoder.argv, workspace),
            width=_required_geometry(plan.source)[0],
            height=_required_geometry(plan.source)[1],
            expected_frames=plan.frame_count,
            nominal_peak_nits=plan.nominal_peak_nits,
            step=step.key,
        )
        return
    if step.action is StepAction.WRITE_RPU_CONFIG:
        config_path = workspace / "rpu-config.json"
        config_path.write_text(
            json.dumps(_rpu_config(plan), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    if step.action is StepAction.INSERT_AMVE:
        source_path = workspace / "muxed.mp4"
        destination = workspace / "with-amve.mp4"
        payload = encode_amve_payload(
            plan.ambient_illuminance,
            plan.ambient_light_x,
            plan.ambient_light_y,
        )
        destination.write_bytes(insert_amve(source_path.read_bytes(), payload))
        return
    if step.action is StepAction.PUBLISH:
        artifact = "with-amve.mp4" if plan.preset is PresetName.INSTAGRAM_DV84 else "muxed.mp4"
        _atomic_publish(workspace / artifact, plan.output_path, force=force)
        return
    raise ConversionError(f"unsupported plan action: {step.action}")


def _run_command(argv: Sequence[str], *, step: str = "command") -> None:
    with tempfile.SpooledTemporaryFile(max_size=_LOG_TAIL_BYTES * 2) as log:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            raise ConversionError(
                f"could not run {argv[0]}: {error}",
                step=step,
                commands=(tuple(argv),),
                likely_fix=_likely_fix(_progress_stage_key(step)),
            ) from error
        if completed.returncode != 0:
            output_lines = _log_tail_lines(log)
            raise ConversionError(
                f"{Path(argv[0]).name} exited {completed.returncode}",
                step=step,
                commands=(tuple(argv),),
                output_lines=output_lines,
                likely_fix=_likely_fix(_progress_stage_key(step)),
            )


def _run_pq_pipe(
    decoder_argv: Sequence[str],
    encoder_argv: Sequence[str],
    *,
    width: int,
    height: int,
    expected_frames: int,
    nominal_peak_nits: float,
    step: str = "pq-to-hlg",
) -> None:
    frame_bytes = width * height * 3 * np.dtype("<u2").itemsize
    with (
        tempfile.SpooledTemporaryFile(max_size=_LOG_TAIL_BYTES * 2) as decoder_log,
        tempfile.SpooledTemporaryFile(max_size=_LOG_TAIL_BYTES * 2) as encoder_log,
    ):
        decoder: subprocess.Popen[bytes] | None = None
        encoder: subprocess.Popen[bytes] | None = None
        try:
            decoder = subprocess.Popen(
                decoder_argv,
                stdout=subprocess.PIPE,
                stderr=decoder_log,
            )
            encoder = subprocess.Popen(
                encoder_argv,
                stdin=subprocess.PIPE,
                stderr=encoder_log,
            )
            if decoder.stdout is None or encoder.stdin is None:
                raise ConversionError("could not establish the PQ frame pipe")

            frames = 0
            while True:
                frame = _read_exact_or_eof(decoder.stdout, frame_bytes)
                if frame is None:
                    break
                _convert_frame(frame, encoder.stdin, nominal_peak_nits)
                frames += 1
            encoder.stdin.close()
            decoder_code = decoder.wait()
            encoder_code = encoder.wait()
        except (BrokenPipeError, OSError) as error:
            _stop_process(decoder)
            _stop_process(encoder)
            raise ConversionError(
                f"PQ frame pipe failed: {error}",
                step=step,
                commands=(tuple(decoder_argv), tuple(encoder_argv)),
                output_lines=_combined_pipe_log_lines(decoder_log, encoder_log),
                likely_fix=_likely_fix("encode"),
            ) from error
        except Exception:
            _stop_process(decoder)
            _stop_process(encoder)
            raise

        if decoder_code != 0 or encoder_code != 0:
            raise ConversionError(
                f"PQ frame pipe exited decoder={decoder_code}, encoder={encoder_code}",
                step=step,
                commands=(tuple(decoder_argv), tuple(encoder_argv)),
                output_lines=_combined_pipe_log_lines(decoder_log, encoder_log),
                likely_fix=_likely_fix("encode"),
            )
        if frames != expected_frames:
            raise ConversionError(
                f"PQ frame count mismatch: converted {frames}, expected {expected_frames}",
                step=step,
                commands=(tuple(decoder_argv), tuple(encoder_argv)),
                output_lines=(),
                likely_fix=(
                    "use --fps passthrough or inspect the source for variable/malformed timestamps"
                ),
            )


def _read_exact_or_eof(stream: BinaryIO, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if remaining == size:
                return None
            received = size - remaining
            raise ConversionError(
                f"decoder ended in a partial raw frame ({received} of {size} bytes)"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _convert_frame(
    frame: bytes,
    output: BinaryIO,
    nominal_peak_nits: float,
) -> None:
    samples = np.frombuffer(frame, dtype="<u2")
    for offset in range(0, samples.size, _SAMPLE_CHUNK):
        source = samples[offset : offset + _SAMPLE_CHUNK].astype(np.float64)
        hlg = pq_to_hlg_scene_light(
            source / np.float64(65535.0),
            nominal_peak_nits=nominal_peak_nits,
        )
        encoded = np.rint(np.asarray(hlg) * 65535.0).astype("<u2")
        output.write(encoded.tobytes())


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _rpu_config(plan: ConversionPlan) -> dict[str, object]:
    # L1 requires min, max, and average PQ fields.  The midpoint is a static,
    # neutral declaration rather than measured or content-adaptive metadata.
    return {
        "cm_version": "V40",
        "length": plan.frame_count,
        "default_metadata_blocks": [
            {
                "Level1": {
                    "min_pq": 0,
                    "max_pq": plan.max_pq,
                    "avg_pq": plan.max_pq // 2,
                }
            }
        ],
    }


def _atomic_publish(source: Path, destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        raise ConversionError(f"output already exists: {destination} (pass --force to replace it)")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _resolve_argv(argv: Sequence[str], workspace: Path) -> tuple[str, ...]:
    return tuple(argument.replace(WORK_TOKEN, str(workspace)) for argument in argv)


def _display_arg(argument: str) -> str:
    return argument.replace(WORK_TOKEN, "$WORK")


def _required_geometry(source: VideoProbe) -> tuple[int, int]:
    if source.width is None or source.height is None:
        raise PlanError("ffprobe did not report a usable video resolution")
    if source.width % 2 or source.height % 2:
        raise PlanError("4:2:0 Main 10 output requires even width and height")
    return source.width, source.height


def _required_fps(source: VideoProbe) -> Fraction:
    if source.fps is None or source.fps <= 0:
        raise PlanError("ffprobe did not report a usable frame rate")
    return source.fps


def _required_frame_count(
    source: VideoProbe,
    fps: Fraction,
    *,
    passthrough: bool,
) -> int:
    if passthrough and source.frame_count is not None and source.frame_count > 0:
        return source.frame_count
    if source.duration is not None and source.duration > 0:
        estimated = round(source.duration * float(fps))
        if estimated > 0:
            return estimated
    raise PlanError("ffprobe did not report enough timing data to determine frame count")


def _validate_metadata(
    *,
    max_pq: int,
    nominal_peak_nits: float,
    ambient_illuminance: int,
    ambient_light_x: int,
    ambient_light_y: int,
) -> None:
    if not 0 <= max_pq <= 4095:
        raise PlanError("max_pq must be an integer from 0 through 4095")
    if not np.isfinite(nominal_peak_nits) or nominal_peak_nits <= 0:
        raise PlanError("nominal_peak_nits must be finite and greater than zero")
    if not 0 <= ambient_illuminance <= 0xFFFFFFFF:
        raise PlanError("ambient_illuminance must fit an unsigned 32-bit field")
    for name, value in (
        ("ambient_light_x", ambient_light_x),
        ("ambient_light_y", ambient_light_y),
    ):
        if not 0 <= value <= 0xFFFF:
            raise PlanError(f"{name} must fit an unsigned 16-bit field")


def _validate_encoding(*, crf: int | None, bitrate: str | None) -> None:
    if crf is not None and bitrate is not None:
        raise PlanError("crf and bitrate are mutually exclusive")
    if crf is None and bitrate is None:
        raise PlanError("either crf or bitrate must be selected")
    if crf is not None and not 0 <= crf <= 51:
        raise PlanError("crf must be an integer from 0 through 51")


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _rate_control_text(crf: int | None, bitrate: str | None) -> str:
    return f"bitrate {bitrate}" if bitrate is not None else f"CRF {crf}"


def _progress_stage(step: PlanStep, plan: ConversionPlan) -> str:
    if step.key.startswith(("encode-", "normalize-", "pq-to-hlg")):
        return "encode"
    if step.key in {"write-rpu-config", "generate-rpu", "inject-rpu"}:
        return "rpu"
    if step.key == "mux":
        return "mux"
    if step.key == "insert-amve":
        return "amve"
    if step.key == "publish":
        return "amve" if plan.preset is PresetName.INSTAGRAM_DV84 else "mux"
    return step.key


def _progress_stage_key(step: str) -> str:
    if step.startswith(("encode-", "normalize-", "pq-to-hlg")):
        return "encode"
    if step in {"write-rpu-config", "generate-rpu", "inject-rpu"}:
        return "rpu"
    if step == "insert-amve":
        return "amve"
    return step


def _resolved_commands(step: PlanStep, workspace: Path) -> tuple[tuple[str, ...], ...]:
    return tuple(_resolve_argv(command.argv, workspace) for command in step.commands)


def _likely_fix(stage: str) -> str:
    return {
        "encode": "run reelhdr doctor, then confirm FFmpeg can decode the input video",
        "rpu": "install or update dovi_tool and retry the instagram-dv84 preset",
        "mux": "install or update MP4Box and confirm the source audio is readable",
        "amve": "confirm the destination has free space and the MP4 was not truncated",
        "publish": "use --force for an existing output and confirm the destination is writable",
    }.get(stage, "run reelhdr doctor and inspect the reported command output")


def _log_tail_lines(log: BinaryIO, *, limit: int = 15) -> tuple[str, ...]:
    log.seek(0, os.SEEK_END)
    size = log.tell()
    log.seek(max(0, size - _LOG_TAIL_BYTES))
    text = log.read().decode("utf-8", errors="replace")
    return tuple(text.splitlines()[-limit:])


def _combined_pipe_log_lines(
    decoder_log: BinaryIO,
    encoder_log: BinaryIO,
) -> tuple[str, ...]:
    decoder = _log_tail_lines(decoder_log, limit=7)
    encoder = _log_tail_lines(encoder_log, limit=7)
    lines: list[str] = []
    if decoder:
        lines.extend(("decoder:", *decoder))
    if encoder:
        lines.extend(("encoder:", *encoder))
    return tuple(lines[-15:])


def format_conversion_failure(error: ConversionError) -> str:
    """Format a bounded diagnostic with the failing command and likely fix."""

    stage = error.step or "unknown"
    elapsed = f" after {error.elapsed_seconds:.2f}s" if error.elapsed_seconds is not None else ""
    lines = [f"conversion failed at {stage}{elapsed}: {error}"]
    if error.commands:
        label = "command" if len(error.commands) == 1 else "commands"
        lines.append(f"{label}:")
        lines.extend(f"  {shlex.join(command)}" for command in error.commands)
    else:
        lines.append(f"command: internal action {stage}")
    lines.append("last tool output (up to 15 lines):")
    if error.output_lines:
        lines.extend(f"  {line}" for line in error.output_lines)
    else:
        lines.append("  (no tool output)")
    lines.append(f"likely fix: {error.likely_fix or _likely_fix(stage)}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_PQ",
    "DEFAULT_PQ_NOMINAL_PEAK_NITS",
    "ConversionError",
    "ConversionPlan",
    "PlanError",
    "PlanStep",
    "ProgressEvent",
    "StepAction",
    "Toolchain",
    "build_conversion_plan",
    "execute_conversion",
    "format_conversion_failure",
    "format_plan",
    "resolve_toolchain",
]
