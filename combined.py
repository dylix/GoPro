#!/usr/bin/python3
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

from apiclient.discovery import build
from apiclient.errors import HttpError
from apiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow
from pathlib import Path
from datetime import datetime, UTC
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

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
MUSIC_FOLDER = r"D:\GoPro\Music"
VIDEO_FOLDER = r"D:\GoPro\today"
WATCH_EXTENSIONS = {'.mp4'}
SETTLE_TIME = 900  # seconds
CHECK_INTERVAL = 10  # seconds
CACHE_FILE = os.path.join(SCRIPT_FOLDER, "playlist_cache.json")
SEARCH_TERM = "royalty free"
CONFIRM = True
FLIP_FILES = False
DELETE_ORIGINALS = True
MAX_RATIO = 2.0
CLIENT_SECRETS_FILE = os.path.join(SCRIPT_FOLDER, "client_secrets.json")

last_event_time = time.time()

with open(os.path.join(SCRIPT_FOLDER, "config.json")) as f:
    config = json.load(f)
API_KEY = config["api_key"]
if not API_KEY or "YOUR_API_KEY_HERE" in API_KEY:
    raise ValueError("Missing or placeholder API key in config.json.")


# =========================
# STEP 1: FLIPME FUNCTIONS
# =========================
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

def run_flipme():
    script_root = Path(VIDEO_FOLDER)
    mp4_files = list(script_root.glob("*.mp4"))
    if not mp4_files:
        print("No MP4 files found.")
        return None

    # Group files
    grouped = {}
    for file in mp4_files:
        key = get_unique_name(file.name)
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
                            candidate.unlink()
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
                        try:
                            Path(script_root / name).unlink()
                        except Exception as e:
                            print(f"Failed to delete {name}: {e}")
            filelist_path.unlink()

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
                    try:
                        file.unlink()
                        print(f"🗑️ Deleted: {file.name}")
                    except Exception as e:
                        print(f"⚠️ Failed to delete {file.name}: {e}")
        list_file.unlink()

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

def get_video_duration(filepath):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", filepath],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0

def search_youtube_playlists(api_key, query, max_results=5):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {"part": "snippet", "q": query, "type": "playlist", "maxResults": max_results, "key": api_key}
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("items", [])

def get_playlist_duration(api_key, playlist_id, cache):
    if playlist_id in cache:
        return cache[playlist_id]["duration"]
    total_seconds = 0
    page_token = None
    while True:
        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 5, "key": api_key}
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        video_ids = [i["contentDetails"]["videoId"] for i in data.get("items", [])]
        if not video_ids: break
        url_vid = "https://www.googleapis.com/youtube/v3/videos"
        params_vid = {"part": "contentDetails", "id": ",".join(video_ids), "key": api_key}
        resp_vid = requests.get(url_vid, params=params_vid)
        videos = resp_vid.json().get("items", [])
        for v in videos:
            total_seconds += iso8601_duration_to_seconds(v["contentDetails"]["duration"])
        page_token = data.get("nextPageToken")
        if not page_token: break
        
        cache[playlist_id] = {
            "title": "",  # optional, can be filled later
            "duration": total_seconds,
            "cached_at": datetime.now(UTC).isoformat()
        }
    return total_seconds

def get_limited_playlist_entries(playlist_url, max_duration_sec):
    cumulative_duration = 0
    selected_entries = []

    print(f"Fetching flat playlist entries from: {playlist_url}")

    ydl_opts_flat = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts_flat) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        entries = info.get('entries', [])
        #shuffle entries so its less boring
        random.shuffle(entries)
        print(f"Found {len(entries)} flat entries")

    ydl_opts_individual = {
        'quiet': True,
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts_individual) as ydl:
        for entry in entries:
            url = entry.get('url')
            title = entry.get('title', 'unknown')
            if not url:
                print(f"Skipping entry with no URL: {title}")
                continue
            try:
                video_info = ydl.extract_info(url, download=False)
                duration = video_info.get('duration', 0)
                print(f"✓ {title} — {duration:.1f}s")
                if cumulative_duration + duration > max_duration_sec:
                    cumulative_duration += duration
                    selected_entries.append(url)
                    print(f"⏹ Stopping at {cumulative_duration:.1f}s (limit: {max_duration_sec}s)")
                    break
                cumulative_duration += duration
                selected_entries.append(url)
            except Exception as e:
                print(f"⚠️ Failed to fetch {url}: {e}")

    print(f"✅ Selected {len(selected_entries)} tracks totaling {cumulative_duration:.1f} seconds")
    return selected_entries



