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

#from argparse import Namespace

CLIENT_SECRETS_FILE = "client_secrets.json"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

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
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Optional: playsound or winsound depending on platform
try:
    from playsound import playsound
except ImportError:
    playsound = None

# Windows-specific imports
if os.name == 'nt':
    import ctypes
    import winsound

# =========================
# CONFIGURATION
# =========================
FFMPEG_PATH = r"C:\Program Files (x86)\ffmpeg\ffmpeg.exe"
SCRIPT_FOLDER = r"D:\Users\dylix\source\repos\GoPro"
MUSIC_FOLDER = r"G:\GoPro\Music"
VIDEO_FOLDER = r"G:\GoPro\Today"
WATCH_EXTENSIONS = {'.mp4'}
SETTLE_TIME = 300  # seconds
CHECK_INTERVAL = 10  # seconds
CACHE_FILE = os.path.join(SCRIPT_FOLDER, "playlist_cache.json")
SEARCH_TERM = "royalty free edm"
CONFIRM = True
FLIP_FILES = False
DELETE_ORIGINALS = True
MAX_RATIO = 2.0
CLIENT_SECRETS_FILE = os.path.join(SCRIPT_FOLDER, "client_secrets.json")
TOKEN_FILE = os.path.join(SCRIPT_FOLDER, "token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

last_event_time = time.time()

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
    # Assumes format like '2025-10-09-07-31-48-GX010835.MP4'
    parts = filename.split('-GX')
    if len(parts) > 1:
        return parts[0]  # returns '2025-10-09-07-31-48'
    return filename  # fallback

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


def run_flipme():
    script_root = Path(VIDEO_FOLDER)
    all_files = list(script_root.glob("*.mp4"))
    mp4_files = []

    for f in all_files:
        if is_valid_mp4(f):
            mp4_files.append(f)
        else:
            print(f"🗑️ Deleting invalid file: {f.name}")
            try:
                f.unlink()
            except Exception as e:
                print(f"⚠️ Failed to delete {f.name}: {e}")

    print(f"✅ Valid MP4 files: {[f.name for f in mp4_files]}")
    if not mp4_files:
        #print("No MP4 files found.")
        return None

    # Group files
    grouped = {}
    for file in mp4_files:
        key = extract_timestamp_key(file.name)
        grouped.setdefault(key, []).append(file)
    processed_patterns = set()
    for group in grouped.values():
        group.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for file in group[1:]:
            askpattern = file.name[-7:-4]
            if askpattern in processed_patterns:
                print(f"Skipping pattern {askpattern}, already processed.")
                continue
            processed_patterns.add(askpattern)
            combined_files = []
            orig_name = ""
            filelist_path = script_root / "filelist.txt"
            with open(filelist_path, "w", encoding="utf-8") as f:
                for candidate in script_root.glob(f"*{askpattern}*.MP4"):
                    if "flipped" in candidate.name or "combined-" in candidate.name:
                        continue
                    if askpattern in candidate.name:
                        if FLIP_FILES:
                            newname = candidate.stem + "-flipped.MP4"
                            if not orig_name:
                                orig_name = newname
                            combined_files.append(newname)
                            f.write(f"file '{script_root / newname}'\n")
                            subprocess.run([
                                FFMPEG_PATH, "-i", str(candidate),
                                "-metadata:s:v", "rotate=180",
                                "-codec", "copy", str(script_root / newname)
                            ])
                            if DELETE_ORIGINALS:
                                delete_if_exists(script_root / candidate)
                        else:
                            if not orig_name:
                                orig_name = candidate.name
                            combined_files.append(candidate.name)
                            f.write(f"file '{candidate}'\n")

            if combined_files:
                #output_name = f"combined-{orig_name}"
                output_path = get_unique_filename(script_root / f"combined-{orig_name}")
                subprocess.run([
                    FFMPEG_PATH, "-f", "concat", "-safe", "0",
                    "-i", str(filelist_path), "-c", "copy", str(output_path)
                ])
                if DELETE_ORIGINALS:
                    for name in combined_files:
                        delete_if_exists(script_root / name)
            delete_if_exists(filelist_path)

    # Second pass: group by date
    date_groups = {}
    input_files = [f for f in script_root.glob("*.MP4")]
    for file in input_files:
        if "-music" in file.name:
            print(f"⏭️ Skipping {file.name} (contains '-music')")
            continue
        date = get_date_from_name(file.name)
        date_groups.setdefault(date, []).append(file)

    final_output = None
    for date, files in date_groups.items():
        if len(files) < 2:
            continue
        files.sort(key=lambda f: get_time_from_name(f.name))
        earliest_time = sorted(get_time_from_name(f.name) for f in files)[0]
        output_file = script_root / f"combined-{date}-{earliest_time}.mp4"

        if output_file.exists():
            output_file = get_unique_filename(output_file)

        list_file = script_root / f"{date}-files.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for file in files:
                f.write(f"file '{file}'\n")

        subprocess.run([
            FFMPEG_PATH, "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(output_file)
        ])

        if output_file.exists() and output_file.stat().st_size > 0:
            print(f"Output file {output_file.name} created successfully.")
            final_output = str(output_file)
            if DELETE_ORIGINALS:
                for file in files:
                    delete_if_exists(script_root / file.name)
        delete_if_exists(list_file)
    return final_output

# =========================
# STEP 2: ADD MUSIC FUNCTIONS
# =========================

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
    import subprocess

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
    if playlist_id in cache and "duration" in cache[playlist_id]:
        return cache[playlist_id]["duration"]
    video_ids = []
    page_token = None
    while True:
        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key
        }
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        video_ids.extend([i["contentDetails"]["videoId"] for i in data.get("items", [])])
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    durations = fetch_video_durations(video_ids, api_key, cache)
    total_seconds = sum(durations.values())

    cache[playlist_id] = {
        "title": "",  # optional
        "duration": total_seconds,
        "cached_at": datetime.now(UTC).isoformat()
    }
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


