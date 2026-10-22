"""Local transport previews. Never changes evidence, decisions, sampling or video."""
import hashlib
import json
from pathlib import Path

from common import identity, roi_box, save_json

FORMAT = "apex-review-transport-v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _root(workdir):
    root = Path(workdir).resolve()
    if not root.is_dir():
        raise ValueError("Use an existing task directory")
    job_path = root / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8")) if job_path.is_file() else {}
    key = _digest([str(root), job.get("source", {}), FORMAT])
    return root, job, key


def _ledger(root):
    path = root / "review_transfer" / "seen.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"format": FORMAT, "seen": {}}


def prepare(workdir, images, region="page", detail=False, lossless=False):
    from PIL import Image
    from config import load_config
    root, job, job_key = _root(workdir)
    cache = root / "review_transfer"
    config = load_config(job.get("config_path"))
    ledger = _ledger(root)
    entries, pending, already_seen = [], {}, {}
    for value in images:
        source = Path(value).resolve()
        if not source.is_relative_to(root) or source.is_relative_to(cache):
            raise ValueError("Input must be original task images inside this workdir, not transport previews")
        original = identity(source)
        with Image.open(source) as opened:
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("Supply every frame separately; animated input is not flattened")
            opened.load()
            frame = opened.convert("RGB")
        if identity(source) != original:
            raise ValueError("Original image changed while preparing preview")
        if region == "event":
            pieces = [(name, roi_box(frame.width, frame.height, name, config["roi"][name])) for name in ("high", "low")]
        elif region in ("stats", "ammo"):
            pieces = [(region, roi_box(frame.width, frame.height, region, config["roi"][region]))]
        else:
            pieces = [(region, (0, 0, frame.width, frame.height))]
        views = []
        for name, box in pieces:
            settings = {"region": region, "view": name, "box": list(box), "quality": 94 if detail else 82,
                        "lossless": lossless, "context_only": region == "context", "format": FORMAT}
            key = _digest([job_key, original["sha256"], settings])
            folder = cache / key
            record_path = folder / "preview.json"
            record = None
            if record_path.is_file():
                candidate = json.loads(record_path.read_text(encoding="utf-8"))
                try:
                    if identity(candidate["preview"]["path"]) == candidate["preview"]:
                        record = candidate
                except OSError:
                    pass
            if record is None:
                folder.mkdir(parents=True, exist_ok=True)
                preview = frame.crop(box)
                if region == "context" and not lossless:
                    preview.thumbnail((960, 540), Image.Resampling.LANCZOS)
                output = folder / ("preview.png" if lossless else "preview.jpg")
                if lossless:
                    preview.save(output, format="PNG", optimize=True)
                else:
                    preview.save(output, format="JPEG", quality=settings["quality"], subsampling=0, optimize=True)
                # An already compressed page may be smaller. Reuse it without re-encoding.
                whole = box == (0, 0, frame.width, frame.height)
                if whole and preview.size == frame.size and output.stat().st_size >= original["size"]:
                    output = source
                record = {"format": FORMAT, "job_key": job_key, "key": key, "settings": settings,
                          "preview": identity(output), "dimensions": list(preview.size)}
                save_json(record_path, record)
            seen = ledger.get("seen", {}).get(key, {})
            valid_seen = (seen.get("job_key") == job_key and seen.get("preview_sha256") == record["preview"]["sha256"])
            view = {**record, "seen": bool(valid_seen), "seen_by": seen.get("reviewer") if valid_seen else None}
            views.append(view)
            (already_seen if valid_seen else pending)[key] = record["preview"]["path"]
        entries.append({"original": original, "views": views})
    batch = _digest([job_key, [(e["original"]["path"], [v["key"] for v in e["views"]]) for e in entries]])
    manifest = cache / ("batch_" + batch[:20] + ".json")
    unique_views = {v["key"]: v for e in entries for v in e["views"]}
    source_bytes = sum(e["original"]["size"] for e in entries)
    preview_bytes = sum(v["preview"]["size"] for v in unique_views.values())
    data = {"format": FORMAT, "job_key": job_key, "region": region, "entries": entries,
            "original_file_bytes": source_bytes, "unique_preview_file_bytes": preview_bytes,
            "note": "Local file sizes only, not measured network traffic. Evidence paths remain the originals. A cached preview is not a reviewed image."}
    save_json(manifest, data)
    return {"manifest": str(manifest), "input_images": len(entries), "unique_views": len(unique_views),
            "pending_views": len(pending), "already_seen_views": len(already_seen),
            "next_paths": list(pending.values())[:12], "original_file_bytes": source_bytes,
            "unique_preview_file_bytes": preview_bytes,
            "note": "View all pending images; acknowledge only paths actually inspected. Do not use previews as formal evidence."}


def acknowledge(workdir, manifest, paths, reviewer):
    root, job, job_key = _root(workdir)
    manifest = Path(manifest).resolve()
    cache = root / "review_transfer"
    if not manifest.is_relative_to(cache):
        raise ValueError("Use a preview manifest from this task")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT or data.get("job_key") != job_key:
        raise ValueError("Preview manifest belongs to another task")
    if not reviewer or not reviewer.strip():
        raise ValueError("An actual visual reviewer is required")
    selected = {str(Path(p).resolve()) for p in paths}
    allowed = {v["preview"]["path"] for e in data["entries"] for v in e["views"]}
    if not selected or not selected <= allowed:
        raise ValueError("Acknowledge only prepared image paths actually viewed")
    ledger = _ledger(root)
    added = set()
    for entry in data["entries"]:
        if not any(v["preview"]["path"] in selected for v in entry["views"]):
            continue
        if identity(entry["original"]["path"]) != entry["original"]:
            raise ValueError("Original image changed; prepare and inspect it again")
        for view in entry["views"]:
            if view["preview"]["path"] not in selected:
                continue
            if identity(view["preview"]["path"]) != view["preview"]:
                raise ValueError("Preview changed; prepare and inspect it again")
            ledger["seen"][view["key"]] = {"job_key": job_key, "preview_sha256": view["preview"]["sha256"],
                                           "reviewer": reviewer.strip()}
            added.add(view["key"])
    save_json(cache / "seen.json", ledger)
    return {"acknowledged_views": len(added), "note": "Transport receipt only; no HUD, event, page, ammo or interval approval was changed."}


def add_commands(subparsers):
    p = subparsers.add_parser("preview", help="Prepare small local images for visual review; original evidence is preserved")
    p.add_argument("--workdir", required=True)
    p.add_argument("--images", nargs="+", required=True)
    p.add_argument("--region", choices=("page", "stats", "ammo", "event", "context", "full"), default="page")
    p.add_argument("--detail", action="store_true", help="Higher JPEG quality at native text resolution")
    p.add_argument("--lossless", action="store_true", help="Lossless crop or original-resolution fallback")
    p = subparsers.add_parser("preview-seen", help="Record images actually inspected; does not approve semantic review")
    p.add_argument("--workdir", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--reviewer", required=True)


def execute(args):
    if args.command == "preview":
        return prepare(args.workdir, args.images, args.region, args.detail, args.lossless)
    return acknowledge(args.workdir, args.manifest, args.paths, args.reviewer)
