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
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# Embedded app version for one-file mode
APP_VERSION = "v1.1.0"
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

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
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
        self.root = tk.Tk()
        self.root.title("YouTube Downloader V2")
        self.root.geometry("980x680")
        self.root.minsize(860, 600)
        self.root.configure(bg="#111827")
        self._apply_window_icon()

        self.log_queue: Queue[str] = Queue()
        self.progress_queue: Queue[float] = Queue()
        self.ready = False
        self.downloading = False

        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Starting...")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_style()
        self._build_ui()

        self.root.after(120, self._flush_queues)
        self.root.after(250, self._bootstrap_async)

    def _apply_window_icon(self) -> None:
        icon_path = BASE_DIR / "app_icon.ico"
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except Exception:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Accent.TButton",
            foreground="#ffffff",
            background="#2563eb",
            borderwidth=0,
            focusthickness=0,
            focuscolor="#2563eb",
            padding=(14, 10),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#1d4ed8"), ("disabled", "#334155")],
            foreground=[("disabled", "#94a3b8")],
        )

        style.configure(
            "Subtle.TButton",
            foreground="#e5e7eb",
            background="#1f2937",
            borderwidth=1,
            padding=(12, 9),
            font=("Segoe UI", 10),
        )
        style.map(
            "Subtle.TButton",
            background=[("active", "#374151"), ("disabled", "#1f2937")],
            foreground=[("disabled", "#6b7280")],
        )

        style.configure(
            "Modern.Horizontal.TProgressbar",
            troughcolor="#1f2937",
            bordercolor="#1f2937",
            background="#22c55e",
            lightcolor="#22c55e",
            darkcolor="#22c55e",
        )

    def _build_ui(self) -> None:
        root = self.root

        header = tk.Frame(root, bg="#0f172a", height=96)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="YouTube Downloader V2",
            bg="#0f172a",
            fg="#f8fafc",
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w", padx=20, pady=(18, 0))

        tk.Label(
            header,
            text=f"Version {APP_VERSION} - Smart auto-update, one-file friendly",
            bg="#0f172a",
            fg="#93c5fd",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=22, pady=(4, 14))

        body = tk.Frame(root, bg="#111827")
        body.pack(fill="both", expand=True, padx=18, pady=16)

        url_card = tk.Frame(body, bg="#1f2937", bd=0, highlightthickness=1, highlightbackground="#374151")
        url_card.pack(fill="x")

        tk.Label(
            url_card,
            text="YouTube URL",
            bg="#1f2937",
            fg="#e5e7eb",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 6))

        entry_row = tk.Frame(url_card, bg="#1f2937")
        entry_row.pack(fill="x", padx=12, pady=(0, 12))

        self.url_entry = tk.Entry(
            entry_row,
            textvariable=self.url_var,
            bg="#0b1220",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 11),
            highlightthickness=1,
            highlightbackground="#334155",
            highlightcolor="#2563eb",
        )
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.url_entry.bind("<Return>", lambda _e: self.on_download())

        self.download_btn = ttk.Button(entry_row, text="Download", style="Accent.TButton", command=self.on_download)
        self.download_btn.pack(side="left", padx=(10, 0))

        actions = tk.Frame(body, bg="#111827")
        actions.pack(fill="x", pady=(12, 8))

        self.open_folder_btn = ttk.Button(actions, text="Open Video Folder", style="Subtle.TButton", command=self.open_video_folder)
        self.open_folder_btn.pack(side="left")

        self.check_updates_btn = ttk.Button(actions, text="Check Updates", style="Subtle.TButton", command=self.check_updates_now)
        self.check_updates_btn.pack(side="left", padx=(10, 0))

        tk.Label(
            actions,
            textvariable=self.status_var,
            bg="#111827",
            fg="#93c5fd",
            font=("Segoe UI", 10),
        ).pack(side="right")

        progress_wrap = tk.Frame(body, bg="#111827")
        progress_wrap.pack(fill="x", pady=(0, 10))

        self.progress = ttk.Progressbar(
            progress_wrap,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
            style="Modern.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")

        logs_card = tk.Frame(body, bg="#1f2937", bd=0, highlightthickness=1, highlightbackground="#374151")
        logs_card.pack(fill="both", expand=True)

        tk.Label(
            logs_card,
            text="Live Logs",
            bg="#1f2937",
            fg="#e5e7eb",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 6))

        self.logs = ScrolledText(
            logs_card,
            bg="#0b1220",
            fg="#cbd5e1",
            insertbackground="#f8fafc",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 10),
            wrap="word",
            height=18,
        )
        self.logs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.logs.configure(state="disabled")

        footer = tk.Label(
            root,
            text="Tip: If your release is private, set environment variable YD_GITHUB_TOKEN.",
            bg="#111827",
            fg="#6b7280",
            font=("Segoe UI", 9),
        )
        footer.pack(fill="x", padx=18, pady=(0, 10), anchor="w")

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.url_entry.configure(state=state)
        self.download_btn.configure(state=state)
        self.check_updates_btn.configure(state=state)

        # Keep folder button always enabled so user can inspect output anytime
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
            self.status_var.set("Updating app...")
            self.root.after(1200, self.root.destroy)
            return

        ensure_ytdlp_is_fresh(self.log)

        if not YTDLP_PATH.exists():
            self.log("Error: yt-dlp.exe is missing. Download cannot continue.")
            self.status_var.set("Initialization failed")
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
            messagebox.showwarning("Missing URL", "Please paste a YouTube URL first.")
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showwarning("Invalid URL", "Please provide a valid URL starting with http:// or https://")
            return

        self.downloading = True
        self.progress_var.set(0.0)
        self.status_var.set("Downloading...")
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
                self.status_var.set("Download complete")
                self.log(f"SUCCESS: Video saved to '{VIDEO_DIR}'")
                messagebox.showinfo("Done", "Download completed successfully.")
            else:
                self.status_var.set("Download failed")
                self.log(f"FAILED: yt-dlp returned exit code {code}")
                messagebox.showerror("Download failed", f"yt-dlp returned exit code {code}.")

        self.root.after(0, finish)

    def open_video_folder(self) -> None:
        ensure_video_dir()
        try:
            os.startfile(str(VIDEO_DIR))
        except Exception:
            messagebox.showinfo("Output Folder", f"Video folder: {VIDEO_DIR}")

    def check_updates_now(self) -> None:
        if self.downloading:
            messagebox.showinfo("Busy", "Please wait until current download finishes.")
            return

        self.status_var.set("Checking updates...")
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
