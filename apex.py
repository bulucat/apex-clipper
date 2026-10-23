#!/usr/bin/env python3
"""Apex Clipper: setup, calibrate, scan, review, plan and export from one entry."""
import argparse
from datetime import datetime
from uuid import uuid4
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"scripts"))
from config import VERSION,apply_tools,digest,load_config,policy_from_config,validate_policy
from environment import env_python


def configuration(args):
    path=getattr(args,"config",None)
    work=getattr(args,"workdir",None)
    if work and (Path(work)/"job.json").is_file():
        job=json.loads((Path(work)/"job.json").read_text(encoding="utf-8"))
        if args.command not in ("status", "audit") and job.get("version") != VERSION:
            raise ValueError("This workdir uses an older installed workflow. Start a fresh scan without --workdir; the assistant selects the current installed workflow automatically.")
        saved_root = job.get("skill_root")
        if args.command not in ("status", "audit") and saved_root and Path(saved_root).resolve() != ROOT.resolve():
            raise ValueError("This job belongs to a different skill installation; start a fresh scan.")
        if path is None:
            path=job.get("config_path")
    config=load_config(path)
    apply_tools(config)
    return config,path


def job_at(work):
    root=Path(work).resolve()
    if not (root/"job.json").is_file():
        raise ValueError("No job.json in workdir; run scan first")
    return root,json.loads((root/"job.json").read_text(encoding="utf-8"))


def check_scan_config(scan,config):
    expected={"fps":config["scan"]["fps"],"mode":config["scan"]["mode"],
              "audit_seconds":config["scan"]["audit_seconds"],"ocr_backend":config["scan"]["ocr"],
              "low_roi":config["roi"]["low"],"high_roi":config["roi"]["high"]}
    if config["prefilter"]["enabled"]:
        expected["prefilter_digest"]=digest({"prefilter":config["prefilter"],"stats_roi":config["roi"]["stats"]})
        if scan.get("mode")!="hud_prefiltered_scan":
            raise ValueError("Scan mode changed. Run scan and review again")
    elif scan.get("mode")=="hud_prefiltered_scan":
        raise ValueError("Scan mode changed. Run scan and review again")
    if any(scan.get("config",{}).get(k)!=v for k,v in expected.items()):
        raise ValueError("Scan/ROI configuration changed. Run scan with this config and review the resulting pages first")
    if config["prefilter"]["enabled"]:
        from hud_prefilter import validate_coverage
        validate_coverage(scan,scan["media"])


def require_admitted_events(root, config, anchors=None):
    if not config["prefilter"]["enabled"]:
        return
    scan = json.loads((Path(root)/"scan.json").read_text(encoding="utf-8"))
    check_scan_config(scan, config)
    if anchors is None:
        events = json.loads((Path(root)/"confirmed_events.json").read_text(encoding="utf-8"))["events"]
        anchors = [e["anchor"] for e in events if e.get("class") == "A"]
    from hud_prefilter import require_eligible_anchors
    require_eligible_anchors(scan, anchors)


