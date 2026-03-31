from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TitleEntry:
    text: str = ""
    first_start_time_seconds: float = 20.0
    first_duration_seconds: float = 5.0
    second_end_offset_seconds: float = 10.0
    second_duration_seconds: float = 5.0

    def _resolve_window(self, start_time: float, duration: float, video_duration_seconds: float) -> tuple[float, float] | None:
        safe_video_duration = max(0.0, video_duration_seconds)
        safe_duration = max(0.0, duration)
        if safe_video_duration <= 0 or safe_duration <= 0:
            return None

        clamped_duration = min(safe_duration, safe_video_duration)
        clamped_start = max(0.0, min(start_time, max(0.0, safe_video_duration - clamped_duration)))
        clamped_end = min(safe_video_duration, clamped_start + clamped_duration)
        if clamped_end <= clamped_start:
            return None
        return clamped_start, clamped_end

    def resolved_appearances(self, video_duration_seconds: float) -> list[tuple[float, float]]:
        appearances: list[tuple[float, float]] = []

        first_window = self._resolve_window(
            max(0.0, self.first_start_time_seconds),
            self.first_duration_seconds,
            video_duration_seconds,
        )
        if first_window is not None:
            appearances.append(first_window)

        second_start = max(0.0, video_duration_seconds - max(0.0, self.second_end_offset_seconds))
        second_window = self._resolve_window(
            second_start,
            self.second_duration_seconds,
            video_duration_seconds,
        )
        if second_window is not None:
            appearances.append(second_window)

        return appearances


@dataclass
class VideoMetadata:
    width: int
    height: int
    duration_seconds: float


@dataclass
class RenderSettings:
    output_width: int | None = None
    output_height: int | None = None
    text_x: int = 0
    text_y: int = 0
    font_family: str = "Segoe UI"
    font_size: int = 48
    font_color: str = "#FFFFFF"
    bold: bool = True
    italic: bool = False
    underline: bool = False
    outline_size: int = 2
    fade_in_seconds: float = 0.5
    fade_out_seconds: float = 0.5
    export_directory: str | None = None
    export_format: str = "mp4_h264"