def get_limited_playlist_entries(api_key, playlist_url, max_duration_sec, download_folder, cache=None, buffer_sec=30):
    cumulative_duration = 0
    selected_entries = []
    target_duration = max_duration_sec + buffer_sec

    os.makedirs(download_folder, exist_ok=True)

    print(f"Fetching flat playlist entries from: {playlist_url}")
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        "extractor_args": {"youtube":{"player_client":["default","-tv_simply"],"player_js_version": "actual"}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        entries = info.get('entries', [])
        random.shuffle(entries)
        print(f"Found {len(entries)} flat entries")

    video_ids = [e.get('id') for e in entries if e.get('id')]
    uncached_ids = [vid for vid in video_ids if not cache or vid not in cache]

    # Fetch durations via YouTube API
    if uncached_ids:
        url = "https://www.googleapis.com/youtube/v3/videos"
        for i in range(0, len(uncached_ids), 50):
            batch_ids = uncached_ids[i:i+50]
            params = {
                "part": "contentDetails",
                "id": ",".join(batch_ids),
                "key": api_key
            }
            resp = requests.get(url, params=params)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                vid = item["id"]
                dur = iso8601_duration_to_seconds(item["contentDetails"]["duration"])
                if cache is not None:
                    cache[vid] = dur

    # Select entries and download into download_folder
    for entry in entries:
        vid = entry.get('id')
        url = entry.get('url')
        title = entry.get('title', 'unknown')
        duration = cache.get(vid, 0)

        if not url or duration == 0:
            print(f"⚠️ Skipping {title} (missing URL or duration)")
            continue

        filename = sanitize_filename(f"{title}.mp3")
        full_path = os.path.join(download_folder, filename)

        if os.path.exists(full_path):
            print(f"✅ Already downloaded: {full_path}")
        else:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': full_path,
                'quiet': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                "extractor_args": {"youtube":{"player_client":["default","-tv_simply"],"player_js_version": "actual"}},
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                print(f"⬇️ Downloaded: {full_path}")
            except Exception as e:
                print(f"❌ Failed to download {title}: {e}")
                continue

        cumulative_duration += duration
        selected_entries.append(url)
        print(f"✓ {title} — {duration:.1f}s → Total: {cumulative_duration:.1f}s")

        if cumulative_duration > target_duration:
            print(f"✅ Target exceeded: {cumulative_duration:.1f}s > {target_duration:.1f}s")
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

def ensure_audio_matches_video(video_file, mp3_folder, api_key, playlist_url, cache, buffer_sec=30):
    """
    Ensure audio duration >= video duration.
    If short, download more tracks until target is met.
    """
    video_duration = fast_audio_duration(video_file)
    mp3_files = [str(Path(mp3_folder) / f) for f in os.listdir(mp3_folder) if f.lower().endswith('.mp3')]
    total_audio = get_total_audio_duration(mp3_files)

    if total_audio + buffer_sec < video_duration:
        print(f"⚠️ Audio too short ({total_audio:.1f}s vs video {video_duration:.1f}s). Fetching more tracks...")
        # Call get_limited_playlist_entries again to top up
        extra_urls = get_limited_playlist_entries(api_key, playlist_url,
                                                  video_duration - total_audio,
                                                  mp3_folder, cache, buffer_sec=buffer_sec)
        download_playlist_parallel(extra_urls, mp3_folder, max_workers=8)
        # Recalculate after top‑up
        mp3_files = [str(Path(mp3_folder) / f) for f in os.listdir(mp3_folder) if f.lower().endswith('.mp3')]
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

#'''
def run_add_music(video_file):
    if not video_file: 
        return None, None

    duration_sec = get_video_duration(video_file)
    print(f"Duration of combined video: {duration_sec/60:.1f} mins")

    playlists = search_youtube_playlists(API_KEY, SEARCH_TERM)
    playlist_info = []
    cache = load_cache()

    for pl in playlists:
        pl_id = pl["id"]["playlistId"]
        title = pl["snippet"]["title"]
        duration = get_playlist_duration(API_KEY, pl_id, cache)
        if duration_sec <= duration:  # and duration <= duration_sec * MAX_RATIO:
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
    choice = input_with_timeout(
        "📝 Enter the number of the playlist you want to download: ",
        timeout=60, default=default_choice, cast_type=int,
        require_input=False, retries=0
    )
    stop_alerts.set()
    selected = playlist_info[choice-1]

    playlist_clean_name = sanitize_filename(selected["title"])
    DOWNLOAD_FOLDER = os.path.join(MUSIC_FOLDER, playlist_clean_name)

    # Download entries
    entry_urls = get_limited_playlist_entries(API_KEY, selected['url'], duration_sec,
                                              DOWNLOAD_FOLDER, cache, buffer_sec=30)
    download_playlist_parallel(entry_urls, DOWNLOAD_FOLDER, max_workers=8)

    # 🔑 Ensure audio length >= video length
    total_audio = ensure_audio_matches_video(video_file, DOWNLOAD_FOLDER,
                                             API_KEY, selected['url'], cache, buffer_sec=30)

    # Merge and mix
    output_mp3 = os.path.join(DOWNLOAD_FOLDER, "combined_playlist.mp3")
    delete_if_exists(output_mp3)
    merge_mp3s_and_cleanup(DOWNLOAD_FOLDER, output_mp3)
    final_file = mix_audio_with_video(video_file, output_mp3)

    print(f'Created {final_file} with music')
    if DELETE_ORIGINALS:
        delete_if_exists(video_file)
    save_cache(cache)

    # 🔑 NEW: Upload the music version right away
    choice = input_with_timeout(
        "📝 Would you like to upload the music version? (y/n): ",
        timeout=30, require_input=False, default="y"
    )
    stop_alerts.set()
    if choice == "y":
        upload_video(final_file, selected["title"], selected["url"], privacy_status="unlisted")

    # ✅ Return title, final file, and URL
    return selected["title"], final_file, selected["url"]


# =========================
# STEP 3: UPLOAD FUNCTIONS
# =========================

def get_authenticated_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    # Create httplib2.Http and save its original request method
    authed_http = httplib2.Http()
    original_request = authed_http.request

    # Define a wrapper that injects the bearer token
    def auth_request(uri, method="GET", body=None, headers=None,
                     redirections=5, connection_type=None):
        if headers is None:
            headers = {}
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        headers["Authorization"] = f"Bearer {creds.token}"
        return original_request(uri, method=method, body=body, headers=headers,
                                redirections=redirections, connection_type=connection_type)

    authed_http.request = auth_request

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=authed_http)