def start_scan(args,config,config_path):
    from common import identity,save_json
    from detect_highlights import scan
    source=Path(args.source).resolve()
    root=Path(args.workdir).resolve() if args.workdir else (
        Path("exports/apex")/source.stem/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8]))
    root=root.resolve()
    root.mkdir(parents=True,exist_ok=True)
    print(f"WORKDIR: {root}",flush=True)
    print("STAGE 1/3: reading source identity",flush=True)
    previous=root/"job.json"
    old=json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() else None
    if old and old.get("version") != VERSION:
        raise ValueError("Do not reuse a workdir from an older installed workflow; start a fresh scan.")
    if not old and any((root/name).exists() for name in ("hud_state.json", "scan.json", "confirmed_events.json", "final.json", "compilation_FULL.mp4")):
        raise ValueError("Existing unbound clipping artifacts require a fresh workdir, not cache reuse.")
    stat=source.stat()
    source_stat={"size":stat.st_size,"mtime_ns":stat.st_mtime_ns,"path":str(source)}
    # HUD review resumes several times. Reuse the first SHA256 while size/mtime/path stay unchanged;
    # export still hashes the actual source again. Avoid rereading a multi-GB video every HUD batch.
    if old and old.get("source_stat")==source_stat:
        source_id=old["source"]
    else:
        source_id=identity(source)
    if old and old["source"]["sha256"]!=source_id["sha256"]:
        raise ValueError("This workdir belongs to a different source; choose a new --workdir")
    previous_config=json.loads((root/"effective-config.json").read_text(encoding="utf-8")) if (root/"effective-config.json").is_file() else None
    from marker_scope import select_scope, assert_source
    scope, marker_mode = select_scope(source_id, explicit=getattr(args, "markers", None),
        ignore=getattr(args, "ignore_markers", False), current=config["prefilter"].get("marker_scope"),
        saved=(previous_config or {}).get("prefilter", {}).get("marker_scope"),
        saved_mode=(old or {}).get("marker_mode"))
    config["prefilter"]["marker_scope"] = scope
    assert_source(scope, source_id)
    if config["prefilter"].get("marker_scope") and not config["prefilter"]["enabled"]:
        raise ValueError("Marked scans require the normal HUD gate")
    if config["prefilter"].get("marker_scope") or marker_mode == "ignore":
        config_path = root/"marker-config.json"
        save_json(config_path, config)
        scope = config["prefilter"].get("marker_scope")
        if scope:
            seconds = sum(b-a for a,b in scope["ranges"])
            print(f"MARKED SCOPE: {len(scope['marker_times'])} marks, {len(scope['ranges'])} merged ranges, {seconds:.3f}s; unmarked footage is outside review scope", flush=True)
    job={"marker_mode":marker_mode,"version":VERSION,"skill_root":str(ROOT.resolve()),"source":source_id,"source_stat":source_stat,"config_path":str(Path(config_path).resolve()) if config_path else None,
         "config_digest":digest(config),"scan_index":str(root/"scan.json")}
    save_json(root/"job.json",job)
    save_json(root/"effective-config.json",config)
    if config["prefilter"]["enabled"]:
        from hud_prefilter import prepare,local_scan
        print("STAGE 1/3: minute HUD gate",flush=True)
        hud=prepare(source,root,config,source_id,previous_config=previous_config)
        if hud["requests"]:
            print(json.dumps({"stage":hud["stage"],"pending_images":len(hud["requests"]),
                "readings_path":hud["readings_path"],"pages":hud["pages"],"state_path":str(root/"hud_state.json")},ensure_ascii=False))
            return
        from quality_guard import gate_audit,read
        registry_path=root/"match_registry.json"
        problems=gate_audit(hud,read(hud["readings_path"]),read(registry_path) if registry_path.is_file() else None,hud["media"]["duration"])
        if problems:
            save_json(root/"quality_report.json",{"ready":False,"errors":problems})
            print(json.dumps({"stage":"awaiting_match_review","errors":problems[:8],"report":str(root/"quality_report.json")},ensure_ascii=False))
            return
        print(f"HUD gate: {len(hud['windows'])} selected windows; local scan only",flush=True)
        result=local_scan(source,root,config,source_id,hud,args.verbose)
    else:
        result=scan(source,root/"scan.json",root/"scan_cache",fps=config["scan"]["fps"],mode=config["scan"]["mode"],
                audit_seconds=config["scan"]["audit_seconds"],low_roi=config["roi"]["low"],high_roi=config["roi"]["high"],
                ocr_backend=config["scan"]["ocr"],verbose=args.verbose,source_snapshot=source_id)
    draft=root/"confirmed_events.json"
    if not draft.is_file():
        save_json(draft,{"source":result["source"],"duration":result["media"]["duration"],"events":[],
            "instructions":"AI/human must visually confirm events, view bounds and ammo; scan/OCR proposals are not final A events. See references/manifest.md."})
    print(f"STAGE 2/3: visual review required. Workdir: {root}")
    print(f"Next: python apex.py status --workdir \"{root}\"")


