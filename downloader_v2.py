import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# Embedded app version for one-file mode
APP_VERSION = "v1.0.1"
APP_VERSION_FILE = "version.txt"  # optional fallback
APP_UPDATE_STAMP_FILE = ".app-last-update-check.txt"
APP_UPDATE_CONFIG_FILE = "update_config.json"  # optional override
APP_UPDATE_SCRIPT_FILE = "_apply_update.bat"

RUNTIME_CONFIG_FILE = "runtime_config.json"  # optional override
FFMPEG_BUNDLE_DEFAULT_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

YTDLP_LATEST_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_EXE_NAME = "yt-dlp.exe"
YTDLP_UPDATE_STAMP_FILE = ".ytdlp-last-update.txt"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
VIDEO_DIR = BASE_DIR / "video"
YTDLP_PATH = BASE_DIR / YTDLP_EXE_NAME
YTDLP_UPDATE_STAMP_PATH = BASE_DIR / YTDLP_UPDATE_STAMP_FILE
APP_VERSION_PATH = BASE_DIR / APP_VERSION_FILE
APP_UPDATE_STAMP_PATH = BASE_DIR / APP_UPDATE_STAMP_FILE
APP_UPDATE_CONFIG_PATH = BASE_DIR / APP_UPDATE_CONFIG_FILE
APP_UPDATE_SCRIPT_PATH = BASE_DIR / APP_UPDATE_SCRIPT_FILE
RUNTIME_CONFIG_PATH = BASE_DIR / RUNTIME_CONFIG_FILE


def ensure_video_dir() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def read_local_app_version() -> str:
    # One-file mode: if version.txt is missing, use embedded version.
    try:
        if APP_VERSION_PATH.exists():
            value = APP_VERSION_PATH.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    return APP_VERSION


def binary_exists(filename: str) -> bool:
    return (BASE_DIR / filename).exists()


def should_check_update_today(stamp_path: Path) -> bool:
    today = datetime.now().date().isoformat()
    if not stamp_path.exists():
        return True

    try:
        last = stamp_path.read_text(encoding="utf-8").strip()
    except OSError:
        return True

    return last != today


def mark_update_checked_today(stamp_path: Path) -> None:
    today = datetime.now().date().isoformat()
    try:
        stamp_path.write_text(today, encoding="utf-8")
    except OSError:
        pass


def load_runtime_config() -> dict:
    defaults = {
        "ffmpeg_auto_install": True,
        "ffmpeg_bundle_url": FFMPEG_BUNDLE_DEFAULT_URL,
        "min_free_space_mb": 500,
    }

    # Optional file-based override.
    try:
        if RUNTIME_CONFIG_PATH.exists():
            data = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
    except (OSError, json.JSONDecodeError):
        pass

    return defaults


