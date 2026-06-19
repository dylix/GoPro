#!/usr/bin/python3
#pip install --upgrade yt-dlp --pre

import argparse
import os
import re
import subprocess
import sys
import time
import random
import shutil
import requests
import yt_dlp
import json
import httplib2
import http.client as httplib
import threading
import wmi
import pythoncom
import socket
import pyperclip
import time
import msvcrt
import ctypes

# Win32 constants
GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_NONE = 0
OPEN_EXISTING = 3

from argparse import Namespace
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
#from apiclient.discovery import build
#from apiclient.errors import HttpError
#from apiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow
from pathlib import Path
from datetime import datetime, UTC
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pydub import AudioSegment
from mutagen.mp3 import MP3
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

HANDLE_EXE = os.path.join(os.path.dirname(__file__), "handle64.exe")
stdout_lock = threading.Lock()

# Windows-specific imports
if os.name == 'nt':
    import ctypes
    import winsound

# Optional: playsound or winsound depending on platform
try:
    from playsound import playsound
except ImportError:
    playsound = None

# =========================
# CONFIGURATION
# =========================
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "FFMPEG_PATH": r"C:\Program Files (x86)\ffmpeg\ffmpeg.exe",
    "SCRIPT_FOLDER": r"D:\Users\dylix\source\repos\GoPro",
    "MUSIC_FOLDER": r"D:\GoPro\Music",
    "VIDEO_FOLDER": r"D:\GoPro\Today",
    "WATCH_EXTENSIONS": [".mp4"],
    "SETTLE_TIME": 300,
    "CHECK_INTERVAL": 10,
    "SEARCH_TERM": "EDM No Copyright Music",
    "CONFIRM": True,
    "FLIP_FILES": False,
    "DELETE_ORIGINALS": True,
    "MAX_RATIO": 2.0,
    "CLIENT_SECRETS_FILE": "client_secrets.json",
    "TOKEN_FILE": "token.json",
    "YOUTUBE_UPLOAD_SCOPE": ["https://www.googleapis.com/auth/youtube.upload"],
    "YOUTUBE_API_SERVICE_NAME": "youtube",
    "YOUTUBE_API_VERSION": "v3"
}

def load_config():
    # Create default config if missing
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        cfg = DEFAULT_CONFIG.copy()
    else:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            # Corrupted file → rewrite defaults
            save_config(DEFAULT_CONFIG)
            cfg = DEFAULT_CONFIG.copy()

    # Auto-repair missing keys
    changed = False
    for key, default_value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = default_value
            changed = True

    if changed:
        save_config(cfg)

    # Normalize paths
    script_folder = cfg["SCRIPT_FOLDER"]

    def resolve(path):
        return path if os.path.isabs(path) else os.path.join(script_folder, path)

    cfg["CLIENT_SECRETS_FILE"] = resolve(cfg["CLIENT_SECRETS_FILE"])
    cfg["TOKEN_FILE"] = resolve(cfg["TOKEN_FILE"])
    cfg["CACHE_FILE"] = os.path.join(script_folder, "playlist_cache.json")

    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

config = load_config()
FFMPEG_PATH = config["FFMPEG_PATH"]
SCRIPT_FOLDER = config["SCRIPT_FOLDER"]
MUSIC_FOLDER = config["MUSIC_FOLDER"]
VIDEO_FOLDER = config["VIDEO_FOLDER"]
WATCH_EXTENSIONS = set(config["WATCH_EXTENSIONS"])
SETTLE_TIME = config["SETTLE_TIME"]
CHECK_INTERVAL = config["CHECK_INTERVAL"]
SEARCH_TERM = config["SEARCH_TERM"]
CONFIRM = config["CONFIRM"]
FLIP_FILES = config["FLIP_FILES"]
DELETE_ORIGINALS = config["DELETE_ORIGINALS"]
MAX_RATIO = config["MAX_RATIO"]
CLIENT_SECRETS_FILE = config["CLIENT_SECRETS_FILE"]
TOKEN_FILE = config["TOKEN_FILE"]
CACHE_FILE = config["CACHE_FILE"]
YOUTUBE_UPLOAD_SCOPE = config["YOUTUBE_UPLOAD_SCOPE"]
YOUTUBE_API_SERVICE_NAME = config["YOUTUBE_API_SERVICE_NAME"]
YOUTUBE_API_VERSION = config["YOUTUBE_API_VERSION"]

# GLOBAL VARS
files_to_delete = []
drive_letter_global = None
last_event_time = time.time()
is_copying = False

with open(os.path.join(SCRIPT_FOLDER, "config.json")) as f:
    config = json.load(f)
API_KEY = config["api_key"]
if not API_KEY or "YOUR_API_KEY_HERE" in API_KEY:
    raise ValueError("Missing or placeholder API key in config.json.")


# =========================
# STEP 1: FLIPME FUNCTIONS
# =========================

def delete_if_exists(file_path):
    path = Path(file_path)
    if path.exists():
        print(f"🗑️ Deleting: {path.name}")
        path.unlink()
    else:
        print(f"📁 File not found: {path.name}")

def get_unique_name(filename):
    return filename[-8:-4]

def get_date_from_name(name):
    return re.sub(r"^combined-", "", name)[:10]

def get_time_from_name(name):
    return re.sub(r"^combined-", "", name)[11:16]

def get_unique_filename(base_name):
    base = Path(base_name)
    counter = 1
    while base.exists():
        base = base.with_name(f"{base.stem}-{counter}{base.suffix}")
        counter += 1
    return base

def extract_timestamp_key(filename):
    # Example filename:
    # 2026-06-07-05-42-57-GX011078.MP4
    base = os.path.basename(filename)
    parts = base.split("-")
    # YYYY-MM-DD-HH-MM-SS
    return "-".join(parts[0:6])

def is_valid_mp4(filepath):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-i", str(filepath),
            "-show_format", "-show_streams"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0
    except Exception as e:
        print(f"Error validating {filepath}: {e}")
        return False

