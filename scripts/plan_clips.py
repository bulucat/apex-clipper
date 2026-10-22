#!/usr/bin/env python3
"""Plan confirmed kills: <=7s between adjacent anchors chains; last anchor +2s tail."""
import argparse
import json
from pathlib import Path

from common import expanded_start, number, save_json, identity
from config import load_config, policy_from_config, validate_policy

POST_ROLL = 2.0
CHAIN_GAP = 7.0


def same_view(a, b):
    return (a["own_view_start"], a["own_view_end"]) == (b["own_view_start"], b["own_view_end"])


def covers(events, start, end):
    cursor = start
    intervals = sorted((e["reviewed_start"], e["reviewed_end"]) for e in events
                       if isinstance(e.get("reviewed_start"), (int,float)) and isinstance(e.get("reviewed_end"), (int,float)))
    for a, b in intervals:
        if a <= cursor:
            cursor = max(cursor, b)
        if cursor >= end:
            return True
    return False


def plan_segments(events, duration, policy=None):
    policy = validate_policy(policy or {"post_roll":POST_ROLL,"chain_gap":CHAIN_GAP})
    confirmed = [e for e in events if e.get("class") == "A"]
    confirmed.sort(key=lambda e: e["anchor"])
    for e in confirmed:
        t = number(e.get("anchor"), "anchor")
        number(e.get("own_view_start"), "own_view_start")
        number(e.get("own_view_end"), "own_view_end")
        if e.get("spectate") is not False or not 0 <= e["own_view_start"] <= t < e["own_view_end"] <= duration:
            raise ValueError("Only confirmed own-view events may be planned")
        if e.get("event_type") not in ("double_banner", "weak_reward", "assist_elimination"):
            raise ValueError("Confirmed events require double_banner, weak_reward or assist_elimination type")
        if e.get("ammo") == "found":
            if not e["own_view_start"] <= number(e.get("fire_start"), "fire_start") <= t:
                raise ValueError("Fire time outside own-view event")
        elif e.get("ammo") != "unknown" or e.get("fire_start", 1) is not None:
            raise ValueError("Unconfirmed ammo must be unknown/null")
    if len({e["anchor"] for e in confirmed}) != len(confirmed):
        raise ValueError("Duplicate anchors: resolve banner persistence vs actual new kill first")
    groups = []
    for event in confirmed:
        if groups and same_view(groups[-1][-1], event) and event["anchor"] - groups[-1][-1]["anchor"] <= policy["chain_gap"]:
            groups[-1].append(event)
        else:
            groups.append([event])
    segments = []
    for group in groups:
        first, last = group[0], group[-1]
        anchors = [e["anchor"] for e in group]
        original_start = max(first["own_view_start"], first["anchor"] - policy["pre_roll"], 0)
        end = min(last["anchor"] + policy["post_roll"], last["own_view_end"], duration)
        fires = [number(e["fire_start"], "fire_start") for e in group if e.get("ammo") == "found"]
        fire = min(fires) if fires else None
        # Separate >7s events stay separate even if old gunfire makes countback windows overlap.
        floor = segments[-1]["end"] if segments else 0
        start = max(first["own_view_start"], floor, expanded_start(original_start, fire,policy["fire_padding"]))
        if start > anchors[0]:
            raise ValueError("Conflicting own-view boundaries or overlap would remove an anchor")
        types = {e["event_type"] for e in group}
        evidence = "; ".join(e.get("evidence", "") for e in group)
        if floor > expanded_start(original_start, fire,policy["fire_padding"]):
            evidence += "; previous separate clip overlaps countback: start trimmed at previous end, no replay"
        reviewed = covers(group, start, end)
        segment = {"class": "A", "spectate": False, "event_type": next(iter(types)) if len(types) == 1 else "mixed",
                   "event_types": sorted(types), "anchor": anchors[0], "anchors": anchors, "start": start, "end": end,
                   "original_start": original_start, "original_end": end, "start_floor": floor,
                   "own_view_start": first["own_view_start"], "own_view_end": first["own_view_end"],
                   "fire_start": fire, "ammo": "found" if fire is not None else "unknown", "evidence": evidence,
                   "evidence_frames": list(dict.fromkeys(p for e in group for p in e.get("evidence_frames", []))),
                   "reviewed_start": start if reviewed else None, "reviewed_end": end if reviewed else None,
                   "review_required": not reviewed}
        segments.append(segment)
    return segments


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", required=True, help="Object with source, coverage, duration, events")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--config")
    args = ap.parse_args()
    data = json.loads(Path(args.events).read_text(encoding="utf-8"))
    config=load_config(args.config)
    from quality_guard import enforce
    work=Path(data["coverage"]["scan_index"]).parent
    quality=enforce(work,config,data)
    from config import digest
    policy = policy_from_config(config)
    segments = plan_segments(data["events"], number(data["duration"], "duration"),policy)
    result = {"schema_version": "2.4", "source": data["source"], "coverage": data["coverage"],
              "config_digest":digest(config),"events_identity":identity(args.events),
              "delivery_scope":quality["delivery_scope"],
              "policy": policy, "segments": segments}
    save_json(args.output, result)
    print(f"PLANNED: {len(segments)} clips; {sum(s['review_required'] for s in segments)} need final interval review")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"FAIL: {exc}")
