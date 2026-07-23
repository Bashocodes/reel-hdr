"""Command-line interface for Reel-HDR."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from reelhdr.pipeline import (
    DEFAULT_PQ_NOMINAL_PEAK_NITS,
    ConversionError,
    PlanError,
    ProgressEvent,
    build_conversion_plan,
    execute_conversion,
    format_conversion_failure,
    format_plan,
    resolve_toolchain,
)
from reelhdr.presets import (
    DEFAULT_PRESET,
    PRESETS,
    format_preset_table,
    resolve_options,
)
from reelhdr.probe import ProbeError, probe_video
from reelhdr.tools import ToolUnavailableError, detect_all_tools, format_tool_report
from reelhdr.verify import (
    VerifyExpectations,
    format_human_report,
    format_json_report,
    verify_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reelhdr",
        description="Convert and verify local HDR reels.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser(
        "doctor",
        help="Report availability and versions of external video tools.",
    )
    doctor.set_defaults(handler=_run_doctor)

    presets = subcommands.add_parser(
        "presets",
        help="List conversion presets and their defaults.",
    )
    presets.set_defaults(handler=_run_presets)

    convert = subcommands.add_parser(
        "convert",
        help="Convert a video to the target delivery format.",
    )
    convert.add_argument("input", type=Path, help="SDR, HLG, or PQ input video.")
    convert.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Destination MP4 path.",
    )
    convert.add_argument(
        "--preset",
        choices=tuple(preset.name.value for preset in PRESETS),
        default=DEFAULT_PRESET.value,
        help="Output contract (default: instagram-dv84).",
    )
    convert.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the complete ordered plan without converting.",
    )
    convert.add_argument(
        "--fast",
        action="store_true",
        help=(
            "Use FFmpeg's approximate PQ-to-HLG filter path instead of the "
            "default frame-accurate NumPy path."
        ),
    )
    convert.add_argument(
        "--max-pq",
        type=int,
        default=None,
        help="Override the preset's Dolby Vision L1 maximum PQ code (0-4095).",
    )
    rate_control = convert.add_mutually_exclusive_group()
    rate_control.add_argument(
        "--crf",
        type=int,
        default=None,
        help="Override x265 constant-rate-factor quality (0-51; default: 18).",
    )
    rate_control.add_argument(
        "--bitrate",
        default=None,
        metavar="RATE",
        help="Use an FFmpeg video bitrate such as 12M instead of CRF.",
    )
    convert.add_argument(
        "--fps",
        default=None,
        metavar="FPS",
        help="Frame-rate policy: passthrough (default), decimal, or fraction such as 30000/1001.",
    )
    convert.add_argument(
        "--pq-peak-nits",
        type=float,
        default=DEFAULT_PQ_NOMINAL_PEAK_NITS,
        help=("PQ luminance used as relative HLG scene-light 1.0 (default: 1000)."),
    )
    convert.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output only after conversion completes.",
    )
    convert.set_defaults(handler=_run_convert)

    verify = subcommands.add_parser(
        "verify",
        help="Verify an encoded video against the delivery contract.",
    )
    verify.add_argument("file", type=Path, help="MP4 deliverable to inspect read-only.")
    verify.add_argument(
        "--preset",
        choices=tuple(preset.name.value for preset in PRESETS),
        default=DEFAULT_PRESET.value,
        help="Contract to verify against (default: instagram-dv84).",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        help="Emit only the machine-readable verification report.",
    )
    verify.add_argument(
        "--expect-audio",
        nargs="?",
        const="*",
        metavar="CODEC",
        help="Require an audio track, optionally requiring a codec such as aac.",
    )
    verify.set_defaults(handler=_run_verify)

    return parser


def _run_doctor(_args: argparse.Namespace) -> int:
    print(format_tool_report(detect_all_tools()))
    return 0


def _run_presets(_args: argparse.Namespace) -> int:
    print(format_preset_table())
    return 0


def _run_convert(args: argparse.Namespace) -> int:
    try:
        options = resolve_options(
            args.preset,
            max_pq=args.max_pq,
            crf=args.crf,
            bitrate=args.bitrate,
            fps=args.fps,
        )
        probe_started = time.monotonic()
        source = probe_video(args.input)
        probe_elapsed = time.monotonic() - probe_started
        plan_started = time.monotonic()
        plan = build_conversion_plan(
            source,
            args.output,
            toolchain=resolve_toolchain(options.preset.name),
            preset=options.preset.name,
            fast=args.fast,
            max_pq=options.max_pq,
            nominal_peak_nits=args.pq_peak_nits,
            crf=options.crf,
            bitrate=options.bitrate,
            fps=options.fps,
        )
        plan_elapsed = time.monotonic() - plan_started
        if args.dry_run:
            print(format_plan(plan))
            return 0

        _print_progress("probe", probe_elapsed, detail=source.source_class.value)
        _print_progress("plan", plan_elapsed, detail=options.preset.name.value)
        output = execute_conversion(
            plan,
            force=args.force,
            progress=_print_progress_event,
        )
    except ConversionError as error:
        print(format_conversion_failure(error), file=sys.stderr)
        return 2
    except ProbeError as error:
        failure = ConversionError(
            str(error),
            step="probe",
            commands=(error.command,) if error.command else (),
            output_lines=error.output_lines,
            likely_fix="run reelhdr doctor, then confirm ffprobe can read the input file",
            elapsed_seconds=(
                time.monotonic() - probe_started if "probe_started" in locals() else None
            ),
        )
        print(format_conversion_failure(failure), file=sys.stderr)
        return 2
    except ToolUnavailableError as error:
        failure = ConversionError(
            str(error),
            step="plan",
            likely_fix="run reelhdr doctor and paste the printed install block",
        )
        print(format_conversion_failure(failure), file=sys.stderr)
        return 2
    except (PlanError, ValueError) as error:
        failure = ConversionError(
            str(error),
            step="plan",
            likely_fix="correct the reported input or flag, then inspect --dry-run",
        )
        print(format_conversion_failure(failure), file=sys.stderr)
        return 2

    print(f"wrote {output}")
    verify_started = time.monotonic()
    report = verify_file(
        output,
        expectations=VerifyExpectations(
            expect_audio=source.has_audio,
            audio_codec=source.audio_codec_name if source.has_audio else None,
            expect_dolby_vision=options.preset.dolby_vision,
            expect_amve=options.preset.amve,
        ),
    )
    _print_progress("verify", time.monotonic() - verify_started, detail=report.verdict.value)
    print(format_human_report(report))
    return 0 if report.fail_count == 0 else 1


def _run_verify(args: argparse.Namespace) -> int:
    preset = resolve_options(args.preset).preset
    expect_audio = args.expect_audio is not None
    audio_codec = None if args.expect_audio in {None, "*"} else args.expect_audio
    report = verify_file(
        args.file,
        expectations=VerifyExpectations(
            expect_audio=expect_audio,
            audio_codec=audio_codec,
            expect_dolby_vision=preset.dolby_vision,
            expect_amve=preset.amve,
        ),
    )
    print(format_json_report(report) if args.json else format_human_report(report))
    return 0 if report.fail_count == 0 else 1


def _print_progress_event(event: ProgressEvent) -> None:
    _print_progress(
        event.stage,
        event.elapsed_seconds,
        skipped=event.skipped,
        detail=event.detail,
    )


def _print_progress(
    stage: str,
    elapsed_seconds: float,
    *,
    skipped: bool = False,
    detail: str | None = None,
) -> None:
    status = "skip" if skipped else "ok"
    suffix = f" — {detail}" if detail else ""
    print(f"[{status}] {stage:<6} {elapsed_seconds:>7.2f}s{suffix}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))
