"""Engineering-only synthetic fixtures; never claim real video decisions from these helpers."""
import json
from pathlib import Path
from common import identity, save_json
from config import digest, load_config
from quality_guard import ammo_tasks, import_ammo_reviews, import_intervals, import_registry
from ammo_diff import analyze


def registry_fixture(work, duration, match_id="one", evidence=None):
    work=Path(work)
    job=json.loads((work/"job.json").read_text(encoding="utf-8"))
    evidence=evidence or [next((work/"hud_cache").rglob("source_*.png"))]
    submission=work/"synthetic_registry.json"
    save_json(submission,{"source_sha256":job["source"]["sha256"],"matches":[{
        "match_id":match_id,"session_id":"synthetic-session","start":0,"end":duration,
        "reviewer":"SYNTHETIC ENGINEERING FIXTURE","reason":"Synthetic source has a single known session",
        "evidence_frames":[str(p) for p in evidence]}]})
    return import_registry(work,submission)


def ammo_fixture(work,config):
    work=Path(work)
    data=json.loads((work/"confirmed_events.json").read_text(encoding="utf-8"))
    job=json.loads((work/"job.json").read_text(encoding="utf-8"))
    if job.get("config_path"):
        config=load_config(job["config_path"])
    duration=json.loads((work/"scan.json").read_text(encoding="utf-8"))["media"]["duration"]
    tasks=ammo_tasks(data["events"],{**job["source"],"duration":duration},config)
    reviews=[]
    for task in tasks:
        analysis=analyze(job["source"]["path"],task["start"],task["end"],work/"ammo",
                         config["scan"]["fps"],config["roi"]["ammo"],"off")
        task["analysis_identity"]=identity(analysis["analysis_path"])
        reviews.append({"task_id":task["task_id"],"anchors":task["anchors"],
            "analysis_path":analysis["analysis_path"],"checked_start":task["start"],"checked_end":task["end"],
            "decision":"unknown","fire_start":None,"same_weapon_confirmed":False,
            "reviewer":"SYNTHETIC ENGINEERING FIXTURE","reason":"Known synthetic black video; not a real firing/kill review",
            "evidence_frames":[analysis["trace"][0]["path"],analysis["trace"][-1]["path"]]})
    save_json(work/"ammo_tasks.json",{"source":job["source"],"config_digest":digest(config),"tasks":tasks})
    save_json(work/"synthetic_ammo_reviews.json",{"source_sha256":job["source"]["sha256"],"reviews":reviews})
    return import_ammo_reviews(work,work/"synthetic_ammo_reviews.json",config)


def interval_fixture(work,config):
    work=Path(work)
    final=json.loads((work/"final.json").read_text(encoding="utf-8"))
    scan=json.loads((work/"scan.json").read_text(encoding="utf-8"))
    evidence=scan["pages"][0]["path"] if scan["pages"] else None
    entries=[]
    for seg in final["segments"]:
        entries.append({"anchors":seg["anchors"],"start":seg["start"],"end":seg["end"],
            "reviewed_start":seg["start"],"reviewed_end":seg["end"],
            "reviewer":"SYNTHETIC ENGINEERING FIXTURE","reason":"Known synthetic fixture interval, not real visual review",
            "start_review":{"time":seg["start"],"reviewer":"SYNTHETIC ENGINEERING FIXTURE",
                "reason":"Known blank synthetic fixture", "firing_at_start":False,"omitted_lead_in":False,
                "frames":[{"time":max(seg["own_view_start"],seg["start"]-.5),"path":evidence},
                          {"time":seg["start"],"path":evidence}]}})
    save_json(work/"synthetic_interval_reviews.json",{"source_sha256":final["source"]["sha256"],"segments":entries})
    return import_intervals(work,work/"synthetic_interval_reviews.json",config)
