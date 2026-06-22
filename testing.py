import json
from pathlib import Path

# import your real function
from combined import build_youtube_chapters, format_ts

def test_from_json(json_path):
    data = json.loads(Path(json_path).read_text())

    # Extract the chapters list from the JSON
    chapter_durations = [
        (entry["file"], entry["duration_sec"])
        for entry in data["chapters"]
    ]

    # Run your grouping logic
    output = build_youtube_chapters(chapter_durations)

    print("\n=== GENERATED CHAPTERS ===")
    print(output)

    print("\n=== RAW JSON CHAPTERS (for comparison) ===")
    for c in data["chapters"]:
        print(f"{c['duration_sec']:>5}  {c['file']}")

test_from_json("D:\GoPro\Today\combined-2026-06-20-music-2625.mp4.meta.json")