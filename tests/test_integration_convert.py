from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from reelhdr.pipeline import build_conversion_plan, execute_conversion, resolve_toolchain
from reelhdr.probe import SourceClass, probe_video
from reelhdr.verify import VerifyExpectations, verify_file

_REQUIRED_TOOLS = ("ffmpeg", "ffprobe", "dovi_tool", "MP4Box")


def _missing_integration_tools() -> tuple[str, ...]:
    missing = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        try:
            encoders = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            missing.append("ffmpeg with libx265")
        else:
            if "libx265" not in encoders.stdout:
                missing.append("ffmpeg with libx265")
    return tuple(missing)


_MISSING_TOOLS = _missing_integration_tools()


def _source_encode_args(source_class: SourceClass) -> list[str]:
    if source_class is SourceClass.SDR:
        return ["-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"]

    transfer = "arib-std-b67" if source_class is SourceClass.HLG else "smpte2084"
    transfer_code = "18" if source_class is SourceClass.HLG else "16"
    return [
        "-vf",
        "format=yuv420p10le",
        "-c:v",
        "libx265",
        "-pix_fmt",
        "yuv420p10le",
        "-color_range",
        "tv",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        transfer,
        "-colorspace",
        "bt2020nc",
        "-x265-params",
        (f"log-level=error:colorprim=9:transfer={transfer_code}:colormatrix=9:range=limited"),
        "-tag:v",
        "hvc1",
    ]


@pytest.mark.integration
@pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"missing integration tools: {', '.join(_MISSING_TOOLS)}",
)
@pytest.mark.parametrize("source_class", [SourceClass.SDR, SourceClass.HLG, SourceClass.PQ])
def test_one_second_synthetic_clip_converts_to_hevc_hlg_dv84(
    tmp_path: Path,
    source_class: SourceClass,
) -> None:
    input_path = tmp_path / f"synthetic-{source_class.value}-input.mp4"
    output_path = tmp_path / f"synthetic-{source_class.value}-output.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            *_source_encode_args(source_class),
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(input_path),
        ],
        check=True,
    )

    source = probe_video(input_path)
    assert source.source_class is source_class
    assert source.has_audio is True
    plan = build_conversion_plan(
        source,
        output_path,
        toolchain=resolve_toolchain(),
    )
    execute_conversion(plan)

    result = probe_video(output_path)
    assert output_path.stat().st_size > 0
    assert result.source_class is SourceClass.DOLBY_VISION
    assert result.dv_profile == 8
    assert result.dv_bl_signal_compatibility_id == 4
    assert result.codec_name == "hevc"
    assert result.has_audio is True
    assert result.bit_depth == 10
    assert result.color_transfer == "arib-std-b67"
    assert result.color_primaries == "bt2020"
    assert result.color_space == "bt2020nc"

    report = verify_file(
        output_path,
        expectations=VerifyExpectations(expect_audio=True, audio_codec="aac"),
    )
    assert report.fail_count == 0
