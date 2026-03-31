from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import winreg
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .models import RenderSettings, TitleEntry, VideoMetadata


class FFmpegError(RuntimeError):
    pass


def find_executable(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise FFmpegError(
            f"Could not find '{name}' in PATH. Install FFmpeg and make sure it is available from the command line."
        )
    return executable


def get_video_metadata(video_path: str) -> dict[str, float | int]:
    ffprobe = find_executable("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,width,height,duration,avg_frame_rate,r_frame_rate:format=duration",
        "-of",
        "json",
        video_path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise FFmpegError(completed.stderr.strip() or "ffprobe failed to read the video file.")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid metadata for the selected file.") from exc

    streams = payload.get("streams", [])
    if not streams:
        raise FFmpegError("No video stream found in the selected file.")

    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video_stream:
        raise FFmpegError("The selected file does not contain a usable video stream.")

    width = _parse_positive_int(video_stream.get("width"), "Video width is missing or invalid.")
    height = _parse_positive_int(video_stream.get("height"), "Video height is missing or invalid.")
    duration = _parse_duration(video_stream, payload.get("format", {}))
    fps = _parse_fps(video_stream)

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "fps": fps,
    }


def _parse_positive_int(value: object, error_message: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise FFmpegError(error_message) from None
    if parsed <= 0:
        raise FFmpegError(error_message)
    return parsed


def _parse_positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _parse_duration(stream: dict[str, object], format_data: dict[str, object]) -> float:
    duration = _parse_positive_float(stream.get("duration"))
    if duration is None:
        duration = _parse_positive_float(format_data.get("duration"))
    if duration is None:
        raise FFmpegError("Video duration is missing or invalid.")
    return duration


def _parse_fps(stream: dict[str, object]) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw_value = stream.get(key)
        if not raw_value:
            continue
        try:
            fps = float(Fraction(str(raw_value)))
        except (ValueError, ZeroDivisionError):
            continue
        if fps > 0:
            return fps
    raise FFmpegError("Video frame rate is missing or invalid.")


def extract_preview_frame(video_path: str, output_path: str, timestamp: float) -> None:
    ffmpeg = find_executable("ffmpeg")
    safe_timestamp = max(timestamp, 0.0)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{safe_timestamp:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        output_path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise FFmpegError(completed.stderr.strip() or "ffmpeg could not extract a preview frame.")


@lru_cache(maxsize=1)
def _windows_font_entries() -> tuple[tuple[str, str], ...]:
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    entries: list[tuple[str, str]] = []
    fonts_dir = Path(r"C:\Windows\Fonts")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            font_path = Path(value)
            if not font_path.is_absolute():
                font_path = fonts_dir / value
            if font_path.exists():
                entries.append((name, str(font_path)))
            index += 1
    return tuple(entries)


def _normalize_font_name(value: str) -> str:
    cleaned = value.lower().replace("(truetype)", " ")
    return " ".join(cleaned.replace("-", " ").replace("_", " ").split())


def find_font_path(font_family: str, bold: bool = False, italic: bool = False) -> str:
    entries = _windows_font_entries()
    family_name = _normalize_font_name(font_family)
    preferred_styles: list[str]
    if bold and italic:
        preferred_styles = ["bold italic", "italic bold", "bold", "italic", ""]
    elif bold:
        preferred_styles = ["bold", ""]
    elif italic:
        preferred_styles = ["italic", ""]
    else:
        preferred_styles = [""]

    for style in preferred_styles:
        for registry_name, font_path in entries:
            normalized_name = _normalize_font_name(registry_name)
            if family_name not in normalized_name:
                continue
            if style and style not in normalized_name:
                continue
            return font_path

    raise FFmpegError(f"Could not resolve a font file for '{font_family}'.")


def _escape_filter_text(value: str) -> str:
    escaped = value.replace("\\", r"\\")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("[", r"\[")
    escaped = escaped.replace("]", r"\]")
    escaped = escaped.replace(",", r"\,")
    escaped = escaped.replace("%", r"\%")
    return escaped


def _to_ffmpeg_color(value: str) -> str:
    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        raise FFmpegError("Text color must be a valid RGB value.")
    int(normalized, 16)
    return f"0x{normalized.upper()}"


def _target_metadata(source_metadata: VideoMetadata, settings: RenderSettings) -> VideoMetadata:
    if settings.output_width and settings.output_height:
        return VideoMetadata(
            width=settings.output_width,
            height=settings.output_height,
            duration_seconds=source_metadata.duration_seconds,
        )
    return source_metadata


def _mpeg2_pal_display_aspect(source_metadata: VideoMetadata) -> str:
    source_aspect = source_metadata.width / max(1, source_metadata.height)
    return "16/9" if source_aspect >= 1.5 else "4/3"


def _mpeg2_pal_aspect_args(source_metadata: VideoMetadata, target_metadata: VideoMetadata, settings: RenderSettings) -> list[str]:
    if settings.export_format == "mpeg2_ps" and (target_metadata.width, target_metadata.height) == (720, 576):
        return ["-aspect", _mpeg2_pal_display_aspect(source_metadata)]
    return []


def _video_base_filter(source_metadata: VideoMetadata, target_metadata: VideoMetadata, settings: RenderSettings) -> str:
    if settings.export_format == "mpeg2_ps" and (target_metadata.width, target_metadata.height) == (720, 576):
        display_aspect = _mpeg2_pal_display_aspect(source_metadata)
        if (target_metadata.width, target_metadata.height) != (source_metadata.width, source_metadata.height):
            return f"[0:v]scale=720:576:flags=lanczos,setdar={display_aspect}[v0]"
        return f"[0:v]setdar={display_aspect}[v0]"

    if (target_metadata.width, target_metadata.height) != (source_metadata.width, source_metadata.height):
        return f"[0:v]scale={target_metadata.width}:{target_metadata.height}:flags=lanczos,setsar=1[v0]"
    return "[0:v]setsar=1[v0]"


def _clamp_fade_durations(start_time: float, end_time: float, settings: RenderSettings) -> tuple[float, float]:
    visible_duration = max(0.0, end_time - start_time)
    fade_in = max(0.0, min(settings.fade_in_seconds, visible_duration / 2 if visible_duration > 0 else 0.0))
    fade_out = max(0.0, min(settings.fade_out_seconds, visible_duration / 2 if visible_duration > 0 else 0.0))
    return fade_in, fade_out


def _alpha_expression(start_time: float, end_time: float, fade_in: float, fade_out: float) -> str:
    if end_time <= start_time:
        return "0"
    if fade_in > 0 and fade_out > 0:
        return (
            f"if(lt(t,{start_time:.3f}),0,"
            f"if(lt(t,{start_time + fade_in:.3f}),(t-{start_time:.3f})/{fade_in:.3f},"
            f"if(lt(t,{end_time - fade_out:.3f}),1,"
            f"if(lt(t,{end_time:.3f}),({end_time:.3f}-t)/{fade_out:.3f},0))))"
        )
    if fade_in > 0:
        return (
            f"if(lt(t,{start_time:.3f}),0,"
            f"if(lt(t,{start_time + fade_in:.3f}),(t-{start_time:.3f})/{fade_in:.3f},"
            f"if(lt(t,{end_time:.3f}),1,0)))"
        )
    if fade_out > 0:
        return (
            f"if(lt(t,{start_time:.3f}),0,"
            f"if(lt(t,{end_time - fade_out:.3f}),1,"
            f"if(lt(t,{end_time:.3f}),({end_time:.3f}-t)/{fade_out:.3f},0)))"
        )
    return f"if(between(t,{start_time:.3f},{end_time:.3f}),1,0)"


def _underline_text(value: str) -> str:
    longest_line = max((len(line.strip()) for line in value.splitlines()), default=0)
    return "_" * max(1, longest_line)


def _mpeg2_gop_size(fps: float) -> int:
    safe_fps = max(1.0, fps)
    if 24.0 <= safe_fps <= 26.0:
        return 15
    if 29.0 <= safe_fps <= 31.0:
        return 18
    return max(12, min(18, int(round(safe_fps * 0.6))))


def validate_export_settings(settings: RenderSettings, source_metadata: VideoMetadata, fps: float) -> None:
    target_metadata = _target_metadata(source_metadata, settings)
    if target_metadata.width <= 0 or target_metadata.height <= 0:
        raise FFmpegError("Export resolution must use a valid width and height.")
    if target_metadata.width % 2 != 0 or target_metadata.height % 2 != 0:
        raise FFmpegError("Export resolution must use even width and height values.")
    if fps <= 0:
        raise FFmpegError("Video frame rate is missing or invalid for export.")
    if settings.export_format == "mpeg2_ps" and (target_metadata.width < 16 or target_metadata.height < 16):
        raise FFmpegError("MPEG-2 export requires a larger output resolution.")


def _export_codec_settings(settings: RenderSettings, fps: float) -> tuple[list[str], str]:
    if settings.export_format == "mpeg2_ps":
        gop_size = _mpeg2_gop_size(fps)
        return (
            [
                "-c:v",
                "mpeg2video",
                "-pix_fmt",
                "yuv420p",
                "-r",
                f"{fps:.3f}",
                "-g",
                str(gop_size),
                "-b:v",
                "6000k",
                "-maxrate",
                "8000k",
                "-bufsize",
                "1835k",
                "-c:a",
                "mp2",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-f",
                "mpeg",
            ],
            ".mpg",
        )
    if settings.export_format == "mpeg4":
        return (
            [
                "-c:v",
                "mpeg4",
                "-q:v",
                "3",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ],
            ".mp4",
        )
    return (
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ],
        ".mp4",
    )


def output_extension_for_format(export_format: str) -> str:
    settings = RenderSettings(export_format=export_format)
    return _export_codec_settings(settings, 25.0)[1]


def build_filter_complex(
    entries: list[TitleEntry],
    source_metadata: VideoMetadata,
    settings: RenderSettings,
    font_path: str,
    has_graphic: bool,
) -> tuple[str, str]:
    target_metadata = _target_metadata(source_metadata, settings)
    filters: list[str] = []

    filters.append(_video_base_filter(source_metadata, target_metadata, settings))
    video_label = "v0"

    if has_graphic:
        graphic_steps = ["format=rgba"]
        if (target_metadata.width, target_metadata.height) != (source_metadata.width, source_metadata.height):
            graphic_steps.append(
                "scale="
                f"w='max(1,trunc(iw*{target_metadata.width}/{source_metadata.width}))':"
                f"h='max(1,trunc(ih*{target_metadata.height}/{source_metadata.height}))':"
                "flags=lanczos"
            )
        filters.append(f"[1:v]{','.join(graphic_steps)}[gbase]")

    graphic_x = str(settings.graphic_x)
    graphic_y = str(settings.graphic_y)
    text_color = _to_ffmpeg_color(settings.font_color)
    border_color = _to_ffmpeg_color("#000000")
    font = font_path.replace("\\", "/").replace(":", r"\:")

    for entry_index, entry in enumerate(entries):
        appearances = entry.resolved_appearances(source_metadata.duration_seconds)
        for appearance_index, (start_time, end_time) in enumerate(appearances):
            enabled = f"between(t,{start_time:.3f},{end_time:.3f})"
            fade_in, fade_out = _clamp_fade_durations(start_time, end_time, settings)
            alpha_expr = _alpha_expression(start_time, end_time, fade_in, fade_out)
            underline_y = settings.text_y + settings.font_size + max(2, settings.outline_size)

            if has_graphic:
                graphic_output = f"g{entry_index}_{appearance_index}"
                graphic_effects = ["format=rgba"]
                if fade_in > 0:
                    graphic_effects.append(f"fade=t=in:st={start_time:.3f}:d={fade_in:.3f}:alpha=1")
                if fade_out > 0:
                    graphic_effects.append(
                        f"fade=t=out:st={max(start_time, end_time - fade_out):.3f}:d={fade_out:.3f}:alpha=1"
                    )
                filters.append(f"[gbase]{','.join(graphic_effects)}[{graphic_output}]")
                overlay_output = f"ov{entry_index}_{appearance_index}"
                filters.append(
                    f"[{video_label}][{graphic_output}]overlay=x={graphic_x}:y={graphic_y}:enable='{enabled}'[{overlay_output}]"
                )
                video_label = overlay_output

            drawtext_output = f"txt{entry_index}_{appearance_index}"
            text = _escape_filter_text(entry.text)
            filters.append(
                f"[{video_label}]drawtext="
                f"fontfile='{font}':"
                f"text='{text}':"
                f"fontcolor={text_color}:"
                f"fontsize={settings.font_size}:"
                f"x={settings.text_x}:"
                f"y={settings.text_y}:"
                f"borderw={settings.outline_size}:"
                f"bordercolor={border_color}:"
                f"alpha='{alpha_expr}':"
                f"enable='{enabled}'"
                f"[{drawtext_output}]"
            )
            video_label = drawtext_output

            if settings.underline:
                underline_output = f"uline{entry_index}_{appearance_index}"
                underline_text = _escape_filter_text(_underline_text(entry.text))
                filters.append(
                    f"[{video_label}]drawtext="
                    f"fontfile='{font}':"
                    f"text='{underline_text}':"
                    f"fontcolor={text_color}:"
                    f"fontsize={settings.font_size}:"
                    f"x={settings.text_x}:"
                    f"y={underline_y}:"
                    f"alpha='{alpha_expr}':"
                    f"enable='{enabled}'"
                    f"[{underline_output}]"
                )
                video_label = underline_output

    return ";".join(filters), video_label


def export_video(
    video_path: str,
    graphic_path: str | None,
    entries: list[TitleEntry],
    output_path: str,
    settings: RenderSettings,
    progress_callback: callable | None = None,
) -> None:
    if not entries:
        raise FFmpegError("Add at least one lower-third entry before exporting.")

    ffmpeg = find_executable("ffmpeg")
    metadata_payload = get_video_metadata(video_path)
    source_metadata = VideoMetadata(
        width=int(metadata_payload["width"]),
        height=int(metadata_payload["height"]),
        duration_seconds=float(metadata_payload["duration"]),
    )
    fps = float(metadata_payload["fps"])
    validate_export_settings(settings, source_metadata, fps)

    font_path = find_font_path(settings.font_family, settings.bold, settings.italic)
    has_graphic = bool(graphic_path)
    filter_complex, final_label = build_filter_complex(entries, source_metadata, settings, font_path, has_graphic)

    codec_args, _extension = _export_codec_settings(settings, fps)
    target_metadata = _target_metadata(source_metadata, settings)
    aspect_args = _mpeg2_pal_aspect_args(source_metadata, target_metadata, settings)

    command = [ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-nostats", "-i", video_path]
    if has_graphic:
        command.extend(["-loop", "1", "-i", graphic_path])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{final_label}]",
            "-map",
            "0:a?",
            *aspect_args,
            *codec_args,
            output_path,
        ]
    )

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    if progress_callback:
        progress_callback(0)

    assert process.stdout is not None
    total_duration_ms = max(1.0, source_metadata.duration_seconds * 1_000_000.0)
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("out_time_ms=") and progress_callback:
            try:
                out_time_ms = float(line.split("=", 1)[1])
            except ValueError:
                continue
            progress = max(0, min(99, int((out_time_ms / total_duration_ms) * 100)))
            progress_callback(progress)
        elif line == "progress=end" and progress_callback:
            progress_callback(100)

    return_code = process.wait()
    stderr_output = process.stderr.read() if process.stderr else ""
    if return_code != 0:
        if settings.export_format == "mpeg2_ps":
            raise FFmpegError(
                "FFmpeg MPEG-2 export failed. Check the selected resolution and MPEG-2 encoder settings.\n\n"
                + (stderr_output.strip() or "ffmpeg export failed.")
            )
        raise FFmpegError(stderr_output.strip() or "ffmpeg export failed.")


def temporary_preview_image() -> str:
    temp_dir = Path(tempfile.gettempdir()) / "lower_third_preview"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir / "preview_frame.png")
