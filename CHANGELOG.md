# Changelog

## v1.2.0 - 2026-04-30

- Renamed executable to `youtube-video-downloader.exe`
- Added custom pink theme and integrated background image in the GUI
- Embedded background/image assets into the executable bundle
- Updated release workflow to publish `youtube-video-downloader.exe`

## v1.1.0 - 2026-04-30

- Replaced console flow with a modern graphical interface
- Added live logs panel and download progress bar
- Added embedded EXE icon and matching app window icon
- Kept one-file behavior with automatic runtime dependency setup

## v1.0.1 - 2026-04-30

- Switched all CLI/runtime messages to English
- One-file first experience improved (`downloader_v2.exe` works standalone)
- Embedded default app update config for `priouzob/youtubedownloader`
- Optional config files kept only as overrides

## v1.0.0 - 2026-04-30

- App auto-update via GitHub Releases
- `yt-dlp.exe` auto-update (daily check)
- FFmpeg auto-install if binaries are missing
- Free disk space check before downloads
- GitHub Actions release workflow (`v*.*.*` tags)
