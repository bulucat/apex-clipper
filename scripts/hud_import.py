"""Validate worker submissions against the exact pending batch before atomic merge."""
import copy
import json
from pathlib import Path
from common import identity, save_json
from hud_prefilter import key, validate_readings


def merge(report_path, submission_path):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    submission = json.loads(Path(submission_path).read_text(encoding="utf-8"))
    if submission.get("cache_key") != report["cache_key"]:
        raise ValueError("Foreign HUD batch")
    data = json.loads(Path(report["readings_path"]).read_text(encoding="utf-8"))
    rows = validate_readings(data, report["cache_key"])
    pending = {key(r["time"]) for r in report["requests"]}
    incoming = validate_readings(submission, report["cache_key"], require_observation=False)
    if not incoming or not set(incoming) <= pending:
        raise ValueError("Empty submission or unrequested timestamps")
    result = copy.deepcopy(data)
    targets = {key(r["time"]): r for r in result["readings"]}
    for k, row in incoming.items():
        if row["state"] == "pending" or rows[k]["state"] != "pending":
            raise ValueError("Pending result or duplicate worker assignment; regenerate status")
        if row["state"] == "spectator":
            terms = set(row.get("spectator_terms", []))
            if len(terms & {"live", "summary", "spectate", "squad"}) < 2 or row.get("spectator_region") != "high":
                raise ValueError("Spectator requires two distinct top-center UI concepts")
            if row.get("own_downed") is True:
                raise ValueError("Conflicting downed and spectator labels require review")
        # Workers cannot replace evidence paths or source identity.
        allowed = {"time", "state", "match_id", "own_view", "kills", "assists", "participation", "damage",
                   "reviewer", "reading_method", "own_downed", "spectator_terms", "spectator_region", "note", "session_id"}
        if set(row) - allowed:
            raise ValueError("Unexpected worker fields")
        # A correction is a new observation, never a patch over old decisions.
        # Preserve generated image paths/history, but clear omitted numbers and labels.
        for field in allowed - {"time"}:
            targets[k].pop(field, None)
        targets[k].update({field: None for field in ("kills", "assists", "participation", "damage")})
        targets[k].update(row)
        if row["state"] in ("valid", "partial"):
            target = targets[k]
            target["observation"] = {"method": "visual", "time": target["time"],
                "values": {f: target.get(f) for f in ("kills", "assists", "participation", "damage", "state", "own_view", "reviewer")},
                "stats_identity": identity(target["stats_path"])}
        else:
            targets[k].pop("observation", None)
            if not str(targets[k].get("note") or "").strip():
                raise ValueError("Negative HUD decisions require a concrete visual reason")
        target=targets[k]
        target["decision_revision"]=5
        target["decision_observation"]={"time":target["time"], "state":target["state"],
            "own_view":target.get("own_view"), "reviewer":target["reviewer"],
            "reason":target.get("note"), "source_identity":identity(target["source_path"]),
            "stats_identity":identity(target["stats_path"])}
    validate_readings(result, report["cache_key"])
    from quality_guard import check_match_rows
    registry_path = Path(report_path).parent/"match_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else None
    errors = check_match_rows(result["readings"], registry, report["source"]["sha256"], report["media"]["duration"])
    if errors:
        raise ValueError("HUD match review required before merge: " + json.dumps(errors[:4],ensure_ascii=False))
    save_json(report["readings_path"], result)
    return {"imported": len(incoming), "remaining": sum(r["state"] == "pending" for r in result["readings"])}


def recheck(report_path, submission_path):
    """Replace reviewed mistakes while preserving the previous decision and original images."""
    report=json.loads(Path(report_path).read_text(encoding="utf-8"))
    submission=json.loads(Path(submission_path).read_text(encoding="utf-8"))
    if submission.get("cache_key")!=report["cache_key"] or submission.get("source_sha256")!=report["source"]["sha256"]:
        raise ValueError("HUD recheck must bind current source and cache")
    if not submission.get("readings") or any(not str(r.get("note") or "").strip() for r in submission["readings"]):
        raise ValueError("Rechecked rows require the actual visual correction reason")
    incoming=validate_readings(submission,report["cache_key"],require_observation=False)
    original=Path(report["readings_path"]).read_text(encoding="utf-8")
    data=json.loads(original)
    by_key={key(r["time"]):r for r in data["readings"]}
    if not set(incoming)<=set(by_key):
        raise ValueError("HUD recheck cannot invent timestamps")
    original_report=Path(report_path).read_text(encoding="utf-8")
    saved_history={k:copy.deepcopy(by_key[k]) for k in incoming}
    for k in incoming:
        by_key[k]["state"]="pending"
    patched=copy.deepcopy(report)
    patched["requests"]=[{"time":r["time"]} for r in incoming.values()]
    save_json(report["readings_path"],data)
    save_json(report_path,patched)
    try:
        result=merge(report_path,submission_path)
        updated=json.loads(Path(report["readings_path"]).read_text(encoding="utf-8"))
        for row in updated["readings"]:
            if key(row["time"]) in saved_history:
                history=saved_history[key(row["time"])]
                row["previous_decisions"]=history.get("previous_decisions",[])+[{k:v for k,v in history.items() if k!="previous_decisions"}]
        save_json(report["readings_path"],updated)
    except Exception:
        Path(report["readings_path"]).write_text(original,encoding="utf-8")
        raise
    finally:
        Path(report_path).write_text(original_report,encoding="utf-8")
    return {**result,"action":"Rerun scan and review changed candidate pages"}
