import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QWidget,
)

# Embedded app version for one-file mode
APP_VERSION = "v1.3.0"
APP_VERSION_FILE = "version.txt"  # optional fallback
APP_UPDATE_STAMP_FILE = ".app-last-update-check.txt"
APP_UPDATE_CONFIG_FILE = "update_config.json"  # optional override
APP_UPDATE_SCRIPT_FILE = "_apply_update.bat"

RUNTIME_CONFIG_FILE = "runtime_config.json"  # optional override
FFMPEG_BUNDLE_DEFAULT_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

YTDLP_LATEST_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_EXE_NAME = "yt-dlp.exe"
YTDLP_UPDATE_STAMP_FILE = ".ytdlp-last-update.txt"

LogFn = Callable[[str], None]
ProgressFn = Callable[[float], None]


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


def get_resource_path(relative_path: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / relative_path
    return BASE_DIR / relative_path


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
    # Preconfigured defaults for your repository.
    defaults = {
        "enabled": True,
        "owner": "priouzob",
        "repo": "youtube-video-downloader",
        "asset_name": "youtube-video-downloader.exe",
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

    req = urllib.request.Request(url, headers=headers)

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


def install_ffmpeg_binaries(bundle_url: str, log: LogFn) -> bool:
    zip_path = BASE_DIR / "ffmpeg_bundle.zip"

    log("Downloading FFmpeg bundle...")
    if not download_file(bundle_url, zip_path, timeout_seconds=120):
        log("FFmpeg download failed.")
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
        log("Invalid or corrupted FFmpeg archive.")
        return False
    finally:
        try:
            if zip_path.exists():
                zip_path.unlink()
        except OSError:
            pass

    if all(target_names.values()):
        log("FFmpeg installed automatically.")
        return True

    log("FFmpeg installation incomplete.")
    return False


def ensure_ffmpeg_ready(runtime_cfg: dict, log: LogFn) -> None:
    needed = ["ffmpeg.exe", "ffprobe.exe", "ffplay.exe"]
    missing = [name for name in needed if not binary_exists(name)]

    if not missing:
        log("FFmpeg OK: ffmpeg/ffprobe/ffplay found.")
        return

    log("Missing FFmpeg binaries: " + ", ".join(missing))

    if not bool(runtime_cfg.get("ffmpeg_auto_install", True)):
        log("FFmpeg auto-install is disabled.")
        return

    bundle_url = str(runtime_cfg.get("ffmpeg_bundle_url", FFMPEG_BUNDLE_DEFAULT_URL)).strip()
    if not bundle_url:
        log("FFmpeg bundle URL is missing.")
        return

    install_ffmpeg_binaries(bundle_url, log)


def get_free_space_mb(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free / (1024 * 1024))
    except OSError:
        return -1


def check_free_space(runtime_cfg: dict, log: LogFn) -> None:
    min_free_mb = int(runtime_cfg.get("min_free_space_mb", 500))
    free_mb = get_free_space_mb(VIDEO_DIR)
    if free_mb < 0:
        log("Disk check: unable to read free space.")
        return

    if free_mb < min_free_mb:
        log(f"Warning: low disk space ({free_mb} MB free).")
        log("Free up space before large downloads.")
    else:
        log(f"Disk space OK: {free_mb} MB free.")


def prepare_and_launch_self_update(new_tag: str, asset_url: str, log: LogFn) -> bool:
    if not getattr(sys, "frozen", False):
        log("App self-update skipped in script mode.")
        return False

    current_exe = Path(sys.executable).name
    new_exe_path = BASE_DIR / f"{Path(current_exe).stem}.new.exe"
    new_version_path = BASE_DIR / "version.new.txt"

    ok = download_file(asset_url, new_exe_path)
    if not ok:
        log("Failed to download updated executable.")
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
        log("Failed to create update script.")
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
        log("Failed to launch update script.")
        return False


def ensure_app_is_fresh(log: LogFn) -> bool:
    cfg = load_update_config()

    if not cfg.get("enabled", False):
        log("App auto-update: disabled.")
        return False

    owner = str(cfg.get("owner", "")).strip()
    repo = str(cfg.get("repo", "")).strip()
    if not owner or not repo:
        log("App auto-update: owner/repo not configured.")
        return False

    interval = str(cfg.get("check_interval", "daily")).lower().strip()
    if interval == "daily" and (not should_check_update_today(APP_UPDATE_STAMP_PATH)):
        log("App auto-update: already checked today.")
        return False

    log("Checking for app updates...")
    release = fetch_latest_release(owner, repo)
    mark_update_checked_today(APP_UPDATE_STAMP_PATH)

    if not release:
        log("App auto-update: could not read latest release.")
        return False

    remote_tag = str(release.get("tag_name", "")).strip()
    if not remote_tag:
        log("App auto-update: release tag not found.")
        return False

    local_version = read_local_app_version()
    if not is_version_newer(remote_tag, local_version):
        log(f"App is up to date ({local_version}).")
        return False

    asset_name = str(cfg.get("asset_name", "downloader_v2.exe")).strip() or "downloader_v2.exe"
    asset_url = pick_release_asset_url(release, asset_name)
    if not asset_url:
        log(f"App auto-update: asset '{asset_name}' not found in release.")
        return False

    log(f"New version found: {remote_tag} (local: {local_version}).")

    auto_apply = bool(cfg.get("auto_apply", True))
    if not auto_apply:
        log("Auto-apply disabled; update is available.")
        return False

    if prepare_and_launch_self_update(remote_tag, asset_url, log):
        log("Update downloaded. Restarting application...")
        return True

    return False


def ensure_ytdlp_is_fresh(log: LogFn) -> None:
    if not YTDLP_PATH.exists():
        log("yt-dlp.exe not found, downloading...")
        ok = download_file(YTDLP_LATEST_URL, YTDLP_PATH)
        mark_update_checked_today(YTDLP_UPDATE_STAMP_PATH)
        if ok:
            log("yt-dlp.exe downloaded successfully.")
        else:
            log("Could not download yt-dlp.exe right now.")
        return

    if not should_check_update_today(YTDLP_UPDATE_STAMP_PATH):
        log("yt-dlp: already checked today.")
        return

    log("Checking yt-dlp updates...")
    ok = download_file(YTDLP_LATEST_URL, YTDLP_PATH)
    mark_update_checked_today(YTDLP_UPDATE_STAMP_PATH)
    if ok:
        log("yt-dlp is up to date.")
    else:
        log("Keeping local yt-dlp version.")


def run_download(url: str, log: LogFn, set_progress: ProgressFn) -> int:
    cmd = [
        str(YTDLP_PATH),
        "--newline",
        "--no-playlist",
        "-f",
        "bestvideo+bestaudio/best",
        "--ffmpeg-location",
        str(BASE_DIR),
        "-o",
        str(VIDEO_DIR / "%(title)s.%(ext)s"),
        url,
    ]

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )

    percent_re = re.compile(r"(\d+(?:\.\d+)?)%")

    assert process.stdout is not None
    for line in process.stdout:
        clean = line.strip()
        if clean:
            log(clean)
            match = percent_re.search(clean)
            if match:
                try:
                    set_progress(float(match.group(1)))
                except ValueError:
                    pass

    return process.wait()


class DownloaderWindow(QWidget):
    DESIGN_W = 1504
    DESIGN_H = 1046

    def __init__(self) -> None:
        super().__init__()

        self.log_queue: Queue[str] = Queue()
        self.progress_queue: Queue[float] = Queue()
        self.ready = False
        self.downloading = False

        self.design_w = self.DESIGN_W
        self.design_h = self.DESIGN_H
        self._load_background_dimensions()

        self.scale_x = self.design_w / self.DESIGN_W
        self.scale_y = self.design_h / self.DESIGN_H

        self.setWindowTitle("YouTube Video Downloader")
        self.setFixedSize(self.design_w, self.design_h)

        icon_path = get_resource_path("app_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_background()
        self._build_widgets()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._flush_queues)
        self.ui_timer.start(120)

        QTimer.singleShot(240, self._bootstrap_async)

    def _load_background_dimensions(self) -> None:
        bg_path = get_resource_path("fondia.png")
        if not bg_path.exists():
            return
        pix = QPixmap(str(bg_path))
        if not pix.isNull():
            self.design_w = pix.width()
            self.design_h = pix.height()

    def _sx(self, value: float) -> int:
        return int(round(value * self.scale_x))

    def _sy(self, value: float) -> int:
        return int(round(value * self.scale_y))

    def _build_background(self) -> None:
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, self.design_w, self.design_h)

        bg_path = get_resource_path("fondia.png")
        if bg_path.exists():
            pix = QPixmap(str(bg_path))
            if not pix.isNull():
                self.bg_label.setPixmap(pix.scaled(self.design_w, self.design_h))
                return

        # Fallback if background missing
        self.bg_label.setStyleSheet("background-color: #f3d4e2;")

    def _build_widgets(self) -> None:
        # URL input
        self.url_entry = QLineEdit(self)
        self.url_entry.setPlaceholderText("Paste your YouTube URL here...")
        self.url_entry.setGeometry(self._sx(130), self._sy(402), self._sx(630), self._sy(62))
        self.url_entry.setStyleSheet(
            "QLineEdit {"
            "background-color: #f3edf0; color: #8a3d64;"
            "border: 2px solid #de8db0; border-radius: 8px; padding: 10px;"
            "}"
        )
        self.url_entry.setFont(QFont("Georgia", 15))
        self.url_entry.returnPressed.connect(self.on_download)

        # Download button
        self.download_btn = QPushButton("Download", self)
        self.download_btn.setGeometry(self._sx(803), self._sy(399), self._sx(151), self._sy(69))
        self.download_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #e58db2; color: #fff6fb;"
            "border: 2px solid #c9668f; border-radius: 8px;"
            "font-family: Georgia; font-size: 17px; font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #d6769f; }"
            "QPushButton:disabled { background-color: #cda0b6; color: #f5dbe8; }"
        )
        self.download_btn.clicked.connect(self.on_download)

        # Actions
        self.open_folder_btn = QPushButton("Open Video Folder", self)
        self.open_folder_btn.setGeometry(self._sx(122), self._sy(500), self._sx(262), self._sy(64))
        self.open_folder_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #f0d2de; color: #9b3d66;"
            "border: 2px solid #c98aa8; border-radius: 8px;"
            "font-family: Georgia; font-size: 16px; font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #e6becf; }"
        )
        self.open_folder_btn.clicked.connect(self.open_video_folder)

        self.check_updates_btn = QPushButton("Check Updates", self)
        self.check_updates_btn.setGeometry(self._sx(420), self._sy(500), self._sx(238), self._sy(64))
        self.check_updates_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #f0d2de; color: #9b3d66;"
            "border: 2px solid #c98aa8; border-radius: 8px;"
            "font-family: Georgia; font-size: 16px; font-weight: 700;"
            "}"
            "QPushButton:hover { background-color: #e6becf; }"
            "QPushButton:disabled { background-color: #ead0db; color: #bb93a8; }"
        )
        self.check_updates_btn.clicked.connect(self.check_updates_now)

        # Status
        self.status_label = QLabel("Starting", self)
        self.status_label.setGeometry(self._sx(825), self._sy(513), self._sx(160), self._sy(45))
        self.status_label.setFont(QFont("Georgia", 18, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: #9f426a; background: transparent;")

        # Progress
        self.progress = QProgressBar(self)
        self.progress.setGeometry(self._sx(37), self._sy(586), self._sx(961), self._sy(20))
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar {"
            "background-color: #f0c9db; border: 0px; border-radius: 7px;"
            "}"
            "QProgressBar::chunk {"
            "background-color: #e885ab; border-radius: 7px;"
            "}"
        )

        # Logs
        self.logs = QPlainTextEdit(self)
        self.logs.setGeometry(self._sx(69), self._sy(712), self._sx(887), self._sy(223))
        self.logs.setReadOnly(True)
        self.logs.setStyleSheet(
            "QPlainTextEdit {"
            "background-color: #37132e; color: #fff0f7;"
            "border: 2px solid #c98aa8; border-radius: 14px;"
            "padding: 8px; font-family: Consolas; font-size: 16px;"
            "}"
            "QScrollBar:vertical {"
            "background: #f0d2de; width: 12px; margin: 12px 0 12px 0;"
            "border-radius: 6px;"
            "}"
            "QScrollBar::handle:vertical {"
            "background: #d978a0; min-height: 30px; border-radius: 6px;"
            "}"
        )

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.url_entry.setEnabled(enabled)
        self.download_btn.setEnabled(enabled)
        self.check_updates_btn.setEnabled(enabled)
        self.open_folder_btn.setEnabled(True)

    def _append_log(self, msg: str) -> None:
        self.logs.appendPlainText(msg)
        scrollbar = self.logs.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def set_progress(self, value: float) -> None:
        self.progress_queue.put(max(0.0, min(100.0, value)))

    def _set_status(self, value: str) -> None:
        self.status_label.setText(value)

    def _flush_queues(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except Empty:
            pass

        try:
            while True:
                value = self.progress_queue.get_nowait()
                self.progress.setValue(int(round(value)))
        except Empty:
            pass

    def _bootstrap_async(self) -> None:
        threading.Thread(target=self._bootstrap_worker, daemon=True).start()

    def _bootstrap_worker(self) -> None:
        self.log("Initializing runtime...")
        ensure_video_dir()
        runtime_cfg = load_runtime_config()

        ensure_ffmpeg_ready(runtime_cfg, self.log)
        check_free_space(runtime_cfg, self.log)

        if ensure_app_is_fresh(self.log):
            self._set_status("Updating")
            QTimer.singleShot(1200, self.close)
            return

        ensure_ytdlp_is_fresh(self.log)

        if not YTDLP_PATH.exists():
            self.log("Error: yt-dlp.exe is missing. Download cannot continue.")
            self._set_status("Error")
            return

        self.ready = True
        self._set_status("Ready")
        self.log("Ready. Paste a YouTube URL and click Download.")
        QTimer.singleShot(0, lambda: self._set_controls_enabled(True))

    def on_download(self) -> None:
        if not self.ready or self.downloading:
            return

        url = self.url_entry.text().strip()
        if not url:
            self.log("Please paste a YouTube URL first.")
            self._set_status("Missing URL")
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            self.log("Invalid URL. It must start with http:// or https://")
            self._set_status("Invalid URL")
            return

        self.downloading = True
        self.progress.setValue(0)
        self._set_status("Downloading")
        self._set_controls_enabled(False)
        self.log("Starting download...")

        threading.Thread(target=self._download_worker, args=(url,), daemon=True).start()

    def _download_worker(self, url: str) -> None:
        code = run_download(url, self.log, self.set_progress)

        def finish() -> None:
            self.downloading = False
            self._set_controls_enabled(True)
            if code == 0:
                self.progress.setValue(100)
                self._set_status("Completed")
                self.log(f"SUCCESS: Video saved to '{VIDEO_DIR}'")
            else:
                self._set_status("Failed")
                self.log(f"FAILED: yt-dlp returned exit code {code}")

        QTimer.singleShot(0, finish)

    def open_video_folder(self) -> None:
        ensure_video_dir()
        try:
            os.startfile(str(VIDEO_DIR))
            self._set_status("Folder opened")
        except Exception:
            self.log(f"Video folder: {VIDEO_DIR}")

    def check_updates_now(self) -> None:
        if self.downloading:
            self.log("Please wait until the current download finishes.")
            return

        self._set_status("Checking")
        self._set_controls_enabled(False)

        def worker() -> None:
            if ensure_app_is_fresh(self.log):
                QTimer.singleShot(1200, self.close)
                return

            ensure_ytdlp_is_fresh(self.log)

            def done() -> None:
                self._set_status("Ready")
                self._set_controls_enabled(True)
                self.log("Update check finished.")

            QTimer.singleShot(0, done)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    window = DownloaderWindow()
    window.show()
    sys.exit(qt_app.exec())

