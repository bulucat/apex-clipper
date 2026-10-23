#!/usr/bin/env python3
"""Verified full-frame cuts with shared policy; defaults are 2s tail and 7s chaining."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from common import binary, expanded_start, identity, media_check, number, probe, run, save_json
from config import load_config, policy_from_config, validate_policy, apply_tools


def validate_manifest(data, source, media):
    if not isinstance(data, dict) or data.get("schema_version") != "2.4":
        raise ValueError("Require schema 2.4 manifest with explicit timing policy")
    policy = validate_policy(data.get("policy"))
    declared = data.get("source", {})
    if any(declared.get(k) != source[k] for k in ("size", "sha256")):
        raise ValueError("Manifest source does not match input video")
    coverage = data.get("coverage", {})
    scan_path = Path(coverage.get("scan_index", ""))
    if not scan_path.is_file():
        raise ValueError("Missing full-scan index")
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    if scan.get("complete") is not True or scan.get("mode") not in ("full_scan", "hud_prefiltered_scan"):
        raise ValueError("Selected scan mode must complete before delivery")
    if any(scan.get("source", {}).get(k) != source[k] for k in ("size", "sha256")):
        raise ValueError("Scan is for a different source")
    fps = number(scan.get("fps"), "scan fps")
    prefiltered=scan.get("mode")=="hud_prefiltered_scan"
    if fps < 2 or scan.get("start") != 0 or abs(number(scan.get("end"), "scan end") - media["duration"]) > .1:
        raise ValueError("Require >=2fps dense scan and declared source timeline")
    allowed=("hud_prefiltered_candidates_and_audits",) if prefiltered else ("all_samples", "candidates_and_audits")
    if scan.get("review_scope") not in allowed or coverage.get("review_scope") != scan["review_scope"]:
        raise ValueError("Declare actual economy/full or HUD/local review scope")
    pages = scan.get("pages", [])
    ids = [page.get("id") for page in pages]
    if (not ids and not prefiltered) or ids != list(range(1, len(ids) + 1)):
        raise ValueError("Missing or unordered scan pages")
    if any(not isinstance(page.get("path"), str) or not Path(page["path"]).is_file() for page in pages):
        raise ValueError("Scan page images must remain available for review")
    reviewed = coverage.get("reviewed_pages")
    if not isinstance(reviewed, list) or any(type(i) is not int for i in reviewed) or sorted(reviewed) != ids:
        raise ValueError("Every scan page must actually be visually reviewed once")
    if not isinstance(coverage.get("reviewer"), str) or not coverage["reviewer"].strip():
        raise ValueError("Missing visual reviewer")
    frames = scan.get("frames", [])
    if prefiltered:
        from hud_prefilter import validate_coverage
        validate_coverage(scan,media)
    else:
        if not frames or scan.get("frame_count") != len(frames) or abs(len(frames) - media["duration"] * fps) > 2:
            raise ValueError("Full-scan frame count is incomplete")
        for n, frame in enumerate(frames):
            if frame.get("seq") != n or abs(number(frame.get("time"), "frame time") - n / fps) > .00001:
                raise ValueError("Scan contains missing or reordered frame timestamps")
    selected = {f["seq"] for f in frames if f.get("selected")}
    represented = {seq for page in pages for f in page.get("frames", []) for seq in f.get("represented_sequences", [f["seq"]])}
    if selected != represented:
        raise ValueError("Review pages omit selected candidates/audits or contain foreign frame records")
    segments = data.get("segments")
    if not isinstance(segments, list):
        raise ValueError("segments must be an array")
    targets = []
    for n, seg in enumerate(segments):
        if not isinstance(seg, dict) or seg.get("class") not in ("A", "B", "C", "D"):
            raise ValueError(f"Invalid classification at segment {n}")
        if seg["class"] != "A":
            continue
        if seg.get("spectate") is not False:
            raise ValueError("A requires explicit spectate=false")
        if seg.get("review_required") is True:
            raise ValueError("Final interval review still required")
        for key in ("start", "end", "original_start", "original_end", "anchor",
                    "own_view_start", "own_view_end", "reviewed_start", "reviewed_end"):
            number(seg.get(key), key)
        start, end, anchor = seg["start"], seg["end"], seg["anchor"]
        if not 0 <= start <= anchor < end <= media["duration"] + .001:
            raise ValueError("Invalid A segment time range/anchor")
        if not 0 <= seg["own_view_start"] <= start < end <= seg["own_view_end"] <= media["duration"] + .001:
            raise ValueError("A crosses spectator/view boundary; split and re-audit")
        if not 0 <= seg["original_start"] <= anchor < seg["original_end"] <= media["duration"] + .001:
            raise ValueError("Invalid original cut range")
        if end > seg["original_end"] + .001:
            raise ValueError("Ammo review must not extend the endpoint")
        if not (0 <= seg["reviewed_start"] <= start and end <= seg["reviewed_end"] <= media["duration"] + .001):
            raise ValueError("Expanded interval has not been reviewed")
        anchors = seg.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError("A requires all anchors, including every multikill")
        for value in anchors:
            number(value, "anchors[]")
        if anchors != sorted(set(anchors)) or anchors[0] != anchor or not all(start <= t < end for t in anchors):
            raise ValueError("Invalid or missing multikill anchors")
        if any(b-a > policy["chain_gap"] for a, b in zip(anchors, anchors[1:])):
            raise ValueError("Kill gap exceeds configured chain_gap: split into separate clips")
        expected_end = min(anchors[-1] + policy["post_roll"], seg["own_view_end"], media["duration"])
        if abs(end - expected_end) > .001 or abs(seg["original_end"] - expected_end) > .001:
            raise ValueError("Clip must end at last kill + configured post_roll, capped by own-view/source end")
        if seg.get("event_type") not in ("double_banner", "weak_reward", "assist_elimination", "mixed"):
            raise ValueError("Missing visual event type")
        if seg.get("ammo") == "found":
            fire = number(seg.get("fire_start"), "fire_start")
            if not seg["own_view_start"] <= fire <= anchors[-1]:
                raise ValueError("Fire timestamp outside own-view event window")
        elif seg.get("ammo") == "unknown" and "fire_start" in seg and seg["fire_start"] is None:
            fire = None
        else:
            raise ValueError("ammo must be found with time, or unknown with fire_start=null")
        floor = number(seg.get("start_floor"), "start_floor")
        expected = max(seg["own_view_start"], floor, expanded_start(seg["original_start"], fire,policy["fire_padding"]))
        if abs(start - expected) > .001:
            raise ValueError("Incorrect extension: keep original start if it is earlier")
        if not isinstance(seg.get("evidence"), str) or not seg["evidence"].strip():
            raise ValueError("Missing visual evidence/reason")
        types=seg.get("event_types",[seg["event_type"]])
        if (not isinstance(types,list) or not types or any(kind not in ("double_banner","weak_reward","assist_elimination","mixed") for kind in types)
                or (seg["event_type"]!="mixed" and types!=[seg["event_type"]])
                or (seg["event_type"]=="mixed" and "mixed" not in types and len(set(types))<2)):
            raise ValueError("Invalid event_types")
        if "assist_elimination" in types and not (any(word in seg["evidence"] for word in ("助攻","Assist","assist")) and any(word in seg["evidence"] for word in ("消灭","消滅","Eliminat","eliminat"))):
            raise ValueError("Assist elimination requires explicit banner evidence")
        if ("weak_reward" in types or types==["mixed"]) and not any(word in seg["evidence"] for word in ("弱者奖励", "弱者獎勵", "Weaker Reward")):
            raise ValueError("Weak-reward events must explicitly name their banner in evidence")
        evidence = seg.get("evidence_frames")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(p, str) and Path(p).is_file() for p in evidence):
            raise ValueError("Keep readable evidence images for every A event")
        targets.append(seg)
    targets.sort(key=lambda s: s["start"])
    for n, seg in enumerate(targets):
        expected_floor = targets[n-1]["end"] if n else 0
        if abs(seg["start_floor"] - expected_floor) > .001:
            raise ValueError("start_floor must equal previous separate clip end (or 0 for first)")
    for previous, current in zip(targets, targets[1:]):
        if current["start"] < previous["end"] - .001:
            raise ValueError("A intervals overlap: merge within the same own-view interval, preserve anchors and re-audit")
        if (previous["own_view_start"], previous["own_view_end"]) == (current["own_view_start"], current["own_view_end"]) and current["anchor"] - previous["anchors"][-1] <= policy["chain_gap"]:
            raise ValueError("Adjacent kills within configured chain_gap in the same view must be connected")
    return targets


def cut_one(src, out, segment, source, media, ffmpeg, verbose=False):
    # Identity includes reviewed decision and source; an unrelated 50KB file is never a cache hit.
    signature = hashlib.sha256(json.dumps({"source": source["sha256"], "segment": segment,
        "encoding": "libx264-fast-crf18-all-audio-v2.7.1"}, sort_keys=True).encode()).hexdigest()
    receipt = out.with_suffix(".receipt.json")
    if out.exists() and receipt.exists():
        try:
            cached = json.loads(receipt.read_text(encoding="utf-8"))
            if cached.get("signature") == signature and cached.get("output_sha256") == identity(out)["sha256"]:
                if verbose:
                    print(f"[VERIFIED CACHE] {out}")
                return "cached"
        except (ValueError, OSError, KeyError):
            pass
    tmp = out.with_name(out.stem + ".partial.mp4")
    try:
        run([ffmpeg, "-y", "-v", "error", "-ss", str(segment["start"]), "-i", str(src),
             "-t", str(segment["end"] - segment["start"]), "-map", "0:v:0", "-map", "0:a?",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", str(tmp)])
        media_check(tmp, media, segment["end"] - segment["start"])
        os.replace(tmp, out)
        save_json(receipt, {"signature": signature, "output_sha256": identity(out)["sha256"], "segment": segment})
        if verbose:
            print(f"[OK] {out}")
        return "encoded"
    finally:
        if tmp.exists():
            tmp.unlink()


def concatenate(paths, output, media, duration, verbose=False):
    if not paths:
        raise ValueError("No A clips to concatenate")
    output = Path(output).resolve()
    if output in [p.resolve() for p in paths]:
        raise ValueError("Compilation path collides with a segment")
    output.parent.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(json.dumps({"clips": [identity(p)["sha256"] for p in paths],
        "fps": media["fps"], "encoding": "libx264-crf18-all-audio-v2.7.1"}, sort_keys=True).encode()).hexdigest()
    receipt = output.with_suffix(".receipt.json")
    if output.is_file() and receipt.is_file():
        try:
            cached = json.loads(receipt.read_text(encoding="utf-8"))
            if cached.get("signature") == signature and cached.get("output_sha256") == identity(output)["sha256"]:
                return "cached"
        except (OSError, ValueError):
            pass
    tmp = output.with_name(output.stem + ".partial.mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="apex_concat_") as work:
            filelist = Path(work) / "files.txt"
            # ffmpeg concat quoting: backslash/apostrophe escaped, use forward-slash paths.
            lines = ["file '" + p.resolve().as_posix().replace("'", "'\\''") + "'" for p in paths]
            filelist.write_text("\n".join(lines) + "\n", encoding="utf-8")
            run([binary("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(filelist), "-map", "0:v:0", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-r", str(media["fps"]),
                 "-fps_mode", "cfr", "-c:a", "aac", "-movflags", "+faststart", str(tmp)])
        media_check(tmp, media, duration)
        os.replace(tmp, output)
        save_json(receipt, {"signature": signature, "output_sha256": identity(output)["sha256"]})
    finally:
        if tmp.exists():
            tmp.unlink()
    if verbose:
        print(f"[COMPILATION OK] {output}")
    return "encoded"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--prefix", default="v1")
    ap.add_argument("--compilation", help="Optional compilation output; same-source clips only")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("a", nargs="?", type=int, default=0)
    ap.add_argument("b", nargs="?", type=int)
    args = ap.parse_args()
    config = load_config(args.config)
    apply_tools(config)
    if not re.fullmatch(r"[\w.-]+", args.prefix) or args.prefix in (".", ".."):
        raise ValueError("prefix must be a filename component")
    source, media = identity(args.src), probe(args.src)
    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if args.config and validate_policy(data.get("policy")) != policy_from_config(config):
        raise ValueError("Manifest policy differs from selected config; regenerate the plan")
    targets = validate_manifest(data, source, media)
    from quality_guard import enforce_manifest
    quality=enforce_manifest(data,config)
    print(f"DELIVERY SCOPE: {quality['delivery_scope']}; scanned={quality['scan_seconds']:.3f}s/{quality['source_seconds']:.3f}s; unresolved={quality['unresolved_seconds']:.3f}s")
    if args.a < 0 or (args.b is not None and args.b < args.a):
        raise ValueError("Invalid segment selection")
    if args.validate_only:
        print(f"VALID: {len(targets)} A clips; review scope={data['coverage']['review_scope']}")
        return
    selected = targets[args.a:args.b]
    if not selected:
        print("NO_A_EVENTS: no compilation created")
        return
    directory = Path(args.outdir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg, outputs, total, cache_hits = binary("ffmpeg"), [], 0, 0
    for i, segment in enumerate(selected, args.a + 1):
        start, end = segment["start"], segment["end"]
        out = directory / f"{args.prefix}_seg{i:02d}_{start:g}-{end:g}_FULL.mp4"
        cache_hits += cut_one(args.src, out, segment, source, media, ffmpeg, args.verbose) == "cached"
        outputs.append(out)
        total += end - start
    if args.compilation:
        status = concatenate(outputs, args.compilation, media, total, args.verbose)
        print(f"COMPILATION: {status}; {args.compilation}")
    print(f"DONE: {len(outputs)} verified A clips, {cache_hits} reused; {total:.3f}s total")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"FAIL: {exc}")
