"""Import and validate visual proof for the 150-damage admission gate."""
import json
import math
from pathlib import Path

from common import identity, save_json


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _finite(v):
    return type(v) in (int, float) and math.isfinite(v)


def _match_registry(work, source_sha256, duration):
    path = Path(work) / "match_registry.json"
    if not path.is_file():
        raise ValueError("Damage review requires the verified canonical match registry")
    from quality_guard import validate_registry
    return validate_registry(_read(path), source_sha256, duration)


def _damage_evidence_paths(review):
    by_role = {o.get("role"): o for o in review.get("observations", []) if isinstance(o, dict)}
    paths = [by_role[k]["path"] for k in ("before", "start", "end")]
    if review.get("damage_measurement", "exact") == "lower_bound":
        paths.append(review["bound_start"]["path"])
    return paths


def _validate_review(review, segment, source_sha256, matches, frame_tolerance):
    if (review.get("anchors") != segment.get("anchors") or review.get("segment_start") != segment.get("start")
            or review.get("segment_end") != segment.get("end")):
        raise ValueError("Damage review is not bound to these exact final anchors and cut points")
    if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
        raise ValueError("Damage review requires a reviewer and concrete reason")
    if review.get("source_sha256") != source_sha256:
        raise ValueError("Damage review is not bound to the current source")
    observations = review.get("observations")
    if not isinstance(observations, list) or len(observations) != 3:
        raise ValueError("Save direct source observations for before/start/end")
    by_role = {o.get("role"): o for o in observations if isinstance(o, dict)}
    if set(by_role) != {"before", "start", "end"}:
        raise ValueError("Damage observations must have unique before/start/end roles")
    before, start, end = (by_role[k] for k in ("before", "start", "end"))
    mode = review.get("damage_measurement", "exact")
    if mode not in ("exact", "lower_bound"):
        raise ValueError("Damage measurement must be exact or lower_bound")
    for obs in observations:
        if not _finite(obs.get("time")):
            raise ValueError("Every observation requires an exact source time")
        value = obs.get("damage")
        if not (type(value) is int and value >= 0):
            if not (mode == "lower_bound" and obs.get("role") in ("before", "start") and value is None):
                raise ValueError("Damage must be a nonnegative integer; unavailable start values remain null")
    if abs(start["time"] - segment["start"]) > 1e-6:
        raise ValueError("Start observation must be bound to the exact final segment start")
    if not start["time"] - 1.001 <= before["time"] < start["time"]:
        raise ValueError("Before observation must be within one second before the segment start")
    if abs(end["time"]-(segment["end"]-frame_tolerance)) > 1e-5:
        raise ValueError("End observation must be the last included frame at the exact segment end")
    checked = list(observations)
    base = start
    if mode == "lower_bound":
        base = review.get("bound_start")
        if (not isinstance(base, dict) or not _finite(base.get("time"))
                or not segment["start"] <= base["time"] <= end["time"]
                or type(base.get("damage")) is not int or base["damage"] < 0):
            raise ValueError("Lower-bound proof needs a directly readable damage counter inside this exact clip")
        checked.append(base)
    match_id = review.get("match_id")
    match = next((m for m in matches if m.get("match_id") == match_id), None)
    if not match:
        raise ValueError("Damage review requires a canonical own-match ID")
    for obs in checked:
        if (obs.get("own_view") is not True or obs.get("match_id") != match_id
                or obs.get("reading_method") != "visual" or obs.get("source_sha256") != source_sha256
                or not match["start"] <= obs["time"] < match["end"]):
            raise ValueError("All damage proof must be visual own-view observations in the same canonical match")
        if not isinstance(obs.get("path"), str) or not Path(obs["path"]).is_file():
            raise ValueError("Each observation must reference an existing original source frame")
    readable = sorted((o for o in checked if type(o.get("damage")) is int), key=lambda o: o["time"])
    if any(b["damage"] < a["damage"] for a, b in zip(readable, readable[1:])):
        raise ValueError("Damage counter must be monotone across readable source observations")
    delta = end["damage"] - base["damage"]
    if review.get("damage_delta") != delta:
        raise ValueError("Submitted damage amount differs from the direct source observations")
    if delta < 150:
        raise ValueError(f"HUD damage growth is {delta}, below the required 150")
    from quality_guard import evidence_records
    identities = evidence_records(_damage_evidence_paths(review))
    if review.get("evidence_identities") != identities:
        raise ValueError("Damage source frames changed; re-import the visual review")
    result = {"source_sha256": source_sha256, "anchors": segment["anchors"],
            "segment_start": segment["start"], "segment_end": segment["end"], "match_id": match_id,
            "observations": observations, "damage_delta": delta, "damage_measurement": mode,
            "reviewer": review["reviewer"], "reason": review["reason"], "evidence_identities": identities}
    if mode == "lower_bound":
        result["bound_start"] = base
    return result