def process_gopro_with_music_in_one_pass():
    script_root = Path(VIDEO_FOLDER)

    def _random_hex_suffix(k=4):
        return ''.join(random.choices('0123456789abcdef', k=k))

    all_files = list(script_root.glob("*.mp4"))
    mp4_files = [
        f for f in all_files
        if is_valid_mp4(f)
        and "combined-" not in f.name.lower()
        and "-music" not in f.name.lower()
    ]

    print(f"✅ Valid MP4 files: {[f.name for f in mp4_files]}")
    if not mp4_files:
        print("❌ No valid GoPro MP4 files found.")
        return

    # --- Group files by day (YYYY-MM-DD) ---
    groups = {}
    for file in mp4_files:
        full_key = extract_timestamp_key(file.name)
        day_key = "-".join(full_key.split("-")[0:3])  # YYYY-MM-DD
        groups.setdefault(day_key, []).append(file)

    # Sort files within each day by time
    for key in groups:
        groups[key].sort(key=lambda f: get_time_from_name(f.name))

    # Sort days
    def parse_timestamp_key(key):
        return datetime.strptime(key, "%Y-%m-%d")

    sorted_group_keys = sorted(groups.keys(), key=parse_timestamp_key)

    print(f"📅 Days detected: {sorted_group_keys}")

    # --- Preload playlists + cache once ---
    playlists = search_youtube_playlists(API_KEY, SEARCH_TERM)
    cache = load_cache()
    print("DEBUG: Raw playlist search result:")
    print(playlists)

    # --- Process each day independently ---
    for day_key in sorted_group_keys:
        day_files = groups[day_key]
        print(f"\n📆 Processing day {day_key} with {len(day_files)} file(s):")
        for f in day_files:
            print(f"   • {f.name}")

        # --- Compute total duration for this day ---
        day_duration_sec = 0
        per_file_durations = []
        for f in day_files:
            d = get_duration_seconds(f)  # ffprobe wrapper should ignore unknown streams
            per_file_durations.append((f, d))
            day_duration_sec += d

        print(f"⏱️ Total duration for {day_key}: {day_duration_sec/60:.1f} mins")

        if day_duration_sec <= 0:
            print(f"⚠️ Skipping {day_key}: zero duration.")
            continue

        # --- Build concat list for THIS day ---
        list_file = script_root / f"all-files-{day_key}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for file in day_files:
                f.write(f"file '{file}'\n")
        print(f"📝 Concat list created: {list_file.name}")

        # --- PLAYLIST SELECTION FOR THIS DAY ---
        print(f"🔎 Finding playlists matching ~{day_duration_sec/60:.1f} mins for {day_key}...")
        playlist_info = []

        for pl in playlists:
            pl_id = pl["id"]["playlistId"]
            title = pl["snippet"]["title"]
            duration = get_playlist_duration(API_KEY, pl_id, cache)
            if duration is None:
                print(f"⚠️ Skipping playlist {pl_id} — duration unavailable.")
                continue

            if day_duration_sec <= duration:
                diff = abs(duration - day_duration_sec)
                playlist_info.append({
                    "title": title,
                    "id": pl_id,
                    "duration": duration,
                    "diff": diff,
                    "url": f"https://www.youtube.com/playlist?list={pl_id}"
                })

        if not playlist_info:
            print(f"❌ No suitable playlists found for {day_key}. Skipping this day.")
            delete_if_exists(list_file)
            continue

        playlist_info.sort(key=lambda x: x["diff"])

        print("🎵 Matching playlists:")
        for i, p in enumerate(playlist_info, start=1):
            match_pct = (p['duration'] / day_duration_sec) * 100
            print(f"{i}. {p['title']} - {p['duration']/60:.1f} min ({match_pct:.0f}%) - {p['url']}")

        default_choice = 1  # best match is first after sorting
        start_alerts()
        choice = input_with_timeout(
            f"📝 [{day_key}] Enter the number of the playlist you want to download "
            f"(default={default_choice}): ",
            timeout=60, default=default_choice, cast_type=int,
            require_input=False, retries=0
        )
        stop_all_alerts()

        if choice is None or choice < 1 or choice > len(playlist_info):
            print(f"⚠️ Invalid or no choice for {day_key}, using default #{default_choice}.")
            choice = default_choice

        selected = playlist_info[choice - 1]
        print(f"✅ Selected playlist for {day_key}: {selected['title']} ({selected['url']})")

        playlist_clean_name = sanitize_filename(selected["title"])
        DOWNLOAD_FOLDER = os.path.join(MUSIC_FOLDER, playlist_clean_name)

        # --- Download enough audio for THIS day ---
        print(f"⬇️ Downloading audio for {day_key} into {DOWNLOAD_FOLDER}...")
        entry_urls = get_limited_playlist_entries(
            API_KEY, selected['url'], day_duration_sec,
            DOWNLOAD_FOLDER, cache, buffer_sec=300
        )
        unified_download_playlist(entry_urls, DOWNLOAD_FOLDER, max_workers=8)

        total_audio = ensure_audio_matches_video(
            None,
            DOWNLOAD_FOLDER,
            API_KEY, selected['url'], cache, buffer_sec=300
        )
        print(f"🎧 Total audio duration available: {total_audio/60:.1f} mins")

        output_mp3 = os.path.join(DOWNLOAD_FOLDER, "combined_playlist.mp3")
        delete_if_exists(output_mp3)
        merge_mp3s_and_cleanup(DOWNLOAD_FOLDER, output_mp3)
        print(f"🎼 Combined audio created: {output_mp3}")

        # --- Output filename for THIS day (date + hex suffix) ---
        hex_suffix = _random_hex_suffix(4)
        output_file = script_root / f"combined-{day_key}-music-{hex_suffix}.mp4"
        while output_file.exists():
            hex_suffix = _random_hex_suffix(4)
            output_file = script_root / f"combined-{day_key}-music-{hex_suffix}.mp4"

        print(f"🎬 Merging chunks and adding music for {day_key} → {output_file.name}")

        # --- Build filter_complex for THIS day ---
        duration = day_duration_sec
        first_video = str(day_files[0])
        if has_audio_stream(first_video):
            filter_complex = (
                f"[0:a]atrim=duration={duration}[a0];"
                f"[1:a]atrim=duration={duration}[a1];"
                f"[a0][a1]amix=inputs=2:duration=shortest:dropout_transition=2[aout]"
            )
        else:
            filter_complex = (
                f"anullsrc=channel_layout=stereo:sample_rate=44100[a0];"
                f"[1:a]atrim=duration={duration}[a1];"
                f"[a0][a1]amix=inputs=2:duration=shortest:dropout_transition=2[aout]"
            )

        # FFmpeg #1: concat GoPro chunks → stdout (MPEG-TS stream)
        merge_proc = subprocess.Popen(
            [
                FFMPEG_PATH, "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                "-f", "mpegts",
                "pipe:1"
            ],
            stdout=subprocess.PIPE
        )

        # FFmpeg #2: read MPEG-TS from stdin, mix audio, write final MP4 (Option C)
        mix_proc = subprocess.run(
            [
                FFMPEG_PATH, "-y",
                "-f", "mpegts",
                "-i", "pipe:0",
                "-i", output_mp3,
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[aout]",
                "-metadata:s:v", "rotate=180",
                "-c:v", "copy",
                "-c:a", "aac",
                str(output_file)
            ],
            stdin=merge_proc.stdout,
            check=True
        )

        merge_proc.wait()
        delete_if_exists(list_file)

        if not (output_file.exists() and output_file.stat().st_size > 0):
            print(f"❌ Merge + music failed or output file missing for {day_key}.")
            continue

        # --- Build chapter text for THIS day's file ---
        # Use per-file chapters with file name as key
        chapter_durations = [
            (f.name, dur) for f, dur in per_file_durations
        ]
        chapter_text = build_youtube_chapters(chapter_durations)

        # --- Save metadata JSON for THIS day ---
        meta = {
            "playlist": {
                "title": selected["title"],
                "url": selected["url"],
                "duration_sec": selected["duration"],
                "duration_min": round(selected["duration"] / 60, 2),
                "match_percent": round((selected["duration"] / day_duration_sec) * 100, 2)
            },
            "video": {
                "output_file": str(output_file),
                "total_duration_sec": day_duration_sec,
                "total_duration_min": round(day_duration_sec / 60, 2),
                "day": day_key
            },
            "chapters": [
                {
                    "file": f.name,
                    "duration_sec": dur,
                    "duration_min": round(dur / 60, 2)
                }
                for f, dur in per_file_durations
            ],
            "chapter_text": chapter_text
        }

        meta_file = Path(str(output_file) + ".meta.json")
        with open(meta_file, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=4)
        print(f"🗂️ Saved metadata JSON for {day_key} to: {meta_file.name}")

        # --- Cleanup original GoPro chunks for THIS day ---
        print(f"🧹 Cleaning up original GoPro files for {day_key}...")
        for file in day_files:
            delete_if_exists(file)
        print(f"🧼 All original chunks deleted for {day_key}.")

        print(f"🎉 Final merged file with music ready for {day_key}: {output_file.name}")

        # --- Upload per day ---
        start_alerts()
        choice = input_with_timeout(
            f"📝 Upload the new music video for {day_key} now? (y/n): ",
            timeout=30,
            require_input=False,
            default="y"
        )
        stop_all_alerts()

        if choice and choice.lower() == "y":
            upload_video(
                str(output_file),
                selected["title"],
                selected["url"],
                chapter_text,
                privacy_status="unlisted"
            )

        # --- Ask whether to delete originals from SD card ---
        confirm_and_delete(require_input=True)


    # --- Save cache once after all days processed ---
    save_cache(cache)
    print("✅ All days processed.")

def format_ts(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def build_youtube_chapters(chapter_durations):
    lines = []
    cumulative = 0
    for name, dur in chapter_durations:
        clean_name = Path(name).stem.replace("combined-", "")
        lines.append(f"{format_ts(cumulative)} {clean_name}")
        cumulative += dur
    return "\n".join(lines)

def get_duration_seconds(path):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return int(float(result.stdout.strip()))
    except:
        return 0

# =========================
# STEP 2: ADD MUSIC FUNCTIONS
# =========================
def load_chapter_text_for(video_path: str) -> str:
    meta_file = Path(video_path + ".meta.json")
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            chapter_text = meta.get("chapter_text", "")
            if chapter_text:
                return chapter_text
            print(f"⚠️ No chapter_text found inside meta for {video_path}")
            return ""
        except Exception as e:
            print(f"❌ Failed to read meta.json for {video_path}: {e}")
            return ""

    print(f"⚠️ No meta.json file found for {video_path}")
    return ""

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def iso8601_duration_to_seconds(duration):
    pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
    match = pattern.match(duration)
    if not match:
        return 0
    h = int(match.group(1)) if match.group(1) else 0
    m = int(match.group(2)) if match.group(2) else 0
    s = int(match.group(3)) if match.group(3) else 0
    return h * 3600 + m * 60 + s

def has_audio_stream(video_path):
    result = subprocess.run([
        'ffprobe', '-v', 'error',
        '-select_streams', 'a',
        '-show_entries', 'stream=index',
        '-of', 'csv=p=0',
        str(video_path)
    ], capture_output=True, text=True)
    return bool(result.stdout.strip())

def get_video_duration(video_file):
    def run_ffprobe(args):
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True
            )
            output = result.stdout.decode().strip()
            return float(output)
        except Exception as e:
            print(f"⚠️ ffprobe failed with args {args}: {e}")
            return None

    # Try stream-level duration (more precise)
    stream_args = [
        "ffprobe", "-v", "error",
        "-ignore_unknown",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file
    ]
    duration = run_ffprobe(stream_args)

    # Fallback to format-level duration
    if duration is None or duration < 1:
        format_args = [
            "ffprobe", "-v", "error",
            "-ignore_unknown",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_file
        ]
        duration = run_ffprobe(format_args)

    if duration is None:
        raise ValueError(f"❌ Could not determine duration for {video_file}")

    print(f"⏱️ Duration of {video_file}: {duration:.2f} seconds")
    return duration

