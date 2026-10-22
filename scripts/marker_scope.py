"""OBS sidecar markers restrict HUD scheduling on the original source timeline."""
import copy
import json
import math
from pathlib import Path

from common import identity

SUFFIX = ".apex-markers.json"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_scope(scope):
    if scope is None:
        return
    if not isinstance(scope, dict) or scope.get("schema") != 1:
        raise ValueError("Invalid marker scope")
    if not isinstance(scope.get("source"), dict) or not scope["source"].get("sha256"):
        raise ValueError("Marker scope must be bound to a video identity")
    ranges = scope.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("No marked ranges; refusing to fall back to a whole-video scan")
    previous = -1
    for interval in ranges:
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError("Invalid marked interval")
        a, b = interval
        if not finite(a) or not finite(b) or a < 0 or b <= a or a <= previous:
            raise ValueError("Marked ranges must be finite, sorted, merged and nonempty")
        previous = b


def assert_source(scope, source):
    validate_scope(scope)
    if scope and any(scope["source"].get(k) != source.get(k) for k in ("sha256", "size", "path")):
        raise ValueError("Markers belong to a different source; start a fresh marked scan")


def merge_ranges(ranges):
    merged = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def load_sidecar(filename, source_id):
    filename = Path(filename).resolve()
    data = json.loads(filename.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("OBS marker file must contain a JSON object")
    if data.get("schema") != "apex-obs-markers/v1":
        raise ValueError("Unknown OBS marker format")
    if data.get("status") != "complete":
        raise ValueError("OBS markers are unfinished or split-file recording was detected. Stop recording normally before clipping.")
    if Path(data.get("recording_path", "")).resolve() != Path(source_id["path"]).resolve():
        raise ValueError("Marker recording path does not match the input video. Keep the original video and marker together.")
    if data.get("clock") != "obs_output_frames":
        raise ValueError("Unsupported marker clock")
    lookback = data.get("lookback_seconds", 60)
    if not finite(lookback) or not 1 <= lookback <= 600:
        raise ValueError("Marker lookback must be 1..600 seconds")
    duration = data.get("duration_seconds")
    if not finite(duration) or duration <= 0:
        raise ValueError("Invalid marker recording duration")
    marks = data.get("markers")
    if not isinstance(marks, list) or not marks:
        raise ValueError("This recording has no marks; no marked scan will be started")
    times = []
    for mark in marks:
        t = mark.get("time_seconds") if isinstance(mark, dict) else None
        if not finite(t) or not 0 < t <= duration + .25:
            raise ValueError("Marker timestamp is outside the recorded timeline")
        times.append(round(t, 6))
    times = sorted(set(times))
    scope = {"schema": 1, "source": copy.deepcopy(source_id), "sidecar": identity(filename),
             "recorded_duration": duration, "lookback_seconds": lookback, "marker_times": times,
             "ranges": merge_ranges([[round(max(0, t-lookback), 6), t] for t in times])}
    validate_scope(scope)
    return scope


def check_duration(scope, media):
    if not scope:
        return
    # Frame count is an OBS clock, not an accuracy promise under encoder/render overload.
    # Reject measurable clock drift instead of silently selecting the wrong minute.
    if abs(scope["recorded_duration"] - media["duration"]) > max(2, 3/media["fps"]):
        raise ValueError("OBS marker clock differs from actual video duration by more than 2 seconds; inspect recording lag/remux before clipping")
    if scope["ranges"][-1][1] > media["duration"] + .25:
        raise ValueError("A marker lies beyond the video end")


def within(a, b, scope):
    return scope is None or any(x-1e-6 <= a and b <= y+1e-6 for x, y in scope["ranges"])


def select_scope(source_id, *, explicit=None, ignore=False, current=None, saved=None, saved_mode=None):
    """Persist the user's scope choice when resuming with an original ROI config."""
    if ignore:
        return None, "ignore"
    if explicit is not None:
        return load_sidecar(explicit, source_id), "marked"
    if saved_mode == "ignore":
        return None, "ignore"
    scope = current if current is not None else saved
    if scope is not None:
        assert_source(scope, source_id)
        selected = scope.get("sidecar", {}).get("path")
        # Refresh the originally selected file; never switch to an unrelated default.
        if selected and Path(selected).is_file():
            scope = load_sidecar(selected, source_id)
        return copy.deepcopy(scope), "marked"
    default = Path(source_id["path"] + SUFFIX)
    return (load_sidecar(default, source_id), "marked") if default.is_file() else (None, "auto")