def status(work,config=None):
    root,job=job_at(work)
    scan_path=Path(job["scan_index"])
    if config is not None and config["prefilter"]["enabled"] and (root/"hud_state.json").is_file():
        hud=json.loads((root/"hud_state.json").read_text(encoding="utf-8"))
        if hud.get("config_digest")!=digest(config):
            return {"stage":"configuration_changed","action":"Rerun scan with current configuration"}
        if hud.get("requests"):
            return {"stage":"awaiting_hud_review","pending_images":len(hud["requests"]),
                "readings_path":hud["readings_path"],"pages":hud["pages"],"action":"Visually read requested HUD crops, save readings, rerun scan"}
    if not scan_path.is_file():
        if config is not None and config["prefilter"]["enabled"] and (root/"hud_state.json").is_file():
            from quality_guard import gate_audit, read
            registry=root/"match_registry.json"
            errors=gate_audit(hud,read(hud["readings_path"]),read(registry) if registry.is_file() else None,hud["media"]["duration"])
            if errors:
                match_errors=any(e["code"].startswith(("match_","session_","counter_reset","suspected_match")) for e in errors)
                return {"stage":"awaiting_match_review" if match_errors else "awaiting_quality_review",
                        "errors":errors,"workdir":str(root),
                        "action":"Review and import the canonical matches, then rerun scan" if match_errors else "Resolve the reported HUD evidence and rerun scan"}
            return {"stage":"awaiting_local_scan","workdir":str(root),"action":"Rerun scan to continue the prepared local scan"}
        return {"stage":"scan_not_started","workdir":str(root)}
    scan=json.loads(scan_path.read_text(encoding="utf-8"))
    if config is not None:
        try:
            check_scan_config(scan,config)
        except ValueError:
            return {"stage":"configuration_changed","action":"Run scan with current configuration and review resulting pages"}
    if not scan.get("complete"):
        return {"stage":"scan_interrupted_or_running","workdir":str(root),"action":"Rerun scan to resume checkpoint"}
    from review_progress import update
    review=update(scan_path)
    final=root/"final.json"
    clips=[]
    if final.is_file():
        clips=json.loads(final.read_text(encoding="utf-8")).get("segments",[])
    stage="awaiting_page_review" if review["unread_pages"] else "ready_to_plan"
    if final.is_file() and not review["unread_pages"]:
        stage="awaiting_final_interval_review" if any(s.get("review_required") or s.get("reviewed_start") is None for s in clips) else "ready_to_export"
        manifest=json.loads(final.read_text(encoding="utf-8"))
        if config is not None and validate_policy(manifest.get("policy"))!=policy_from_config(config):
            stage="timing_changed_replan_required"
        elif config is not None and manifest.get("config_digest")!=digest(config):
            stage="configuration_changed_replan_required"
    from quality_guard import audit_workdir,read
    events_path=root/"confirmed_events.json"
    quality=audit_workdir(root,config,read(events_path) if events_path.is_file() else None,
                          read(final) if final.is_file() else None) if config else None
    if quality and quality["errors"] and not review["unread_pages"]:
        stage="awaiting_quality_review"
    elif quality and stage=="ready_to_export" and quality["delivery_scope"]=="limited":
        stage="ready_to_export_limited"
    return {"stage":stage,"quality":quality,"workdir":str(root),"fps":scan["fps"],"review_scope":scan["review_scope"],
            "scanned_frames":scan["frame_count"],"candidate_frames":scan["candidate_count"],
            "reviewed_pages":len(review["reviewed_pages"]),"total_pages":len(scan["pages"]),
            "next_page_ids":review["unread_pages"][:12],"planned_clips":len(clips),
            "note":"Ready status is not a claim that exports already passed verification"}