def search_youtube_playlists(api_key, query, max_results=49):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {"part": "snippet", "q": query, "type": "playlist", "maxResults": max_results, "key": api_key}
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("items", [])

def get_playlist_duration(api_key, playlist_id, cache):
    # Cache hit
    if playlist_id in cache:
        val = cache[playlist_id]
        if isinstance(val, (int, float)):
            return val
        else:
            print(f"⚠️ Cached value for {playlist_id} was not numeric. Resetting.")
            del cache[playlist_id]

    url = (
        "https://www.googleapis.com/youtube/v3/playlistItems"
        "?part=contentDetails"
        f"&playlistId={playlist_id}"
        "&maxResults=50"
        f"&key={api_key}"
    )

    total_seconds = 0
    next_page = None
    attempts = 0
    video_ids = []

    while True:
        attempts += 1
        if attempts > 10:
            print(f"❌ Timeout reading playlist {playlist_id}, aborting.")
            break

        try:
            resp = requests.get(
                url + (f"&pageToken={next_page}" if next_page else ""),
                timeout=10
            )
            data = resp.json()

            # API error
            if "error" in data:
                print(f"❌ API error for playlist {playlist_id}: {data['error']}")
                break

            items = data.get("items", [])
            if not items:
                break

            # Collect video IDs
            for item in items:
                vid = item["contentDetails"]["videoId"]
                video_ids.append(vid)

            next_page = data.get("nextPageToken")
            if not next_page:
                break

        except Exception as e:
            print(f"❌ Exception reading playlist {playlist_id}: {e}")
            break

    # Now fetch durations in batch using your existing helper
    if video_ids:
        durations = fetch_video_durations(video_ids, api_key, cache)
        for vid in video_ids:
            dur = durations.get(vid, 0)
            if isinstance(dur, (int, float)):
                total_seconds += dur
            else:
                print(f"⚠️ Non-numeric duration for video {vid}: {dur}")

    if total_seconds == 0:
        # Timeout or failure — mark as unusable
        cache[playlist_id] = None
        return None

    cache[playlist_id] = total_seconds
    return total_seconds


def chunkify(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def fetch_video_durations(video_ids, api_key, cache=None):
    durations = {}
    uncached_ids = [vid for vid in video_ids if not cache or vid not in cache]

    if uncached_ids:
        url = "https://www.googleapis.com/youtube/v3/videos"
        for chunk in chunkify(uncached_ids, 50):
            params = {
                "part": "contentDetails",
                "id": ",".join(chunk),
                "key": api_key
            }
            resp = requests.get(url, params=params)
            resp.raise_for_status()

            for item in resp.json().get("items", []):
                vid = item["id"]
                dur = iso8601_duration_to_seconds(item["contentDetails"]["duration"])
                durations[vid] = dur
                if cache is not None:
                    cache[vid] = dur

    # Merge cached durations
    if cache:
        for vid in video_ids:
            if vid in cache:
                durations[vid] = cache[vid]

    return durations


def get_limited_playlist_entries(api_key, playlist_url, max_duration_sec, download_folder, cache=None, buffer_sec=300):
    """
    Download tracks one-by-one and measure REAL durations.
    Stop only when REAL total >= max_duration_sec + buffer_sec.
    """
    import subprocess

    def real_duration(path):
        """Return actual audio duration in seconds using ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ]
        try:
            out = subprocess.check_output(cmd).decode().strip()
            return float(out)
        except Exception:
            return 0.0

    os.makedirs(download_folder, exist_ok=True)

    target_duration = max_duration_sec + buffer_sec
    total_real = 0
    selected_entries = []

    print(f"Fetching flat playlist entries from: {playlist_url}")
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        "extractor_args": {"youtube": {"player_client": ["default", "-tv_simply"], "player_js_version": "actual"}},
    }

    # Fetch playlist entries
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        entries = info.get('entries', [])
        random.shuffle(entries)
        print(f"Found {len(entries)} flat entries")

    # Download + measure REAL durations
    for entry in entries:
        url = entry.get('url')
        title = entry.get('title', 'unknown')

        if not url:
            print(f"⚠️ Skipping {title} (missing URL)")
            continue

        filename = sanitize_filename(f"{title}.mp3")
        full_path = os.path.join(download_folder, filename)

        # Download immediately
        unified_download_playlist([url], download_folder, max_workers=1)

        # Measure REAL duration
        real = real_duration(full_path)

        if real < 5:
            print(f"⚠️ Skipping broken/short file: {title} ({real:.1f}s)")
            delete_if_exists(full_path)
            continue

        selected_entries.append(url)
        total_real += real
        print(f"✓ {title} — REAL {real:.1f}s → Total REAL: {total_real:.1f}s")

        if total_real >= target_duration:
            print(f"✅ REAL target met: {total_real:.1f}s ≥ {target_duration:.1f}s")
            break

    return selected_entries

def download_single_mp3(url, output_path, archive_path):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{output_path}/%(title)s.%(ext)s',
        'ignoreerrors': True,
        'download_archive': archive_path,
        'overwriteskip': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'final_ext': 'mp3',
        'quiet': False,
        'no_warnings': False,
        "extractor_args": {"youtube":{"player_client":["default","-tv_simply"],"player_js_version": "actual"}},
    }

    try:
        print(ydl_opts)
        print(url)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return f"✅ Downloaded: {url}"
    except Exception as e:
        return f"❌ Failed: {url} — {e}"

def download_playlist_parallel(entry_urls, output_path, max_workers=4):
    os.makedirs(output_path, exist_ok=True)
    archive_path = os.path.join(output_path, "archive.txt")

    print(f"🚀 Starting parallel download with {max_workers} workers...")
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_single_mp3, url, output_path, archive_path) for url in entry_urls]
        for future in as_completed(futures):
            results.append(future.result())

    print("🎧 Download complete:")
    for r in results:
        print(r)

# NEW

def unified_download_playlist(entry_urls, output_path, max_workers=8):
    """
    Unified downloader:
    - Parallel
    - No duplicates
    - Normalized filenames
    - Always produces *.mp3 (never .mp3.mp3)
    - Uses archive.txt to avoid re-downloading
    """
    os.makedirs(output_path, exist_ok=True)
    archive_path = os.path.join(output_path, "archive.txt")

    print(f"🚀 Unified parallel download with {max_workers} workers...")
    results = []

    def worker(url):
        # yt-dlp sometimes includes ".mp3" in the title → strip it
        def strip_mp3(name):
            return name[:-4] if name.lower().endswith(".mp3") else name

        # Template: always output *.mp3, never *.mp3.mp3
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{output_path}/%(title)s.%(ext)s',
            'download_archive': archive_path,
            'overwriteskip': True,
            'quiet': True,
            'no_warnings': True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["default", "-tv_simply"],
                    "player_js_version": "actual"
                }
            },
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'final_ext': 'mp3',
            # Normalize filenames BEFORE writing
            'sanitize_info': {
                'title': strip_mp3
            }
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return f"⬇️ {url}"
        except Exception as e:
            return f"❌ {url} — {e}"

    # Parallel execution
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, url) for url in entry_urls]
        for future in as_completed(futures):
            results.append(future.result())

    print("🎧 Unified download complete:")
    for r in results:
        print(r)

def fast_audio_duration(file):
    """Return duration in seconds using ffprobe metadata."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"⚠️ Failed to probe {file}: {e}")
        return 0.0

def get_total_audio_duration(file_list, workers=8):
    """Audit audio durations quickly using parallel ffprobe calls."""
    total = 0.0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fast_audio_duration, f): f for f in file_list}
        for future in as_completed(futures):
            dur = future.result()
            f = futures[future]
            if dur > 0:
                print(f"✅ {f}: {dur:.2f}s")
                total += dur
    print(f"🎯 Total audio duration: {total/60:.2f} minutes")
    return total

