"""Minute HUD gate, one midpoint, fixed ten-way recovery, then local V2.5 scans.
Counter readings are explicitly saved by the image-capable reviewer, never guessed by OCR.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw
from common import binary, identity, probe, roi_box, run, save_json
from config import digest

REVISION = "minute-hud-ten-way-v5-tristate-hidden-counter-probe"
SCOPE = "hud_prefiltered_candidates_and_audits"
COUNTERS = ("kills", "assists", "participation", "damage")


def key(t):
    return f"{t:.6f}"


def timeline(media, step=60, scope=None):
    # EOF itself has no decodable frame. Keep the final real frame as the endpoint.
    last = max(0, media["duration"] - 1 / media["fps"])
    if scope:
        points = set()
        for start, end in scope["ranges"]:
            end = min(end, last)
            if end <= start:
                raise ValueError("Marked interval contains no decodable frames")
            points.update((round(start, 6), round(end, 6)))
            points.update(round(start+n*step, 6) for n in range(1, math.ceil((end-start)/step)) if start+n*step < end)
        return sorted(points)
    return sorted({0.0, *[float(t) for t in range(step, math.ceil(last), step) if t < last], round(last, 6)})


def validate_readings(data, cache_key, *, require_observation=True):
    if data.get("cache_key") != cache_key or not isinstance(data.get("readings"), list):
        raise ValueError("HUD readings belong to a different source/configuration")
    rows = {}
    for row in data["readings"]:
        t = row.get("time")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
            raise ValueError("HUD time must be finite and nonnegative")
        k = key(t)
        if k in rows:
            raise ValueError("Duplicate HUD reading timestamp")
        if row.get("state") not in ("pending", "valid", "partial", "missing", "uncertain", "spectator"):
            raise ValueError("HUD state: pending/valid/missing/uncertain/spectator")
        if row["state"] != "pending" and not str(row.get("reviewer") or "").strip():
            raise ValueError("HUD decisions require an actual visual reviewer")
        if row["state"] in ("valid", "partial"):
            if row.get("own_view") is not True or not isinstance(row.get("match_id"), str) or not row["match_id"].strip():
                raise ValueError("Valid HUD requires own_view=true and an explicit match_id")
            for field in COUNTERS:
                if row["state"] == "partial" and row.get(field) is None:
                    continue
                if type(row.get(field)) is not int or row[field] < 0:
                    raise ValueError("Valid HUD counters must be nonnegative integers; unknown is not zero")
            if row["state"] == "partial" and not any(row.get(f) is not None for f in COUNTERS):
                raise ValueError("Partial requires at least one known counter; otherwise use uncertain/missing")
        if row["state"] in ("valid", "partial"):
            if row.get("reading_method") != "visual" or any(row.get(f) is True for f in ("interpolated", "estimated", "synthetic")):
                raise ValueError("HUD gate requires actual visual readings, not interpolation/estimation")
            text = str(row.get("reviewer", "")) + " " + str(row.get("note", ""))
            if any(word in text.lower() for word in ("interpolated", "interpolation", "estimated", "synthetic", "插值", "推算", "估算")):
                raise ValueError("Inferred HUD values cannot be marked valid/partial")
            if require_observation:
                observation = row.get("observation", {})
                expected = {f: row.get(f) for f in (*COUNTERS, "state", "own_view", "reviewer")}
                if observation.get("method") != "visual" or observation.get("time") != t or observation.get("values") != expected:
                    raise ValueError("HUD reading is missing a bound visual observation or was edited; re-import actual readings")
                path = row.get("stats_path")
                if not path or Path(path).name != f"stats_{k}.png" or observation.get("stats_identity") != identity(path):
                    raise ValueError("HUD observation must bind the generated image at the exact reading time")
        if row.get("decision_revision")==5 and row["state"] in ("missing","uncertain","spectator"):
            observed=row.get("decision_observation",{})
            if (observed.get("time")!=t or observed.get("state")!=row["state"]
                    or observed.get("own_view")!=row.get("own_view")
                    or observed.get("reviewer")!=row.get("reviewer")
                    or not str(observed.get("reason") or "").strip()
                    or observed.get("stats_identity")!=identity(row["stats_path"])
                    or observed.get("source_identity")!=identity(row["source_path"])):
                raise ValueError("Negative HUD decision changed or lacks reviewed source evidence")
        rows[k] = row
    return rows


def usable(row):
    return row and row.get("state") in ("valid", "partial") and row.get("own_view") is True


def same_match_own(a, b):
    return (usable(a) and usable(b) and a["match_id"] == b["match_id"])


def comparable(a, b):
    return (same_match_own(a, b)
            and all(b[f] >= a[f] for f in ("kills", "assists", "damage")
                    if a.get(f) is not None and b.get(f) is not None))


def growth_state(a, b):
    """Return True/False/None: positive growth / proven unchanged / not knowable."""
    if not comparable(a, b):
        return None
    if any(a.get(f) is not None and b.get(f) is not None and b[f] > a[f]
           for f in ("kills", "assists")):
        return True
    if all(a.get(f) is not None and b.get(f) is not None and b[f] == a[f]
           for f in ("kills", "assists")):
        return False
    return None


def growth(a, b):
    # Kept as the known-positive predicate for callers that admit a half-window.
    return growth_state(a, b) is True


def passes(a, b, damage):
    return (growth_state(a, b) is True and a.get("damage") is not None and b.get("damage") is not None
            and b["damage"] - a["damage"] >= damage)


def damage_growth(a, b):
    return (comparable(a, b) and type(a.get("damage")) is int and type(b.get("damage")) is int
            and b["damage"] - a["damage"] >= 0)


def damage_provenance(a, b, source):
    return {"source": source, "start_time": a["time"], "end_time": b["time"],
            "start_damage": a["damage"], "end_damage": b["damage"],
            "growth": b["damage"] - a["damage"]}


def evaluate(rows, times, settings):
    """Pure scheduling: damage applies once per comparable minute/run, never per subwindow."""
    requests, windows, skipped, unresolved = {}, [], [], []
    def get(t, reason):
        row = rows.get(key(t))
        if not row or row["state"] == "pending":
            requests[key(t)] = {"time": round(t, 6), "reason": reason}
            return None
        return row
    for t in times:
        get(t, "minute")
    if requests:
        return dict(requests=list(requests.values()), windows=[], skipped=[], unresolved=[])
    def add(a, b, minute, method, provenance=None):
        # Missing interior HUD points can leave a gap >30s. Cover that gap without more pictures.
        count = max(1, math.ceil((b-a) / settings["max_window"]))
        for i in range(count):
            window = {"start": round(a+(b-a)*i/count, 6),
                            "end": round(a+(b-a)*(i+1)/count, 6),
                            "minute": minute, "method": method}
            if provenance is not None:
                window["damage_provenance"] = provenance
            windows.append(window)
    for start, end in zip(times, times[1:]):
        from marker_scope import within
        if not within(start, end, settings.get("marker_scope")):
            continue
        minute = [start, end]
        left, right = rows[key(start)], rows[key(end)]
        known_pair = comparable(left, right)
        count_growth = growth_state(left, right)
        has_damage = damage_growth(left, right)
        damage_delta = right["damage"] - left["damage"] if has_damage else None
        if count_growth is False:
            skipped.append({"interval": minute, "reason": "known_fields_fail_minute_gate"})
            continue
        if (count_growth is None and has_damage and damage_delta >= settings["min_damage"]):
            provenance = damage_provenance(left, right, "minute")
            unresolved.append({"interval": minute, "reason": "counter_growth_unknown",
                               "damage_provenance": provenance})
            if end-start <= settings["max_window"]:
                add(start, end, minute, "hidden_counters_damage_probe", provenance)
                continue
            middle=(start+end)/2
            mid=get(middle, "midpoint_hidden_counters")
            if mid is None:
                continue
            if damage_growth(left,mid) and damage_growth(mid,right):
                for a,b in ((left,mid),(mid,right)):
                    if b["damage"]>a["damage"]:
                        add(a["time"],b["time"],minute,"hidden_counters_damage_probe",provenance)
            else:
                add(start,end,minute,"hidden_counters_damage_probe",provenance)
            continue
        if known_pair and has_damage and damage_delta < settings["min_damage"]:
            skipped.append({"interval": minute, "reason": "known_fields_fail_minute_gate"})
            continue
        fallback = not (known_pair and passes(left, right, settings["min_damage"]))
        if not fallback:
            if not passes(left, right, settings["min_damage"]):
                skipped.append({"interval": minute, "reason": "minute_below_counter_or_damage_gate"})
                continue
            if end-start <= settings["max_window"]:
                add(start, end, minute, "short_tail")
                continue
            middle = (start+end)/2
            mid = get(middle, "midpoint")
            if mid is None:
                continue
            fallback = not (comparable(left, mid) and comparable(mid, right)
                            and all(mid.get(f) is not None for f in ("kills", "assists")))
            if not fallback:
                for a, b in ((left, mid), (mid, right)):
                    if growth(a, b):
                        add(a["time"], b["time"], minute, "single_bisection")
                continue
        points = [start+(end-start)*i/settings["fallback_divisions"] for i in range(settings["fallback_divisions"]+1)]
        recovered = [get(t, "ten_way_missing_hud") for t in points]
        if any(row is None for row in recovered):
            continue
        valid = [r for r in recovered if usable(r)]
        # Preserve the explicit blind spans, including the final valid-to-missing tail.
        for a, b in zip(recovered, recovered[1:]):
            if not comparable(a, b):
                unresolved.append({"interval": [a["time"], b["time"]], "reason": "unavailable_or_cross_match_hud_after_ten_way"})
        runs = []
        for row in valid:
            if runs and comparable(runs[-1][-1], row):
                runs[-1].append(row)
            else:
                runs.append([row])
        before = len(windows)
        for group in runs:
            if len(group) < 2:
                continue
            run_growth = growth_state(group[0], group[-1])
            run_has_damage = damage_growth(group[0], group[-1])
            run_delta = group[-1]["damage"] - group[0]["damage"] if run_has_damage else None
            if run_growth is None:
                unresolved_row = {"interval": [group[0]["time"], group[-1]["time"]],
                                  "reason": "counter_growth_unknown"}
                if run_has_damage:
                    provenance = damage_provenance(group[0], group[-1], "comparable_recovery_run")
                    unresolved_row["damage_provenance"] = provenance
                unresolved.append(unresolved_row)
                if run_has_damage and run_delta >= settings["min_damage"]:
                    for a, b in zip(group, group[1:]):
                        if type(a.get("damage")) is int and type(b.get("damage")) is int and b["damage"] > a["damage"]:
                            half_provenance = damage_provenance(group[0], group[-1], "comparable_recovery_run")
                            add(a["time"], b["time"], minute, "hidden_counters_damage_probe", half_provenance)
                continue
            if run_growth is True and run_has_damage and run_delta >= settings["min_damage"]:
                for a, b in zip(group, group[1:]):
                    if growth_state(a, b) is True:
                        add(a["time"], b["time"], minute, "ten_way_recovery")
        if len(windows) == before and not any(u["interval"][0] < end and u["interval"][1] > start for u in unresolved):
            skipped.append({"interval": minute, "reason": "no_comparable_run_passed_gate_after_ten_way"})
    return dict(requests=sorted(requests.values(), key=lambda r:r["time"]), windows=windows,
                skipped=skipped, unresolved=unresolved)


def scan_ranges(windows, duration, padding):
    result = []
    for window in sorted(windows, key=lambda w:w["start"]):
        a, b = max(0, window["start"]-padding), min(duration, window["end"]+padding)
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return [[round(a,6), round(b,6)] for a,b in result]


def prepare(source, root, config, source_id, previous_config=None):
    root = Path(root)
    media = probe(source)
    from marker_scope import assert_source, check_duration
    scope = config["prefilter"].get("marker_scope")
    assert_source(scope, source_id)
    check_duration(scope, media)
    cache_key = hashlib.sha256(json.dumps({"source":source_id["sha256"],"config":config,
        "revision":REVISION},sort_keys=True).encode()).hexdigest()
    directory = root/"hud_cache"/cache_key[:16]
    directory.mkdir(parents=True,exist_ok=True)
    readings_path = directory/"hud_readings.json"
    if readings_path.is_file():
        data = json.loads(readings_path.read_text(encoding="utf-8"))
    else:
        data = {"cache_key":cache_key,"source":source_id,"config_digest":digest(config),"readings":[]}
        previous_path=root/"hud_state.json"
        effective_path=root/"effective-config.json"
        if previous_path.is_file() and effective_path.is_file():
            previous=json.loads(previous_path.read_text(encoding="utf-8"))
            old_config=previous_config or json.loads(effective_path.read_text(encoding="utf-8"))
            if (previous.get("source",{}).get("sha256")==source_id["sha256"]
                    and previous.get("settings")==config["prefilter"]
                    and old_config.get("roi",{}).get("stats")==config["roi"]["stats"]):
                old_data=json.loads(Path(previous["readings_path"]).read_text(encoding="utf-8"))
                validate_readings(old_data,previous["cache_key"])
                data=copy.deepcopy(old_data)
                data.update(cache_key=cache_key,config_digest=digest(config),
                            migrated_from=previous["readings_path"])
                save_json(readings_path,data)
    rows = validate_readings(data,cache_key)
    times = timeline(media,config["prefilter"]["minute_seconds"],scope)
    result = evaluate(rows,times,config["prefilter"])
    added = False
    for request in result["requests"]:
        t = request["time"]
        k = key(t)
        full = directory/f"source_{k}.png"
        crop = directory/f"stats_{k}.png"
        view = directory/f"view_{k}.png"
        if not full.is_file() or not crop.is_file() or not view.is_file():
            run([binary("ffmpeg"),"-y","-v","error","-ss",str(t),"-i",str(source),"-frames:v","1",str(full)])
            with Image.open(full) as im:
                im.crop(roi_box(im.width,im.height,"stats",config["roi"]["stats"])).save(crop)
                im.crop(roi_box(im.width,im.height,"high",config["roi"]["high"])).save(view)
        request.update(stats_path=str(crop),view_path=str(view),source_path=str(full))
        if k not in rows:
            row = {"time":t,"state":"pending","match_id":None,"own_view":None,
                   **{f:None for f in COUNTERS},"reviewer":None,"stats_path":str(crop),"source_path":str(full)}
            data["readings"].append(row)
            added = True
    if added or not readings_path.is_file():
        data["readings"].sort(key=lambda r:r["time"])
        save_json(readings_path,data)
    pages=[]
    pending=result["requests"]
    for offset in range(0,len(pending),12):
        batch=pending[offset:offset+12]
        columns=min(3,len(batch))
        sheet=Image.new("RGB",(600*columns,250*math.ceil(len(batch)/columns)),"#171717")
        draw=ImageDraw.Draw(sheet)
        for i,request in enumerate(batch):
            x,y=(i%columns)*600,(i//columns)*250
            draw.text((x+4,y+4),f'{request["time"]:.3f}s  {request["reason"]}',fill="white")
            with Image.open(request["stats_path"]) as im:
                im.thumbnail((592,150));sheet.paste(im,(x+4,y+25))
            with Image.open(request["view_path"]) as im:
                im.thumbnail((420,65));sheet.paste(im,(x+4,y+180))
        page=directory/("hud_"+hashlib.sha256(json.dumps(batch,sort_keys=True).encode()).hexdigest()[:16]+".jpg")
        if not page.is_file():
            sheet.save(page,quality=94)
        pages.append({"path":str(page),"times":[r["time"] for r in batch]})
        task=page.with_suffix(".task.json")
        neighbours=[]
        known=sorted((r for r in rows.values() if usable(r)),key=lambda r:r["time"])
        for t in (batch[0]["time"],batch[-1]["time"]):
            for r in sorted(known,key=lambda r:abs(r["time"]-t))[:2]:
                if r not in neighbours:neighbours.append(r)
        registry_path=root/"match_registry.json"
        registry=json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else None
        save_json(task,{"cache_key":cache_key,"page":str(page),"requests":batch,"known_neighbours":neighbours,
            "match_registry":registry,
            "worker_rules":"Read assigned times only. Use only primary-verified match_registry IDs for usable rows; never invent time-range match labels. If a match is unregistered, report observed session ID/boundary evidence separately to the primary and leave uncertain. Return cache_key and readings with reviewer and reading_method=visual for valid/partial. Read every numeric value from the assigned image; never interpolate, estimate, carry forward, or force counters monotonic. Unknown=null, never infer zero. valid=all four counters; partial=own view and known match with some unknown counters; missing=no HUD; uncertain=unclear view/match. Own downed shield is NOT spectator. Spectator requires two distinct top-center concepts live/summary/spectate/squad: include spectator_region=high and spectator_terms. Do not use killfeed. Do not run extraction, spawn workers, or reread other batches. Inspect original only for an ambiguous assigned row. Return JSON only."})
        pages[-1]["task_path"]=str(task)
    result.update(cache_key=cache_key,source=source_id,media=media,minute_times=times,
                  readings_path=str(readings_path),pages=pages,config_digest=digest(config),
                  settings=config["prefilter"],stats_roi=config["roi"]["stats"],revision=REVISION)
    if pending:
        result.update(stage="awaiting_hud_review",instructions="Read each page task once; use partial for known own-view counters with null unknowns. Workers write separate submissions; apex.py hud-import validates them before merging. Never infer zero or spectator from a downed shield. Rerun the same scan command after imports; no user confirmation is needed.")
    else:
        evidence_paths=sorted({r[field] for r in data["readings"] for field in ("stats_path","source_path") if r.get(field)})
        result.update(stage="hud_gate_complete",readings_identity=identity(readings_path),
                      evidence_identities=[identity(p) for p in evidence_paths])
        result["scan_ranges"]=scan_ranges(result["windows"],media["duration"],config["prefilter"]["boundary_padding"])
    registry_path=root/"match_registry.json"
    result["match_registry_identity"]=identity(registry_path) if registry_path.is_file() else None
    report=directory/"hud_report.json"
    save_json(report,result)
    result["report_path"]=str(report)
    save_json(root/"hud_state.json",result)
    return result


def local_scan(source, root, config, source_id, report, verbose=False):
    from detect_highlights import scan
    root=Path(root)
    frames,pages,children=[],[],[]
    totals={name:0 for name in ("candidate_count","selected_count","representative_count","skipped_count","audit_count","ocr_calls","ocr_cache_hits","seconds")}
    errors=[]
    for n,(start,end) in enumerate(report["scan_ranges"]):
        output=root/"scan_cache"/f"window_{n:04d}.json"
        child=scan(source,output,root/"scan_cache"/f"window_{n:04d}",fps=config["scan"]["fps"],
            start=start,end=end,mode=config["scan"]["mode"],audit_seconds=config["scan"]["audit_seconds"],
            low_roi=config["roi"]["low"],high_roi=config["roi"]["high"],ocr_backend=config["scan"]["ocr"],
            verbose=verbose,source_snapshot=source_id)
        offset=len(frames)
        for frame in child["frames"]:
            frames.append(dict(frame,seq=frame["seq"]+offset))
        for page in child["pages"]:
            records=[]
            for frame in page["frames"]:
                record=copy.deepcopy(frame)
                record["seq"]+=offset
                if "represented_sequences" in record:
                    record["represented_sequences"]=[s+offset for s in record["represented_sequences"]]
                records.append(record)
            pages.append(dict(page,id=len(pages)+1,frames=records))
        children.append(identity(output))
        for name in totals:
            totals[name]+=child.get(name,0)
        if child.get("ocr_error"):
            errors.append(child["ocr_error"])
    manifest={"report_identity":identity(report["report_path"]),"readings_identity":report["readings_identity"],
              "ranges":report["scan_ranges"],"children":children}
    cache_key=hashlib.sha256(json.dumps({"config":config,"prefilter":manifest},sort_keys=True).encode()).hexdigest()
    index={"schema_version":"2.4","source":source_id,"media":report["media"],
        "config":{"fps":config["scan"]["fps"],"mode":config["scan"]["mode"],
          "audit_seconds":config["scan"]["audit_seconds"],"ocr_backend":config["scan"]["ocr"],
          "low_roi":config["roi"]["low"],"high_roi":config["roi"]["high"],
          "prefilter_digest":digest({"prefilter":config["prefilter"],"stats_roi":config["roi"]["stats"]})},
        "cache_key":cache_key,"mode":"hud_prefiltered_scan","review_scope":SCOPE,
        "start":0,"end":report["media"]["duration"],"fps":config["scan"]["fps"],"complete":True,
        "frames":frames,"pages":pages,"frame_count":len(frames),**totals,
        "review_state":str(root/"scan_cache"/("review_"+cache_key[:16]+".json")),
        "prefilter":manifest,"ocr_error":"; ".join(set(errors)) or None,
        "sampling_interval":1/config["scan"]["fps"]}
    save_json(root/"scan.json",index)
    return index


def require_eligible_anchors(scan, anchors):
    """Only admitted minute/window anchors may enter banner or ammo review."""
    if scan.get("mode") != "hud_prefiltered_scan":
        raise ValueError("The minute HUD gate has not completed")
    validate_coverage(scan, scan["media"])
    report = json.loads(Path(scan["prefilter"]["report_identity"]["path"]).read_text(encoding="utf-8"))
    padding = report["settings"]["boundary_padding"]
    for anchor in anchors:
        if isinstance(anchor, bool) or not isinstance(anchor, (int, float)) or not math.isfinite(anchor):
            raise ValueError("Invalid event anchor")
        if not any(w["minute"][0] <= anchor <= w["minute"][1] and
                   w["start"]-padding <= anchor <= w["end"]+padding for w in report["windows"]):
            raise ValueError(f"Anchor {anchor} did not pass minute HUD admission; no banner/ammo review allowed")


def validate_coverage(scan, media):
    """Validate local scan coverage and gate provenance; never impersonate whole-video 2fps."""
    manifest=scan.get("prefilter",{})
    for field in ("report_identity","readings_identity"):
        saved=manifest.get(field,{})
        if not saved.get("path") or identity(saved["path"])["sha256"]!=saved.get("sha256"):
            raise ValueError("HUD evidence changed or is missing; rerun scan and review")
    report=json.loads(Path(manifest["report_identity"]["path"]).read_text(encoding="utf-8"))
    data=json.loads(Path(manifest["readings_identity"]["path"]).read_text(encoding="utf-8"))
    if report.get("stage")!="hud_gate_complete" or report["source"]["sha256"]!=scan["source"]["sha256"]:
        raise ValueError("Incomplete or foreign HUD report")
    for saved in report.get("evidence_identities",[]):
        if identity(saved["path"])["sha256"]!=saved["sha256"]:
            raise ValueError("HUD screenshot evidence changed")
    if report.get("minute_times")!=timeline(media,report["settings"]["minute_seconds"],report["settings"].get("marker_scope")):
        raise ValueError("HUD minute timeline is incomplete")
    from quality_guard import check_match_rows
    saved_registry=report.get("match_registry_identity")
    registry_path=Path(scan["prefilter"]["report_identity"]["path"]).parents[2]/"match_registry.json"
    registry=None
    if saved_registry:
        registry_path=Path(saved_registry["path"])
        if identity(registry_path)["sha256"]!=saved_registry["sha256"]:
            raise ValueError("Canonical match registry changed; rerun scan")
        registry=json.loads(registry_path.read_text(encoding="utf-8"))
    match_errors=check_match_rows(data["readings"],registry,scan["source"]["sha256"],media["duration"])
    if match_errors:
        raise ValueError("Canonical match conflicts: "+json.dumps(match_errors[:4],ensure_ascii=False))
    rows=validate_readings(data,report["cache_key"])
    from marker_scope import assert_source, check_duration
    scope = report["settings"].get("marker_scope")
    assert_source(scope, scan["source"])
    check_duration(scope, media)
    result=evaluate(rows,report["minute_times"],report["settings"])
    ranges=scan_ranges(result["windows"],media["duration"],report["settings"]["boundary_padding"])
    if result["requests"] or ranges!=manifest.get("ranges") or ranges!=report.get("scan_ranges"):
        raise ValueError("HUD gate evidence does not match selected scan ranges")
    frames=scan.get("frames",[])
    expected=[]
    fps=scan["fps"]
    for start,end in ranges:
        expected.extend(round(start+n/fps,6) for n in range(math.ceil((end-start)*fps-.00001)))
    if len(frames)!=len(expected) or scan.get("frame_count")!=len(frames):
        raise ValueError("Local scan frame count is incomplete")
    for n,(frame,t) in enumerate(zip(frames,expected)):
        if frame.get("seq")!=n or abs(frame.get("time",-1)-t)>.00001:
            raise ValueError("Local scan contains missing or reordered timestamps")
    children=manifest.get("children",[])
    if len(children)!=len(ranges):
        raise ValueError("Missing local scan index")
    for child in children:
        if identity(child["path"])["sha256"]!=child["sha256"]:
            raise ValueError("Local scan evidence changed")
    return True
