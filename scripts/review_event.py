#!/usr/bin/env python3
"""Sparse original-resolution evidence first; --dense escalates to 12fps paired review."""
import argparse
import hashlib
import json
from pathlib import Path

from common import binary, identity, probe, roi_box, run, save_json
from detect_highlights import scan
from PIL import Image


def review(src, anchor, directory, dense=False, low_roi=None, high_roi=None,
           sparse_offset=.5,dense_fps=12,dense_radius=2,ocr_backend="auto",source_snapshot=None):
    media, source = probe(src), source_snapshot or identity(src)
    if not 0 <= anchor < media["duration"]:
        raise ValueError("Anchor outside source")
    config = {"anchor": anchor, "dense": dense, "low_roi": low_roi, "high_roi": high_roi,
              "sparse_offset":sparse_offset,"dense_fps":dense_fps,"dense_radius":dense_radius,
              "ocr":ocr_backend,"version": "2.5"}
    key = hashlib.sha256(json.dumps([source["sha256"], config], sort_keys=True).encode()).hexdigest()[:16]
    root = Path(directory).resolve() / key
    root.mkdir(parents=True, exist_ok=True)
    output = root / "review.json"
    if output.is_file():
        cached = json.loads(output.read_text(encoding="utf-8"))
        paths = [p for frame in cached.get("frames", []) for p in frame["images"].values()]
        if paths and all(Path(p).is_file() for p in paths):
            print(f"REVIEW CACHE: {output}")
            return cached
    if dense:
        return scan(src, output, root / "dense", fps=dense_fps, start=max(0, anchor-dense_radius),
                    end=min(media["duration"], anchor+dense_radius), mode="full", low_roi=low_roi,
                    high_roi=high_roi,ocr_backend=ocr_backend,source_snapshot=source)
    times = sorted({max(0, anchor-sparse_offset), anchor, min(media["duration"]-1/media["fps"], anchor+sparse_offset)})
    frames = []
    for n, timestamp in enumerate(times):
        full = root / f"frame_{n}.png"
        run([binary("ffmpeg"), "-y", "-v", "error", "-ss", str(timestamp), "-i", str(src), "-frames:v", "1", str(full)])
        images = {"full": str(full)}
        with Image.open(full) as frame:
            for kind, custom in (("low", low_roi), ("high", high_roi)):
                target = root / f"{kind}_{n}.png"
                frame.crop(roi_box(frame.width, frame.height, kind, custom)).save(target)
                images[kind] = str(target)
        frames.append({"time": timestamp, "images": images})
    data = {"source": source, "config": config, "frames": frames, "stage": "sparse",
            "instructions": "Read paired low/high crops. Escalate --dense if pairing, onset or view boundary is unclear."}
    save_json(output, data)
    print(f"SPARSE REVIEW: {len(frames)} original-resolution frames; {output}")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-s", "--source", required=True)
    ap.add_argument("--anchor", type=float, required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--dense", action="store_true")
    ap.add_argument("--low-roi", nargs=4, type=float)
    ap.add_argument("--high-roi", nargs=4, type=float)
    args = ap.parse_args()
    review(args.source, args.anchor, args.outdir, args.dense, args.low_roi, args.high_roi)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"FAIL: {exc}")