def ensure_audio_matches_video(video_file, mp3_folder, api_key, playlist_url, cache, buffer_sec=300):
    """
    Ensure REAL audio duration >= REAL video duration.
    If short, download more tracks until target is met.
    """
    video_duration = fast_audio_duration(video_file)

    # Get REAL total audio duration
    mp3_files = [
        str(Path(mp3_folder) / f)
        for f in os.listdir(mp3_folder)
        if f.lower().endswith('.mp3')
    ]
    total_audio = get_total_audio_duration(mp3_files)

    # Keep topping up until REAL duration is enough
    while total_audio + buffer_sec < video_duration:
        missing = video_duration - total_audio
        print(f"⚠️ Audio too short ({total_audio:.1f}s vs video {video_duration:.1f}s). Need +{missing:.1f}s")

        extra_urls = get_limited_playlist_entries(
            api_key, playlist_url,
            missing,
            mp3_folder, cache,
            buffer_sec=buffer_sec
        )

        # Already downloaded inside get_limited_playlist_entries()

        # Recalculate REAL duration
        mp3_files = [
            str(Path(mp3_folder) / f)
            for f in os.listdir(mp3_folder)
            if f.lower().endswith('.mp3')
        ]
        total_audio = get_total_audio_duration(mp3_files)

    return total_audio

def merge_mp3s_and_cleanup(mp3_folder, output_mp3):
    # Collect and shuffle MP3 files
    mp3_files = [f for f in os.listdir(mp3_folder) if f.lower().endswith('.mp3')]
    random.shuffle(mp3_files)

    # Optional: audit duration before shuffle (if you want to keep this)
    full_paths = [os.path.join(mp3_folder, f) for f in mp3_files]
    actual_duration = get_total_audio_duration(full_paths)
    print(f"🧮 Actual total audio duration: {actual_duration/60:.2f} minutes")

    # Write shuffled file list for ffmpeg
    filelist_path = os.path.join(mp3_folder, 'filelist.txt')
    with open(filelist_path, 'w', encoding='utf-8') as filelist:
        for mp3 in mp3_files:
            path = os.path.join(mp3_folder, mp3).replace('\\', '/')
            safe_path = path.replace("'", "'\\''")
            filelist.write(f"file '{safe_path}'\n")

    # Merge with ffmpeg
    subprocess.run(['ffmpeg','-f','concat','-safe','0','-i',filelist_path,'-c','copy',output_mp3], check=True)

    # Cleanup
    os.remove(filelist_path)

def mix_audio_with_video(video_file, new_audio_file):
    base, ext = os.path.splitext(video_file)
    output_file = f"{base}-music{ext}"
    duration = get_video_duration(video_file)
    video_duration = get_video_duration(video_file)
    audio_duration = get_video_duration(new_audio_file)
    print(f"🎬 Video duration: {video_duration:.1f}s")
    print(f"🎵 Audio duration: {audio_duration:.1f}s")
    if has_audio_stream(video_file):
        filter_complex = (
            f"[0:a]atrim=duration={duration}[a0];"
            f"[1:a]atrim=duration={duration}[a1];"
            f"[a0][a1]amix=inputs=2:duration=shortest:dropout_transition=2[aout]"
        )
    else:
        filter_complex = (
            f"anullsrc=channel_layout=stereo:sample_rate=44100[a0];"
            f"[1:a]atrim=duration={duration}[a1];"
            f"[a0][a1]amix=inputs=2:duration=shortest:dropout_transition=2[aout]"
        )

    command = [
        'ffmpeg', '-y',
        '-i', video_file,
        '-i', new_audio_file,
        '-filter_complex', filter_complex,
        '-map', '0:v',
        '-map', '[aout]',
        '-c:v', 'copy',
        '-c:a', 'aac',
        output_file
    ]
    subprocess.run(command, check=True)
    return output_file

def sanitize_filename(filename, replacement=""):
    name, ext = os.path.splitext(filename)
    # Remove unwanted characters
    name = re.sub(r"[^a-zA-Z0-9 _-]", replacement, name)
    # Only collapse and strip if replacement is non-empty
    if replacement:
        name = re.sub(rf"{re.escape(replacement)}+", replacement, name)
        name = name.strip(" _-")
    else:
        name = name.strip()
    if not name:
        name = "untitled"
    return f"{name}{ext}"

def run_add_music(video_file):
    if not video_file:
        return None, None, None

    duration_sec = get_video_duration(video_file)
    print(f"Duration of combined video: {duration_sec/60:.1f} mins")

    playlists = search_youtube_playlists(API_KEY, SEARCH_TERM)
    playlist_info = []
    cache = load_cache()

    for pl in playlists:
        pl_id = pl["id"]["playlistId"]
        title = pl["snippet"]["title"]
        duration = get_playlist_duration(API_KEY, pl_id, cache)
        if duration_sec <= duration:
            diff = abs(duration - duration_sec)
            playlist_info.append({
                "title": title,
                "id": pl_id,
                "duration": duration,
                "diff": diff,
                "url": f"https://www.youtube.com/playlist?list={pl_id}"
            })
    playlist_info.sort(key=lambda x: x["diff"])

    for i, p in enumerate(playlist_info, start=1):
        match_pct = (p['duration'] / duration_sec) * 100
        print(f"{i}. {p['title']} - {p['duration']/60:.1f} min ({match_pct:.0f}%) - {p['url']}")

    default_choice = random.randint(1, len(playlist_info))
    start_alerts()
    choice = input_with_timeout(
        "📝 Enter the number of the playlist you want to download: ",
        timeout=60, default=default_choice, cast_type=int,
        require_input=False, retries=0
    )
    stop_all_alerts()
    selected = playlist_info[choice-1]

    playlist_clean_name = sanitize_filename(selected["title"])
    DOWNLOAD_FOLDER = os.path.join(MUSIC_FOLDER, playlist_clean_name)

    entry_urls = get_limited_playlist_entries(
        API_KEY, selected['url'], duration_sec,
        DOWNLOAD_FOLDER, cache, buffer_sec=300
    )
    unified_download_playlist(entry_urls, DOWNLOAD_FOLDER, max_workers=8)

    total_audio = ensure_audio_matches_video(
        video_file, DOWNLOAD_FOLDER,
        API_KEY, selected['url'], cache, buffer_sec=300
    )

    output_mp3 = os.path.join(DOWNLOAD_FOLDER, "combined_playlist.mp3")
    delete_if_exists(output_mp3)
    merge_mp3s_and_cleanup(DOWNLOAD_FOLDER, output_mp3)
    final_file = mix_audio_with_video(video_file, output_mp3)

    print(f'Created {final_file} with music')
    if DELETE_ORIGINALS:
        delete_if_exists(video_file)
    save_cache(cache)

    return selected["title"], final_file, selected["url"]


# =========================
# STEP 3: UPLOAD FUNCTIONS
# =========================
class ProgressFile:
    def __init__(self, file_path, progress_state):
        self.f = open(file_path, "rb")
        self.progress_state = progress_state

    def read(self, size=-1):
        chunk = self.f.read(size)
        self.progress_state["uploaded"] += len(chunk)
        #print("READ:", len(chunk))
        return chunk

    def seek(self, offset, whence=0):
        return self.f.seek(offset, whence)

    def tell(self):
        return self.f.tell()

    def close(self):
        return self.f.close()

_last_len = 0

def safe_print_line(text):
    import sys
    global _last_len

    # Overwrite previous line fully
    sys.stdout.write("\r" + text)

    # If new text is shorter, overwrite leftovers
    if len(text) < _last_len:
        sys.stdout.write(" " * (_last_len - len(text)) + "\r" + text)

    sys.stdout.flush()
    _last_len = len(text)