def load_update_config() -> dict:
    # Preconfigured defaults for your private repo.
    defaults = {
        "enabled": True,
        "owner": "priouzob",
        "repo": "youtubedownloader",
        "asset_name": "downloader_v2.exe",
        "auto_apply": True,
        "check_interval": "daily",
    }

    # Optional file-based override.
    try:
        if APP_UPDATE_CONFIG_PATH.exists():
            data = json.loads(APP_UPDATE_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
    except (OSError, json.JSONDecodeError):
        pass

    return defaults


def version_to_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_version_newer(remote: str, local: str) -> bool:
    rv = version_to_tuple(remote)
    lv = version_to_tuple(local)

    max_len = max(len(rv), len(lv))
    rv += (0,) * (max_len - len(rv))
    lv += (0,) * (max_len - len(lv))

    return rv > lv


def fetch_latest_release(owner: str, repo: str, timeout_seconds: int = 15) -> Optional[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "youtube-downloader-updater",
    }
    token = os.getenv("YD_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        url,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def pick_release_asset_url(release_data: dict, expected_asset_name: str) -> Optional[str]:
    assets = release_data.get("assets")
    if not isinstance(assets, list):
        return None

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if asset.get("name") == expected_asset_name:
            url = asset.get("browser_download_url")
            if isinstance(url, str) and url.strip():
                return url

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe"):
            url = asset.get("browser_download_url")
            if isinstance(url, str) and url.strip():
                return url

    return None


def download_file(url: str, output_path: Path, timeout_seconds: int = 30) -> bool:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            data = response.read()

        with open(temp_path, "wb") as f:
            f.write(data)

        os.replace(temp_path, output_path)
        return True

    except (urllib.error.URLError, TimeoutError, OSError):
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        return False


def install_ffmpeg_binaries(bundle_url: str) -> bool:
    zip_path = BASE_DIR / "ffmpeg_bundle.zip"

    print("Downloading FFmpeg bundle...")
    if not download_file(bundle_url, zip_path, timeout_seconds=120):
        print("FFmpeg download failed.")
        return False

    target_names = {
        "ffmpeg.exe": False,
        "ffprobe.exe": False,
        "ffplay.exe": False,
    }

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name.lower()
                if name in target_names:
                    out = BASE_DIR / name
                    with archive.open(info, "r") as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    target_names[name] = True
    except (OSError, zipfile.BadZipFile):
        print("Invalid or corrupted FFmpeg archive.")
        return False
    finally:
        try:
            if zip_path.exists():
                zip_path.unlink()
        except OSError:
            pass

    if all(target_names.values()):
        print("FFmpeg installed automatically.")
        return True

    print("FFmpeg installation incomplete.")
    return False


def ensure_ffmpeg_ready(runtime_cfg: dict) -> None:
    needed = ["ffmpeg.exe", "ffprobe.exe", "ffplay.exe"]
    missing = [name for name in needed if not binary_exists(name)]

    if not missing:
        print("FFmpeg OK: ffmpeg/ffprobe/ffplay found.")
        return

    print("Missing FFmpeg binaries: " + ", ".join(missing))

    if not bool(runtime_cfg.get("ffmpeg_auto_install", True)):
        print("FFmpeg auto-install is disabled.")
        return

    bundle_url = str(runtime_cfg.get("ffmpeg_bundle_url", FFMPEG_BUNDLE_DEFAULT_URL)).strip()
    if not bundle_url:
        print("FFmpeg bundle URL is missing.")
        return

    install_ffmpeg_binaries(bundle_url)


def get_free_space_mb(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free / (1024 * 1024))
    except OSError:
        return -1


def check_free_space(runtime_cfg: dict) -> None:
    min_free_mb = int(runtime_cfg.get("min_free_space_mb", 500))
    free_mb = get_free_space_mb(VIDEO_DIR)
    if free_mb < 0:
        print("Disk check: unable to read free space.")
        return

    if free_mb < min_free_mb:
        print(f"Warning: low disk space ({free_mb} MB free).")
        print("Free up space before large downloads.")
    else:
        print(f"Disk space OK: {free_mb} MB free.")


def prepare_and_launch_self_update(new_tag: str, asset_url: str) -> bool:
    if not getattr(sys, "frozen", False):
        print("App self-update skipped in script mode.")
        return False

    current_exe = Path(sys.executable).name
    new_exe_path = BASE_DIR / f"{Path(current_exe).stem}.new.exe"
    new_version_path = BASE_DIR / "version.new.txt"

    ok = download_file(asset_url, new_exe_path)
    if not ok:
        print("Failed to download updated executable.")
        return False

    try:
        new_version_path.write_text(new_tag, encoding="utf-8")
    except OSError:
        pass

    script = f"""@echo off
setlocal
set "APP={current_exe}"
set "NEW={new_exe_path.name}"
set "VERNEW={new_version_path.name}"
set "VERCUR={APP_VERSION_FILE}"

for /l %%i in (1,1,20) do (
  move /y "%NEW%" "%APP%" >nul 2>&1
  if not exist "%NEW%" goto replaced
  timeout /t 1 /nobreak >nul
)

echo Update could not be applied (file lock).
exit /b 1

:replaced
if exist "%VERNEW%" (
  move /y "%VERNEW%" "%VERCUR%" >nul 2>&1
)

start "" "%APP%"
del "%~f0" >nul 2>&1
exit /b 0
"""

    try:
        APP_UPDATE_SCRIPT_PATH.write_text(script, encoding="utf-8")
    except OSError:
        print("Failed to create update script.")
        return False

    try:
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        subprocess.Popen(
            ["cmd", "/c", str(APP_UPDATE_SCRIPT_PATH)],
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
        return True
    except OSError:
        print("Failed to launch update script.")
        return False


def ensure_app_is_fresh() -> bool:
    cfg = load_update_config()

    if not cfg.get("enabled", False):
        print("App auto-update: disabled.")
        return False

    owner = str(cfg.get("owner", "")).strip()
    repo = str(cfg.get("repo", "")).strip()
    if not owner or not repo:
        print("App auto-update: owner/repo not configured.")
        return False

    interval = str(cfg.get("check_interval", "daily")).lower().strip()
    if interval == "daily" and (not should_check_update_today(APP_UPDATE_STAMP_PATH)):
        print("App auto-update: already checked today.")
        return False

    print("Checking for app updates...")
    release = fetch_latest_release(owner, repo)
    mark_update_checked_today(APP_UPDATE_STAMP_PATH)

    if not release:
        print("App auto-update: could not read latest release.")
        return False

    remote_tag = str(release.get("tag_name", "")).strip()
    if not remote_tag:
        print("App auto-update: release tag not found.")
        return False

    local_version = read_local_app_version()
    if not is_version_newer(remote_tag, local_version):
        print(f"App is up to date ({local_version}).")
        return False

    asset_name = str(cfg.get("asset_name", "downloader_v2.exe")).strip() or "downloader_v2.exe"
    asset_url = pick_release_asset_url(release, asset_name)
    if not asset_url:
        print(f"App auto-update: asset '{asset_name}' not found in release.")
        return False

    print(f"New version found: {remote_tag} (local: {local_version}).")

    auto_apply = bool(cfg.get("auto_apply", True))
    if not auto_apply:
        answer = input("Apply update now? (y/n): ").strip().lower()
        if answer != "y":
            print("Update skipped.")
            return False

    if prepare_and_launch_self_update(remote_tag, asset_url):
        print("Update downloaded. Restarting application...")
        return True

    return False


def ensure_ytdlp_is_fresh() -> None:
    if not YTDLP_PATH.exists():
        print("yt-dlp.exe not found, downloading...")
        ok = download_file(YTDLP_LATEST_URL, YTDLP_PATH)
        mark_update_checked_today(YTDLP_UPDATE_STAMP_PATH)
        if ok:
            print("yt-dlp.exe downloaded successfully.")
        else:
            print("Could not download yt-dlp.exe right now.")
        return

    if not should_check_update_today(YTDLP_UPDATE_STAMP_PATH):
        print("yt-dlp: already checked today.")
        return

    print("Checking yt-dlp updates...")
    ok = download_file(YTDLP_LATEST_URL, YTDLP_PATH)
    mark_update_checked_today(YTDLP_UPDATE_STAMP_PATH)
    if ok:
        print("yt-dlp is up to date.")
    else:
        print("Keeping local yt-dlp version.")


def run_download(url: str) -> int:
    cmd = [
        str(YTDLP_PATH),
        "--no-playlist",
        "-f",
        "bestvideo+bestaudio/best",
        "--ffmpeg-location",
        str(BASE_DIR),
        "-o",
        str(VIDEO_DIR / "%(title)s.%(ext)s"),
        url,
    ]

    result = subprocess.run(cmd)
    return result.returncode


def run_bot() -> bool:
    print("=== YOUTUBE DOWNLOADER V2 ===")
    print(f"Version: {APP_VERSION}")
    print(f"Output folder: {VIDEO_DIR}")

    ensure_video_dir()
    runtime_cfg = load_runtime_config()

    ensure_ffmpeg_ready(runtime_cfg)
    check_free_space(runtime_cfg)

    if ensure_app_is_fresh():
        return False

    ensure_ytdlp_is_fresh()

    if not YTDLP_PATH.exists():
        print("Error: yt-dlp.exe is missing, cannot continue.")
        return True

    url = input("\nPaste a YouTube URL and press Enter: ").strip()
    if not url:
        print("Error: no URL provided.")
        return True

    print("\nDownloading...")
    code = run_download(url)

    print("\n" + "=" * 30)
    if code == 0:
        print(f"SUCCESS: Video saved to '{VIDEO_DIR}'")
    else:
        print(f"FAILED: yt-dlp returned exit code {code}")
    print("=" * 30)
    return True


if __name__ == "__main__":
    while True:
        keep_running = run_bot()
        if not keep_running:
            sys.exit(0)

        choice = input("\nDownload another video? (y/n): ").lower().strip()
        if choice != "y":
            print("Goodbye!")
            break
