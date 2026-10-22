#!/usr/bin/env python3
"""Persist actual page-review decisions. Never marks a page reviewed automatically."""
import argparse
import json
from pathlib import Path

from common import identity, save_json


def update(scan_path, pages=None, decisions=None, reviewer=None):
    scan = json.loads(Path(scan_path).read_text(encoding="utf-8"))
    if not scan.get("complete"):
        raise ValueError("Finish scanning before reviewing pages")
    path = Path(scan["review_state"])
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"cache_key": scan["cache_key"], "pages": {}}
    if state.get("cache_key") != scan["cache_key"]:
        raise ValueError("Review cache no longer matches source/config")
    by_id = {p["id"]: p for p in scan["pages"]}
    # A deleted/changed decision file or rendered page invalidates its prior review.
    for page_id, record in list(state["pages"].items()):
        try:
            valid = (identity(record["decisions"])["sha256"] == record["decisions_sha256"] and
                     identity(by_id[int(page_id)]["path"])["sha256"] == record["page_sha256"])
        except (OSError, KeyError):
            valid = False
        if not valid:
            del state["pages"][page_id]
    if pages:
        if not decisions or not reviewer or not reviewer.strip():
            raise ValueError("Actual reviewer and saved decisions JSON are required")
        decisions = str(Path(decisions).resolve())
        parsed = json.loads(Path(decisions).read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
            raise ValueError("decisions must be an object with events array (empty for negative pages)")
        for page_id in pages:
            if page_id not in by_id:
                raise ValueError(f"Unknown page {page_id}")
            state["pages"][str(page_id)] = {"reviewer": reviewer, "decisions": decisions,
                "decisions_sha256": identity(decisions)["sha256"], "page_sha256": identity(by_id[page_id]["path"])["sha256"]}
    save_json(path, state)
    return {"reviewed_pages": sorted(map(int, state["pages"])),
            "unread_pages": [i for i in by_id if str(i) not in state["pages"]], "state_path": str(path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", required=True)
    ap.add_argument("--pages", nargs="+", type=int)
    ap.add_argument("--decisions")
    ap.add_argument("--reviewer")
    args = ap.parse_args()
    print(json.dumps(update(args.scan, args.pages, args.decisions, args.reviewer), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"FAIL: {exc}")