def download_playlist_as_mp3(entry_urls, output_path):
    os.makedirs(output_path, exist_ok=True)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{output_path}/%(title)s.%(ext)s',
        'ignoreerrors': True,
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        'quiet': True, 'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download(entry_urls)


def merge_mp3s_and_cleanup(mp3_folder, output_mp3):
    mp3_files = [f for f in os.listdir(mp3_folder) if f.lower().endswith('.mp3')]
    filelist_path = os.path.join(mp3_folder, 'filelist.txt')
    with open(filelist_path, 'w', encoding='utf-8') as filelist:
        for mp3 in mp3_files:
            path = os.path.join(mp3_folder, mp3).replace('\\', '/')
            safe_path = path.replace("'", "'\\''")
            filelist.write(f"file '{safe_path}'\n")
    subprocess.run(['ffmpeg','-f','concat','-safe','0','-i',filelist_path,'-c','copy',output_mp3], check=True)
    os.remove(filelist_path)

def mix_audio_with_video(video_file, new_audio_file):
    base, ext = os.path.splitext(video_file)
    output_file = f"{base}-music{ext}"
    duration = get_video_duration(video_file)

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


def sanitize_filename(filename, replacement="_"):
    name, ext = os.path.splitext(filename)
    name = re.sub(r"[^a-zA-Z0-9 _-]", replacement, name)
    name = re.sub(rf"{re.escape(replacement)}+", replacement, name).strip(f" {replacement}")
    if not name: name = "untitled"
    return f"{name}{ext}"

def run_add_music(video_file):
    if not video_file: return None, None
    duration_sec = get_video_duration(video_file)
    print(f"Duration of combined video: {duration_sec/60:.1f} mins")
    playlists = search_youtube_playlists(API_KEY, SEARCH_TERM)
    playlist_info = []
    cache = load_cache()
    for pl in playlists:
        pl_id = pl["id"]["playlistId"]
        title = pl["snippet"]["title"]
        #duration = get_playlist_duration(API_KEY, pl_id)
        duration = get_playlist_duration(API_KEY, pl_id, cache)
        if duration_sec <= duration: # and duration <= duration_sec * MAX_RATIO:
            diff = abs(duration - duration_sec)
            playlist_info.append({"title": title,"id": pl_id,"duration": duration,"diff": diff,"url": f"https://www.youtube.com/playlist?list={pl_id}"})
    playlist_info.sort(key=lambda x: x["diff"])

    for i, p in enumerate(playlist_info, start=1):
        match_pct = (p['duration'] / duration_sec) * 100
        print(f"{i}. {p['title']} - {p['duration']/60:.1f} min ({match_pct:.0f}%) - {p['url']}")
    #choice = int(input(""))
    #choice = input_with_timeout("📝 Enter the number of the playlist you want to download:\n📝Enter your response within 30 seconds:", timeout=30)
    choice = input_with_timeout("📝 Enter the number of the playlist you want to download:\n📝 Enter your response within 60 seconds:", timeout=60, default=1, cast_type=int, require_input=False, retries=0)
    selected = playlist_info[choice-1]
    playlist_clean_name = sanitize_filename(selected["title"])
    DOWNLOAD_FOLDER = os.path.join(MUSIC_FOLDER, playlist_clean_name)
    #download_playlist_as_mp3(selected['url'], DOWNLOAD_FOLDER)
    #download_playlist_as_mp3(selected['url'], DOWNLOAD_FOLDER, duration_sec)
    entry_urls = get_limited_playlist_entries(selected['url'], duration_sec)
    download_playlist_as_mp3(entry_urls, DOWNLOAD_FOLDER)
    #output_mp3 = 'merged_playlist.mp3'
    output_mp3 = os.path.join(DOWNLOAD_FOLDER, "combined_playlist.mp3")
    merge_mp3s_and_cleanup(DOWNLOAD_FOLDER, output_mp3)
    final_file = mix_audio_with_video(video_file, output_mp3)
    print(f'Created {final_file} with music')
    save_cache(cache)
    return selected["title"], final_file


# =========================
# STEP 3: UPLOAD FUNCTIONS
# =========================
httplib2.RETRIES = 1
MAX_RETRIES = 10
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib.NotConnected,
    httplib.IncompleteRead, httplib.ImproperConnectionState,
    httplib.CannotSendRequest, httplib.CannotSendHeader,
    httplib.ResponseNotReady, httplib.BadStatusLine,)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
VALID_PRIVACY_STATUSES = ("public","private","unlisted")

