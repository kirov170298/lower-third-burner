# Lower Third Burner

Windows desktop GUI application for burning lower-third titles into a video.

## Features

- Import a video file
- Optionally import a transparent PNG lower-third graphic
- Add multiple title entries
- Type custom text directly in the app
- Set start and end time for each title
- Choose export resolution, including Source / Original, 720x576, 1280x720, and 1920x1080
- Select an export directory with predictable output naming
- Manually position text with X and Y controls
- Customize font family, size, color, bold, italic, and outline
- Fade lower thirds in and out with adjustable durations
- Preview the lower-third placement before export
- Export the final video to MP4 with the text and optional graphic burned in
- Shows live export progress without freezing the UI
- Works with Bulgarian and Latin text through a Windows Unicode font

## Requirements

- Windows 10 or newer
- Python 3.11+
- FFmpeg installed and available in `PATH`

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

```powershell
python app.py
```

## FFmpeg setup

Install FFmpeg and make sure both `ffmpeg.exe` and `ffprobe.exe` are available in your terminal `PATH`.

Quick check:

```powershell
ffmpeg -version
ffprobe -version
```

## Usage

1. Import a video.
2. Optionally import a transparent PNG lower-third graphic.
3. Add one or more title entries.
4. Select each entry and edit the text, timing, and render settings.
5. Choose the export resolution and optional export directory.
6. Use **Refresh Preview** to see placement over the current video frame.
7. Export the result as MP4.

## Author and License

Lower Third Burner was created by Valentin Kirov.

This project is licensed under Apache-2.0. Redistribution and modification are allowed under the Apache License 2.0, provided that attribution and applicable notices are preserved in the project files and notices.

See `LICENSE` and `NOTICE` for the full license text and attribution notice.

## Notes

- The optional PNG graphic is placed near the bottom center of the frame.
- Text placement, font styling, output resolution, and fade durations are applied to the final FFmpeg render.
- Export keeps the original audio track when one exists.
