import subprocess
from pathlib import Path
from datetime import datetime, timedelta
import json
import os
import signal
import sys
import subprocess
import fitdecode
import secrets

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

FPS = 30
WIDTH, HEIGHT = 3840, 2160

PREVIEW_SECONDS = None

TODAY_DIR = Path(r"D:\GoPro\Today")
OVERLAY_DIR = Path(r"D:\Users\dylix\source\repos\GoPro\Overlay")
ASS_FILE = "hud_overlay.ass"

POS_SPEED   = ( 350, 2030 )
POS_HR      = ( 1263, 2030 )
POS_POWER   = ( 2176, 2030 )
POS_CAD     = ( 3089, 2030 )

POS_DIST    = ( 350, 1910 )
POS_ELEV    = ( 3089, 1910 )
POS_MOVTIME = ( 1720, 1910 )

ICON_SPEED = "💨"
ICON_HR    = "♥"
ICON_POWER = "⚡"
ICON_CAD   = "⟳"
ICON_DIST  = "📏️"
ICON_ELEV  = "🏔️"
ICON_TIME  = "⏱"

# ------------------------------------------------------------
# AUTO-DETECT INPUTS
# ------------------------------------------------------------
def cancel_encoding():
    if process:
        process.send_signal(signal.CTRL_BREAK_EVENT)

def find_base_video() -> Path:
    mp4s = [
        p for p in TODAY_DIR.glob("*.mp4")
        if "overlay" not in p.name.lower()
    ]
    if not mp4s:
        raise FileNotFoundError("No base MP4 found in TODAY_DIR")
    return sorted(mp4s)[-1]

def find_fit_file() -> Path:
    for pattern in ("*.fit", "*.FIT"):
        for f in TODAY_DIR.glob(pattern):
            return f
    raise SystemExit("No FIT file found in TODAY_DIR")

def find_json_file() -> Path:
    for f in TODAY_DIR.glob("*.json"):
        if "sync" not in f.name.lower():
            return f
    raise SystemExit("No JSON chapter file found in TODAY_DIR")

def find_sync_markers() -> Path:
    for f in OVERLAY_DIR.glob("sync_markers.json"):
        return f
    raise SystemExit("sync_markers.json not found in OVERLAY_DIR — run sync tool first")

def generate_hashed_overlay_name(video_path: Path) -> Path:
    h = secrets.token_hex(3)
    return video_path.with_name(f"{video_path.stem}-overlay-{h}{video_path.suffix}")

# ------------------------------------------------------------
# GOPRO GROUP SYNC SYSTEM
# ------------------------------------------------------------

def extract_group_key(filename: str) -> str:
    base = filename.split("-")[-1]
    core = base.split(".")[0]
    return core[-4:]  # "1102"

def build_group_map(meta, sync_markers):
    chapters = meta["chapters"]

    # 1. Build groups
    groups = []
    for ch in chapters:
        gkey = extract_group_key(ch["file"])
        dur = ch["duration_sec"]

        if not groups or groups[-1]["group_key"] != gkey:
            groups.append({"group_key": gkey, "duration": dur})
        else:
            groups[-1]["duration"] += dur

    # 2. Compute video boundaries
    t = 0
    for g in groups:
        g["video_start_sec"] = t
        g["video_end_sec"] = t + g["duration"]
        t += g["duration"]

    # 3. Attach sync markers
    for m in sync_markers:
        gi = m["group"]
        groups[gi]["anchor_video_sec"] = m["video_sec"]
        groups[gi]["anchor_fit_timestamp"] = m["fit_timestamp"]

    return groups

def map_video_to_fit(video_sec, groups):
    for g in groups:
        if g["video_start_sec"] <= video_sec < g["video_end_sec"]:
            anchor_video = g["anchor_video_sec"]
            anchor_fit = datetime.fromisoformat(g["anchor_fit_timestamp"])
            delta = video_sec - anchor_video
            return anchor_fit + timedelta(seconds=delta)

    # clamp to last group
    g = groups[-1]
    anchor_video = g["anchor_video_sec"]
    anchor_fit = datetime.fromisoformat(g["anchor_fit_timestamp"])
    delta = video_sec - anchor_video
    return anchor_fit + timedelta(seconds=delta)

# ------------------------------------------------------------
# FIT LOADING
# ------------------------------------------------------------

