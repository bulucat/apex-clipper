#!/usr/bin/env python3
"""Cached local ammo OCR, compact burst witnesses; numeric decreases are hints only."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image

from common import binary, identity, probe, roi_box, run, save_json
from vision import LocalOCR, OCR_VERSION, glyph_signature


def ammo_value(ocr):
    choices = []
    for line in ocr.get("lines", []):
        text = line["text"].strip()
        if not re.fullmatch(r"\d{1,3}", text):
            continue
        points = np.asarray(line.get("box") or [])
        # Magazine digits are taller; area incorrectly favors wide reserve numbers.
        area = float(np.ptp(points[:, 1])) if len(points) else 0
        choices.append((area, int(text), line["score"]))
    if not choices:
        return None
    tallest = max(choices)
    # An uncertain magazine must stay unknown, never fall through to the reserve line.
    return tallest[1] if tallest[2] >= .9 else None


def bursts(trace):
    runs = []
    for before, after in zip(trace, trace[1:]):
        a, b = before["value"], after["value"]
        if a is None or b is None:
            continue
        if b < a:
            if runs and before["time"] <= runs[-1]["last_time"] + .001:
                runs[-1]["last_time"] = after["time"]
            else:
                runs.append({"first_time": before["time"], "observed_change": after["time"], "last_time": after["time"],
                             "before": before["path"], "after": after["path"], "from": a, "to": b})
    return runs


def analyze(source, start, end, directory, fps=2, ammo_roi=None, backend="auto", verbose=False, source_snapshot=None):
    media, source_id = probe(source), source_snapshot or identity(source)
    start, end = max(0, start), min(end, media["duration"])
    if not 0 <= start <= end or not 0 < fps <= 240:
        raise ValueError("Invalid ammo interval/fps")
    config = {"start": start, "end": end, "fps": fps, "roi": ammo_roi, "backend": backend, "version": "2.6.2", "ocr_revision": OCR_VERSION}
    key = hashlib.sha256(json.dumps([source_id["sha256"], config], sort_keys=True).encode()).hexdigest()[:16]
    root = Path(directory).resolve() / key
    root.mkdir(parents=True, exist_ok=True)
    output = root / "ammo.json"
    if output.is_file():
        data = json.loads(output.read_text(encoding="utf-8"))
        if not data.get("ocr_error") and all(Path(t["path"]).is_file() for t in data["trace"]):
            print(f"AMMO CACHE: {output}")
            data["analysis_path"] = str(output)
            return data
    x, y, right, bottom = roi_box(media["width"], media["height"], "ammo", ammo_roi)
    # Temporary extraction is bounded by the 15/20s backtrack window, persistent ROI evidence is cached.
    with tempfile.TemporaryDirectory(prefix="apex_ammo_") as tmp:
        run([binary("ffmpeg"), "-y", "-v", "error", "-ss", str(start), "-t", str(max(end-start,1/fps)), "-i", str(source),
             "-an", "-vf", f"tpad=stop_mode=clone:stop_duration={1/fps},fps={fps}:start_time=0:round=up,crop={right-x}:{bottom-y}:{x}:{y}",
             "-frames:v", str(max(1,math.ceil((end-start)*fps-.00001))),
             str(Path(tmp) / "%06d.png")])
        ocr = LocalOCR(root / "ocr", backend)
        trace, previous, last_key, last_result = [], None, None, None
        for n, path in enumerate(sorted(Path(tmp).glob("*.png"))):
            with Image.open(path) as handle:
                image = handle.convert("RGB")
            signature = glyph_signature(image)
            result = last_result if signature == last_key else ocr.read(image, "ammo")
            last_key, last_result = signature, result
            target = root / f"ammo_{n:05d}.png"
            image.save(target)
            gray = np.asarray(image.convert("L"), dtype=np.int16)
            diff = float(np.percentile(np.abs(gray-previous), 99.5)) if previous is not None else 0
            record = {"time": round(start+n/fps, 6), "path": str(target), "value": ammo_value(result),
                      "ocr": result, "diff": diff}
            trace.append(record)
            previous = gray
        if len(trace) < (1 if start==end else 2):
            raise ValueError("Too few ammo frames; review source manually")
    runs = bursts(trace)
    data = {"source": source_id, "config": config, "trace": trace, "decrease_runs": runs,
            "fire_start_hint": None, "candidate_fire_start": runs[0]["first_time"] if runs else None,
            "requires_confirmation": True, "ocr_error": ocr.error,
            "instructions": "Compare selected weapon silhouette AND ammo-type icon/color, not weapon name alone. Confirm SAME weapon and magazine decrease or actual muzzle/hit evidence before setting fire_start. Switch/reserve changes are not fire; otherwise unknown/null."}
    data["analysis_path"] = str(output)
    save_json(output, data)
    if verbose:
        for item in trace:
            print(f"{item['time']:.3f}s ammo={item['value']} diff={item['diff']:.1f}")
    print(f"AMMO: {len(trace)} frames, {len(runs)} tentative bursts, {ocr.calls} OCR calls; result={output}; HINTS ONLY")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--outdir", default="exports/full/audit/ammo_cache")
    ap.add_argument("--ammo-roi", nargs=4, type=float)
    ap.add_argument("--ocr", choices=("auto", "required", "off"), default="auto")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    analyze(args.source, args.start, args.end, args.outdir, args.fps, args.ammo_roi, args.ocr, args.verbose)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"FAIL: {exc}")