def create_plan(args,config):
    from common import identity,save_json
    from plan_clips import plan_segments
    from review_progress import update
    root,job=job_at(args.workdir)
    scan=json.loads(Path(job["scan_index"]).read_text(encoding="utf-8"))
    check_scan_config(scan,config)
    reviewed=update(job["scan_index"])
    if reviewed["unread_pages"]:
        raise ValueError(f"Visual review pending: {len(reviewed['unread_pages'])} pages; use status and mark after actual review")
    events_path=Path(args.events) if args.events else root/"confirmed_events.json"
    data=json.loads(events_path.read_text(encoding="utf-8"))
    if data.get("source",{}).get("sha256")!=job["source"]["sha256"]:
        raise ValueError("Confirmed events refer to a different video")
    from quality_guard import enforce
    quality=enforce(root,config,data)
    policy=policy_from_config(config)
    segments=plan_segments(data["events"],scan["media"]["duration"],policy)
    result={"schema_version":"2.4","release_version":VERSION,"source":job["source"],"policy":policy,
            "config_digest":digest(config),"events_identity":identity(events_path),
            "delivery_scope":quality["delivery_scope"],"quality_summary":{k:quality[k] for k in ("source_seconds","scan_seconds","scan_fraction","unresolved_seconds","unresolved_fraction","warnings")},
            "coverage":{"scan_index":job["scan_index"],"review_scope":scan["review_scope"],
            "reviewed_pages":reviewed["reviewed_pages"],"reviewer":"saved visual reviews"},"segments":segments}
    save_json(root/"final.json",result)
    for category,filename in (("B","assist_pending.json"),("C","rejected.json"),("D","review_pending.json")):
        save_json(root/filename,{"source":job["source"],"events":[e for e in data["events"] if e.get("class")==category]})
    save_json(root/"effective-config.json",config)
    print(f"PLAN: {len(segments)} clips; {sum(s['review_required'] for s in segments)} need full interval review; {root/'final.json'}")