def load_fit(path: Path):
    pts = []
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.records.FitDataMessage):
                continue
            row = {f.name: f.value for f in frame.fields}
            ts = row.get("timestamp")
            if ts is None:
                continue

            if ts.tzinfo is not None:
                ts = ts.astimezone().replace(tzinfo=None)

            speed = row.get("enhanced_speed") or row.get("speed")
            altitude = row.get("enhanced_altitude") or row.get("altitude")

            pts.append({
                "timestamp": ts,
                "speed": speed,
                "hr": row.get("heart_rate"),
                "cadence": row.get("cadence"),
                "power": row.get("power"),
                "altitude": altitude,
                "distance": row.get("distance"),
            })

    pts.sort(key=lambda p: p["timestamp"])
    return pts

# ------------------------------------------------------------
# FIT INTERPOLATION (timestamp-based)
# ------------------------------------------------------------

def interpolate_fit(points, fit_ts):
    lo, hi = 0, len(points) - 1

    if fit_ts <= points[0]["timestamp"]:
        return points[0]
    if fit_ts >= points[-1]["timestamp"]:
        return points[-1]

    while lo <= hi:
        mid = (lo + hi) // 2
        if points[mid]["timestamp"] < fit_ts:
            lo = mid + 1
        else:
            hi = mid - 1

    p0 = points[hi]
    p1 = points[lo]

    t0 = p0["timestamp"]
    t1 = p1["timestamp"]

    if t1 == t0:
        return p0

    alpha = (fit_ts - t0).total_seconds() / (t1 - t0).total_seconds()

    def lerp(a, b):
        if a is None or b is None:
            return None
        return a + (b - a) * alpha

    mt0 = p0.get("moving_time")
    mt1 = p1.get("moving_time")
    if mt0 is None or mt1 is None:
        moving_time = None
    else:
        moving_time = lerp(mt0, mt1)

    return {
        "timestamp": fit_ts,
        "speed":   lerp(p0["speed"],   p1["speed"]),
        "hr":      lerp(p0["hr"],      p1["hr"]),
        "cadence": lerp(p0["cadence"], p1["cadence"]),
        "power":   lerp(p0["power"],   p1["power"]),
        "altitude":lerp(p0["altitude"],p1["altitude"]),
        "distance":lerp(p0["distance"],p1["distance"]),
        "moving_time": moving_time,
    }

# ------------------------------------------------------------
# MOVING TIME + NORMALIZATION
# ------------------------------------------------------------

def postprocess_points(points):
    # Normalize distance
    first_dist = points[0]["distance"] or 0.0
    for p in points:
        if p["distance"] is not None:
            p["distance"] = max(0.0, p["distance"] - first_dist)

    # Compute moving time
    moving_time = 0.0
    last = points[0]
    points[0]["moving_time"] = 0.0

    for p in points[1:]:
        dt = (p["timestamp"] - last["timestamp"]).total_seconds()
        spd = p["speed"]
        if spd is not None and spd > 0.5:
            moving_time += dt
        p["moving_time"] = moving_time
        last = p

# ------------------------------------------------------------
# ASS SUBTITLE GENERATION
# ------------------------------------------------------------

