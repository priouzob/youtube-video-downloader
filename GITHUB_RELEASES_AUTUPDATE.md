# GitHub Releases Auto-Update

## Current setup
- App auto-update from GitHub Releases (`youtube-video-downloader.exe`)
- Daily `yt-dlp.exe` update check with fallback download sources
- Automatic `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` install when missing
- Disk free space check before heavy downloads
- GitHub Actions workflow to build/release on tags `v*.*.*`

## Config files
- `update_config.json` -> app auto-update configuration
- `runtime_config.json` -> runtime behavior (`ffmpeg` auto-install + minimum disk)
- `version.txt` -> local app version fallback

## Public repository mode
No token is required when your repository is public.

`YD_GITHUB_TOKEN` is only needed if:
- the repository is private, or
- you want authenticated GitHub API requests.

## Minimal update_config.json

```json
{
  "enabled": true,
  "owner": "priouzob",
  "repo": "youtube-video-downloader",
  "asset_name": "youtube-video-downloader.exe",
  "auto_apply": true,
  "check_interval": "daily"
}
```

## Release flow
1. Commit and push to `master`
2. Create a semantic version tag (example: `v1.4.0`)
3. Push the tag
4. GitHub Actions builds and publishes the release asset

Workflow: `.github/workflows/release.yml`

## End-user requirements
End users only need:
- `youtube-video-downloader.exe`

Everything else is handled automatically by the app at runtime.

## Note about large binaries
Do not commit FFmpeg binaries to Git (GitHub blocks files >100 MiB).
The app already downloads/installs them automatically when needed.
