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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
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

YTDLP_CANDIDATE_URLS = (
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_win.zip",
    "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe",
)
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
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"


def get_resource_path(relative_path: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / relative_path
    return BASE_DIR / relative_path


def load_custom_fonts() -> dict[str, str]:
    # Default serif stack if no custom font is bundled.
    families = {
        "ui": "Georgia",
        "mono": "Consolas",
    }

    font_roots = [get_resource_path("assets/fonts")]
    if not getattr(sys, "frozen", False):
        font_roots.append(FONTS_DIR)

    loaded_any = False
    for root in font_roots:
        if not root.exists():
            continue
        for ext in ("*.ttf", "*.otf"):
            for font_file in root.glob(ext):
                font_id = QFontDatabase.addApplicationFont(str(font_file))
                if font_id >= 0:
                    fams = QFontDatabase.applicationFontFamilies(font_id)
                    if fams and not loaded_any:
                        # Use the first loaded custom family as main UI font.
                        families["ui"] = fams[0]
                        loaded_any = True
    return families


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
        "ui_offset_x": 0,
        "ui_offset_y": 0,
        "ui_debug_hitboxes": False,
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


def download_file_with_error(
    url: str, output_path: Path, timeout_seconds: int = 30
) -> tuple[bool, str]:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            data = response.read()

        with open(temp_path, "wb") as f:
            f.write(data)

        os.replace(temp_path, output_path)
        return True, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        return False, str(exc)


def download_ytdlp_binary(log: LogFn) -> bool:
    zip_temp_path = BASE_DIR / "yt-dlp_tmp.zip"

    for url in YTDLP_CANDIDATE_URLS:
        log(f"Trying yt-dlp source: {url}")

        if url.lower().endswith(".zip"):
            ok, err = download_file_with_error(url, zip_temp_path, timeout_seconds=90)
            if not ok:
                log(f"Source failed: {err or 'download error'}")
                continue

            try:
                extracted = False
                with zipfile.ZipFile(zip_temp_path, "r") as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        name = Path(info.filename).name.lower()
                        if name == YTDLP_EXE_NAME:
                            with archive.open(info, "r") as src, open(YTDLP_PATH, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            extracted = True
                            break
                if extracted:
                    return True
                log("ZIP source downloaded but yt-dlp.exe not found inside archive.")
            except (OSError, zipfile.BadZipFile) as exc:
                log(f"ZIP source invalid: {exc}")
            finally:
                try:
                    if zip_temp_path.exists():
                        zip_temp_path.unlink()
                except OSError:
                    pass
            continue

        ok, err = download_file_with_error(url, YTDLP_PATH, timeout_seconds=90)
        if ok:
            return True
        log(f"Source failed: {err or 'download error'}")

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
        ok = download_ytdlp_binary(log)
        mark_update_checked_today(YTDLP_UPDATE_STAMP_PATH)
        if ok:
            log("yt-dlp.exe downloaded successfully.")
        else:
            log("Could not download yt-dlp.exe right now.")
            log("Check internet access/firewall/antivirus and try again.")
        return

    if not should_check_update_today(YTDLP_UPDATE_STAMP_PATH):
        log("yt-dlp: already checked today.")
        return

    log("Checking yt-dlp updates...")
    ok = download_ytdlp_binary(log)
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
    WIDTH = 1200
    HEIGHT = 760

    def __init__(self) -> None:
        super().__init__()

        self.log_queue: Queue[str] = Queue()
        self.progress_queue: Queue[float] = Queue()
        self.event_queue: Queue[tuple[str, object]] = Queue()
        self.ready = False
        self.downloading = False
        self.fonts = load_custom_fonts()
        self.runtime_cfg = load_runtime_config()

        self.setWindowTitle("YouTube Video Downloader")
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        icon_path = get_resource_path("app_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._flush_queues)
        self.ui_timer.start(100)

        QTimer.singleShot(200, self._bootstrap_async)

    def _apply_shadow(self, widget: QWidget, blur: int = 26) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(73, 28, 56, 95))
        widget.setGraphicsEffect(shadow)

    def _build_ui(self) -> None:
        ui_font = self.fonts.get("ui", "Georgia")
        mono_font = self.fonts.get("mono", "Consolas")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        header = QFrame(self)
        header.setObjectName("headerCard")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 18, 24, 20)
        header_layout.setSpacing(8)

        bow = QLabel("🎀", header)
        bow.setObjectName("bowLabel")
        bow.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(bow)

        title = QLabel("YouTube Video Downloader", header)
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont(ui_font, 34, QFont.Bold))
        header_layout.addWidget(title)

        subtitle = QLabel("Version v1.3.0 · Smart auto-update · One-file friendly", header)
        subtitle.setObjectName("subtitlePill")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont(ui_font, 12, QFont.DemiBold))
        header_layout.addWidget(subtitle, 0, Qt.AlignCenter)

        self._apply_shadow(header)
        root.addWidget(header)

        center = QHBoxLayout()
        center.setSpacing(12)
        root.addLayout(center, 1)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)
        center.addLayout(left_col, 3)

        url_card = QFrame(self)
        url_card.setObjectName("card")
        url_layout = QVBoxLayout(url_card)
        url_layout.setContentsMargins(16, 14, 16, 14)
        url_layout.setSpacing(10)

        url_title = QLabel("🌸 YouTube URL", url_card)
        url_title.setObjectName("sectionTitle")
        url_layout.addWidget(url_title)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.url_entry = QLineEdit(url_card)
        self.url_entry.setObjectName("urlInput")
        self.url_entry.setPlaceholderText("Paste your YouTube URL here...")
        self.url_entry.setMinimumHeight(50)
        self.url_entry.returnPressed.connect(self.on_download)
        top_row.addWidget(self.url_entry, 1)

        self.download_btn = QPushButton("Download", url_card)
        self.download_btn.setObjectName("downloadButton")
        self.download_btn.setMinimumSize(210, 50)
        self.download_btn.clicked.connect(self.on_download)
        top_row.addWidget(self.download_btn)

        url_layout.addLayout(top_row)

        actions = QHBoxLayout()
        actions.setSpacing(10)

        self.open_folder_btn = QPushButton("📁 Open Video Folder", url_card)
        self.open_folder_btn.setObjectName("secondaryButton")
        self.open_folder_btn.setMinimumHeight(44)
        self.open_folder_btn.clicked.connect(self.open_video_folder)
        actions.addWidget(self.open_folder_btn)

        self.check_updates_btn = QPushButton("🔄 Check Updates", url_card)
        self.check_updates_btn.setObjectName("secondaryButton")
        self.check_updates_btn.setMinimumHeight(44)
        self.check_updates_btn.clicked.connect(self.check_updates_now)
        actions.addWidget(self.check_updates_btn)

        actions.addStretch(1)

        self.status_label = QLabel("Ready", url_card)
        self.status_label.setObjectName("statusChip")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumWidth(130)
        self.status_label.setMinimumHeight(40)
        actions.addWidget(self.status_label)

        url_layout.addLayout(actions)

        self.progress = QProgressBar(url_card)
        self.progress.setObjectName("progressBar")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(8)
        url_layout.addWidget(self.progress)

        self._apply_shadow(url_card)
        left_col.addWidget(url_card)

        logs_card = QFrame(self)
        logs_card.setObjectName("card")
        logs_layout = QVBoxLayout(logs_card)
        logs_layout.setContentsMargins(16, 14, 16, 14)
        logs_layout.setSpacing(10)

        logs_title = QLabel("🌸 Live Logs", logs_card)
        logs_title.setObjectName("sectionTitle")
        logs_layout.addWidget(logs_title)

        logs_shell = QFrame(logs_card)
        logs_shell.setObjectName("logsShell")
        logs_shell_layout = QVBoxLayout(logs_shell)
        logs_shell_layout.setContentsMargins(8, 8, 8, 8)

        self.logs = QPlainTextEdit(logs_shell)
        self.logs.setObjectName("logView")
        self.logs.setReadOnly(True)
        self.logs.setFont(QFont(mono_font, 11))
        logs_shell_layout.addWidget(self.logs)
        logs_layout.addWidget(logs_shell, 1)

        self._apply_shadow(logs_card)
        left_col.addWidget(logs_card, 1)

        right_card = QFrame(self)
        right_card.setObjectName("card")
        right_card.setMinimumWidth(320)
        right_card.setMaximumWidth(340)
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        right_title = QLabel("Theme Artwork", right_card)
        right_title.setObjectName("sectionTitleCenter")
        right_layout.addWidget(right_title)

        art_box = QFrame(right_card)
        art_box.setObjectName("artBox")
        art_layout = QVBoxLayout(art_box)
        art_layout.setContentsMargins(18, 18, 18, 18)
        art_layout.setSpacing(10)

        avatar = QLabel("♡", art_box)
        avatar.setObjectName("avatarCircle")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(120, 120)
        art_layout.addWidget(avatar, 0, Qt.AlignHCenter)

        art_heart = QLabel("♥", art_box)
        art_heart.setObjectName("artHeart")
        art_heart.setAlignment(Qt.AlignCenter)
        art_layout.addWidget(art_heart)

        art_text = QLabel("MODERN\nPINK UI", art_box)
        art_text.setObjectName("artText")
        art_text.setAlignment(Qt.AlignCenter)
        art_layout.addWidget(art_text)
        art_layout.addStretch(1)

        right_layout.addWidget(art_box, 1)

        right_deco = QLabel("🌸     🎀     🌸", right_card)
        right_deco.setObjectName("rightDeco")
        right_deco.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(right_deco)

        self._apply_shadow(right_card)
        center.addWidget(right_card)

        self.footer_label = QLabel(
            "Tip: Auto-update checks GitHub releases. Keep internet enabled for updates.",
            self,
        )
        self.footer_label.setObjectName("footerTip")
        self.footer_label.setAlignment(Qt.AlignCenter)
        self.footer_label.setMinimumHeight(40)
        root.addWidget(self.footer_label)

        self.setStyleSheet(
            f"""
            QWidget {{
                font-family: '{ui_font}';
                color: #7f3558;
                background: #ffdceb;
            }}

            #headerCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffeef6, stop:1 #ffd9e9);
                border: 2px solid #e79abf;
                border-radius: 22px;
            }}

            #bowLabel {{
                font-size: 28px;
                color: #d66f9d;
                background: transparent;
            }}

            #titleLabel {{
                color: #a24470;
                letter-spacing: 1px;
                background: transparent;
            }}

            #subtitlePill {{
                color: #fff7fb;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #f2a3c6, stop:0.5 #e47cad, stop:1 #f2a3c6);
                border: 1px solid #cb6f98;
                border-radius: 14px;
                padding: 6px 18px;
            }}

            #card {{
                background: #fff4f9;
                border: 2px solid #e5a2c2;
                border-radius: 18px;
            }}

            #sectionTitle {{
                font-size: 32px;
                font-weight: 700;
                color: #9e446f;
                background: transparent;
            }}

            #sectionTitleCenter {{
                font-size: 28px;
                font-weight: 700;
                color: #9e446f;
                qproperty-alignment: AlignCenter;
                background: transparent;
            }}

            #urlInput {{
                background: #fff8fc;
                border: 2px solid #ecb0cd;
                border-radius: 14px;
                padding: 8px 12px;
                font-size: 17px;
                color: #8b3d65;
            }}

            #urlInput::placeholder {{
                color: #c184a4;
            }}

            #downloadButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f7a9cc, stop:1 #dd6f9f);
                border: 1px solid #c86390;
                border-radius: 14px;
                color: #fff8fc;
                font-size: 28px;
                font-weight: 700;
                padding: 4px 12px;
            }}

            #downloadButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #fbb8d5, stop:1 #e17ca9);
            }}

            #secondaryButton {{
                background: #ffeaf4;
                border: 1px solid #df9cbd;
                border-radius: 12px;
                color: #8d3f65;
                font-size: 17px;
                font-weight: 650;
                padding: 4px 10px;
            }}

            #secondaryButton:hover {{
                background: #ffddea;
            }}

            #statusChip {{
                background: #ffe2ef;
                border: 1px solid #d68caf;
                border-radius: 12px;
                color: #a0456f;
                font-size: 18px;
                font-weight: 700;
                padding: 4px 10px;
            }}

            #progressBar {{
                background: #f9d6e7;
                border: none;
                border-radius: 4px;
            }}

            #progressBar::chunk {{
                background: #e16f9e;
                border-radius: 4px;
            }}

            #logsShell {{
                background: #471033;
                border: 1px solid #7e2f58;
                border-radius: 14px;
            }}

            #logView {{
                background: #2a0a25;
                border: none;
                border-radius: 10px;
                color: #ffe7f3;
                padding: 6px;
                selection-background-color: #bc6e95;
            }}

            QScrollBar:vertical {{
                background: #f3cfe0;
                width: 12px;
                border-radius: 6px;
                margin: 6px 0 6px 0;
            }}

            QScrollBar::handle:vertical {{
                background: #d97aa3;
                border-radius: 6px;
                min-height: 24px;
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}

            #artBox {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffd9e9, stop:1 #f2a9c7);
                border: 2px solid #d182a7;
                border-radius: 16px;
            }}

            #avatarCircle {{
                background: #ffeef6;
                border: 2px solid #c96e97;
                border-radius: 60px;
                color: #af547d;
                font-size: 50px;
            }}

            #artHeart {{
                color: #b45079;
                font-size: 44px;
                font-weight: 700;
                background: transparent;
            }}

            #artText {{
                color: #8e3f66;
                font-size: 38px;
                font-weight: 800;
                background: transparent;
            }}

            #rightDeco {{
                color: #9b4a71;
                font-size: 24px;
                font-weight: 700;
                background: transparent;
            }}

            #footerTip {{
                background: #fff1f8;
                border: 1px solid #e1a0be;
                border-radius: 12px;
                color: #99476e;
                font-size: 15px;
                font-weight: 600;
                padding: 8px 12px;
            }}
            """
        )

        self._set_controls_enabled(True)

    def _set_controls_enabled(self, interactive: bool) -> None:
        can_use = interactive and (not self.downloading)
        self.url_entry.setEnabled(can_use)
        self.download_btn.setEnabled(can_use)
        self.check_updates_btn.setEnabled(can_use)
        self.open_folder_btn.setEnabled(True)

    def _append_log(self, msg: str) -> None:
        self.logs.appendPlainText(msg)
        sb = self.logs.verticalScrollBar()
        sb.setValue(sb.maximum())

    def log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def set_progress(self, value: float) -> None:
        self.progress_queue.put(max(0.0, min(100.0, value)))

    def post_event(self, name: str, payload: object = None) -> None:
        self.event_queue.put((name, payload))

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

        try:
            while True:
                name, payload = self.event_queue.get_nowait()
                if name == "status":
                    self.status_label.setText(str(payload).strip())
                elif name == "ready":
                    self.ready = bool(payload)
                    self._set_controls_enabled(True)
                elif name == "downloading":
                    self.downloading = bool(payload)
                    self.progress.setVisible(self.downloading)
                    if not self.downloading:
                        QTimer.singleShot(1200, lambda: self.progress.setVisible(False))
                    self._set_controls_enabled(True)
        except Empty:
            pass

    def _bootstrap_async(self) -> None:
        threading.Thread(target=self._bootstrap_worker, daemon=True).start()

    def _prepare_runtime_dependencies(self) -> bool:
        ensure_video_dir()
        runtime_cfg = self.runtime_cfg

        ensure_ffmpeg_ready(runtime_cfg, self.log)
        check_free_space(runtime_cfg, self.log)
        ensure_ytdlp_is_fresh(self.log)

        if not YTDLP_PATH.exists():
            self.log("Error: yt-dlp.exe is missing. Download cannot continue.")
            self.post_event("status", "Error")
            self.post_event("ready", False)
            return False

        self.post_event("status", "Ready")
        self.post_event("ready", True)
        return True

    def _bootstrap_worker(self) -> None:
        self.log("Initializing runtime...")

        if ensure_app_is_fresh(self.log):
            self.post_event("status", "Updating")
            QTimer.singleShot(1200, self.close)
            return

        if not self._prepare_runtime_dependencies():
            return

        self.log("Ready. Paste a YouTube URL and click Download.")

    def on_download(self) -> None:
        if self.downloading:
            return

        url = self.url_entry.text().strip()
        if not url:
            self.log("Please paste a YouTube URL first.")
            self.post_event("status", "Missing URL")
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            self.log("Invalid URL. It must start with http:// or https://")
            self.post_event("status", "Invalid URL")
            return

        if not self.ready:
            self.log("Runtime not ready yet. Preparing dependencies, then starting download...")
            self.post_event("status", "Preparing")
            self.post_event("downloading", True)
            threading.Thread(target=self._prepare_and_download_worker, args=(url,), daemon=True).start()
            return

        self.progress.setValue(0)
        self.post_event("status", "Downloading")
        self.post_event("downloading", True)
        self.log("Starting download...")

        threading.Thread(target=self._download_worker, args=(url,), daemon=True).start()

    def _prepare_and_download_worker(self, url: str) -> None:
        if not self._prepare_runtime_dependencies():
            self.post_event("downloading", False)
            return

        self.log("Starting download...")
        self.post_event("status", "Downloading")
        self._download_worker(url)

    def _download_worker(self, url: str) -> None:
        code = run_download(url, self.log, self.set_progress)

        if code == 0:
            self.progress_queue.put(100.0)
            self.post_event("status", "Completed")
            self.log(f"SUCCESS: Video saved to '{VIDEO_DIR}'")
        else:
            self.post_event("status", "Failed")
            self.log(f"FAILED: yt-dlp returned exit code {code}")

        self.post_event("downloading", False)

    def open_video_folder(self) -> None:
        ensure_video_dir()
        try:
            os.startfile(str(VIDEO_DIR))
            self.post_event("status", "Folder opened")
        except Exception:
            self.log(f"Video folder: {VIDEO_DIR}")

    def check_updates_now(self) -> None:
        if self.downloading:
            self.log("Please wait until the current download finishes.")
            return

        self.post_event("status", "Checking")
        self.post_event("downloading", True)

        def worker() -> None:
            if ensure_app_is_fresh(self.log):
                QTimer.singleShot(1200, self.close)
                return

            ensure_ytdlp_is_fresh(self.log)
            self.log("Update check finished.")
            self.post_event("status", "Ready")
            self.post_event("downloading", False)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qt_app = QApplication(sys.argv)
    window = DownloaderWindow()
    window.show()
    sys.exit(qt_app.exec())