def ass_time(t):
    td = timedelta(seconds=float(t))
    total_ms = int(td.total_seconds() * 1000)
    h = total_ms // 3600000
    m = (total_ms // 60000) % 60
    s = (total_ms // 1000) % 60
    cs = (total_ms % 1000) // 10
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

def generate_ass(points, duration, ass_path: Path, groups):
    max_t = duration

    header = "[Script Info]\n" + r"""ScriptType: v4.00+
PlayResX: 3840
PlayResY: 2160
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HUD,Roboto Medium,80,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,0,7,40,40,40,1
Style: HUDBG,Roboto Medium,60,&H00000000,&H00000000,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: HUDBOX,Roboto Medium,60,&H00000000,&H00000000,&H00000000,&H60000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text

; ============================================================
; BOTTOM BACKGROUND BAR
; ============================================================
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBG,,0,0,0,,{\p1\c&H000000&\alpha&H80&}m 0 1900 l 3840 1900 l 3840 2160 l 0 2160 l 0 1900

; ============================================================
; TOP ROW BOXES
; ============================================================

; DISTANCE box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 280 240 l 820 240 b 860 240 860 240 860 280 l 860 460 b 860 500 860 500 820 500 l 280 500 b 240 500 240 500 240 460 l 240 280 b 240 240 240 240 280 240

; ELEVATION box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 3020 240 l 3560 240 b 3600 240 3600 240 3600 280 l 3600 460 b 3600 500 3600 500 3560 500 l 3020 500 b 2980 500 2980 500 2980 460 l 2980 280 b 2980 240 2980 240 3020 240

; MOVING TIME box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 280 540 l 820 540 b 860 540 860 540 860 580 l 860 760 b 860 800 860 800 820 800 l 280 800 b 240 800 240 800 240 760 l 240 580 b 240 540 240 540 280 540

; ============================================================
; BOTTOM ROW BOXES
; ============================================================

; SPEED box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 280 1900 l 820 1900 b 860 1900 860 1900 860 1940 l 860 2140 b 860 2180 860 2180 820 2180 l 280 2180 b 240 2180 240 2180 240 2140 l 240 1940 b 240 1900 240 1900 280 1900

; HR box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 1193 1900 l 1733 1900 b 1773 1900 1773 1900 1773 1940 l 1773 2140 b 1773 2180 1773 2180 1733 2180 l 1193 2180 b 1153 2180 1153 2180 1153 2140 l 1153 1940 b 1153 1900 1153 1900 1193 1900

; POWER box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 2106 1900 l 2646 1900 b 2686 1900 2686 1900 2686 1940 l 2686 2140 b 2686 2180 2686 2180 2646 2180 l 2106 2180 b 2066 2180 2066 2180 2066 2140 l 2066 1940 b 2066 1900 2066 1900 2106 1900

; CADENCE box
Dialogue: 0,0:00:00.00,9:59:59.00,HUDBOX,,0,0,0,,{\p1\c&H000000&\alpha&H60&}
m 3019 1900 l 3559 1900 b 3599 1900 3599 1900 3599 1940 l 3599 2140 b 3599 2180 3599 2180 3559 2180 l 3019 2180 b 2979 2180 2979 2180 2979 2140 l 2979 1940 b 2979 1900 2979 1900 3019 1900
"""

    lines = [header]

    def tele_at(t):
        fit_ts = map_video_to_fit(t, groups)
        return interpolate_fit(points, fit_ts)

    for frame in range(int(max_t * FPS)):
        t0 = frame / FPS
        t1 = (frame + 1) / FPS

        tele = tele_at(t0)

        speed_mps = tele["speed"]
        speed_mph = None if speed_mps is None else speed_mps * 2.23694
        hr = tele["hr"]
        cad = tele["cadence"]
        power = tele["power"]
        dist_m = tele["distance"]
        elev_m = tele["altitude"]
        moving_time = tele["moving_time"]

        dist_miles = None if dist_m is None else dist_m * 0.000621371
        elev_ft = None if elev_m is None else elev_m * 3.28084

        def fmt(val, fmt_str, default="N/A"):
            return default if val is None else fmt_str.format(val)

        txt_speed   = fmt(speed_mph, "{:4.1f} mph")
        txt_hr      = fmt(hr,        "{:3.0f} bpm")
        txt_power   = fmt(power,     "{:4.0f} W")
        txt_cad     = fmt(cad,       "{:3.0f} rpm")
        txt_dist    = fmt(dist_miles,"{:5.2f} mi")
        txt_elev    = fmt(elev_ft,   "{:5.0f} ft")
        txt_mtime   = "Elapsed: " + ("N/A" if moving_time is None else str(timedelta(seconds=int(moving_time))))

        def dlg(text, pos, icon):
            x, y = pos
            return (
                f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},HUD,,0,0,0,,"
                f"{{\\pos({x},{y})}}{icon}  {text}\n"
            )

        lines.append(dlg(txt_speed,   POS_SPEED,   ICON_SPEED))
        lines.append(dlg(txt_hr,      POS_HR,      ICON_HR))
        lines.append(dlg(txt_power,   POS_POWER,   ICON_POWER))
        lines.append(dlg(txt_cad,     POS_CAD,     ICON_CAD))
        lines.append(dlg(txt_dist,    POS_DIST,    ICON_DIST))
        lines.append(dlg(txt_elev,    POS_ELEV,    ICON_ELEV))
        lines.append(dlg(txt_mtime,   POS_MOVTIME, ICON_TIME))

    ass_text = "".join(lines)

    # ⭐ Strip BOM + any accidental leading whitespace/newlines
    while ass_text and ass_text[0] in ("\ufeff", "\n", "\r", "\t", " "):
        ass_text = ass_text[1:]

    with open(ass_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(ass_text)
    with open(ass_path, "rb") as f:
        print(f.read(16))
    with open("hud_overlay.ass", "rb") as f:
        print(f.read(32))

# ------------------------------------------------------------
# VIDEO DURATION
# ------------------------------------------------------------

def get_video_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())

# ------------------------------------------------------------
# MAIN PIPELINE
# ------------------------------------------------------------

def main(video_path: Path, fit_path: Path, json_path: Path, output_mp4: Path):
    print("Loading FIT telemetry…")
    raw_points = load_fit(fit_path)
    if not raw_points:
        raise SystemExit("No telemetry points found in FIT file.")

    print("Loading chapter metadata…")
    with open(json_path, "r") as f:
        chapter_meta = json.load(f)

    print("Loading sync markers…")
    marker_path = find_sync_markers()

    with marker_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # NEW FORMAT
    if isinstance(data, dict):
        saved_video = data.get("video_file")
        markers = data.get("markers", [])

        if saved_video != video_path.name:
            print(f"Sync markers belong to different video ({saved_video}), ignoring.")
            sync_markers = []
        else:
            sync_markers = markers

    # OLD FORMAT (raw list)
    elif isinstance(data, list):
        sync_markers = data

    else:
        print("Unknown sync marker format, ignoring.")
        sync_markers = []

    print("Building GoPro group map…")
    groups = build_group_map(chapter_meta, sync_markers)

    print("Normalizing FIT data…")
    postprocess_points(raw_points)

    duration = get_video_duration(video_path)

    print("Generating ASS HUD overlay…")
    ass_path = (OVERLAY_DIR / "hud_overlay.ass").resolve()
    generate_ass(raw_points, duration, ass_path, groups)

    if PREVIEW_SECONDS is not None:
        print(f"PREVIEW MODE: encoding first {PREVIEW_SECONDS} seconds")
        t_limit = PREVIEW_SECONDS
        use_ass = False
    else:
        t_limit = duration
        use_ass = True

    print("Starting ffmpeg…")
    ass_path = (OVERLAY_DIR / ASS_FILE)
    #vf_arg = f"subtitles=filename={ass_path.as_posix()}"
    #vf_arg = "".join(vf_arg.splitlines())
    #print("ASS FILE CONTENTS:\n", ass_path.read_text(encoding="utf-8")[:200])

    if use_ass:
        ffmpeg_cmd = [
            r"D:\Users\dylix\source\repos\GoPro\Overlay\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe",
            "-y",
            "-progress", "pipe:1",
            "-nostats",
            "-i", str(video_path),
            "-vf", f"subtitles={ass_path.as_posix().replace(':', '\\\\:')}",
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-b:v", "40M",
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-t", str(t_limit),
            str(output_mp4),
        ]
    else:
        ffmpeg_cmd = [
            r"D:\Users\dylix\source\repos\GoPro\Overlay\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe",
            "-y",
            "-progress", "pipe:1",
            "-nostats",
            "-i", str(video_path),
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-b:v", "40M",
            "-an",
            "-movflags", "+faststart",
            "-t", str(t_limit),
            str(output_mp4),
        ]
    #print("ARGV:", ffmpeg_cmd)

    #print(" ".join(ffmpeg_cmd))

    # ------------------------------------------------------------
    # REAL-TIME FFMPEG PROGRESS OUTPUT FOR GUI
    # ------------------------------------------------------------
    process = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # ffmpeg gets its own group
    )

    #print("OVERLAY_DIR:", OVERLAY_DIR)
    #print("PID FILE PATH:", OVERLAY_DIR / "ffmpeg_pid.json")
    #print("DIR EXISTS:", (OVERLAY_DIR).exists())
    #print("CWD:", os.getcwd())
    # Write PID immediately
    PID_FILE.write_text(json.dumps({"pid": process.pid}))

    for line in process.stdout:
        line = line.strip()
        print(line)          # GUI sees this live
        sys.stdout.flush()   # CRITICAL for real-time updates

    process.wait()

    print("Done. Final MP4 written to", output_mp4)


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------

if __name__ == "__main__":
    base_video = Path(find_base_video())
    fit_path = find_fit_file()
    json_path = find_json_file()
    output_mp4 = TODAY_DIR / generate_hashed_overlay_name(base_video).name
    PID_FILE = Path(__file__).resolve().parent / "ffmpeg_pid.json"

    print(f"Detected video: {base_video}")
    print(f"Detected FIT:   {fit_path}")
    print(f"Detected JSON:  {json_path}")
    print(f"Output MP4:     {output_mp4}")

    main(base_video, fit_path, json_path, output_mp4)
