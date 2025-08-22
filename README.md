🎬 GoPro Auto-Processor

Automates the entire workflow for processing GoPro-style footage: watching folders, grouping clips, adding royalty-free music, and optionally uploading to YouTube. Built for batch safety, expressive prompts, and robust media handling.
📦 Features

    🔍 Folder Watcher: Detects new .mp4 files and waits for them to settle before processing.

    🎞️ Clip Grouping & Concatenation: Combines clips based on filename patterns and timestamps.

    🔄 Optional Rotation: Flips videos 180° if configured.

    🧹 Cleanup: Deletes original files after processing (optional).

    🎵 Music Integration:

        Searches YouTube for royalty-free playlists.

        Matches playlist duration to video length.

        Downloads and merges selected tracks.

        Mixes music with original audio or replaces it.

    ☁️ YouTube Upload:

        Authenticates via OAuth2.

        Uploads final video with metadata.

    🧪 Dummy Clip Generator: Creates synthetic GoPro-style videos with overlays for testing.

    🔔 User Alerts: Plays sound and flashes window to prompt user input.

    ⏱️ Timeout Prompts: Uses expressive, fallback-safe input prompts with timeout logic.

📁 Folder Structure
Folder	Purpose
VIDEO_FOLDER	Contains raw .mp4 files
MUSIC_FOLDER	Stores downloaded music tracks
SCRIPT_FOLDER	Holds config and cache files
⚙️ Configuration

Set these in the script or via config.json:
json