def start_real_timer_thread(stop_event, progress_state):
    def fmt_time(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def fmt_bytes(b):
        for unit in ["B","KB","MB","GB","TB"]:
            if b < 1024:
                return f"{b:.1f}{unit}"
            b /= 1024
        return f"{b:.1f}PB"

    while not stop_event.is_set():
        now = time.time()
        uploaded = progress_state["uploaded"]
        total = progress_state["total"]

        dt = now - progress_state["last_time"]
        db = uploaded - progress_state["last_uploaded"]
        inst_speed = db / dt if dt > 0 else 0

        elapsed = now - progress_state["start_time"]
        avg_speed = uploaded / elapsed if elapsed > 0 else 0

        remaining = total - uploaded
        eta = remaining / avg_speed if avg_speed > 0 else 0

        progress = uploaded / total if total > 0 else 0
        bar_len = 30
        filled = int(bar_len * progress)
        bar = "█" * filled + "░" * (bar_len - filled)

        line = (
            f"[{bar}] {progress*100:5.1f}%  "
            f"{fmt_bytes(uploaded)}/{fmt_bytes(total)}  "
            f"⚡ {inst_speed/1024/1024:4.2f} MB/s inst  "
            f"📈 {avg_speed/1024/1024:4.2f} MB/s avg  "
            f"ETA {fmt_time(eta)}"
        )

        safe_print_line(line)

        progress_state["last_uploaded"] = uploaded
        progress_state["last_time"] = now

        time.sleep(1)

    # ❌ REMOVE THIS — it prints the blank padded line
    # safe_print_line("")

def get_authenticated_service():
    creds = None

    # Load existing token
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_UPLOAD_SCOPE)
        except Exception as e:
            print(f"⚠️ token.json corrupted, deleting: {e}")
            os.remove(TOKEN_FILE)
            creds = None

    # If missing or invalid, try refresh
    if not creds or not creds.valid:
        if creds and creds.refresh_token:
            try:
                print("🔄 Refreshing YouTube OAuth token...")
                creds.refresh(Request())
            except Exception as e:
                print(f"❌ Refresh failed ({e}). Token revoked or expired.")
                print("🧹 Deleting token.json and re-authenticating...")
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                creds = None

        if not creds or not creds.valid:
            print("🌐 Opening browser for YouTube OAuth login...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, YOUTUBE_UPLOAD_SCOPE
            )
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    # ---- FORCE REFRESH BEFORE EVERY UPLOAD ----
    if creds and creds.refresh_token:
        try:
            print("🔄 Forcing OAuth token refresh before upload...")
            creds.refresh(Request())
            print("✅ OAuth token refreshed.")
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
        except Exception as e:
            print(f"❌ Forced refresh failed: {e}")
            print("🧹 Deleting token.json and re-authenticating...")
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            return get_authenticated_service()

    # ---- FAST PATH BELOW ----
    authed_http = httplib2.Http()
    original_request = authed_http.request

    def auth_request(uri, method="GET", body=None, headers=None,
                     redirections=5, connection_type=None):
        if headers is None:
            headers = {}
        headers["Authorization"] = f"Bearer {creds.token}"
        return original_request(uri, method, body, headers, redirections, connection_type)

    authed_http.request = auth_request

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=authed_http)

def resumable_upload(insert_request, file_size):
    response, error, retry = None, None, 0
    start_time = time.time()

    # --- REUSE TIMER + PROGRESS STATE CREATED IN initialize_upload() ---
    stop_event = insert_request.stop_event
    timer_thread = insert_request.timer_thread
    progress_state = insert_request.progress_state

    try:
        while response is None:
            try:
                status, response = insert_request.next_chunk()

                # ProgressFile updates progress_state["uploaded"] automatically

                if response and "id" in response:
                    # Upload finished
                    stop_event.set()
                    timer_thread.join()

                    total_time = time.time() - start_time
                    video_url = f"https://youtu.be/{response['id']}"

                    # Strava webhook
                    strava_activity_id = get_latest_strava_activity()
                    strava_url = (
                        f"https://www.strava.com/activities/{strava_activity_id}"
                        if strava_activity_id else ""
                    )
                    if strava_activity_id and video_url:
                        webhook_url = (
                            f"https://dylix.org/stravaWebhook?youtube"
                            f"&activityid={strava_activity_id}&url={video_url}"
                        )
                        resp = requests.get(webhook_url)
                        print(f"📡 Webhook called: {webhook_url} (status {resp.status_code})")

                    print(f"✅ Uploaded: {video_url}")

                    # Pretty total time
                    seconds = int(total_time)
                    mins, secs = divmod(seconds, 60)
                    hours, mins = divmod(mins, 60)

                    if hours > 0:
                        readable = f"{hours}h {mins}m {secs}s"
                    elif mins > 0:
                        readable = f"{mins}m {secs}s"
                    else:
                        readable = f"{secs}s"

                    print(f"⏱️ Total time: {readable}")

                    try:
                        pyperclip.copy(video_url)
                    except Exception:
                        print("Clipboard copy not available.")

                    return video_url

            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    error = f"Retriable HTTP error {e.resp.status}"
                else:
                    raise

            except Exception as e:
                error = f"Retriable error: {e}"

            if error:
                retry += 1
                if retry > 10:
                    raise RuntimeError("Upload failed after max retries.")
                sleep_seconds = random.random() * (2 ** retry)
                print(f"{error}, sleeping {sleep_seconds:.1f}s...")
                time.sleep(sleep_seconds)
                error = None

    finally:
        # --- ALWAYS CLEAN UP TIMER THREAD ---
        stop_event.set()
        try:
            timer_thread.join(timeout=1)
        except Exception:
            pass

def initialize_upload(youtube, options):
    # --- KILL ANY PREVIOUS TIMER THREAD (THE REAL FIX) ---
    if hasattr(youtube, "active_stop_event"):
        try:
            youtube.active_stop_event.set()
            youtube.active_timer_thread.join(timeout=1)
        except Exception:
            pass

    body = dict(
        snippet=dict(
            title=options.title,
            description=options.description,
            categoryId=options.category
        ),
        status=dict(
            privacyStatus=options.privacyStatus,
            selfDeclaredMadeForKids=False
        )
    )

    file_size = os.path.getsize(options.file)

    # --- REAL PROGRESS STATE ---
    progress_state = {
        "uploaded": 0,
        "total": file_size,
        "last_uploaded": 0,
        "last_time": time.time(),
        "start_time": time.time()
    }

    # --- WRAP THE FILE ---
    wrapped_file = ProgressFile(options.file, progress_state)

    # --- USE MediaIoBaseUpload ---
    media_body = MediaIoBaseUpload(
        wrapped_file,
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media_body
    )

    # --- START REAL TIMER THREAD (ONLY ONE ALLOWED) ---
    stop_event = threading.Event()
    timer_thread = threading.Thread(
        target=start_real_timer_thread,
        args=(stop_event, progress_state),
        daemon=True
    )
    timer_thread.start()

    # --- STORE ACTIVE TIMER FOR NEXT RUN ---
    youtube.active_stop_event = stop_event
    youtube.active_timer_thread = timer_thread

    # --- ATTACH TIMER + STATE TO REQUEST ---
    insert_request.stop_event = stop_event
    insert_request.timer_thread = timer_thread
    insert_request.progress_state = progress_state

    # --- RUN UPLOAD ---
    resumable_upload(insert_request, file_size)

    # --- CLEANUP ---
    stop_event.set()
    timer_thread.join()
    wrapped_file.close()

