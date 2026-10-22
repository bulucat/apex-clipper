#!/usr/bin/env python3
"""V2.4 full-video local scan, 2fps economy review, exact dedup and resumable OCR.

No audio/scene/Top K gating. Proposals are NOT verified kills. Economy mode sends
visual proposals plus periodic negative audits to the model. Full mode retains
every sampled frame. All sampled frames remain catalogued in either mode.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image, ImageDraw

from common import binary, identity, probe, roi_box, save_json
from vision import LocalOCR, OCR_VERSION, glyph_signature, representatives, visual_proposal

VERSION = "2.4"


def read_frame(pipe, size):
    data = bytearray()
    while len(data) < size:
        block = pipe.read(size - len(data))
        if not block:
            break
        data.extend(block)
    if data and len(data) != size:
        raise ValueError("Truncated raw video frame")
    return bytes(data)


def make_card(low, high, timestamp, label=""):
    low, high = low.copy(), high.copy()
    low.thumbnail((560, 220))
    high.thumbnail((560, 110))
    card = Image.new("RGB", (564, 360), "#151515")
    ImageDraw.Draw(card).text((4, 4), f"SOURCE {timestamp:.3f}s  {label}", fill="white")
    card.paste(high, (2, 24))
    card.paste(low, (2, 138))
    return card


def scan(src, output, directory, fps=2, start=0, end=None, mode="economy", audit_seconds=15,
         low_roi=None, high_roi=None, ocr_backend="auto", verbose=False, checkpoint_frames=40,
         stop_after=None, source_snapshot=None):
    begun = time.monotonic()
    info, source = probe(src), source_snapshot or identity(src)
    end = info["duration"] if end is None else min(end, info["duration"])
    if not (math.isfinite(fps) and 0 < fps <= 240 and 0 <= start < end and math.isfinite(audit_seconds) and audit_seconds > 0):
        raise ValueError("Invalid scan fps, interval or audit interval")
    if mode not in ("economy", "full"):
        raise ValueError("Invalid review mode")
    width, height = 1280, max(2, round(info["height"] * 1280 / info["width"]))
    low_box, high_box = roi_box(width, height, "low", low_roi), roi_box(width, height, "high", high_roi)
    config = {"version": VERSION, "fps": fps, "start": start, "end": end, "mode": mode,
              "audit_seconds": audit_seconds, "low_roi": low_roi, "high_roi": high_roi,
              "ocr_backend": ocr_backend, "ocr_revision": OCR_VERSION,
              "dedup": "exact-glyph-mask-first-middle-last-v1", "render": "tight-last-page-v2"}
    key = hashlib.sha256(json.dumps({"source": source["sha256"], "config": config}, sort_keys=True).encode()).hexdigest()
    run_dir = Path(directory).resolve() / key[:16]
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = run_dir / "checkpoint.json"
    frames = []
    if checkpoint.is_file():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        if cached.get("cache_key") != key:
            raise ValueError("Invalid checkpoint identity")
        frames = cached.get("frames", [])
        if any(f.get("seq") != n for n, f in enumerate(frames)):
            raise ValueError("Checkpoint has missing frame records")
        for record in frames:
            if record.get("selected") and not all(Path(record[k]).is_file() for k in ("low_path", "high_path")):
                raise ValueError("Checkpoint image missing; use a fresh output root")
        retry_ocr = cached.get("ocr_error") and ocr_backend != "off" and importlib.util.find_spec("rapidocr_onnxruntime") is not None
        if cached.get("complete") is True and not retry_ocr and all(Path(p["path"]).is_file() for p in cached.get("pages", [])):
            save_json(output, cached)
            print(f"CACHE: {len(frames)} scanned frames, {len(cached['pages'])} review pages; {cached['review_state']}")
            return cached
    index = {"schema_version": VERSION, "source": source, "media": info, "config": config,
             "cache_key": key, "mode": "full_scan" if start == 0 and end == info["duration"] else "local_review",
             "review_scope": "all_samples" if mode == "full" else "candidates_and_audits",
             "start": start, "end": end, "fps": fps, "complete": False, "frames": frames,
             "pages": [], "review_state": str(run_dir / "review_state.json")}
    save_json(output, index)
    count, initial_count = len(frames), len(frames)
    expected_count = math.ceil((end-start) * fps - .00001)
    progress_path=run_dir/"progress.json"
    def progress(stage,done,total):
        percent=round(done/max(1,total)*100,1)
        save_json(progress_path,{"stage":stage,"done":done,"total":total,"percent":percent,
                                 "seconds":round(time.monotonic()-begun,2)})
    progress("scan",count,expected_count)
    last_notice=time.monotonic()
    resume_time = start + count / fps
    if resume_time < end:
        args = [binary("ffmpeg"), "-v", "error", "-ss", str(resume_time), "-t", str(end-resume_time), "-i", str(src),
                "-an", "-sn", "-dn", "-vf",
                f"tpad=stop_mode=clone:stop_duration={1/fps},fps={fps}:start_time=0:round=up,scale={width}:{height}",
                "-frames:v", str(expected_count-count),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
            try:
                while True:
                    raw = read_frame(process.stdout, width * height * 3)
                    if not raw:
                        break
                    timestamp = start + count / fps
                    if timestamp >= end:
                        continue
                    frame = Image.frombytes("RGB", (width, height), raw)
                    low, high = frame.crop(low_box), frame.crop(high_box)
                    gate = visual_proposal(low)
                    audit = count % max(1, round(audit_seconds * fps)) == 0
                    selected = mode == "full" or gate["candidate"] or audit
                    record = {"seq": count, "time": round(timestamp, 6), "gate": gate,
                              "audit": audit, "selected": selected}
                    if selected:
                        record["signature"] = glyph_signature(low) + ":" + glyph_signature(high)
                        for name, image in (("low", low), ("high", high)):
                            path = run_dir / f"{name}_{count:07d}.png"
                            image.save(path)
                            record[name + "_path"] = str(path)
                    frames.append(record)
                    count += 1
                    if count % checkpoint_frames == 0:
                        save_json(checkpoint, index)
                        progress("scan",count,expected_count)
                        if time.monotonic()-last_notice>=30:
                            print(f"PROGRESS: scan {count/expected_count:.0%}",flush=True)
                            last_notice=time.monotonic()
                        if verbose:
                            print(f"SCANNED {count} frames at {timestamp:.1f}s", flush=True)
                    if stop_after is not None and count - initial_count >= stop_after:
                        save_json(checkpoint, index)
                        save_json(output, index)
                        return index
                if process.wait():
                    errors.seek(0)
                    raise ValueError(errors.read().decode("utf-8", "replace")[-2000:])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                save_json(checkpoint, index)
    if count != expected_count:
        raise ValueError("Incomplete scan; checkpoint retained")
    selected = [r for r in frames if r["selected"]]
    chosen = selected if mode == "full" else representatives(selected)
    ocr = LocalOCR(run_dir / "ocr", ocr_backend)
    cards, records = [], []

    def flush():
        if not cards:
            return
        page_id = len(index["pages"]) + 1
        columns = min(3,len(cards))
        sheet = Image.new("RGB", (564 * columns, 360 * math.ceil(len(cards)/columns)), "#303030")
        for pos, card in enumerate(cards):
            sheet.paste(card, ((pos % columns) * 564, (pos // columns) * 360))
        path = run_dir / f"page_{page_id:05d}.jpg"
        sheet.save(path, quality=93)
        index["pages"].append({"id": page_id, "path": str(path), "frames": records.copy()})
        cards.clear()
        records.clear()

    last_signature, last_result = None, None
    print(f"STAGE: local OCR/review pages, {len(chosen)} representative frames",flush=True)
    for position,record in enumerate(chosen):
        with Image.open(record["low_path"]) as lo, Image.open(record["high_path"]) as hi:
            low, high = lo.convert("RGB"), hi.convert("RGB")
        if record["signature"] == last_signature:
            result = last_result
        else:
            result = {"low": ocr.read(low, "low"), "high": ocr.read(high, "high")}
            last_signature, last_result = record["signature"], result
        review = {k: v for k, v in record.items() if k != "gate"}
        review["ocr"] = result
        records.append(review)
        label = "AUDIT" if record["audit"] and not record["gate"]["candidate"] else "CANDIDATE"
        cards.append(make_card(low, high, record["time"], label))
        if len(cards) == 12:
            flush()
            progress("ocr_and_pages",position+1,len(chosen))
            if time.monotonic()-last_notice>=30:
                print(f"PROGRESS: local OCR {position+1}/{len(chosen)}",flush=True)
                last_notice=time.monotonic()
    flush()
    index.update(complete=True, frame_count=count, candidate_count=sum(r["gate"]["candidate"] for r in frames),
                 selected_count=len(selected), representative_count=len(chosen),
                 skipped_count=count - len(selected), audit_count=sum(r["audit"] for r in frames),
                 ocr_calls=ocr.calls, ocr_cache_hits=ocr.hits, ocr_error=ocr.error,
                 seconds=round(time.monotonic() - begun, 3), sampling_interval=1 / fps)
    save_json(checkpoint, index)
    save_json(output, index)
    progress("complete",len(chosen),len(chosen))
    summary = {k: index[k] for k in ("frame_count", "candidate_count", "representative_count", "skipped_count", "ocr_calls", "seconds", "review_scope")}
    print("COMPLETE: " + json.dumps(summary) + f"; review {len(index['pages'])} pages; index={output}")
    if ocr.error:
        print("OCR unavailable/disabled: candidates preserved for visual review")
    return index


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-s", "--source", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--outdir")
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--mode", choices=("economy", "full"), default="economy")
    ap.add_argument("--audit-seconds", type=float, default=15)
    ap.add_argument("--ocr", choices=("auto", "required", "off"), default="auto")
    ap.add_argument("--low-roi", nargs=4, type=float)
    ap.add_argument("--high-roi", nargs=4, type=float)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    scan(args.source, args.output, args.outdir or str(Path(args.output).with_suffix("")) + "_cache",
         args.fps, args.start, args.end, args.mode, args.audit_seconds, args.low_roi,
         args.high_roi, args.ocr, args.verbose)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"FAIL: {exc}")