def check_damage_reviews(work, manifest):
    errors = []
    if not manifest or not manifest.get("segments"):
        return errors
    try:
        root = Path(work)
        job = _read(root / "job.json")
        scan = _read(root / "scan.json")
        matches = _match_registry(root, job["source"]["sha256"], scan["media"]["duration"])
        tolerance = 1 / scan["media"].get("fps", 60)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return [{"code": "damage_review_invalid", "detail": str(exc)}]
    for segment in manifest["segments"]:
        try:
            review = segment.get("damage_review")
            if not isinstance(review, dict):
                raise ValueError("Every final segment needs a threshold review")
            _validate_review(review, segment, job["source"]["sha256"], matches, tolerance)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({"code": "damage_review_invalid", "detail": str(exc), "anchors": segment.get("anchors")})
    return errors

def import_damage_reviews(work, submission, config):
    root = Path(work)
    manifest, decisions = _read(root / "final.json"), _read(submission)
    source_sha = manifest.get("source", {}).get("sha256")
    if decisions.get("source_sha256") != source_sha:
        raise ValueError("Damage review submission belongs to another source")
    segments = decisions.get("segments", [])
    by_anchors = {tuple(r.get("anchors", [])): r for r in segments}
    expected = {tuple(s.get("anchors", [])) for s in manifest.get("segments", [])}
    if len(by_anchors) != len(segments) or set(by_anchors) != expected:
        raise ValueError("Submit one damage review for each exact final segment")
    job, scan = _read(root / "job.json"), _read(root / "scan.json")
    matches = _match_registry(root, source_sha, scan["media"]["duration"])
    tolerance = 1 / scan["media"].get("fps", 60)
    exclusions = []
    for segment in manifest["segments"]:
        review = dict(by_anchors[tuple(segment["anchors"])])
        review["source_sha256"] = source_sha
        if review.get("decision", "admit") == "exclude":
            # Validate evidence and exact interval before accepting a below-threshold exclusion.
            observations = review.get("observations", [])
            by_role = {o.get("role"): o for o in observations if isinstance(o, dict)}
            if set(by_role) != {"before", "start", "end"}:
                raise ValueError("Exclusion needs exact before/start/end source observations")
            start, end = by_role["start"], by_role["end"]
            if review.get("anchors") != segment["anchors"] or review.get("segment_start") != segment["start"] or review.get("segment_end") != segment["end"]:
                raise ValueError("Exclusion is not bound to the exact final segment")
            delta = end.get("damage", math.nan) - start.get("damage", math.nan)
            if not _finite(delta) or delta < 0 or delta >= 150 or review.get("damage_delta") != delta:
                raise ValueError("Exclusion is allowed only when exact source HUD growth is below 150")
            if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
                raise ValueError("Document why this below-threshold segment is excluded")
            # The common validator checks timestamps, match, own view and real images; permit only the threshold shortfall.
            from quality_guard import evidence_records
            review["evidence_identities"] = evidence_records([by_role[k]["path"] for k in ("before", "start", "end")])
            try:
                _validate_review(review, segment, source_sha, matches, tolerance)
            except ValueError as exc:
                if "below the required 150" not in str(exc):
                    raise
            from quality_guard import evidence_records
            review["evidence_identities"] = evidence_records([by_role[k]["path"] for k in ("before", "start", "end")])
            exclusions.append({"anchors": segment["anchors"], "start": segment["start"], "end": segment["end"],
                               "damage_delta": delta, "reviewer": review["reviewer"], "reason": review["reason"],
                               "evidence_identities": review["evidence_identities"]})
            continue
        from quality_guard import evidence_records
        review["evidence_identities"] = evidence_records(_damage_evidence_paths(review))
        segment["damage_review"] = _validate_review(review, segment, source_sha, matches, tolerance)
    if exclusions:
        excluded_anchors = {a for item in exclusions for a in item["anchors"]}
        events_path = root / "confirmed_events.json"
        events_data = _read(events_path)
        before_count = len(events_data.get("events", []))
        found=False
        for event in events_data.get("events",[]):
            if event.get("class")=="A" and event.get("anchor") in excluded_anchors:
                event.update(previous_class="A", **{"class":"C"},exclusion_reason="final_segment_hud_damage_below_150")
                found=True
        if not found:
            raise ValueError("Excluded segment anchors were not found in confirmed_events.json")
        save_json(events_path, events_data)
        prior_path = root / "damage_exclusions.json"
        prior = _read(prior_path).get("exclusions", []) if prior_path.is_file() else []
        combined = {tuple(x["anchors"]): x for x in prior + exclusions}
        save_json(prior_path, {"source_sha256": source_sha, "exclusions": list(combined.values())})
    save_json(root / "final.json", manifest)
    return {"reviewed_segments": len(segments) - len(exclusions), "excluded_below_threshold": exclusions,
            "action": "Excluded events removed from confirmed_events.json; rerun plan and review new cuts" if exclusions else "Run export --validate-only"}


