"""Semantic delivery guards: canonical matches, gate coverage and final-event backtracking."""
import copy
import hashlib
import json
import math
from pathlib import Path

from common import identity, save_json
from config import digest, policy_from_config

DEFAULTS = {"goal": "hud_gate", "long_source_seconds": 1800,
            "min_scan_fraction": .02, "max_unresolved_fraction": .20}
REVISION = "delivery-guard-v3-damage-proof-scoped-skip"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def union(spans, duration):
    merged = []
    for a, b in sorted((max(0, a), min(duration, b)) for a, b in spans):
        if not finite(a) or not finite(b) or a >= b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return merged


def length(spans, duration):
    return sum(b-a for a, b in union(spans, duration))


def covered(start, end, spans):
    cursor = start
    for a, b in sorted(spans):
        if a > cursor + .001:
            break
        if b >= cursor:
            cursor = max(cursor, b)
        if cursor >= end - .001:
            return True
    return False


def issue(code, detail, **context):
    return {"code": code, "detail": detail, **context}


def evidence_records(paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError("Saved visual evidence is required")
    return [identity(p) for p in paths]


def validate_registry(registry, source_sha256, duration):
    if registry.get("source_sha256") != source_sha256:
        raise ValueError("Match registry belongs to another source")
    matches = registry.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("Match registry requires verified matches")
    ids, sessions, previous = set(), {}, None
    for match in sorted(matches, key=lambda m: m.get("start", -1)):
        start, end, mid = match.get("start"), match.get("end"), match.get("match_id")
        if not (finite(start) and finite(end) and 0 <= start < end <= duration + .001):
            raise ValueError("Invalid canonical match interval")
        if not isinstance(mid, str) or not mid.strip() or mid in ids:
            raise ValueError("Canonical match IDs must be nonempty and unique")
        if previous and start < previous["end"]:
            raise ValueError("Canonical match intervals overlap")
        if not str(match.get("reviewer") or "").strip() or not str(match.get("reason") or "").strip():
            raise ValueError("Canonical matches require reviewer and boundary evidence reason")
        actual = evidence_records(match.get("evidence_frames"))
        if match.get("evidence_identities") != actual:
            raise ValueError("Match evidence changed; re-import the verified registry")
        session = match.get("session_id")
        if session:
            if not isinstance(session, str) or session in sessions:
                raise ValueError("One visible session_id must use one canonical match ID")
            sessions[session] = mid
        ids.add(mid)
        previous = match
    return matches


def match_at(matches, time):
    hits = [m for m in matches if m["start"] <= time < m["end"]]
    return hits[0] if len(hits) == 1 else None


def check_match_rows(rows, registry=None, source_sha256=None, duration=0):
    from hud_prefilter import usable
    own = sorted((r for r in rows if usable(r)), key=lambda r: r["time"])
    if not own:
        return []
    errors = []
    if registry is None:
        errors.append(issue("match_registry_missing", "Import a primary-reviewed canonical match registry before using HUD rows."))
        matches = []
    else:
        try:
            matches = validate_registry(registry, source_sha256, duration)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return [issue("match_registry_invalid", str(exc))]
        for row in own:
            match = match_at(matches, row["time"])
            if not match:
                errors.append(issue("match_not_registered", "Usable reading is outside verified canonical intervals.", time=row["time"]))
            elif row["match_id"] != match["match_id"]:
                errors.append(issue("match_label_conflict", "Worker label differs from the verified canonical match.",
                                    time=row["time"], actual=row["match_id"], expected=match["match_id"]))
            elif row.get("session_id") and match.get("session_id") != row["session_id"]:
                errors.append(issue("session_label_conflict", "Observed game session differs from registry.", time=row["time"]))
    # A label change with continuous counters is a review request, never an automatic merge.
    for a, b in zip(own, own[1:]):
        shared = [f for f in ("kills", "assists", "damage") if a.get(f) is not None and b.get(f) is not None]
        if (a["match_id"] != b["match_id"] and b["time"]-a["time"] <= 60 and len(shared) >= 2
                and all(b[f] >= a[f] for f in shared)):
            canonical_a, canonical_b = match_at(matches, a["time"]), match_at(matches, b["time"])
            if not canonical_a or not canonical_b or canonical_a["match_id"] == canonical_b["match_id"]:
                errors.append(issue("suspected_match_split", "Counters continue across different labels; verify actual session identity.",
                                    interval=[a["time"], b["time"]], labels=[a["match_id"], b["match_id"]]))
    # Even a canonical label cannot bridge a counter reset without renewed boundary review.
    for a, b in zip(own, own[1:]):
        if a["match_id"] == b["match_id"] and any(b[f] < a[f] for f in ("kills", "assists", "damage")
                                               if a.get(f) is not None and b.get(f) is not None):
            errors.append(issue("counter_reset_in_match", "A canonical match contains a counter rollback.",
                                interval=[a["time"], b["time"]], match_id=a["match_id"]))
    return errors


def import_registry(work, submission):
    root = Path(work)
    job = read(root/"job.json")
    data = read(submission)
    data = copy.deepcopy(data)
    for match in data.get("matches", []):
        match["evidence_identities"] = evidence_records(match.get("evidence_frames"))
    report = read(root/"hud_state.json") if (root/"hud_state.json").is_file() else None
    if report is None:
        scan = read(root/"scan.json")
        if scan.get("mode") != "full_scan":
            raise ValueError("Minute-gated scans require their HUD state before match registration")
        duration = scan["media"]["duration"]
    else:
        duration = report["media"]["duration"]
    validate_registry(data, job["source"]["sha256"], duration)
    readings = read(report["readings_path"]) if report else {"readings": []}
    for row in readings["readings"]:
        if row.get("state") not in ("valid", "partial"):
            continue
        canonical = match_at(data["matches"], row["time"])
        if not canonical:
            raise ValueError(f"Usable HUD row at {row['time']} is outside the supplied registry")
        if row["match_id"] != canonical["match_id"]:
            row["previous_match_id"] = row["match_id"]
            row["match_id"] = canonical["match_id"]
    errors = check_match_rows(readings["readings"], data, job["source"]["sha256"], duration)
    if errors:
        raise ValueError("Registry conflict: " + json.dumps(errors[:4], ensure_ascii=False))
    save_json(root/"match_registry.json", data)
    if report:
        save_json(report["readings_path"], readings)
    return {"matches": len(data["matches"]), "action": "Rerun scan; old HUD/scan/review identities are invalidated"}


def gate_audit(hud, readings, registry, duration):
    from hud_prefilter import evaluate, key, usable, validate_readings
    try:
        validate_readings(readings, hud["cache_key"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return [issue("hud_observation_invalid", str(exc))]
    errors = check_match_rows(readings["readings"], registry, hud["source"]["sha256"], duration)
    canonical = copy.deepcopy(readings["readings"])
    if registry:
        try:
            matches = validate_registry(registry, hud["source"]["sha256"], duration)
            for row in canonical:
                match = match_at(matches, row["time"])
                if usable(row) and match:
                    row["match_id"] = match["match_id"]
        except (ValueError, OSError, KeyError, TypeError):
            pass
    # Recalculate from recorded evidence, rather than trusting a saved "skipped" reason.
    expected = evaluate({key(r["time"]): r for r in canonical}, hud["minute_times"], hud["settings"])
    ranges = hud.get("scan_ranges", [])
    if hud.get("windows", []) != expected["windows"]:
        errors.append(issue("gate_windows_changed", "Saved windows differ from the actual minute admission decisions; rerun scan."))
    for window in expected["windows"]:
        if not covered(window["start"], window["end"], ranges):
            errors.append(issue("gate_window_missing", "A gate-eligible interval is absent from the saved scan ranges.",
                                interval=[window["start"], window["end"]], minute=window["minute"]))
    if expected["requests"] and not hud.get("requests"):
        errors.append(issue("gate_readings_incomplete", "Re-evaluation requests missing HUD evidence; rerun scan."))
    return errors


def ammo_tasks(events, source, config):
    from plan_clips import plan_segments
    segments = plan_segments(events, source["duration"], policy_from_config(config))
    tasks = []
    for segment in segments:
        start = max(0, segment["own_view_start"], segment["anchor"]-config["timing"]["lookback"])
        payload = {"source_sha256": source["sha256"], "config_digest": digest(config),
                   "anchors": segment["anchors"], "own_view_start": segment["own_view_start"],
                   "own_view_end": segment["own_view_end"], "start": start, "end": segment["anchors"][-1]}
        tasks.append({"task_id": fingerprint(payload)[:20], **payload})
    return tasks


def check_ammo(events, source, config):
    errors = []
    try:
        tasks = ammo_tasks(events, source, config)
    except (ValueError, KeyError, TypeError) as exc:
        return [issue("event_input_invalid", str(exc))]
    for task in tasks:
        group = [e for e in events if e.get("class") == "A" and e["anchor"] in task["anchors"]]
        reviews = [e.get("ammo_review") for e in group]
        if not reviews or any(not isinstance(r, dict) for r in reviews):
            errors.append(issue("ammo_review_missing", "Every final A group needs its own saved analysis and visual review.",
                                anchors=task["anchors"], task_id=task["task_id"]))
            continue
        if any(r != reviews[0] for r in reviews[1:]):
            errors.append(issue("ammo_group_review_conflict", "One connected group must use one consistent review.",
                                anchors=task["anchors"]))
            continue
        review = reviews[0]
        try:
            if review.get("task_id") != task["task_id"] or review.get("anchors") != task["anchors"]:
                raise ValueError("Ammo review targets another event/group")
            if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
                raise ValueError("Ammo review requires actual reviewer and reason")
            analysis_id = identity(review["analysis_path"])
            if analysis_id["sha256"] != review.get("analysis_sha256"):
                raise ValueError("Ammo analysis changed since visual review")
            analysis = read(review["analysis_path"])
            if analysis.get("source", {}).get("sha256") != source["sha256"]:
                raise ValueError("Ammo analysis belongs to another source")
            ac = analysis["config"]
            earliest=max(0,task["own_view_start"],task["anchors"][0]-config["timing"]["max_lookback"])
            if (not earliest-.001 <= ac["start"] <= task["start"]+.001 or abs(ac["end"]-task["end"]) > .001
                    or ac["roi"] != config["roi"]["ammo"] or ac["fps"] != config["scan"]["fps"]):
                raise ValueError("Ammo interval/ROI/fps does not match this final group's task; rerun ammo --all")
            trace = analysis.get("trace", [])
            if (not trace or
                    len(trace) != max(1,math.ceil((ac["end"]-ac["start"])*ac["fps"]-.00001))):
                raise ValueError("Ammo trace is incomplete")
            for n, sample in enumerate(trace):
                if abs(sample["time"]-(ac["start"]+n/ac["fps"])) > .00001 or not Path(sample["path"]).is_file():
                    raise ValueError("Ammo samples/evidence are missing or reordered")
            if (review.get("checked_start") != ac["start"] or review.get("checked_end") != task["end"]):
                raise ValueError("Review must cover the complete requested backtrack, including unknown OCR frames")
            if review.get("evidence_identities") != evidence_records(review.get("evidence_frames")):
                raise ValueError("Ammo visual evidence changed; re-import the visual review")
            decision, fire = review.get("decision"), review.get("fire_start")
            if decision == "found":
                if review.get("same_weapon_confirmed") is not True or not finite(fire) or not ac["start"] <= fire <= task["anchors"][0]:
                    raise ValueError("Found fire requires confirmed same-weapon evidence and a valid earliest fire time")
            elif decision == "unknown":
                if fire is not None:
                    raise ValueError("Unknown ammo requires null fire time")
            else:
                raise ValueError("Visual ammo decision must be found/unknown")
            if any(e.get("ammo") != decision or e.get("fire_start") != fire for e in group):
                raise ValueError("Final events do not match the reviewed ammo decision")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(issue("ammo_review_invalid", str(exc), anchors=task["anchors"]))
    return errors


def check_clip_starts(manifest):
    errors = []
    for seg in manifest.get("segments", []):
        if seg.get("class") != "A":
            continue
        review = seg.get("start_review")
        try:
            if not isinstance(review, dict) or review.get("time") != seg["start"]:
                raise ValueError("Final clip start needs a time-bound visual review")
            if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
                raise ValueError("Clip start review needs reviewer/reason")
            if type(review.get("firing_at_start")) is not bool or type(review.get("omitted_lead_in")) is not bool:
                raise ValueError("Explicitly check ongoing gunfire and omitted lead-in")
            frames = review.get("frames", [])
            times = [f["time"] for f in frames if finite(f.get("time"))]
            if review.get("evidence_identities") != evidence_records([f["path"] for f in frames]):
                raise ValueError("Clip start evidence changed; re-import the final review")
            before = max(seg["own_view_start"], seg["start"]-1)
            if not times or not any(before-.001 <= t <= seg["start"] for t in times) or not any(seg["start"] <= t <= seg["start"]+1 for t in times):
                raise ValueError("Keep before-start and at/after-start evidence within one second")
            boundary = abs(seg["start"]-max(seg["own_view_start"], seg["start_floor"])) < .001
            if (review["firing_at_start"] or review["omitted_lead_in"]) and not boundary:
                raise ValueError("Clip begins mid-fire or omits lead-in; backtrack and replan")
            if boundary and (review["firing_at_start"] or review["omitted_lead_in"]) and not review.get("boundary_reason"):
                raise ValueError("Document the verified view/previous-clip boundary that prevents more lead-in")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(issue("clip_start_review_invalid", str(exc), anchor=seg.get("anchor")))
    return errors


def audit_workdir(work, config, events_data=None, manifest=None, coverage_override=None):
    root = Path(work)
    job = read(root/"job.json")
    scan = read(root/"scan.json") if (root/"scan.json").is_file() else None
    policy = {**DEFAULTS, **config.get("quality", {})}
    hud, readings, registry = None, None, None
    if (root/"hud_state.json").is_file() and (config["prefilter"]["enabled"] or not scan):
        hud = read(root/"hud_state.json")
        readings = read(hud["readings_path"])
        registry = read(root/"match_registry.json") if (root/"match_registry.json").is_file() else None
    duration = (scan or hud)["media"]["duration"] if (scan or hud) else 0
    ranges = scan.get("prefilter", {}).get("ranges", []) if scan and scan.get("mode") == "hud_prefiltered_scan" else [[0, duration]] if scan and scan.get("complete") else []
    if not scan and hud:
        ranges = hud.get("scan_ranges", [])
    scanned = length(ranges, duration)
    unknown = length([x["interval"] for x in hud.get("unresolved", [])], duration) if hud else 0
    errors, warnings = [], []
    marked_scope = hud.get("settings", {}).get("marker_scope") if hud else None
    scope_skip = None
    if hud:
        errors.extend(gate_audit(hud, readings, registry, duration))
    fraction = scanned/duration if duration else 0
    if policy["goal"] == "complete" and (not scan or scan.get("mode") == "hud_prefiltered_scan"):
        errors.append(issue("complete_goal_requires_full_scan", "Complete coverage uses a full-source scan; speed/HUD gating is an explicit limited-coverage profile."))
    if duration >= policy["long_source_seconds"] and fraction < policy["min_scan_fraction"]:
        warnings.append(issue("sparse_scan_coverage", "Very little of this long recording reached local review.", fraction=fraction))
    if duration and unknown/duration > policy["max_unresolved_fraction"]:
        warnings.append(issue("large_unresolved_coverage", "Unresolved intervals exceed the configured review threshold.", fraction=unknown/duration))
    reviewed_count=0
    if scan and scan.get("complete"):
        try:
            state=read(scan["review_state"])
            for page in scan.get("pages",[]):
                entry=state.get("pages",{}).get(str(page["id"]),{})
                if (entry and identity(page["path"])["sha256"]==entry.get("page_sha256") and
                        identity(entry["decisions"])["sha256"]==entry.get("decisions_sha256")):
                    reviewed_count+=1
        except (OSError,ValueError,KeyError,TypeError):
            pass
        if events_data is not None and reviewed_count!=len(scan.get("pages",[])):
            errors.append(issue("page_review_incomplete","All actual selected pages and audits need valid saved visual decisions."))
    if events_data is not None:
        if events_data.get("source",{}).get("sha256")!=job["source"]["sha256"]:
            errors.append(issue("event_source_mismatch","Final events belong to another source."))
        if config["prefilter"]["enabled"] and scan:
            try:
                from hud_prefilter import require_eligible_anchors
                require_eligible_anchors(scan, [e["anchor"] for e in events_data.get("events", []) if e.get("class") == "A"])
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(issue("event_outside_hud_admission", str(exc)))
        errors.extend(check_ammo(events_data.get("events", []), {**job["source"], "duration": duration}, config))
        from damage_review import check_admission_reviews
        errors.extend(check_admission_reviews(root, events_data))
    if manifest is not None:
        from damage_review import check_damage_reviews
        errors.extend(check_damage_reviews(root, manifest))
        errors.extend(check_clip_starts(manifest))
        if events_data is not None:
            try:
                from plan_clips import plan_segments
                expected=plan_segments(events_data["events"],duration,policy_from_config(config))
                fields=("anchors","start","end","ammo","fire_start","own_view_start","own_view_end")
                if len(expected)!=len(manifest.get("segments",[])) or any(
                        any(a.get(f)!=b.get(f) for f in fields) for a,b in zip(expected,manifest["segments"])):
                    errors.append(issue("plan_events_mismatch","Final cuts differ from the reviewed final events; replan."))
            except (ValueError,KeyError,TypeError) as exc:
                errors.append(issue("plan_events_invalid",str(exc)))
    binding = {"revision": REVISION, "source": job["source"]["sha256"], "config": digest(config),
               "scan": identity(root/"scan.json") if scan else None,
               "readings": identity(hud["readings_path"]) if hud else None,
               "registry": identity(root/"match_registry.json") if registry else None}
    signature = fingerprint(binding)
    exception = None
    if warnings and policy["goal"] == "hud_gate":
        # Low scan fraction is an expected outcome of the user's hard admission gate.
        # Resolve uncertain HUD evidence without searching excluded low-damage minutes.
        if any(w["code"] == "large_unresolved_coverage" for w in warnings):
            skip_path = root / "USER_SKIP_UNRESOLVED.json"
            try:
                recorded = read(skip_path)
                recorded_intervals = [x.get("interval") for x in recorded.get("unresolved_intervals", [])]
                actual_intervals = [x.get("interval") for x in hud.get("unresolved", [])]
                if (recorded.get("source_sha256") != job["source"]["sha256"]
                        or recorded.get("scope") != "this_task_only"
                        or recorded.get("decision") != "skip_unresolved_hud_and_export_confirmed_admitted_events"
                        or not str(recorded.get("user_statement") or "").strip()
                        or abs(recorded.get("unresolved_seconds", -1) - unknown) > .001
                        or recorded_intervals != actual_intervals):
                    raise ValueError("Skip record must match this source and the complete current unresolved HUD scope")
                scope_skip = {"decision": recorded["decision"], "scope": recorded["scope"],
                              "unresolved_seconds": unknown, "unresolved_fraction": unknown/duration if duration else 0}
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(issue("hud_unresolved_review_required", str(exc)))
    elif warnings:
        try:
            exception = coverage_override if coverage_override is not None else read(root/"coverage_review.json")
            if policy["goal"] != "fast" or exception.get("signature") != signature or exception.get("delivery_scope") != "limited":
                raise ValueError("Coverage review must bind current evidence and explicitly declare limited delivery")
            if not str(exception.get("reviewer") or "").strip() or not str(exception.get("reason") or "").strip():
                raise ValueError("Coverage review requires a reviewer and concrete explanation")
            if set(exception.get("issues", [])) != {w["code"] for w in warnings}:
                raise ValueError("Coverage review must address each reported anomaly")
            samples = exception.get("samples", [])
            if len(samples) < 3:
                raise ValueError("Review at least three stratified excluded intervals, not a single convenient sample")
            blocks = set()
            for sample in samples:
                if sample.get("decision") != "no_qualifying_banner" or not str(sample.get("reason") or "").strip():
                    raise ValueError("Excluded audit sample needs a visual decision and reason")
                a, b = sample["interval"]
                if not 0 <= a < b <= duration or any(max(a,c)<min(b,d) for c,d in ranges):
                    raise ValueError("Audit samples must address excluded source intervals")
                if sample.get("evidence_identities") != evidence_records(sample["evidence_frames"]):
                    raise ValueError("Excluded audit evidence changed; re-import coverage review")
                blocks.add(min(2, int(((a+b)/2)/duration*3)))
            if blocks != {0, 1, 2}:
                raise ValueError("Stratify excluded audits over early, middle and late source thirds")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(issue("coverage_review_required", str(exc)))
            exception = None
    return {"revision": REVISION, "signature": signature, "goal": policy["goal"],
            "source_seconds": duration, "scan_seconds": scanned, "scan_fraction": fraction,
            "marker_scope": marked_scope,
            "marked_seconds": sum(b-a for a,b in marked_scope["ranges"]) if marked_scope else None,
            "unmarked_footage_reviewed": False if marked_scope else None,
            "unresolved_seconds": unknown, "unresolved_fraction": unknown/duration if duration else 0,
            "reviewed_pages": reviewed_count, "total_review_pages": len(scan.get("pages", [])) if scan else 0,
            "delivery_scope": "limited" if config["prefilter"]["enabled"] else "full_source_samples",
            "authorized_skip_scope": scope_skip,
            "errors": errors, "warnings": warnings, "coverage_exception": exception,
            "ready": not errors,
            "note": "Sampled review is not a zero-miss guarantee. Limited coverage must be disclosed in delivery."}


def enforce(work, config, events_data=None, manifest=None):
    report = audit_workdir(work, config, events_data, manifest)
    save_json(Path(work)/"quality_report.json", report)
    if report["errors"]:
        brief = "; ".join(x["code"]+": "+x["detail"] for x in report["errors"][:6])
        raise ValueError("Delivery guard blocked: "+brief+"; see quality_report.json")
    return report


def enforce_manifest(data, config):
    event_id = data.get("events_identity", {})
    if not event_id.get("path") or identity(event_id["path"])["sha256"] != event_id.get("sha256"):
        raise ValueError("Delivery guard requires unchanged confirmed-events identity; replan via apex.py")
    root = Path(data["coverage"]["scan_index"]).parent
    report=enforce(root, config, read(event_id["path"]), data)
    if data.get("config_digest")!=digest(config):
        raise ValueError("Delivery config changed; replan with current quality policy")
    return report


def import_ammo_reviews(work, submission, config):
    root = Path(work)
    data, decisions = read(root/"confirmed_events.json"), read(submission)
    job, prepared = read(root/"job.json"), read(root/"ammo_tasks.json")
    if (decisions.get("source_sha256") != job["source"]["sha256"] or
            prepared.get("config_digest") != digest(config)):
        raise ValueError("Ammo review source/config changed")
    expected = {t["task_id"]: t for t in prepared["tasks"]}
    rows = decisions.get("reviews", [])
    if len(rows) != len(expected) or {r.get("task_id") for r in rows} != set(expected):
        raise ValueError("Submit one visual ammo review for every current final A group")
    for review in rows:
        task = expected[review["task_id"]]
        if review.get("anchors") != task["anchors"] or "analysis_identity" not in task:
            raise ValueError("Run ammo --all for the current events before submitting reviews")
        actual = identity(review["analysis_path"])
        if actual != task["analysis_identity"]:
            raise ValueError("Ammo analysis is not the prepared artifact for this group")
        review["analysis_sha256"] = actual["sha256"]
        review["evidence_identities"] = evidence_records(review.get("evidence_frames"))
        for event in data["events"]:
            if event.get("class") == "A" and event["anchor"] in task["anchors"]:
                event.update(ammo=review.get("decision"), fire_start=review.get("fire_start"),
                             ammo_review=review)
    problems = check_ammo(data["events"], {**job["source"], "duration": read(root/"scan.json")["media"]["duration"]}, config)
    if problems:
        raise ValueError("Invalid ammo review: " + json.dumps(problems, ensure_ascii=False))
    save_json(root/"confirmed_events.json", data)
    return {"reviewed_groups": len(rows), "action": "Run plan; final interval reviews must follow the new cuts"}


def import_intervals(work, submission, config):
    root = Path(work)
    data, decisions = read(root/"final.json"), read(submission)
    if decisions.get("source_sha256") != data["source"]["sha256"]:
        raise ValueError("Final interval review belongs to another source")
    items = decisions.get("segments", [])
    if len(items) != len(data["segments"]):
        raise ValueError("Review every final interval")
    by_anchors = {tuple(r.get("anchors", [])): r for r in items}
    if len(by_anchors) != len(items):
        raise ValueError("Duplicate final interval review")
    for segment in data["segments"]:
        review = by_anchors.get(tuple(segment["anchors"]))
        if not review or (review.get("start"), review.get("end")) != (segment["start"], segment["end"]):
            raise ValueError("Interval/anchors changed; review the newly planned cut")
        if not str(review.get("reviewer") or "").strip() or not str(review.get("reason") or "").strip():
            raise ValueError("Complete interval review requires an actual reviewer and reason")
        if review.get("reviewed_start", math.inf) > segment["start"] or review.get("reviewed_end", -1) < segment["end"]:
            raise ValueError("Review must cover the entire final interval")
        start_review = review.get("start_review", {})
        start_review["evidence_identities"] = evidence_records([f["path"] for f in start_review.get("frames", [])])
        segment.update(reviewed_start=review["reviewed_start"], reviewed_end=review["reviewed_end"],
                       review_required=False, start_review=start_review,
                       interval_review={"reviewer": review["reviewer"], "reason": review["reason"]})
    problems = check_clip_starts(data)
    if problems:
        raise ValueError("Clip start needs correction: "+json.dumps(problems, ensure_ascii=False))
    save_json(root/"final.json", data)
    return {"reviewed_segments": len(items), "action": "Run export --validate-only, then export"}


def import_coverage_review(work, submission, config):
    root = Path(work)
    decision = read(submission)
    current = audit_workdir(root, config)
    decision["signature"] = current["signature"]
    for sample in decision.get("samples", []):
        sample["evidence_identities"] = evidence_records(sample.get("evidence_frames"))
    checked = audit_workdir(root, config, coverage_override=decision)
    failures = [e for e in checked["errors"] if e["code"] == "coverage_review_required"]
    if failures or not checked["warnings"]:
        raise ValueError("Invalid/unnecessary limited-coverage review: "+json.dumps(failures, ensure_ascii=False))
    save_json(root/"coverage_review.json", decision)
    return {"delivery_scope": "limited", "warnings": checked["warnings"],
            "note": "This accepts a disclosed fast-mode tradeoff; it does not resolve unknown intervals or prove no misses."}