def resumable_upload(insert_request):
    response, error, retry = None, None, 0
    start_time = time.time()
    while response is None:
        try:
            status, response = insert_request.next_chunk()
            if status:
                uploaded = int(status.progress() * 100)
                elapsed = time.time() - start_time
                print(f"Uploaded {uploaded}% - elapsed {elapsed:.1f}s")
            if response and "id" in response:
                total_time = time.time() - start_time
                video_url = f"https://youtu.be/{response['id']}"
                print(f"✅ Uploaded: {video_url}")
                print(f"⏱️ Total time: {total_time:.1f}s")
                try:
                    pyperclip.copy(video_url)
                except Exception:
                    print("Clipboard copy not available.")
                return video_url   # <-- return URL here
        except HttpError as e:
            if e.resp.status in [500,502,503,504]:
                error = f"Retriable HTTP error {e.resp.status}"
            else:
                raise
        except Exception as e:
            error = f"Retriable error: {e}"
        if error:
            retry += 1
            if retry > 10:
                raise RuntimeError("Upload failed after max retries.")
            sleep_seconds = random.random() * (2**retry)
            print(f"{error}, sleeping {sleep_seconds:.1f}s...")
            time.sleep(sleep_seconds)

def initialize_upload(youtube, options):
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
    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True)
    )
    resumable_upload(insert_request)

