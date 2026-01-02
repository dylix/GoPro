import os
from googleapiclient.discovery import build
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
import httplib2
import http.client as httplib


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
YOUTUBE_UPLOAD_SCOPE = [ "https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/youtube" ]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

def get_authenticated_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_UPLOAD_SCOPE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, YOUTUBE_UPLOAD_SCOPE)
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

# Replace with your API key or authorized credentials
video_id = "kRVoPt_uAMI"  # from the upload response

youtube = get_authenticated_service()

request = youtube.videos().list(
    part="status,processingDetails",
    id=video_id
)
response = request.execute()

status = response["items"][0]["status"]["uploadStatus"]
processing = response["items"][0]["processingDetails"]["processingStatus"]

print(f"Upload status: {status}")
print(f"Processing status: {processing}")