def _probe_only_event(anchor, scan):
    windows = scan.get("prefilter", {}).get("windows", []) or scan.get("windows", [])
    if not _finite(anchor):
        return False
    padding=scan.get("settings",{}).get("boundary_padding",1)
    matching = [w for w in windows if w.get("start", math.inf)-padding <= anchor <= w.get("end", -math.inf)+padding
                and w.get("minute",[-math.inf,math.inf])[0] <= anchor <= w.get("minute",[-math.inf,math.inf])[1]]
    return bool(matching) and all(w.get("method") == "hidden_counters_damage_probe" for w in matching)


def check_admission_reviews(work, events_data):
    root = Path(work)
    try:
        scan = _read(root / "hud_state.json")
        scan2 = _read(root / "scan.json")
        scan.setdefault("windows", scan2.get("prefilter", {}).get("windows", []))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return [{"code": "admission_review_invalid", "detail": str(exc)}]
    errors = []
    for event in events_data.get("events", []):
        if event.get("class") != "A" or not _probe_only_event(event.get("anchor"), scan):
            continue
        review = event.get("admission_review")
        try:
            if (event.get("event_type") != "double_banner" or not isinstance(review, dict)
                    or review.get("basis") != "own_knockdown_for_hidden_counters"
                    or review.get("own_knockdown_confirmed") is not True
                    or review.get("source_sha256") != events_data.get("source", {}).get("sha256")):
                raise ValueError("Probe-only event requires confirmed own knockdown basis; assists/weak rewards do not qualify")
            if review.get("event_type") != "double_banner" or review.get("anchor") != event.get("anchor"):
                raise ValueError("Admission review must bind this double-banner event and exact anchor")
            if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
                raise ValueError("Admission review requires reviewer and concrete reason")
            from quality_guard import evidence_records
            actual = evidence_records(review.get("evidence_frames"))
            if review.get("evidence_identities") != actual:
                raise ValueError("Admission evidence changed; re-import the visual review")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({"code": "admission_review_invalid", "detail": str(exc), "anchor": event.get("anchor")})
    return errors


def import_admission_reviews(work, submission):
    root = Path(work)
    data, decision = _read(root / "confirmed_events.json"), _read(submission)
    source_sha = data.get("source", {}).get("sha256")
    if decision.get("source_sha256") != source_sha:
        raise ValueError("Admission review belongs to another source")
    events = data.get("events", [])
    scan = _read(root / "hud_state.json")
    scan2 = _read(root / "scan.json")
    scan.setdefault("windows", scan2.get("prefilter", {}).get("windows", []))
    targets = [e for e in events if e.get("class") == "A" and _probe_only_event(e.get("anchor"), scan)]
    rows = decision.get("reviews", [])
    by_anchor = {r.get("anchor"): r for r in rows}
    if len(by_anchor) != len(rows) or set(by_anchor) != {e.get("anchor") for e in targets}:
        raise ValueError("Submit one own-knockdown admission review for every probe-only event")
    from quality_guard import evidence_records
    for event in targets:
        review = dict(by_anchor[event["anchor"]])
        review["source_sha256"] = source_sha
        review["evidence_identities"] = evidence_records(review.get("evidence_frames"))
        event["admission_review"] = review
    errors = check_admission_reviews(root, data)
    if errors:
        raise ValueError("Invalid admission review: " + json.dumps(errors, ensure_ascii=False))
    save_json(root / "confirmed_events.json", data)
    return {"reviewed_events": len(rows), "action": "Run plan; review each final damage interval"}