def format_time(seconds):
    """Convert seconds into H:MM:SS format."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"

def get_latest_strava_activity():
    url = "https://dylix.org/stravaWebhook?last"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    # data is a list of dicts, so take the first element
    if isinstance(data, list) and len(data) > 0:
        return data[0].get("activityid")
    return None

def upload_video(video_file, playlist_title, playlist_url, privacy_status="unlisted"):
    from argparse import Namespace
    
    # Fetch latest Strava activity ID
    strava_activity_id = get_latest_strava_activity()
    strava_url = f"https://www.strava.com/activities/{strava_activity_id}" if strava_activity_id else ""

    args = Namespace(
        file=video_file,
        title=Path(video_file).stem,
        description=f"Raw GoPro footage with music automatically added from '{playlist_title}' and then uploaded.\n\n🎵 Listen to the full playlist here: {playlist_url}\n🚴 Strava activity: {strava_url}",
        category="22",
        privacyStatus=privacy_status
    )
    youtube = get_authenticated_service()
    try:
        video_url = initialize_upload(youtube, args)   # capture returned URL
        if strava_activity_id and video_url:
            webhook_url = f"https://dylix.org/stravaWebhook?youtube&activityid={strava_activity_id}&url={video_url}"
            resp = requests.get(webhook_url)
            print(f"📡 Webhook called: {webhook_url} (status {resp.status_code})")
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

class SettlingHandler(FileSystemEventHandler):
    def dispatch(self, event):
        global last_event_time
        if not event.is_directory:
            ext = os.path.splitext(event.src_path)[1].lower()
            if ext in WATCH_EXTENSIONS:
                last_event_time = time.time()
                print(f"📁 Event: {event.event_type.upper()} → {event.src_path}")

def wait_for_settle():
    print(f"⏳ Waiting for directory to settle...")
    stable_start = None
    previous_sizes = get_file_sizes()

    while True:
        time.sleep(CHECK_INTERVAL)

        current_sizes = get_file_sizes()
        changed_files = [
            f for f in current_sizes
            if current_sizes[f] != previous_sizes.get(f)
        ]

        if changed_files:
            print("📏 File sizes changed:")
            for f in changed_files:
                old = previous_sizes.get(f, 0)
                new = current_sizes[f]
                print(f"   - {f.name}: {old} → {new}")
            stable_start = None
            previous_sizes = current_sizes
            continue

        if time.time() - last_event_time < SETTLE_TIME:
            wait_time = int(time.time() - last_event_time)
            print(f"🕒 Recent file event detected ({wait_time}s ago). Waiting...", end="\r")
            stable_start = None
            continue

        if stable_start is None:
            stable_start = time.time()
            print("📦 File sizes stable. Starting settle timer...              ")

        elif time.time() - stable_start >= SETTLE_TIME:
            print("✅ Directory settled. No changes and stable sizes.           ")
            break

def process_video_file(video_file):
    if not video_file:
        #print("No combined video created.")
        return
    else:
        if has_music_version(video_file):
            print(f"🎵 Skipping {video_file} — music version already exists.")
            return
        playlist_title, final_video, playlist_url = run_add_music(video_file)
        if final_video:
            choice = input_with_timeout("🛠️ Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n): ", timeout=30, require_input=False, default="y")
            stop_alerts.set()
            if choice == "y":
                upload_video(final_video, playlist_title, playlist_url)

def process_all_new_files():
    print("🚀 Starting batch processing...")
    video_file = run_flipme()
    candidates = [
        f for f in Path(VIDEO_FOLDER).glob("*.mp4")
        if not f.name.lower().endswith("-music.mp4") and f.stat().st_size > 0
    ]
    for f in candidates:
        print(f"🎬 Processing: {f.name}")
        process_video_file(f)

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

def eject_drive(drive_letter):
    try:
        # Use PowerShell COM object to eject the drive
        cmd = (
            "powershell",
            f"(New-Object -comObject Shell.Application)"
            f".NameSpace(17).ParseName('{drive_letter}:').InvokeVerb('Eject')"
        )
        subprocess.run(cmd, check=True, shell=True)
        print(f"💽 Ejected {drive_letter}: successfully")
    except Exception as e:
        print(f"⚠️ Could not eject {drive_letter}: {e}")

def copy_gopro_files(drive_letter):
    mount_point = f"{drive_letter}:\\"
    files_to_delete = []

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
                            # find and queue matching THM/LRV by numeric ID
                            files_to_delete.extend(find_sidecars(root, file))
                        else:
                            print(f"⚠️ Size mismatch for {file}, not deleting")
                    except Exception as e:
                        print(f"⚠️ Error copying {src}: {e}")

    if DELETE_ORIGINALS and files_to_delete:
        print(f"🗑️ Deleting {len(files_to_delete)} files from USB...")
        for f in set(files_to_delete):  # deduplicate
            try:
                os.remove(f)
                print(f"   Removed {f}")
            except Exception as e:
                print(f"⚠️ Could not delete {f}: {e}")
        # After deletion, eject the drive
        eject_drive(drive_letter)
    elif not DELETE_ORIGINALS and files_to_delete:
        print(f"ℹ️ {len(files_to_delete)} files verified, but not deleted (DELETE_ORIGINALS=False)")

# --- USB Listener Thread ---
def usb_listener():
    pythoncom.CoInitialize()  # COM init required in threads
    c = wmi.WMI()
    watcher = c.watch_for(
        notification_type="Creation",
        wmi_class="Win32_VolumeChangeEvent"
    )
    while True:
        event = watcher()
        if event.EventType == 2:  # Device arrival
            drive_letter = event.DriveName.strip(":\\")
            print(f"💽 USB inserted: {drive_letter}:\\")
            copy_gopro_files(drive_letter)

# --- Your Watchdog Loop ---
def start_watcher_then_process():
    global last_event_time
    observer = Observer()
    observer.schedule(SettlingHandler(), path=VIDEO_FOLDER, recursive=False)
    observer.start()

    print(f"👀 Watching {VIDEO_FOLDER} for new files...")

    # Run USB listener in parallel
    threading.Thread(target=usb_listener, daemon=True).start()

    try:
        while True:
            if last_event_time:  # only run if handler saw an event
                wait_for_settle()          # settle logic
                process_all_new_files()    # batch processing
                print("🔁 Returning to watch mode...\n")
                last_event_time = None     # reset so we don't re-trigger
            else:
                time.sleep(1)  # idle quietly until an event occurs
    except KeyboardInterrupt:
        print("❌ Watcher stopped by user.")
    finally:
        observer.stop()
        observer.join()
## END USB DRIVE MOD

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

def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

# --- Timeout Logic ---

def input_with_timeout(prompt, timeout=30, default=None, cast_type=str, require_input=False, retries=0):
    if require_input:
        # Block until valid input is received
        while True:
            with print_lock:
                sys.stdout.write(f"{prompt} (required): ")
                sys.stdout.flush()
            try:
                user_input = input().strip()
                return cast_type(user_input)
            except Exception as e:
                safe_print(f"\n⚠️ Invalid input: {e}. Please try again.")
    else:
        # Use timeout + fallback logic
        attempt = 0
        while retries is None or attempt <= retries:
            result = [None]
            input_ready = threading.Event()

            def get_input():
                input_ready.set()
                try:
                    user_input = input()
                    result[0] = cast_type(user_input)
                except Exception as e:
                    safe_print(f"\n⚠️ Input casting failed: {e}")
                    result[0] = default

            with print_lock:
                sys.stdout.write(f"{prompt} (waiting {timeout}s{'...' if default is None else f', default: {default}'}): ")
                sys.stdout.flush()

            thread = threading.Thread(target=get_input)
            thread.daemon = True
            thread.start()

            input_ready.wait(timeout=1.0)
            thread.join(timeout)

            if thread.is_alive():
                safe_print(f"\n⏰ Timeout reached on attempt {attempt + 1}.")
                thread.join(0.1)
                attempt += 1
                if retries is not None and attempt > retries:
                    return default
                elif retries is None:
                    return default
            else:
                return result[0] if result[0] is not None else default


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
    upload_video(video_file, playlist_title, playlist_url, privacy_status="unlisted")


# =========================
# MAIN PIPELINE
# =========================
if __name__ == "__main__":
    # Optional quick test:
    # upload_video(r"D:\GoPro\today\combined-2025-09-20-07-03-music.mp4",
    #              "Dubstep  [No Copyright Sound] [ FREE USE MUSIC ]", "", privacy_status="unlisted")
    video_file = run_flipme()

    # --- Start Alert Threads ---
    threading.Thread(target=sound_loop, daemon=True).start()
    if os.name == 'nt':
        threading.Thread(target=flash_window, daemon=True).start()

        # First: look for raw MP4s (no '-music' suffix, case-insensitive)
        candidates = [
            f for f in Path(VIDEO_FOLDER).glob("*.mp4")
            if not f.name.lower().endswith("-music.mp4")
        ]

        if candidates:
            print("\n🎵 Available MP4 files:")
            for i, f in enumerate(candidates, 1):
                print(f"{i}. {f.name}")
            try:
                choice = input_with_timeout(
                    "📝 Select a file to add music to or press ENTER to skip..: ",
                    timeout=30, require_input=False, default=""
                )
                stop_alerts.set()

                if not choice:  # ENTER pressed or timeout
                    print("⏭️ Skipping add-music step, entering watch mode..")
                    start_watcher_then_process()
                    exit()

                index = int(choice) - 1
                selected_file = candidates[index]
                print(f"🎬 You selected: {selected_file.name}")

                playlist_title, final_video, playlist_url = run_add_music(str(selected_file))
                if final_video:
                    choice = input_with_timeout(
                        "📝 Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n): ",
                        timeout=30, require_input=False, default="y"
                    )
                    stop_alerts.set()
                    if choice == "y":
                        upload_video(final_video, playlist_title, playlist_url, privacy_status="private")
            except (ValueError, IndexError):
                print("❌ Invalid selection. Entering file watch mode..")
                start_watcher_then_process()
                exit()

        else:
            # NEW: If there are no raw files, offer to upload completed music versions
            music_candidates = [
                f for f in Path(VIDEO_FOLDER).glob("*.mp4")
                if f.name.lower().endswith("-music.mp4")
            ]

            if music_candidates:
                print("\n🎵 Completed music MP4 files available for upload:")
                for i, f in enumerate(music_candidates, 1):
                    print(f"{i}. {f.name}")
                try:
                    choice = input_with_timeout(
                        "📝 Select a file to upload or press ENTER to skip..: ",
                        timeout=30, require_input=False, default=""
                    )
                    stop_alerts.set()

                    if not choice:  # ENTER pressed or timeout
                        print("⏭️ Skipping upload, entering watch mode..")
                        start_watcher_then_process()
                        exit()

                    index = int(choice) - 1
                    selected_file = music_candidates[index]
                    print(f"📤 You selected: {selected_file.name} for upload")

                    # Use stem as a reasonable title; playlist_url unknown here
                    upload_video(str(selected_file), Path(selected_file).stem, "", privacy_status="private")
                except (ValueError, IndexError):
                    print("❌ Invalid selection. Entering file watch mode..")
                    start_watcher_then_process()
                    exit()
            else:
                print("📁 No eligible MP4 files found.")
                choice = input_with_timeout(
                    "🛠️ Would you like to generate dummy GoPro clips? (y/n): ",
                    timeout=10, default="n", cast_type=str
                ).strip().lower()
                stop_alerts.set()
                if choice == "y":
                    generate_dummy_gopro_clips(VIDEO_FOLDER)
                    mp4_files = sorted([f for f in Path(VIDEO_FOLDER).glob("*") if f.suffix.lower() == ".mp4"])
                    print(f"✅ Generated {len(mp4_files)} dummy clips.")
                    video_file = run_flipme()
                else:
                    print("🚪 Exiting without generating clips.")
    else:
        process_video_file(video_file)

    # 🔁 Start watching for new files after initial run
    start_watcher_then_process()