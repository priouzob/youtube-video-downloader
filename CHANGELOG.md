# Changelog

## v1.3.3 - 2026-05-01

- Hardened download/update pipeline with trusted HTTPS host allowlist
- Added redirect host validation and max download size limits
- Added binary validation for downloaded EXE files (MZ header + minimum size)
- Enforced validation for app self-update executable and FFmpeg/yt-dlp binaries
- Improved runtime readiness checks so Ready appears only when dependencies are installed

## v1.3.2 - 2026-05-01

- Added SSL fallback mode for VM environments with broken certificate stores
- Added multiple resilient `yt-dlp.exe` download sources with clearer diagnostics
- Improved FFmpeg download error reporting for certificate/network failures

## v1.3.1 - 2026-05-01

- Switched to a clean modern pink Qt interface (no background image dependency)
- Improved first-run behavior on fresh machines/VMs (buttons remain usable)
- Added resilient `yt-dlp.exe` download fallbacks and clearer network error logs
- Updated public-repo docs for GitHub Releases auto-update flow

## v1.3.0 - 2026-04-30

- Migrated UI from Tk/CustomTkinter to PySide6 (Qt)
- Switched to exact skin background from `fondia.png`
- Repositioned controls on top of the skin with fixed pixel layout
- Kept one-file behavior and download/runtime automation

## v1.2.3 - 2026-04-30

- Refined the pink UI proportions, typography, and spacing to better match the provided target mockup
- Removed unsupported glyph icons that were rendering as '?' on some systems
- Kept rounded controls and soft pastel palette while preserving one-file behavior

## v1.2.2 - 2026-04-30

- Switched UI to CustomTkinter for real rounded corners and softer pink styling
- Removed the plain "Theme Artwork" title block for a more natural layout
- Improved visual hierarchy to match the requested romantic/lace-inspired pink direction
- Kept no extra popup behavior during downloads

## v1.2.1 - 2026-04-30

- Fixed hidden downloader subprocess window during downloads
- Removed completion/error popup dialogs after download action
- Improved pink UI styling for clearer theme consistency
- Added a dedicated artwork panel so background image is clearly visible

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

