"""Regression invariants for the October 18 clipping failure; no real-clip recall claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from common import identity, save_json
from config import load_config, digest, policy_from_config
from hud_prefilter import evaluate, key
from hud_import import merge
from quality_guard import (check_match_rows, validate_registry, gate_audit, ammo_tasks, check_ammo,
                           check_clip_starts, audit_workdir, import_registry, import_coverage_review)
from plan_clips import plan_segments
from test_hud_prefilter import row, table


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.evidence=self.root/"visual.png"
        self.evidence.write_bytes(b"SYNTHETIC ENGINEERING EVIDENCE")
        self.config=load_config(use_local=False)
        self.source={"sha256":"synthetic-source","duration":120}
        self.registry={"source_sha256":"synthetic-source","matches":[{
            "match_id":"canonical","session_id":"game-001","start":0,"end":120,
            "reviewer":"synthetic fixture","reason":"Known one-session fixture",
            "evidence_frames":[str(self.evidence)],"evidence_identities":[identity(self.evidence)]}]}

    def event(self,t):
        return {"class":"A","spectate":False,"anchor":t,"event_type":"double_banner",
                "own_view_start":0,"own_view_end":120,"ammo":"unknown","fire_start":None,
                "evidence":"SYNTHETIC ENGINEERING FIXTURE","evidence_frames":[str(self.evidence)]}

    def valid_ammo(self,events):
        tasks=ammo_tasks(events,self.source,self.config)
        for task in tasks:
            trace=[{"time":task["start"]+n/self.config["scan"]["fps"],"path":str(self.evidence),"value":None}
                   for n in range(round((task["end"]-task["start"])*self.config["scan"]["fps"]))]
            path=self.root/(task["task_id"]+".json")
            save_json(path,{"source":self.source,"config":{"start":task["start"],"end":task["end"],
                "roi":self.config["roi"]["ammo"],"fps":self.config["scan"]["fps"]},
                "trace":trace,"ocr_error":"OCR unavailable in this engineering fixture"})
            review={"task_id":task["task_id"],"anchors":task["anchors"],"analysis_path":str(path),
                "analysis_sha256":identity(path)["sha256"],"checked_start":task["start"],"checked_end":task["end"],
                "decision":"unknown","fire_start":None,"reviewer":"synthetic fixture",
                "reason":"All trace images explicitly inspected in this synthetic fixture",
                "evidence_frames":[str(self.evidence)],"evidence_identities":[identity(self.evidence)]}
            for event in events:
                if event["anchor"] in task["anchors"]:
                    event["ammo_review"]=copy.deepcopy(review)
        return events

    def test_default_full_source_and_explicit_speed(self):
        self.assertFalse(self.config["prefilter"]["enabled"])
        self.assertEqual((self.config["scan"]["fps"],self.config["scan"]["mode"]),(4,"full"))
        speed=load_config(Path(__file__).resolve().parent.parent/"profiles/speed.json",use_local=False)
        self.assertTrue(speed["prefilter"]["enabled"])
        self.assertEqual(speed["quality"]["goal"],"fast")

    def test_recorded_match_split_keeps_both_observed_knockdowns(self):
        assets=Path(__file__).resolve().parent.parent/"assets/regression"
        fixture=json.loads((assets/"hud-match-split.json").read_text(encoding="utf-8"))
        rows=fixture["readings"]
        before=evaluate({key(r["time"]):r for r in rows},fixture["minute_times"],self.config["prefilter"])
        self.assertFalse(any(w["start"]<=t<=w["end"] for t in fixture["known_banner_times"] for w in before["windows"]))
        registered=copy.deepcopy(self.registry)
        match=registered["matches"][0]
        match.update(start=fixture["registry_interval"][0],end=fixture["registry_interval"][1],
                     evidence_frames=[str(assets/p) for p in fixture["evidence"]])
        match["evidence_identities"]=[identity(p) for p in match["evidence_frames"]]
        self.assertIn("match_label_conflict",{e["code"] for e in check_match_rows(rows,registered,"synthetic-source",1000)})
        corrected=copy.deepcopy(rows)
        for r in corrected:r["match_id"]="canonical"
        after=evaluate({key(r["time"]):r for r in corrected},fixture["minute_times"],self.config["prefilter"])
        for t in fixture["known_banner_times"]:
            self.assertTrue(any(w["start"]<=t<=w["end"] for w in after["windows"]),t)

    def test_same_session_different_worker_labels_are_blocked(self):
        rows=[row(54,k=1,d=563,match="batch-A"),row(60,k=3,d=747,match="batch-B")]
        codes={e["code"] for e in check_match_rows(rows,self.registry,"synthetic-source",120)}
        self.assertIn("match_label_conflict",codes)
        self.assertIn("suspected_match_split",codes)

    def test_different_real_sessions_are_not_merged(self):
        registry=copy.deepcopy(self.registry)
        registry["matches"][0]["end"]=60
        second=copy.deepcopy(registry["matches"][0])
        second.update(match_id="second",session_id="game-002",start=60,end=120)
        registry["matches"].append(second)
        rows=[row(54,k=1,d=563,match="canonical"),row(60,k=3,d=747,match="second")]
        self.assertEqual(check_match_rows(rows,registry,"synthetic-source",120),[])

    def test_registry_evidence_or_duplicate_session_invalidates(self):
        broken=copy.deepcopy(self.registry)
        second=copy.deepcopy(broken["matches"][0]);second.update(match_id="other",start=60)
        broken["matches"][0]["end"]=60;broken["matches"].append(second)
        with self.assertRaises(ValueError):validate_registry(broken,"synthetic-source",120)
        self.evidence.write_bytes(b"changed")
        with self.assertRaises(ValueError):validate_registry(self.registry,"synthetic-source",120)

    def test_eligible_excluded_window_detected_after_canonicalization(self):
        rows=[row(t,k=1 if t<60 else 3,d=417 if t<54 else 563 if t==54 else 747,
                  match="batch-B" if t in (0,60) else "batch-A") for t in range(0,61,6)]
        rows.append(row(30,k=1,d=459,match="batch-A"))
        # One timestamp only; fixture intentionally exercises a midpoint that uses another batch label.
        rows={key(r["time"]):r for r in rows}
        hud={"source":self.source,"minute_times":[0,60],"settings":self.config["prefilter"],
             "scan_ranges":[],"requests":[]}
        problems=gate_audit(hud,{"readings":list(rows.values())},self.registry,120)
        self.assertIn("gate_window_missing",{p["code"] for p in problems})
        fixed=copy.deepcopy(list(rows.values()))
        for r in fixed:r["match_id"]="canonical"
        result=evaluate({key(r["time"]):r for r in fixed},[0,60],self.config["prefilter"])
        self.assertTrue(any(w["start"]<=54 and w["end"]>=60 for w in result["windows"]))

    def test_worker_import_conflict_is_atomic(self):
        data={"cache_key":"x","readings":[row(54,state="pending")]}
        save_json(self.root/"rows.json",data)
        save_json(self.root/"match_registry.json",self.registry)
        report={"cache_key":"x","readings_path":str(self.root/"rows.json"),"requests":[{"time":54}],
                "source":self.source,"media":{"duration":120}}
        save_json(self.root/"hud_state.json",report)
        save_json(self.root/"submission.json",{"cache_key":"x","readings":[row(54,match="invented")]})
        original=(self.root/"rows.json").read_bytes()
        with self.assertRaises(ValueError):merge(self.root/"hud_state.json",self.root/"submission.json")
        self.assertEqual((self.root/"rows.json").read_bytes(),original)

    def test_final_event_groups_generate_tasks_without_diagnostic_anchor(self):
        events=[self.event(40),self.event(46),self.event(80)]
        tasks=ammo_tasks(events,self.source,self.config)
        self.assertEqual([t["anchors"] for t in tasks],[[40,46],[80]])
        self.assertEqual([(t["start"],t["end"]) for t in tasks],[(25,46),(65,80)])
        self.assertNotEqual(tasks[0]["task_id"],ammo_tasks([self.event(41)],self.source,self.config)[0]["task_id"])

    def test_unknown_does_not_waive_final_group_review(self):
        self.assertEqual(check_ammo([self.event(40)],self.source,self.config)[0]["code"],"ammo_review_missing")
        events=self.valid_ammo([self.event(40)])
        self.assertEqual(check_ammo(events,self.source,self.config),[])
        events[0]["ammo_review"]["anchors"]=[39]
        self.assertEqual(check_ammo(events,self.source,self.config)[0]["code"],"ammo_review_invalid")

    def test_foreign_source_changed_analysis_and_missing_trace_block(self):
        for mutation in ("source","analysis","trace"):
            with self.subTest(mutation=mutation):
                events=self.valid_ammo([self.event(40)])
                review=events[0]["ammo_review"];path=Path(review["analysis_path"])
                data=json.loads(path.read_text())
                if mutation=="source":data["source"]["sha256"]="another"
                elif mutation=="trace":data["trace"].pop()
                else:self.evidence.write_bytes(b"changed evidence")
                if mutation!="analysis":
                    save_json(path,data);review["analysis_sha256"]=identity(path)["sha256"]
                self.assertTrue(check_ammo(events,self.source,self.config))

    def test_starting_mid_fire_requires_new_cut(self):
        seg=plan_segments([self.event(40)],120)[0]
        seg["start_review"]={"time":seg["start"],"reviewer":"synthetic fixture","reason":"Synthetic start",
            "firing_at_start":True,"omitted_lead_in":False,
            "frames":[{"time":seg["start"]-.5,"path":str(self.evidence)},{"time":seg["start"],"path":str(self.evidence)}],
            "evidence_identities":[identity(self.evidence),identity(self.evidence)]}
        self.assertTrue(check_clip_starts({"segments":[seg]}))
        seg["start_review"]["firing_at_start"]=False
        self.assertEqual(check_clip_starts({"segments":[seg]}),[])
        seg["start"]-=1
        self.assertTrue(check_clip_starts({"segments":[seg]}))

    def test_sparse_coverage_requires_bound_limited_review_not_minimum_clip_count(self):
        self.config["prefilter"]["enabled"]=True
        self.config["quality"]["goal"]="fast"
        duration=11763.133333
        rows=[row(0,state="missing"),row(duration-1/30,state="missing")]
        save_json(self.root/"rows.json",{"cache_key":"x","readings":rows})
        save_json(self.root/"job.json",{"source":self.source})
        save_json(self.root/"hud_state.json",{"source":self.source,"media":{"duration":duration},
            "readings_path":str(self.root/"rows.json"),"minute_times":[0,duration-1/30],
            "settings":self.config["prefilter"],"requests":[{"time":6}],"scan_ranges":[[0,104]],
            "unresolved":[{"interval":[1000,7549.1]}]})
        report=audit_workdir(self.root,self.config)
        self.assertAlmostEqual(report["scan_fraction"],104/duration)
        self.assertIn("coverage_review_required",{e["code"] for e in report["errors"]})
        decision={"delivery_scope":"limited","reviewer":"synthetic fixture","reason":"Explicit speed tradeoff in a synthetic test",
                  "issues":[w["code"] for w in report["warnings"]],"samples":[]}
        for a,b in [(200,210),(5000,5010),(11000,11010)]:
            decision["samples"].append({"interval":[a,b],"decision":"no_qualifying_banner",
                "reason":"Known synthetic blank interval","evidence_frames":[str(self.evidence)]})
        save_json(self.root/"coverage_input.json",decision)
        import_coverage_review(self.root,self.root/"coverage_input.json",self.config)
        checked=audit_workdir(self.root,self.config)
        self.assertNotIn("coverage_review_required",{e["code"] for e in checked["errors"]})
        rows[0]["note"]="new evidence";save_json(self.root/"rows.json",{"cache_key":"x","readings":rows})
        self.assertIn("coverage_review_required",{e["code"] for e in audit_workdir(self.root,self.config)["errors"]})


if __name__=="__main__":
    unittest.main(verbosity=2)