def export(args,config):
    from common import identity,save_json
    from review_progress import update
    root,job=job_at(args.workdir)
    scan=json.loads(Path(job["scan_index"]).read_text(encoding="utf-8"))
    check_scan_config(scan,config)
    if update(job["scan_index"])["unread_pages"]:
        raise ValueError("Page review cache is missing or invalidated; review pages before export")
    final=root/"final.json"
    if not final.is_file():
        raise ValueError("Run plan and review final intervals before export")
    manifest=json.loads(final.read_text(encoding="utf-8"))
    if validate_policy(manifest.get("policy"))!=policy_from_config(config):
        raise ValueError("Timing configuration changed; run plan and final interval review again")
    if manifest.get("config_digest")!=digest(config):
        raise ValueError("Configuration changed; review affected evidence and replan before export")
    saved_events=manifest.get("events_identity",{})
    if not saved_events.get("path") or identity(saved_events["path"])["sha256"]!=saved_events.get("sha256"):
        raise ValueError("Confirmed events changed or are missing; replan before export")
    from quality_guard import enforce_manifest
    enforce_manifest(manifest,config)
    save_json(root/"effective-config.json",config)
    command=[sys.executable,str(ROOT/"scripts/cut_full.py"),"--src",job["source"]["path"],"--json",str(final),
             "--outdir",str(root/"clips"),"--prefix","highlights","--config",str(root/"effective-config.json")]
    if args.validate_only:
        command.append("--validate-only")
    elif not args.no_compilation:
        command.extend(["--compilation",str(root/"compilation_FULL.mp4")])
    if args.verbose:
        command.append("--verbose")
    print("STAGE 3/3: manifest validation and export",flush=True)
    if getattr(args,"quiet",False):
        result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
        logs=root/"workflow"
        logs.mkdir(exist_ok=True)
        (logs/"export.log").write_text((result.stdout or "")+(result.stderr or ""),encoding="utf-8")
    else:
        result=subprocess.run(command)
    if result.returncode:
        raise ValueError("Export failed; previous validated outputs and audit records are retained")


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version",action="version",version=VERSION)
    sub=ap.add_subparsers(dest="command",required=True)
    from review_transfer import add_commands
    add_commands(sub)
    from workflow import add_commands as add_workflow_commands
    add_workflow_commands(sub)
    from marked_export import add_commands as add_marked_commands
    add_marked_commands(sub)
    from evaluate_reviews import add_commands as add_eval_commands
    add_eval_commands(sub)
    p=sub.add_parser("setup",help="Install dependencies into local .venv")
    p.add_argument("--without-ocr",action="store_true")
    p.add_argument("--create-only",action="store_true",help="Create environment without downloading packages")
    p=sub.add_parser("doctor",help="Check dependencies, tools and optional video")
    p.add_argument("--source")
    p.add_argument("--report")
    p.add_argument("--config")
    p=sub.add_parser("init",help="Save a validated editable configuration")
    p.add_argument("--profile",choices=("default","full-review","legacy-economy","speed"),default="default")
    p.add_argument("--output",default=str(ROOT/".local/config.json"))
    p.add_argument("--ffmpeg")
    p.add_argument("--ffprobe")
    p.add_argument("--overwrite",action="store_true")
    p=sub.add_parser("calibrate",help="Preview banner, spectator, ammo and four-counter HUD rectangles")
    p.add_argument("--source",required=True)
    p.add_argument("--outdir",default="exports/calibration")
    p.add_argument("--at",nargs="+",type=float)
    p.add_argument("--save-config")
    p.add_argument("--config")
    p=sub.add_parser("scan",help="Minute HUD admission (kill/assist growth AND >=150 damage), one midpoint, then local 2fps")
    p.add_argument("-s","--source",required=True)
    p.add_argument("--workdir")
    p.add_argument("--config")
    p.add_argument("--verbose",action="store_true")
    markers = p.add_mutually_exclusive_group()
    markers.add_argument("--markers", help="OBS marker sidecar; same-name sidecars are auto-detected")
    markers.add_argument("--ignore-markers", action="store_true", help="Explicitly scan using the usual whole-source minute HUD gate")
    for name in ("status","review","ammo","mark","plan","export","hud-import","match-import","audit","ammo-tasks","ammo-review-import","interval-import","coverage-import","damage-review-import","admission-review-import","resume-upgrade","hud-recheck-import"):
        p=sub.add_parser(name)
        p.add_argument("--workdir",required=True)
        if name!="status":
            p.add_argument("--config")
        if name in ("review","ammo"):
            p.add_argument("--anchor",required=name=="review",type=float)
        if name=="review":
            p.add_argument("--dense",action="store_true")
        if name=="ammo":
            p.add_argument("--all",action="store_true",help="Generate backtracking jobs from final A event groups")
            p.add_argument("--last-anchor",type=float)
            p.add_argument("--view-start",type=float,default=0)
            p.add_argument("--extended",action="store_true")
        if name=="mark":
            p.add_argument("--pages",nargs="+",type=int,required=True)
            p.add_argument("--decisions",required=True)
            p.add_argument("--reviewer",required=True)
        if name in ("hud-import","match-import","ammo-review-import","interval-import","coverage-import","damage-review-import","admission-review-import","hud-recheck-import"):
            p.add_argument("--submission",required=True)
        if name=="audit":
            p.add_argument("--report")
        if name=="plan":
            p.add_argument("--events")
        if name=="export":
            p.add_argument("--validate-only",action="store_true")
            p.add_argument("--no-compilation",action="store_true")
            p.add_argument("--verbose",action="store_true")
    p=sub.add_parser("package",help="Build a clean source ZIP; does not upload to GitHub")
    p.add_argument("--output",default=f"dist/apex-clipper-{VERSION}.zip")
    p=sub.add_parser("benchmark",help="Compare annotated and predicted anchors without claiming unmeasured savings")
    p.add_argument("--truth",required=True)
    p.add_argument("--predictions",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--tolerance",type=float,default=1)
    p.add_argument("--elapsed-seconds",type=float)
    p.add_argument("--usage")
    return ap


def main():
    # Re-enter the project's own environment when available; never mutate system Python.
    python=env_python()
    if python.is_file() and Path(sys.prefix).resolve()!=python.parent.parent.resolve() and "setup" not in sys.argv[1:] and os.environ.get("APEX_NO_VENV")!="1":
        raise SystemExit(subprocess.call([str(python),str(Path(__file__).resolve()),*sys.argv[1:]]))
    args=parser().parse_args()
    if args.command=="evaluate":
        from evaluate_reviews import evaluate
        print(json.dumps(evaluate(args.labels,args.answers,args.output),ensure_ascii=False))
        return
    if args.command=="marked-export":
        from marked_export import export_marked
        config,_=configuration(args)
        print(json.dumps(export_marked(args.source,args.output,args.markers),ensure_ascii=False))
        return
    if args.command in ("run", "next", "submit"):
        from workflow import execute
        print(json.dumps(execute(args),ensure_ascii=False))
        return
    if args.command in ("preview", "preview-seen"):
        from review_transfer import execute
        print(json.dumps(execute(args),ensure_ascii=False))
        return
    if args.command=="setup":
        from environment import setup
        setup(args.without_ocr,args.create_only)
        return
    if args.command=="package":
        from package_release import package
        print(package(ROOT,args.output))
        return
    if args.command=="benchmark":
        from benchmark import report
        result=report(args.truth,args.predictions,args.output,args.tolerance,args.elapsed_seconds,args.usage)
        print(json.dumps(result,ensure_ascii=False))
        return
    if args.command=="init":
        from common import save_json
        output=Path(args.output)
        if output.exists() and not args.overwrite:
            raise ValueError("Config exists; choose another --output or use --overwrite")
        config=load_config(ROOT/"profiles"/(args.profile+".json"),use_local=False)
        for name in ("ffmpeg","ffprobe"):
            if getattr(args,name):
                config["tools"][name]=getattr(args,name)
        save_json(output,config)
        print(f"CONFIG: {output.resolve()}")
        return
    if args.command=="resume-upgrade":
        from common import save_json
        import shutil
        root,job=job_at(args.workdir)
        if job.get("version") not in ("2.6.4", VERSION):
            raise ValueError("Only the compatible 2.6.4 task can be upgraded; start a fresh task otherwise")
        source=Path(job["source"]["path"])
        stat=source.stat()
        snapshot=job.get("source_stat",{})
        if stat.st_size!=job["source"]["size"] or (snapshot.get("mtime_ns") and stat.st_mtime_ns!=snapshot["mtime_ns"]):
            raise ValueError("Source changed since the task was created; cannot migrate evidence")
        previous=job["version"]
        if previous!=VERSION:
            backup=root/("before_upgrade_"+previous+"_to_"+VERSION)
            backup.mkdir(exist_ok=True)
            for name in ("job.json","scan.json","hud_state.json","confirmed_events.json","final.json","quality_report.json"):
                if (root/name).is_file() and not (backup/name).exists():
                    shutil.copy2(root/name,backup/name)
            job.update(version=VERSION,skill_root=str(ROOT.resolve()),previous_version=previous)
            save_json(root/"job.json",job)
        print(json.dumps({"version":VERSION,"previous_version":previous,"action":"Rerun scan, review changed pages and all final segment damage evidence"}))
        return
    if args.command=="hud-recheck-import":
        from hud_import import recheck
        configuration(args)
        print(json.dumps(recheck(Path(args.workdir)/"hud_state.json",args.submission),ensure_ascii=False))
        return
    if args.command=="damage-review-import":
        from damage_review import import_damage_reviews
        config,_=configuration(args)
        print(json.dumps(import_damage_reviews(args.workdir,args.submission,config),ensure_ascii=False))
        return
    if args.command=="admission-review-import":
        from damage_review import import_admission_reviews
        configuration(args)
        print(json.dumps(import_admission_reviews(args.workdir,args.submission),ensure_ascii=False))
        return
    config,config_path=configuration(args)
    if args.command=="doctor":
        from common import save_json
        from environment import doctor
        report=doctor(config,args.source)
        for check in report["checks"]:
            print(f"{'OK' if check['ok'] else 'MISSING'} {check['name']}: {check['detail']}")
            if not check["ok"]:
                print("  Fix: "+check["fix"])
        print(report["model_vision"])
        if args.report:
            save_json(args.report,report)
        if not report["ready_for_local_scan"]:
            raise SystemExit(2)
    elif args.command=="calibrate":
        from calibrate import calibrate
        calibrate(args.source,args.outdir,config,args.at,args.save_config)
    elif args.command=="scan":
        start_scan(args,config,config_path)
    elif args.command=="status":
        print(json.dumps(status(args.workdir,config),ensure_ascii=False))
    elif args.command in ("audit","match-import","ammo-tasks","ammo-review-import","interval-import","coverage-import"):
        from quality_guard import read,import_registry,audit_workdir,ammo_tasks,import_ammo_reviews,import_intervals,import_coverage_review
        from common import save_json
        root,job=job_at(args.workdir)
        if args.command=="match-import":
            print(json.dumps(import_registry(root,args.submission),ensure_ascii=False))
        elif args.command=="coverage-import":
            print(json.dumps(import_coverage_review(root,args.submission,config),ensure_ascii=False))
        elif args.command=="ammo-review-import":
            print(json.dumps(import_ammo_reviews(root,args.submission,config),ensure_ascii=False))
        elif args.command=="interval-import":
            print(json.dumps(import_intervals(root,args.submission,config),ensure_ascii=False))
        elif args.command=="ammo-tasks":
            require_admitted_events(root,config)
            tasks=ammo_tasks(read(root/"confirmed_events.json")["events"],
                             {**job["source"],"duration":read(root/"scan.json")["media"]["duration"]},config)
            save_json(root/"ammo_tasks.json",{"source":job["source"],"config_digest":digest(config),"tasks":tasks})
            print(json.dumps({"tasks":len(tasks),"path":str(root/"ammo_tasks.json")},ensure_ascii=False))
        else:
            events=read(root/"confirmed_events.json") if (root/"confirmed_events.json").is_file() else None
            manifest=read(root/"final.json") if (root/"final.json").is_file() else None
            report=audit_workdir(root,config,events,manifest)
            save_json(args.report or root/"quality_report.json",report)
            print(json.dumps(report,ensure_ascii=False))
    elif args.command=="hud-import":
        from hud_import import merge
        root,_=job_at(args.workdir)
        print(json.dumps(merge(root/"hud_state.json",args.submission)))
    elif args.command=="mark":
        from review_progress import update
        _,job=job_at(args.workdir)
        print(json.dumps(update(job["scan_index"],args.pages,args.decisions,args.reviewer)))
    elif args.command=="review":
        from review_event import review
        from common import job_snapshot
        root,job=job_at(args.workdir)
        require_admitted_events(root,config,[args.anchor])
        review(job["source"]["path"],args.anchor,root/"events",args.dense,
               config["roi"]["low"],config["roi"]["high"],**config["review"],ocr_backend=config["scan"]["ocr"],source_snapshot=job_snapshot(job))
    elif args.command=="ammo":
        from ammo_diff import analyze
        from common import job_snapshot
        root,job=job_at(args.workdir)
        lookback=config["timing"]["max_lookback" if args.extended else "lookback"]
        if args.all:
            require_admitted_events(root,config)
            from quality_guard import ammo_tasks,read
            from common import save_json,identity
            events=read(root/"confirmed_events.json")
            tasks=ammo_tasks(events["events"],{**job["source"],"duration":read(root/"scan.json")["media"]["duration"]},config)
            for task in tasks:
                analysis_start=max(0,task["own_view_start"],task["anchors"][0]-lookback)
                analysis=analyze(job["source"]["path"],analysis_start,task["end"],root/"ammo",
                                 config["scan"]["fps"],config["roi"]["ammo"],config["scan"]["ocr"],source_snapshot=job_snapshot(job))
                task["analysis_identity"]=identity(analysis["analysis_path"])
                task["review_template"]={"task_id":task["task_id"],"anchors":task["anchors"],
                    "analysis_path":analysis["analysis_path"],"checked_start":analysis_start,"checked_end":task["end"],
                    "decision":None,"fire_start":None,"same_weapon_confirmed":None,"reviewer":None,"reason":None,"evidence_frames":[]}
            save_json(root/"ammo_tasks.json",{"source":job["source"],"config_digest":digest(config),"tasks":tasks})
            print(json.dumps({"tasks":len(tasks),"review_templates":str(root/"ammo_tasks.json")},ensure_ascii=False))
        else:
            if args.anchor is None:
                raise ValueError("Use ammo --all for final groups, or supply --anchor for diagnostic review")
            require_admitted_events(root,config,[args.anchor,args.last_anchor or args.anchor])
            analyze(job["source"]["path"],max(0,args.view_start,args.anchor-lookback),args.last_anchor or args.anchor,
                    root/"ammo",config["scan"]["fps"],config["roi"]["ammo"],config["scan"]["ocr"],source_snapshot=job_snapshot(job))
    elif args.command=="plan":
        create_plan(args,config)
    elif args.command=="export":
        export(args,config)


if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Interrupted; rerun the same scan command to resume its saved checkpoint")
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise SystemExit(f"ERROR: {exc}")
