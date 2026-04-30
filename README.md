# YouTube Video Downloader 

Modern Windows GUI YouTube downloader (PySide6 + skinned UI) powered by `yt-dlp`, with:
- automatic app updates from GitHub Releases
- automatic `yt-dlp.exe` updates
- automatic `ffmpeg/ffprobe/ffplay` installation if missing
- downloads saved to `video/`
- custom pink-themed interface with integrated background artwork

## Quick Start (End Users)

1. Download `youtube-video-downloader.exe` from the latest GitHub Release.
2. Put it in any folder (for example `C:\YoutubeDownloader`).
3. Double-click `youtube-video-downloader.exe` to open the graphical app.
4. Paste a YouTube URL.

That's it. One file is enough.

## One-file behavior

When users run only `youtube-video-downloader.exe`, the app will:
- create `video/` automatically
- download `yt-dlp.exe` if missing
- download/install `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` if missing
- check app updates from GitHub Releases

For public repositories, no token is required.
If you ever switch back to private releases, set `YD_GITHUB_TOKEN` in the environment.

## Optional local overrides

The app works without these files, but supports optional overrides:
- `update_config.json`
- `runtime_config.json`
- `version.txt`

## Maintainer Release Flow

1. Push code to `master`
2. Create a semver tag (example: `v1.2.0`)
3. Push the tag
4. GitHub Actions builds and publishes release assets

Workflow: `.github/workflows/release.yml`

## Notes

- GitHub repository files have size limits; large FFmpeg binaries are not committed.
- Runtime auto-install handles FFmpeg for end users.

## Security

See `SECURITY.md`.