def get_authenticated_service(args):
    flow = flow_from_clientsecrets(CLIENT_SECRETS_FILE, scope=YOUTUBE_UPLOAD_SCOPE)
    storage = Storage("%s-oauth2.json" % sys.argv[0])
    credentials = storage.get()
    if credentials is None or credentials.invalid:
        credentials = run_flow(flow, storage, args)
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=credentials.authorize(httplib2.Http()))

def initialize_upload(youtube, options):
    body = dict(snippet=dict(title=options.title, description=options.description, categoryId=options.category),
                status=dict(privacyStatus=options.privacyStatus,selfDeclaredMadeForKids=False))
    insert_request = youtube.videos().insert(part=",".join(body.keys()), body=body,
        media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True))
    resumable_upload(insert_request)

def resumable_upload(insert_request):
    response = None; error = None; retry = 0
    while response is None:
        try:
            print("Uploading file...")
            status, response = insert_request.next_chunk()
            if response and "id" in response:
                print("Video id '%s' was successfully uploaded." % response["id"])
                return
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES: error = f"Retriable HTTP error {e.resp.status}"
            else: raise
        except RETRIABLE_EXCEPTIONS as e: error = f"Retriable error: {e}"
        if error:
            retry += 1
            if retry > MAX_RETRIES: sys.exit("No longer attempting to retry.")
            sleep_seconds = random.random() * (2**retry)
            time.sleep(sleep_seconds)

def upload_video(video_file, playlist_title, privacy_status="unlisted"):
    from argparse import Namespace

    args = Namespace(
        file=video_file,
        title=Path(video_file).stem,
        description=f"Raw GoPro footage with music automatically added from {playlist_title} and then uploaded.",
        category="22",  # People & Blogs
        privacyStatus=privacy_status,
        logging_level="ERROR",
        auth_host_name="localhost",
        noauth_local_webserver=False,
        auth_host_port=[8080, 8090]
    )

    youtube = get_authenticated_service(args)

    try:
        initialize_upload(youtube, args)
    except HttpError as e:
        print(f"🚨 HTTP error {e.resp.status} occurred:\n{e.content}")

# =========================
# Generates dummy GoPro-style videos with overlays and sets file modification times.
# =========================

def generate_dummy_gopro_clips(
    output_dir=Path(VIDEO_FOLDER),
    timestamps=None,
    camera_ids=None,
    duration_sec=10
):
    if timestamps is None:
        timestamps = [
            ("2025-08-18", "05-55", "0731"),
            ("2025-08-18", "06-30", "0732"),
            ("2025-08-18", "07-15", "0733"),
            ("2025-08-18", "07-30", "0734"),
            ("2025-08-19", "05-56", "0735"),
            ("2025-08-19", "06-31", "0736"),
            ("2025-08-19", "07-16", "0737"),
            ("2025-08-19", "07-31", "0738"),
        ]

    if camera_ids is None:
        camera_ids = ["1", "2", "3"]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def create_dummy_video(filepath, start_time_sec, camera_id):
        filename = filepath.name

        drawtext_filters = [
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
            "fontsize=48:fontcolor=white:x=50:y=50:"
            "text='%{pts\\:hms}'",

            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
            f"fontsize=36:fontcolor=yellow:x=50:y=120:"
            f"text='Camera ID\\: {camera_id}'",

            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
            f"fontsize=36:fontcolor=cyan:x=50:y=180:"
            f"text='File\\: {filename}'"
        ]

        vf_chain = ",".join(drawtext_filters)

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "smptebars=size=1920x1080:rate=30",
            "-vf", vf_chain,
            "-t", str(duration_sec),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(filepath)
        ]
        subprocess.run(cmd)

    def set_mtime(filepath, date_str, time_str):
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H-%M")
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
        if not f.name.endswith("-music.mp4")
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
        print("No combined video created.")
    else:
        if has_music_version(video_file):
            print(f"🎵 Skipping {video_file} — music version already exists.")
            return
        playlist_title, final_video = run_add_music(video_file)
        if final_video:
            #choice = input("🛠️ Upload the video? This uses a lot of API daily credits. (y/n): ").strip().lower()
            #choice = input_with_timeout("🛠️ Upload the video? This uses a lot of API daily credits. (y/n):\n📝 Enter your response within 30 seconds:", timeout=30)
            choice = input_with_timeout("🛠️ Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n):", timeout=30, require_input=True)
            if choice == "y":
                upload_video(final_video, playlist_title)

def process_all_new_files():
    print("🚀 Starting batch processing...")
    candidates = [
        f for f in Path(VIDEO_FOLDER).glob("*.mp4")
        if not f.name.endswith("-music.mp4") and f.stat().st_size > 0
    ]
    for f in candidates:
        print(f"🎬 Processing: {f.name}")
        process_video_file(f)

