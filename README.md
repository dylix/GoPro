# 🎬 GoPro Auto-Processor

Automates the entire workflow for processing GoPro-style footage: watching folders, grouping clips, adding royalty-free music, and optionally uploading to YouTube. Built for batch safety, expressive prompts, and robust media handling.

---

## 📦 Features

- 🔍 **Folder Watcher**: Detects new `.mp4` files and waits for them to settle before processing.
- 🎞️ **Clip Grouping & Concatenation**: Combines clips based on filename patterns and timestamps.
- 🔄 **Optional Rotation**: Flips videos 180° if configured.
- 🧹 **Cleanup**: Deletes original files after processing (optional).
- 🎵 **Music Integration**:
  - Searches YouTube for royalty-free playlists.
  - Matches playlist duration to video length.
  - Downloads and merges selected tracks.
  - Mixes music with original audio or replaces it.
- ☁️ **YouTube Upload**:
  - Authenticates via OAuth2.
  - Uploads final video with metadata.
- 🧪 **Dummy Clip Generator**: Creates synthetic GoPro-style videos with overlays for testing.
- 🔔 **User Alerts**: Plays sound and flashes window to prompt user input.
- ⏱️ **Timeout Prompts**: Uses expressive, fallback-safe input prompts with timeout logic.

---

## 📁 Folder Structure

| Folder         | Purpose                                 |
|----------------|------------------------------------------|
| `VIDEO_FOLDER` | Contains raw `.mp4` files                |
| `MUSIC_FOLDER` | Stores downloaded music tracks           |
| `SCRIPT_FOLDER`| Holds config and cache files             |

---

## ⚙️ Configuration

Set these in the script or via `config.json`:

```json
{
  "api_key": "YOUR_YOUTUBE_API_KEY"
}
```

Other key settings:

- `FFMPEG_PATH`: Path to ffmpeg executable  
- `SETTLE_TIME`: Time to wait for file stability  
- `SEARCH_TERM`: YouTube search query for music  
- `MAX_RATIO`: Max allowed mismatch between video and playlist duration  

---

## 🚀 Workflow Overview

1. **Watch Mode**
   - Detects new `.mp4` files
   - Waits until file sizes stabilize
   - Triggers batch processing

2. **Video Processing**
   - Groups clips by timestamp pattern
   - Optionally flips them
   - Concatenates into a single video

3. **Music Matching**
   - Searches YouTube for playlists
   - Filters by duration match
   - Downloads and merges tracks
   - Mixes with video audio

4. **Upload (Optional)**
   - Prompts user to upload
   - Uses YouTube Data API with resumable upload

5. **Fallbacks & Prompts**
   - Timeout-safe user input
   - Sound and visual alerts
   - Optional popup prompts

---

## 🧪 Testing

Use the built-in dummy generator:

```bash
python combined.py
# Then choose to generate dummy clips when prompted
```

---

## 🛡️ Safety & Batch Robustness

- ✅ File size checks and event timestamps prevent premature processing  
- 🧠 Caches playlist durations to reduce API usage  
- 🎧 Handles missing audio streams gracefully  
- 🔒 Sanitizes filenames for safe filesystem and YouTube usage  

---

## 📚 Dependencies

- `ffmpeg`, `ffprobe`  
- `yt_dlp`  
- `requests`, `oauth2client`, `google-api-python-client`  
- `watchdog`  
- `playsound` or `winsound` (optional)  

---

## 🧠 Author Notes

This script is designed for expressive automation with batch safety, interactive prompts, and robust fallback logic. It’s modular, secure, and built for creative media workflows.