def format_time(seconds):
    """Convert seconds into H:MM:SS format."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"

def get_latest_strava_activity():
    url = "https://dylix.org/stravaWebhook?lastride"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    # data is a single dict, so just return the id field
    if isinstance(data, dict):
        return data.get("id")

    return None

def upload_video(video_file, playlist_title, playlist_url, chapter_text, privacy_status="unlisted"):
    strava_activity_id = get_latest_strava_activity()
    strava_url = f"https://www.strava.com/activities/{strava_activity_id}" if strava_activity_id else ""

    args = Namespace(
        file=video_file,
        title=Path(video_file).stem,
        description=(
            f"=== Chapters ===\n{chapter_text}\n\n"
            f"Raw GoPro footage with music automatically added from '{playlist_title}' and then uploaded.\n"
            f"🎵 Listen to the full playlist here: {playlist_url}\n"
            f"🚴 Strava activity: {strava_url}"
        ),
        category="22",
        privacyStatus=privacy_status
    )

    youtube = get_authenticated_service()

    try:
        initialize_upload(youtube, args)

        # NEW: cleanup using meta.json instead of chapters.txt
        meta_path = Path(str(video_file) + ".meta.json")
        cleanup_final_outputs(video_file, meta_path)

    except HttpError as e:
        print(f"🚨 HTTP error {e.resp.status} occurred:\n{e.content}")



# =========================
# Generates dummy GoPro-style videos with overlays and sets file modification times.
# =========================

def generate_dummy_gopro_clips(output_dir):
    timestamps = [
        ("2025-08-18", "05-55-12", "0731"),
        ("2025-08-18", "06-30-14", "0732"),
        ("2025-08-18", "07-15-16", "0733"),
        ("2025-08-18", "07-30-18", "0734"),
        ("2025-08-19", "05-56-11", "0735"),
        ("2025-08-19", "06-31-13", "0736"),
        ("2025-08-19", "07-16-15", "0737"),
        ("2025-08-19", "07-31-17", "0738"),
    ]

    camera_ids = ["1", "2", "3"]
    duration_sec = 10

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def create_dummy_video(filepath, start_time_sec, camera_id):
        filename = filepath.name

        # Build drawtext overlays
        drawtext_filters = [
            # Timestamp overlay (centered at top)
            "drawtext=fontsize=48:fontcolor=white:x=(w-text_w)/2:y=50:"
            f"text='%{{eif\\:t+{start_time_sec}\\:d}}'",

            # Camera ID overlay
            f"drawtext=fontsize=36:fontcolor=yellow:x=50:y=120:"
            f"text='Camera ID\\: {camera_id}'",

            # Filename overlay
            f"drawtext=fontsize=36:fontcolor=cyan:x=50:y=180:"
            f"text='File\\: {filename}'"
        ]

        vf_chain = ",".join(drawtext_filters)

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "color=c=gray:size=1920x1080:rate=30",
            "-vf", vf_chain,
            "-t", "10",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(filepath)
        ]

        print("\nRunning FFmpeg command:\n", " ".join(cmd), "\n")
        subprocess.run(cmd, check=True)



    def set_mtime(filepath, date_str, time_str):
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H-%M-%S")
        mod_time = dt.timestamp()
        os.utime(filepath, (mod_time, mod_time))

    cumulative_time = 0

    for date, time, seq in timestamps:
        for cam in camera_ids:
            filename = f"{date}-{time}-GX{cam}{seq}.MP4"
            filepath = output_dir / filename
            create_dummy_video(filepath, cumulative_time, cam)
            set_mtime(filepath, date, time)
            print(f"✅ Created: {filepath.name} with mtime {date} {time}")
            cumulative_time += duration_sec

# =========================
# WATCHER STUFF
# =========================

def get_file_sizes():
    return {
        f: f.stat().st_size
        for f in Path(VIDEO_FOLDER).glob("*.mp4")
        if not f.name.lower().endswith("-music.mp4")
    }

def process_video_file(video_file, meta_json_path):
    if not video_file:
        return

    if has_music_version(video_file):
        print(f"🎵 Skipping {video_file} — music version already exists.")
        return

    #playlist_title, final_video, playlist_url = run_add_music(video_file)
    playlist_title, final_video, playlist_url = load_metadata_and_upload(video_file)

    if final_video:
        # STRICT MODE: require meta.json instead of chapters.txt
        if not meta_json_path or not Path(meta_json_path).exists():
            print(f"❌ Metadata file missing for {final_video}")
            print("ℹ️ Upload aborted (metadata required).")
            return

        # Load chapter text from meta.json
        try:
            meta = json.loads(Path(meta_json_path).read_text(encoding="utf-8"))
            chapter_text = meta.get("chapter_text", "")
            if not chapter_text:
                print(f"⚠️ No chapter_text found in meta.json for {final_video}")
                print("ℹ️ Upload aborted (chapters required).")
                return
        except Exception as e:
            print(f"❌ Failed to read meta.json: {e}")
            print("ℹ️ Upload aborted.")
            return

        start_alerts()
        choice = input_with_timeout(
            "🛠️ Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n): ",
            timeout=30, require_input=False, default="y"
        )
        stop_all_alerts()

        if choice == "y":
            upload_video(final_video, playlist_title, playlist_url, chapter_text)

def wait_until_drive_is_stable(drive_letter, wait_time=1.0, retries=10):
    """Wait until file sizes on the drive stop changing."""
    base = f"{drive_letter}:\\"
    last_sizes = {}

    for _ in range(retries):
        current_sizes = {}
        for root, _, files in os.walk(base):
            for f in files:
                path = os.path.join(root, f)
                try:
                    current_sizes[path] = os.path.getsize(path)
                except:
                    current_sizes[path] = -1

        if current_sizes == last_sizes:
            return True

        last_sizes = current_sizes
        time.sleep(wait_time)

    return False

def process_all_new_files():
    print("🚀 Starting batch processing...")
    #video_file = run_flipme()
    #result = run_flipme()
    result = process_gopro_with_music_in_one_pass()
    if not result:
        return

    video_file, chapter_durations, playlist_title, playlist_url = result
    candidates = [
        f for f in Path(VIDEO_FOLDER).glob("*.mp4")
        if not f.name.lower().endswith("-music.mp4") and f.stat().st_size > 0
    ]
    for f in candidates:
        print(f"🎬 Processing: {f.name}")
        process_video_file(f, chapter_durations)

# --- Helper: Copy GoPro files with progress ---
def copy_with_progress(src, dst, buffer_size=1024*1024):
    total_size = os.path.getsize(src)
    with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst, tqdm(
        total=total_size,
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
        desc=os.path.basename(src)
    ) as pbar:
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            pbar.update(len(buf))
    shutil.copystat(src, dst)  # preserve metadata

def find_sidecars(root, mp4_name):
    sidecars = []
    # Extract numeric sequence from MP4 filename
    match = re.search(r"\d{4,}", mp4_name)
    if match:
        num = match.group(0)
        for f in os.listdir(root):
            if f.upper().endswith((".THM", ".LRV")) and num in f:
                sidecars.append(os.path.join(root, f))
    return sidecars

def get_blocking_pids(drive_letter: str):
    """Return list of PIDs holding handles on the drive."""
    try:
        result = subprocess.run(
            [HANDLE_EXE, drive_letter],
            capture_output=True,
            text=True,
            timeout=5
        )
    except Exception:
        return []

    pids = set()
    for line in result.stdout.splitlines():
        if drive_letter in line:
            m = re.search(r"pid:\s*(\d+)", line)
            if m:
                pids.add(int(m.group(1)))
    return list(pids)


def wait_for_drive_release(drive_letter: str, timeout_seconds: int = 60):
    """Wait up to timeout_seconds for the drive to become free."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        pids = get_blocking_pids(drive_letter)
        if not pids:
            return True
        time.sleep(1)
    return False


def kill_blockers(pids):
    """Force-kill blocking processes."""
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        except Exception:
            pass