def start_watcher_then_process():
    global last_event_time
    observer = Observer()
    observer.schedule(SettlingHandler(), path=VIDEO_FOLDER, recursive=False)
    observer.start()

    print(f"👀 Watching {VIDEO_FOLDER} for new files...")

    try:
        while True:
            last_event_time = time.time()
            wait_for_settle()  # Wait until files are stable
            process_all_new_files()  # Trigger batch logic
            print("🔁 Returning to watch mode...\n")
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
    while not stop_alerts.is_set():
        if os.name == 'nt':
            winsound.Beep(1000, 500)
        elif playsound:
            playsound("alert.wav")
        time.sleep(2)

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
    while not stop_alerts.is_set():
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        time.sleep(5)

# --- Timeout Logic ---
def input_with_timeout(prompt, timeout=30, default=None, cast_type=str, require_input=False, retries=None):
    attempt = 0
    while retries is None or attempt <= retries:
        print(f"{prompt} (waiting {timeout}s{'...' if default is None else f', default: {default}'})")
        result = [None]

        def get_input():
            try:
                user_input = input()
                result[0] = cast_type(user_input)
                stop_alerts.set()  # Stop alerts once input is received
            except Exception:
                result[0] = default  # fallback if cast fails
                stop_alerts.set()  # Stop alerts once input is received

        thread = threading.Thread(target=get_input)
        thread.daemon = True
        thread.start()
        thread.join(timeout)

        if thread.is_alive():
            print(f"\n⏰ Timeout reached on attempt {attempt + 1}.")
            attempt += 1
            if retries is not None and attempt > retries:
                if require_input:
                    raise TimeoutError("No input received after retries and no default provided.")
                return default
        else:
            return result[0] if result[0] is not None else default

# --- Optional Popup (cross-platform) ---
def show_popup():
    if os.name == 'nt':
        os.system('msg * "Please respond to the script!"')
    else:
        os.system('zenity --info --text="Please respond to the script!"')

# =========================
# MAIN PIPELINE
# =========================
if __name__ == "__main__":

    # --- Start Alert Threads ---
    threading.Thread(target=sound_loop, daemon=True).start()
    if os.name == 'nt':
        threading.Thread(target=flash_window, daemon=True).start()


    video_file = run_flipme()
    if not video_file:
        print("No combined video created.")

        # Fallback: look for MP4s that don't end in -music.mp4
        candidates = [
            f for f in Path(VIDEO_FOLDER).glob("*.mp4")
            if not f.name.endswith("-music.mp4")
        ]

        if candidates:
            print("\n🎵 Available MP4 files:")
            for i, f in enumerate(candidates, 1):
                print(f"{i}. {f.name}")

            #choice = input("Select a file to add music to (enter a number) or press enter to skip..")
            #choice = input_with_timeout("📝 Select a file to add music to (enter a number) or press enter to skip..\n📝 Enter your response within 30 seconds:", timeout=30)
            choice = input_with_timeout("📝 Select a file to add music to (enter a number) or press enter to skip..:", timeout=30, require_input=True)
            try:
                index = int(choice) - 1
                selected_file = candidates[index]
                print(f"🎬 You selected: {selected_file.name}")
                playlist_title, final_video = run_add_music(selected_file)
                if final_video:
                    #choice = input("🛠️ Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n): ").strip().lower()
                    #choice = input_with_timeout("️ Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n):", timeout=30)
                    choice = input_with_timeout("📝 Would you like to upload the video? This uses a lot of API daily credits. 1600 out of 10000. (y/n):", timeout=30, require_input=True)
                    if choice == "y":
                        upload_video(final_video, playlist_title)
            except (ValueError, IndexError):
                print("❌ Invalid selection. Entering file watch mode..")
                start_watcher_then_process()
                exit()
        else:
            print("📁 No eligible MP4 files found.")
            #choice = input("🛠️ Would you like to generate dummy GoPro clips? (y/n): ").strip().lower()
            choice = input_with_timeout("🛠️ Would you like to generate dummy GoPro clips? (y/n): ", timeout=10, default="n", cast_type=str).strip().lower()
            if choice == "y":
                generate_dummy_gopro_clips(VIDEO_FOLDER)
                mp4_files = sorted([f for f in Path(VIDEO_FOLDER).glob("*") if f.suffix.lower() == ".mp4"])
                print(f"✅ Generated {len(mp4_files)} dummy clips.")
                video_file = run_flipme()
            else:
                print("🚪 Exiting without generating clips.")
                exit()
    else:
        process_video_file(video_file)

    # 🔁 Start watching for new files after initial run
    start_watcher_then_process()
