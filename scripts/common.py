"""Shared media, identity and validation helpers. No audio-based selection."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


def binary(name):
    value = os.environ.get(name.upper() + "_BINARY") or shutil.which(name)
    if not value:
        raise ValueError(f"Missing {name}; install it or set {name.upper()}_BINARY")
    return value


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"Command failed ({result.returncode}): {result.stderr[-2000:]}")
    return result


def probe(path):
    data = json.loads(run([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout)
    videos = [s for s in data["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("No video stream")
    v = videos[0]
    num, den = v.get("avg_frame_rate", "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0
    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    if duration <= 0 or fps <= 0:
        raise ValueError("Invalid video duration/frame rate")
    return {"width": v["width"], "height": v["height"], "fps": fps,
            "duration": duration, "audio": any(s["codec_type"] == "audio" for s in data["streams"]),
            "audio_tracks": sum(s["codec_type"] == "audio" for s in data["streams"])}


def identity(path):
    path = Path(path).resolve()
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while reading")
    return {"path": str(path), "size": after.st_size, "sha256": digest.hexdigest()}


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def job_snapshot(job):
    """Reuse initial source hash only while its path/size/mtime still match."""
    path = Path(job["source"]["path"]).resolve()
    st = path.stat()
    actual = {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if job.get("source_stat") != actual:
        raise ValueError("Source changed; rerun scan before local review")
    return job["source"]


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def expanded_start(original, fire_start, padding=3):
    return max(0, min(original, fire_start - padding)) if fire_start is not None else original


def roi_box(width, height, kind, override=None):
    # Ratios are fractions of source dimensions, rounded inside the frame.
    ratios = {"low": (.28, .55, .44, .28), "high": (.28, .02, .44, .20),
              "ammo": (.84, .88, .12, .11), "stats": (.74, .035, .20, .14)}
    x, y, w, h = override if override is not None else ratios[kind]
    if not all(math.isfinite(v) for v in (x, y, w, h)) or not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1.00001 and y + h <= 1.00001):
        raise ValueError("ROI must be normalized x y width height inside the frame")
    left, top = int(width * x), int(height * y)
    return left, top, min(width, left + max(2, int(width * w))), min(height, top + max(2, int(height * h)))


def media_check(path, source, duration):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing/empty output: {path}")
    actual = probe(path)
    if (actual["width"], actual["height"]) != (source["width"], source["height"]):
        raise ValueError("Output dimensions differ from source")
    if abs(actual["fps"] - source["fps"]) > max(.01, source["fps"] * .001):
        raise ValueError("Output frame rate differs from source")
    if actual["audio"] != source["audio"]:
        raise ValueError("Output audio stream presence differs from source")
    if "audio_tracks" in source and actual["audio_tracks"] != source["audio_tracks"]:
        raise ValueError("Output audio track count differs from source")
    if abs(actual["duration"] - duration) > max(.15, 2 / source["fps"]):
        raise ValueError(f"Unexpected duration: {actual['duration']} vs {duration}")
    run([binary("ffmpeg"), "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
    return actual
