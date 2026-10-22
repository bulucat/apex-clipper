"""Engineering-only synthetic fixtures; never claim real video decisions from these helpers."""
import json
from pathlib import Path
from common import identity, save_json, binary, run
from config import digest, load_config
from quality_guard import ammo_tasks, import_ammo_reviews, import_intervals, import_registry
from ammo_diff import analyze


def registry_fixture(work, duration, match_id="one", evidence=None):
    work=Path(work)
    job=json.loads((work/"job.json").read_text(encoding="utf-8"))
    if not evidence:
        frames=list((work/"hud_cache").rglob("source_*.png"))
        evidence=frames[:1] or [json.loads((work/"scan.json").read_text(encoding="utf-8"))["pages"][0]["path"]]
    submission=work/"synthetic_registry.json"
    save_json(submission,{"source_sha256":job["source"]["sha256"],"matches":[{
        "match_id":match_id,"session_id":"synthetic-session","start":0,"end":duration,
        "reviewer":"SYNTHETIC ENGINEERING FIXTURE","reason":"Synthetic source has a single known session",
        "evidence_frames":[str(p) for p in evidence]}]})
    return import_registry(work,submission)


def observed_fixture_row(row, directory):
    """Bind a unit-test observation to an image whose displayed values are known."""
    from PIL import Image, ImageDraw
    from hud_prefilter import key, COUNTERS
    row=dict(row,reviewer="ENGINEERING TEST FIXTURE",reading_method="visual")
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    image=directory/("stats_"+key(row["time"])+".png")
    im=Image.new("RGB",(480,80),"white")
    ImageDraw.Draw(im).text((4,4),str({f:row.get(f) for f in COUNTERS}),fill="black")
    im.save(image)
    row.update(stats_path=str(image),source_path=str(image))
    row["observation"]={"method":"visual","time":row["time"],
        "values":{f:row.get(f) for f in (*COUNTERS,"state","own_view","reviewer")},
        "stats_identity":identity(image)}
    return row


def import_fixture_readings(work, rows):
    """Exercise the real pending-batch importer, only inside generated test jobs."""
    from hud_import import merge
    work=Path(work)
    report=json.loads((work/"hud_state.json").read_text(encoding="utf-8"))
    if not (work/"match_registry.json").is_file():
        registry_fixture(work,report["media"]["duration"])
    allowed={"time","state","match_id","own_view","kills","assists","participation","damage"}
    submitted=[]
    for row in rows:
        item={k:v for k,v in row.items() if k in allowed}
        item.update(reviewer="ENGINEERING TEST FIXTURE",reading_method="visual")
        if item["state"] not in ("valid","partial"):
            item["note"]="Known generated frame has no usable HUD; engineering test only"
        submitted.append(item)
    submission=work/"synthetic_hud_submission.json"
    save_json(submission,{"cache_key":report["cache_key"],"readings":submitted})
    merge(work/"hud_state.json",submission)
    return json.loads(Path(report["readings_path"]).read_text(encoding="utf-8"))


def damage_fixture(work,config):
    """Build exact-cut damage proof for synthetic media; never a real Apex judgment."""
    from damage_review import import_damage_reviews
    work=Path(work)
    final=json.loads((work/"final.json").read_text(encoding="utf-8"))
    scan=json.loads((work/"scan.json").read_text(encoding="utf-8"))
    if not (work/"match_registry.json").is_file():
        registry_fixture(work,scan["media"]["duration"])
    directory=work/"synthetic_damage_frames";directory.mkdir(exist_ok=True)
    reviews=[]
    for n,segment in enumerate(final["segments"]):
        observations=[]
        for role,t,d in [("before",max(0,segment["start"]-.5),100+n*300),
                         ("start",segment["start"],100+n*300),
                         ("end",segment["end"]-1/scan["media"]["fps"],250+n*300)]:
            frame=directory/f"segment_{n}_{role}.png"
            run([binary("ffmpeg"),"-y","-v","error","-ss",str(t),"-i",final["source"]["path"],"-frames:v","1",str(frame)])
            observations.append(dict(role=role,time=t,damage=d,path=str(frame),own_view=True,
                                     match_id="one",reading_method="visual",source_sha256=final["source"]["sha256"]))
        reviews.append(dict(source_sha256=final["source"]["sha256"],anchors=segment["anchors"],
                            segment_start=segment["start"],segment_end=segment["end"],match_id="one",
                            observations=observations,damage_delta=150,
                            reviewer="ENGINEERING TEST FIXTURE",reason="Injected counter values for generated-media validation only"))
    submission=work/"synthetic_damage_reviews.json"
    save_json(submission,dict(source_sha256=final["source"]["sha256"],segments=reviews))
    return import_damage_reviews(work,submission,config)


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