def can_lock_volume(drive_letter: str) -> bool:
    """Attempt to take an exclusive lock on the volume."""
    path = f"\\\\.\\{drive_letter}:"
    handle = ctypes.windll.kernel32.CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_NONE,
        None,
        OPEN_EXISTING,
        0,
        None
    )

    # INVALID_HANDLE_VALUE = -1 or 0xFFFFFFFF
    if handle in (0, -1, 0xFFFFFFFF):
        return False

    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def wait_for_kernel_release(drive_letter: str, timeout_seconds: int = 60):
    """Wait until Windows kernel releases the volume."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        if can_lock_volume(drive_letter):
            return True
        time.sleep(1)
    return False

def eject_drive(drive_letter):
    drive = f"{drive_letter}:"

    # Quick check: drive already gone
    if not os.path.exists(drive):
        print(f"💽 Drive {drive} already ejected.")
        return

    # Initial settle delay
    print("🕒 Waiting 10 seconds before eject...")
    time.sleep(10)

    # Kernel-level wait
    print(f"🔍 Checking for kernel locks on {drive}...")
    if wait_for_kernel_release(drive_letter, timeout_seconds=60):
        print("✅ Drive is free at kernel level, proceeding with eject...")
    else:
        print("⚠️ Kernel still holding the drive after 60s. Proceeding anyway (Windows will likely still reject eject).")

    # Primary PowerShell eject
    ps_cmd = (
        f"(New-Object -ComObject Shell.Application)"
        f".NameSpace(17).ParseName('{drive}').InvokeVerb('Eject')"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            check=True,
            timeout=5
        )
        print(f"💽 Ejected {drive} successfully")
        return
    except subprocess.TimeoutExpired:
        print(f"⚠️ Eject timed out for {drive}, trying fallback...")
    except Exception as e:
        print(f"⚠️ Primary eject failed: {e}, trying fallback...")

    # Fallback eject
    fallback_cmd = (
        f"$drive = Get-WmiObject Win32_Volume | "
        f"Where-Object {{$_.DriveLetter -eq '{drive}'}}; "
        f"if ($drive) {{ $drive.Dismount($false, $false) | Out-Null }}"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", fallback_cmd],
            timeout=5
        )
        print(f"💽 Fallback eject attempted for {drive}")
    except Exception as e:
        print(f"⚠️ Fallback eject failed: {e}")

def copy_gopro_files(drive_letter):
    global files_to_delete, drive_letter_global
    drive_letter_global = drive_letter  # remember which drive we’re working with
    mount_point = f"{drive_letter}:\\"
    global is_copying
    is_copying = True
    try:
        for root, _, files in os.walk(mount_point):
            for file in files:
                name_upper = file.upper()
                if file.lower().endswith(".mp4") and ("GX" in name_upper or "GOPR" in name_upper):
                    src = os.path.join(root, file)
                    dst = os.path.join(VIDEO_FOLDER, file)
                    if not os.path.exists(dst):
                        print(f"📥 Starting copy: {src} -> {dst}")
                        try:
                            copy_with_progress(src, dst)
                            if os.path.getsize(src) == os.path.getsize(dst):
                                print(f"✅ Finished copying {file}, marking for deletion")
                                files_to_delete.append(src)
                                files_to_delete.extend(find_sidecars(root, file))
                            else:
                                print(f"⚠️ Size mismatch for {file}, not deleting")
                        except Exception as e:
                            print(f"⚠️ Error copying {src}: {e}")
    finally:
        is_copying = False

def confirm_and_delete(require_input=False):
    global files_to_delete, drive_letter_global

    if not files_to_delete:
        print("ℹ️ No files marked for deletion.")
        return

    if not drive_letter_global:
        print("⚠️ No drive letter stored.")
        return

    # REQUIRED INPUT MODE (pure blocking)
    if require_input:
        choice = input_with_timeout(
            "🗑️ Delete originals from GoPro drive? (y/N): ",
            timeout=None,
            require_input=True,
            default=None
        ).strip().lower()

    else:
        # ORIGINAL TIMEOUT MODE
        start_alerts()
        choice = input_with_timeout(
            "🗑️ Delete originals from GoPro drive? (y/N): ",
            timeout=30,
            require_input=False,
            default="n"
        ).strip().lower()
        stop_all_alerts()

    if choice == "y":
        for f in set(files_to_delete):
            try:
                os.remove(f)
                print(f"   Removed {f}")
            except Exception as e:
                print(f"⚠️ Could not delete {f}: {e}")

        eject_drive(drive_letter_global)
        files_to_delete.clear()
        drive_letter_global = None

    else:
        print("ℹ️ Files left on the GoPro drive.")

# --- USB Listener Thread ---
def usb_listener():
    pythoncom.CoInitialize()
    c = wmi.WMI()
    watcher = c.watch_for(
        notification_type="Creation",
        wmi_class="Win32_VolumeChangeEvent"
    )

    while True:
        event = watcher()
        if event.EventType != 2:  # Only care about arrivals
            continue

        drive_letter = event.DriveName.strip(":\\")
        if not drive_letter:
            continue

        # Prevent double-firing for the same drive
        global drive_letter_global
        if drive_letter == drive_letter_global:
            continue
        drive_letter_global = drive_letter

        print(f"💽 USB inserted: {drive_letter}:\\")

        # --- 1. Wait for the drive to actually mount ---
        root_path = f"{drive_letter}:\\"
        for _ in range(20):  # ~10 seconds max
            if os.path.isdir(root_path):
                break
            time.sleep(0.5)
        else:
            print(f"⚠️ Drive {drive_letter}:\\ never mounted — skipping.")
            continue

        # --- 2. Wait for the filesystem to settle ---
        if not wait_until_drive_is_stable(drive_letter):
            print(f"⚠️ Drive {drive_letter}:\\ never stabilized — skipping.")
            continue

        # --- 3. Now it's safe to copy ---
        copy_gopro_files(drive_letter)


# ============================
#  SAFE WATCHER REWRITE
# ============================

pending_files = set()
last_event_time = None
EVENT_SETTLE_SECONDS = 1.0

def is_valid_input_file(path: Path) -> bool:
    """Return True only for real GoPro input files that should be processed."""
    if not path.exists():
        return False

    if path.stat().st_size == 0:
        return False

    name = path.name.lower()

    # Ignore output files
    if name.startswith("combined-"):
        return False
    if name.endswith("-music.mp4"):
        return False
    if name.endswith("-flipped.mp4"):
        return False

    # Ignore metadata files
    if name.endswith(".meta.json"):
        return False

    # Ignore temp files
    if name.endswith(".tmp") or name.endswith(".partial"):
        return False

    # Only MP4s from GoPro
    return name.endswith(".mp4")

class SettlingHandler(FileSystemEventHandler):
    """Filters noisy Windows events and only registers real input files."""

    def on_modified(self, event):
        global last_event_time

        if event.is_directory:
            return

        path = Path(event.src_path)

        # Only register valid input files
        if is_valid_input_file(path):
            pending_files.add(path)
            last_event_time = time.time()

    # Some GoPro writes trigger CREATED instead of MODIFIED
    def on_created(self, event):
        self.on_modified(event)


def wait_for_settle():
    """Wait until filesystem events stop firing for a moment."""
    global last_event_time

    while True:
        now = time.time()
        if last_event_time and (now - last_event_time) < EVENT_SETTLE_SECONDS:
            time.sleep(0.2)
        else:
            break

def cleanup_final_outputs(final_video_path, meta_json_path):
    """
    Ask user whether to delete the merged final MP4 and its metadata file.
    Returns True if files were deleted, False otherwise.
    """
    choice = input("🗑️ Delete merged MP4 and metadata? (y/N): ").strip().lower()

    if choice != "y":
        print("❎ Keeping merged MP4 + metadata.")
        return False

    deleted_any = False

    # Delete final merged video
    if final_video_path and os.path.exists(final_video_path):
        try:
            os.remove(final_video_path)
            print(f"   Removed {final_video_path}")
            deleted_any = True
        except Exception as e:
            print(f"⚠️ Error deleting {final_video_path}: {e}")

    # Delete metadata JSON
    if meta_json_path and os.path.exists(meta_json_path):
        try:
            os.remove(meta_json_path)
            print(f"   Removed {meta_json_path}")
            deleted_any = True
        except Exception as e:
            print(f"⚠️ Error deleting {meta_json_path}: {e}")

    if deleted_any:
        print("✅ Final merged video + metadata deleted.")
    else:
        print("⚠️ Nothing was deleted (files missing or errors).")

    return deleted_any

def start_watcher_then_process():
    global last_event_time

    observer = Observer()
    observer.schedule(SettlingHandler(), path=VIDEO_FOLDER, recursive=False)
    observer.start()

    print(f"👀 Watching {VIDEO_FOLDER} for new files...")

    # USB listener stays as-is
    threading.Thread(target=usb_listener, daemon=True).start()

    try:
        while True:
            # Only trigger processing if:
            # 1. An event happened
            # 2. We have real pending files
            # 3. We are not currently copying
            if last_event_time and pending_files and not is_copying:
                wait_for_settle()

                # Re-validate pending files before processing
                real_files = [f for f in pending_files if is_valid_input_file(f)]
                pending_files.clear()

                # ⭐ FIX: Only process if real_files is non-empty
                if real_files:
                    print(f"📦 Processing batch of {len(real_files)} new files...")

                    result = process_all_new_files()
                    if result:
                        final_output, _, _, _ = result

                        if drive_letter_global:
                            # Prevent double delete prompt if the one-pass function already removed the files
                            if final_output and os.path.exists(final_output):
                                confirm_and_delete()
                            else:
                                print("ℹ️ Final merged MP4 already deleted — skipping delete prompt.")
                    else:
                        print("ℹ️ No output returned — skipping delete prompt.")

                print("🔁 Returning to watch mode...\n")
                last_event_time = None

            else:
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("❌ Watcher stopped by user.")

    finally:
        observer.stop()
        observer.join()

def has_music_version(file_path):
    base, ext = os.path.splitext(file_path)
    music_path = f"{base}-music{ext}"
    return os.path.exists(music_path)

# =========================
# NOTIFICATION STUFF
# =========================

# --- Shared Stop Signal ---
stop_alerts = threading.Event()

# --- Sound Alert ---
def sound_loop():
    while True:
        if stop_alerts.wait(timeout=1.0):  # Wait until stop_alerts is set
            break
        winsound.Beep(1000, 500)

# --- Flash Window (Windows only) ---
def flash_window():
    FLASHW_ALL = 3
    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint)]
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL, 5, 0)
    while True:
        if stop_alerts.wait(timeout=1.0):  # Wait until stop_alerts is set
            break
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))

# Shared flags
stop_alerts = threading.Event()
print_lock = threading.Lock()

# --- Timeout Logic ---
def input_with_timeout(prompt, timeout=30, default=None, cast_type=str, require_input=False, retries=0):
    # ----------------------------------------------------
    # REQUIRED INPUT MODE (pure blocking, no timeout)
    # ----------------------------------------------------
    def read_line_blocking():
        """Standard blocking input for require_input=True."""
        while True:
            raw = input(prompt).strip()
            try:
                return cast_type(raw)
            except Exception as e:
                print(f"⚠️ Invalid input: {e}. Please try again.")

    if require_input:
        return read_line_blocking()

    # ----------------------------------------------------
    # TIMEOUT MODE
    # ----------------------------------------------------
    attempt = 0

    while retries is None or attempt <= retries:

        # Print prompt ONCE per attempt, AFTER buffer/timer is ready
        sys.stdout.write(f"{prompt} (waiting {timeout}s, default: {default}) ")
        sys.stdout.flush()

        buffer = []
        start = time.time()

        while True:
            # Check for keystrokes
            if msvcrt.kbhit():
                ch = msvcrt.getwch()

                # ENTER ends input
                if ch == "\r":
                    print()  # newline
                    raw = "".join(buffer).strip()
                    if raw == "":
                        return default
                    try:
                        return cast_type(raw)
                    except Exception as e:
                        print(f"⚠️ Invalid input: {e}. Using default.")
                        return default

                # BACKSPACE
                elif ch == "\b":
                    if buffer:
                        buffer.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()

                # NORMAL CHAR
                else:
                    buffer.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()

            # TIMEOUT
            if time.time() - start > timeout:
                print()  # newline
                attempt += 1
                if retries is not None and attempt > retries:
                    return default
                print(f"⏰ Timeout reached on attempt {attempt}. Retrying...")
                break

            time.sleep(0.05)

def load_metadata_only(video_file):
    """
    Loads metadata JSON for an existing merged video WITHOUT uploading.
    Returns (playlist_title, final_video, playlist_url).
    """
    meta_path = Path(str(video_file) + ".meta.json")

    if not meta_path.exists():
        print(f"⚠️ No metadata file found for {video_file}.")
        return Path(video_file).stem, str(video_file), ""

    meta = json.loads(meta_path.read_text())

    playlist_title = meta["playlist"]["title"]
    playlist_url = meta["playlist"]["url"]
    final_video = str(video_file)

    return playlist_title, final_video, playlist_url

def load_metadata_and_upload(final_video):
    meta_file = Path(str(final_video) + ".meta.json")
    if not meta_file.exists():
        raise FileNotFoundError(f"No metadata file found for {final_video}")

    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)

    playlist_title = meta["playlist"]["title"]
    playlist_url = meta["playlist"]["url"]
    chapter_text = meta["chapter_text"]

    upload_video(
        str(final_video),
        playlist_title,
        playlist_url,
        chapter_text,
        privacy_status="unlisted"
    )

    return playlist_title, str(final_video), playlist_url

# --- Optional Popup (cross-platform) ---
def show_popup():
    if os.name == 'nt':
        os.system('msg * "Please respond to the script!"')
    else:
        os.system('zenity --info --text="Please respond to the script!"')


if __name__ == "!__main__":
    # Point to your finished music file
    video_file = r"D:\Users\dylix\source\repos\GoPro\dummy.mp4"
    #video_file = r"G:\GoPro\Today\combined-2025-12-07-08-35-music.mp4"
    # Give it a title and playlist info (you can reuse what run_add_music returned)
    playlist_title = "Royalty Free Electronic Music"
    playlist_url = "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID"

    # Upload directly
    upload_video(video_file, playlist_title, playlist_url, chapter_durations, privacy_status="unlisted")


def start_alerts():
    stop_alerts.clear()
    threading.Thread(target=sound_loop, daemon=True).start()
    if os.name == 'nt':
        threading.Thread(target=flash_window, daemon=True).start()

def stop_all_alerts():
    stop_alerts.set()

# =========================
# MAIN PIPELINE
# =========================
if __name__ == "__main__":
    script_root = Path(VIDEO_FOLDER).resolve()

    all_mp4s = list(script_root.glob("*.mp4"))

    raw_chunks = [
        f for f in all_mp4s
        if is_valid_input_file(f)
    ]

    music_candidates = [
        f for f in all_mp4s
        if f.name.lower().endswith("-music.mp4")
    ]

    merged_candidates = [
        f for f in all_mp4s
        if f.name.lower().startswith("combined-")
        and not f.name.lower().endswith("-music.mp4")
    ]

    print("🔍 raw_chunks:", [f.name for f in raw_chunks])
    print("🔍 merged_candidates:", [f.name for f in merged_candidates])
    print("🔍 music_candidates:", [f.name for f in music_candidates])

    video_file = None
    chapter_text = ""

    # 1) If raw chunks exist → merge
    if raw_chunks:
        video_file, chapter_text, playlist_title, playlist_url = process_gopro_with_music_in_one_pass()
        if not video_file:
            print("❌ Merge failed. Exiting.")
            exit()

        print(f"🎬 One-pass final video: {video_file}")
        print(f"📄 Chapters loaded ({len(chapter_text.splitlines())} lines)")
        print(f"🎵 Playlist: {playlist_title}")
        print(f"🔗 Playlist URL: {playlist_url or '(none)'}")

        start_watcher_then_process()
        exit()

    # 2) Else if merged file exists → use it
    elif merged_candidates:
        video_file = str(merged_candidates[0])
        chapter_text = load_chapter_text_for(video_file)

    # 3) Else if only music file exists → upload only
    elif music_candidates:
        final_video = str(music_candidates[0])
        meta_path = Path(final_video + ".meta.json")

        print(f"🎬 Final music video: {final_video}")
        print(f"🗂️ Metadata file: {meta_path}")

        # STRICT MODE: require meta.json
        if not meta_path.exists():
            print(f"❌ Metadata file missing for {final_video}")
            print("ℹ️ Upload aborted (metadata required).")
            start_watcher_then_process()
            exit()

        # Load metadata
        meta = json.loads(meta_path.read_text())
        chapter_text = meta.get("chapter_text", "")
        playlist_title = meta["playlist"]["title"]
        playlist_url = meta["playlist"]["url"]

        start_alerts()
        choice = input_with_timeout(
            "📝 Upload the existing music video? (y/n): ",
            timeout=30,
            require_input=False,
            default="y"
        )
        stop_all_alerts()

        if choice.lower() == "y":
            upload_video(
                final_video,
                playlist_title,
                playlist_url,
                chapter_text,
                privacy_status="unlisted"
            )

            cleanup_final_outputs(final_video, meta_path)

        start_watcher_then_process()
        exit()

    else:
        print("📁 No eligible MP4 files found.")

        start_alerts()
        choice = input_with_timeout(
            "🛠️ Generate dummy GoPro clips for testing? (y/n): ",
            timeout=15,
            require_input=False,
            default="n"
        )
        stop_all_alerts()

        if choice.lower() == "y":
            generate_dummy_gopro_clips(VIDEO_FOLDER)
            print("✅ Dummy clips generated. Starting merge...")
            video_file, chapter_text, playlist_title, playlist_url = process_gopro_with_music_in_one_pass()
        else:
            print("⏳ Entering watch mode...")
            start_watcher_then_process()
            exit()

    print(f"🎬 Final merged file: {video_file}")

    # Add music if no music version yet
    existing_music = [
        f for f in script_root.glob("*.mp4")
        if f.name.lower().endswith("-music.mp4")
    ]
    if existing_music:
        final_video = str(existing_music[0])
        print(f"🎵 Using existing music video: {Path(final_video).name}")

        # Load metadata
        meta_path = Path(final_video + ".meta.json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            playlist_title = meta["playlist"]["title"]
            playlist_url = meta["playlist"]["url"]
        else:
            playlist_title = Path(final_video).stem
            playlist_url = ""

    else:
        #playlist_title, final_video, playlist_url = run_add_music(video_file)
        #playlist_title, final_video, playlist_url = load_metadata_and_upload(video_file)
        playlist_title, final_video, playlist_url = load_metadata_only(video_file)
        final_video = final_video or video_file


    if final_video:
        start_alerts()
        choice = input_with_timeout(
            "📝 Upload the video? (y/n): ",
            timeout=30,
            require_input=False,
            default="y"
        )
        stop_all_alerts()

        if choice.lower() == "y":
            if not chapter_text:
                chapter_text = load_chapter_text_for(video_file)

            upload_video(final_video, playlist_title, playlist_url, chapter_text, privacy_status="unlisted")

    start_watcher_then_process()
