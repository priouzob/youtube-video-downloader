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

import tkinter as tk
from PIL import Image, ImageEnhance, ImageTk
import customtkinter as ctk

# Embedded app version for one-file mode
APP_VERSION = "v1.2.1"
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


class DownloaderApp:
    def __init__(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("YouTube Video Downloader")
        self.root.geometry("1160x760")
        self.root.minsize(980, 680)
        self.root.configure(fg_color="#f6dce6")
        self._apply_window_icon()

        self.log_queue: Queue[str] = Queue()
        self.progress_queue: Queue[float] = Queue()
        self.ready = False
        self.downloading = False

        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Starting...")
        self.progress_var = tk.DoubleVar(value=0.0)

        self.bg_original: Optional[Image.Image] = None
        self.bg_photo: Optional[ImageTk.PhotoImage] = None
        self.hero_photo: Optional[ctk.CTkImage] = None

        self._build_ui()

        self.root.after(120, self._flush_queues)
        self.root.after(260, self._bootstrap_async)
        self.root.bind("<Configure>", self._on_root_resize)

    def _apply_window_icon(self) -> None:
        icon_path = get_resource_path("app_icon.ico")
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.bg_label = tk.Label(self.root, bd=0, highlightthickness=0)
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._prepare_background()

        self.main = ctk.CTkFrame(
            self.root,
            fg_color="#fff4f8",
            corner_radius=26,
            border_width=2,
            border_color="#eaa3bc",
        )
        self.main.pack(fill="both", expand=True, padx=14, pady=14)

        title = ctk.CTkLabel(
            self.main,
            text="YouTube Video Downloader",
            text_color="#a43f63",
            font=ctk.CTkFont(family="Times New Roman", size=54, weight="bold"),
        )
        title.pack(pady=(14, 2))

        subtitle_wrap = ctk.CTkFrame(
            self.main,
            fg_color="#e985ad",
            corner_radius=22,
            border_width=1,
            border_color="#cd5b87",
            width=440,
            height=38,
        )
        subtitle_wrap.pack_propagate(False)
        subtitle_wrap.pack()

        ctk.CTkLabel(
            subtitle_wrap,
            text=f"Version {APP_VERSION} ? Smart auto-update ? one-file friendly",
            text_color="#fff3f8",
            font=ctk.CTkFont(family="Georgia", size=16),
        ).pack(expand=True)

        content = ctk.CTkFrame(self.main, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=(14, 10))

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)

        right = ctk.CTkFrame(
            content,
            fg_color="#f9e3ec",
            corner_radius=20,
            border_width=2,
            border_color="#e4a1bd",
            width=330,
        )
        right.pack(side="left", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        url_card = ctk.CTkFrame(
            left,
            fg_color="#fdf0f5",
            corner_radius=20,
            border_width=2,
            border_color="#e7b3c8",
            height=156,
        )
        url_card.pack(fill="x")
        url_card.pack_propagate(False)

        ctk.CTkLabel(
            url_card,
            text="?  YouTube URL",
            text_color="#a34c6f",
            font=ctk.CTkFont(family="Georgia", size=34, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(14, 8))

        row = ctk.CTkFrame(url_card, fg_color="transparent")
        row.pack(fill="x", padx=18)

        self.url_entry = ctk.CTkEntry(
            row,
            textvariable=self.url_var,
            placeholder_text="Paste your YouTube URL here...",
            fg_color="#fff8fb",
            border_color="#e58dad",
            text_color="#6b2f4a",
            placeholder_text_color="#bb7d99",
            corner_radius=16,
            height=54,
            font=ctk.CTkFont(family="Georgia", size=23),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.url_entry.bind("<Return>", lambda _e: self.on_download())

        self.download_btn = ctk.CTkButton(
            row,
            text="Download",
            command=self.on_download,
            corner_radius=18,
            fg_color="#ea84af",
            hover_color="#d86a98",
            border_width=2,
            border_color="#cf5f8b",
            text_color="#fff7fb",
            font=ctk.CTkFont(family="Georgia", size=31, weight="bold"),
            width=250,
            height=54,
        )
        self.download_btn.pack(side="left")

        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.pack(fill="x", pady=(12, 6))

        self.open_folder_btn = ctk.CTkButton(
            actions,
            text="? Open Video Folder",
            command=self.open_video_folder,
            corner_radius=16,
            fg_color="#f7d2e1",
            hover_color="#edbfd3",
            border_width=2,
            border_color="#d893af",
            text_color="#8d3559",
            font=ctk.CTkFont(family="Georgia", size=30, weight="bold"),
            width=270,
            height=54,
        )
        self.open_folder_btn.pack(side="left")

        self.check_updates_btn = ctk.CTkButton(
            actions,
            text="? Check Updates",
            command=self.check_updates_now,
            corner_radius=16,
            fg_color="#f7d2e1",
            hover_color="#edbfd3",
            border_width=2,
            border_color="#d893af",
            text_color="#8d3559",
            font=ctk.CTkFont(family="Georgia", size=30, weight="bold"),
            width=250,
            height=54,
        )
        self.check_updates_btn.pack(side="left", padx=(12, 0))

        ctk.CTkLabel(
            actions,
            textvariable=self.status_var,
            text_color="#b24f74",
            font=ctk.CTkFont(family="Georgia", size=37, weight="bold"),
        ).pack(side="right", padx=(8, 4))

        progress_wrap = ctk.CTkFrame(left, fg_color="transparent")
        progress_wrap.pack(fill="x", pady=(2, 10))

        self.progress = ctk.CTkProgressBar(
            progress_wrap,
            progress_color="#eb75a3",
            fg_color="#f5c9dc",
            corner_radius=12,
            height=14,
        )
        self.progress.pack(fill="x")
        self.progress.set(0.0)

        logs_card = ctk.CTkFrame(
            left,
            fg_color="#fdf0f5",
            corner_radius=20,
            border_width=2,
            border_color="#e7b3c8",
        )
        logs_card.pack(fill="both", expand=True)

        ctk.CTkLabel(
            logs_card,
            text="?  Live Logs",
            text_color="#a34c6f",
            font=ctk.CTkFont(family="Georgia", size=35, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(10, 8))

        self.logs = ctk.CTkTextbox(
            logs_card,
            corner_radius=16,
            border_width=2,
            border_color="#e4a1bd",
            fg_color="#2a1024",
            text_color="#ffe6f1",
            font=ctk.CTkFont(family="Consolas", size=23),
            wrap="word",
        )
        self.logs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.logs.configure(state="disabled")

        # Right artwork panel without explicit label text
        self.art_label = ctk.CTkLabel(right, text="", fg_color="transparent")
        self.art_label.pack(fill="both", expand=True, padx=10, pady=10)
        self._refresh_hero_art(300, 560)

        footer = ctk.CTkLabel(
            self.main,
            text="? Tip: If your release is private, set environment variable YD_GITHUB_TOKEN.",
            text_color="#b35c80",
            font=ctk.CTkFont(family="Georgia", size=17),
        )
        footer.pack(fill="x", pady=(4, 10))

        self._set_controls_enabled(False)

    def _prepare_background(self) -> None:
        bg_path = get_resource_path("assets/makima_bg.jpg")
        if not bg_path.exists():
            self.bg_original = None
            return

        try:
            raw = Image.open(bg_path).convert("RGBA")
            overlay = Image.new("RGBA", raw.size, (255, 160, 200, 95))
            mixed = Image.alpha_composite(raw, overlay)
            mixed = ImageEnhance.Brightness(mixed).enhance(0.92)
            self.bg_original = mixed
        except Exception:
            self.bg_original = None

    def _refresh_background(self, width: int, height: int) -> None:
        if self.bg_original is None or width < 2 or height < 2:
            return
        try:
            resized = self.bg_original.resize((width, height), Image.Resampling.LANCZOS)
            self.bg_photo = ImageTk.PhotoImage(resized)
            self.bg_label.configure(image=self.bg_photo)
        except Exception:
            pass

    def _refresh_hero_art(self, width: int, height: int) -> None:
        bg_path = get_resource_path("assets/makima_bg.jpg")
        if not bg_path.exists() or width < 10 or height < 10:
            return
        try:
            img = Image.open(bg_path).convert("RGBA")
            overlay = Image.new("RGBA", img.size, (255, 145, 195, 65))
            mixed = Image.alpha_composite(img, overlay)
            crop = mixed.resize((width, height), Image.Resampling.LANCZOS)
            self.hero_photo = ctk.CTkImage(light_image=crop, dark_image=crop, size=(width, height))
            self.art_label.configure(image=self.hero_photo)
        except Exception:
            pass

    def _on_root_resize(self, _event: tk.Event) -> None:
        self._refresh_background(self.root.winfo_width(), self.root.winfo_height())
        if hasattr(self, "art_label"):
            self._refresh_hero_art(self.art_label.winfo_width(), self.art_label.winfo_height())

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.url_entry.configure(state=state)
        self.download_btn.configure(state=state)
        self.check_updates_btn.configure(state=state)
        self.open_folder_btn.configure(state="normal")

    def _append_log(self, msg: str) -> None:
        self.logs.configure(state="normal")
        self.logs.insert("end", msg + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def set_progress(self, value: float) -> None:
        self.progress_queue.put(max(0.0, min(100.0, value)))

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
                self.progress_var.set(value)
                self.progress.set(value / 100.0)
        except Empty:
            pass

        self.root.after(120, self._flush_queues)

    def _bootstrap_async(self) -> None:
        threading.Thread(target=self._bootstrap_worker, daemon=True).start()

    def _bootstrap_worker(self) -> None:
        self.log("Initializing runtime...")
        ensure_video_dir()
        runtime_cfg = load_runtime_config()

        ensure_ffmpeg_ready(runtime_cfg, self.log)
        check_free_space(runtime_cfg, self.log)

        if ensure_app_is_fresh(self.log):
            self.status_var.set("Updating...")
            self.root.after(1200, self.root.destroy)
            return

        ensure_ytdlp_is_fresh(self.log)

        if not YTDLP_PATH.exists():
            self.log("Error: yt-dlp.exe is missing. Download cannot continue.")
            self.status_var.set("Error")
            return

        self.ready = True
        self.status_var.set("Ready")
        self.log("Ready. Paste a YouTube URL and click Download.")
        self.root.after(0, lambda: self._set_controls_enabled(True))

    def on_download(self) -> None:
        if not self.ready or self.downloading:
            return

        url = self.url_var.get().strip()
        if not url:
            self.log("Please paste a YouTube URL first.")
            self.status_var.set("Missing URL")
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            self.log("Invalid URL. It must start with http:// or https://")
            self.status_var.set("Invalid URL")
            return

        self.downloading = True
        self.progress_var.set(0.0)
        self.progress.set(0.0)
        self.status_var.set("Downloading")
        self._set_controls_enabled(False)
        self.log("Starting download...")

        threading.Thread(target=self._download_worker, args=(url,), daemon=True).start()

    def _download_worker(self, url: str) -> None:
        code = run_download(url, self.log, self.set_progress)

        def finish() -> None:
            self.downloading = False
            self._set_controls_enabled(True)
            if code == 0:
                self.progress_var.set(100.0)
                self.progress.set(1.0)
                self.status_var.set("Completed")
                self.log(f"SUCCESS: Video saved to '{VIDEO_DIR}'")
            else:
                self.status_var.set("Failed")
                self.log(f"FAILED: yt-dlp returned exit code {code}")

        self.root.after(0, finish)

    def open_video_folder(self) -> None:
        ensure_video_dir()
        try:
            os.startfile(str(VIDEO_DIR))
            self.status_var.set("Folder opened")
        except Exception:
            self.log(f"Video folder: {VIDEO_DIR}")

    def check_updates_now(self) -> None:
        if self.downloading:
            self.log("Please wait until the current download finishes.")
            return

        self.status_var.set("Checking updates")
        self._set_controls_enabled(False)

        def worker() -> None:
            if ensure_app_is_fresh(self.log):
                self.root.after(1200, self.root.destroy)
                return

            ensure_ytdlp_is_fresh(self.log)

            def done() -> None:
                self.status_var.set("Ready")
                self._set_controls_enabled(True)
                self.log("Update check finished.")

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    app = DownloaderApp()
    app.run()
