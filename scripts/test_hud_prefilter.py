"""Behavioral tests for the minute gate and real media integration; no recall claims."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import apex
from common import binary,identity,probe,run,save_json
from config import load_config,policy_from_config
from cut_full import validate_manifest
from hud_prefilter import evaluate,key,timeline,validate_readings,validate_coverage
from plan_clips import plan_segments
from test_public import cli
from test_guard_support import import_fixture_readings


def row(t,k=0,a=0,d=0,p=0,match="one",state="valid"):
    return dict(time=t,kills=k,assists=a,participation=p,damage=d,match_id=match,
                state=state,own_view=state=="valid",reviewer="synthetic fixture")


def table(*rows):
    return {key(r["time"]):r for r in rows}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.settings=load_config(ROOT/"profiles/speed.json",use_local=False)["prefilter"]

    def test_damage_exact_threshold_and_bisection_only_once(self):
        rows=table(row(0),row(30,d=150),row(60,k=1,d=150))
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual(result["requests"],[])
        self.assertEqual([(w["start"],w["end"]) for w in result["windows"]],[(30,60)])
        rows[key(60)]["damage"]=149
        self.assertEqual(evaluate(rows,[0,60],self.settings)["windows"],[])

    def test_both_halves_keep_and_participation_never_triggers(self):
        rows=table(row(0),row(30,k=1,d=80),row(60,k=1,a=1,d=160))
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual(len(result["windows"]),2)
        rows=table(row(0),row(60,p=3,d=400))
        self.assertFalse(evaluate(rows,[0,60],self.settings)["windows"])

    def test_pending_is_not_missing_or_zero(self):
        result=evaluate(table(row(0)),[0,60],self.settings)
        self.assertEqual([r["time"] for r in result["requests"]],[60])
        self.assertEqual(result["windows"],[])
        with self.assertRaises(ValueError):
            validate_readings({"cache_key":"x","readings":[row(0,k=True)]},"x")
        with self.assertRaises(ValueError):
            validate_readings({"cache_key":"x","readings":[row(0,match=None)]},"x")

    def test_missing_endpoint_requests_exactly_nine_then_stops(self):
        rows=table(row(0),row(60,state="missing"))
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual([r["time"] for r in result["requests"]],list(range(6,60,6)))
        for t in range(6,60,6):
            rows[key(t)]=row(t,state="missing")
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual(result["requests"],[])
        self.assertFalse(result["windows"])
        self.assertTrue(result["unresolved"])

    def test_ten_way_damage_once_not_every_six_seconds(self):
        rows=table(row(0),row(60,state="missing"))
        for t in range(6,60,6):
            rows[key(t)]=row(t,k=int(t>=42),d=min(180,int(t/6)*30))
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual([(w["start"],w["end"]) for w in result["windows"]],[(36,42)])
        self.assertTrue(any(r["interval"]==[54,60] for r in result["unresolved"]))

    def test_reset_and_new_match_never_compared_to_zero_or_old_match(self):
        rows=table(row(0,k=5,d=1000),row(60,k=1,d=200,match="two"))
        for t in range(6,60,6):
            rows[key(t)]=row(t,k=1,d=200,match="two")
        result=evaluate(rows,[0,60],self.settings)
        self.assertFalse(result["windows"])
        self.assertTrue(result["unresolved"])
        # Even a mistakenly reused match label cannot bridge a counter reset.
        rows[key(60)]["match_id"]="one"
        result=evaluate(rows,[0,60],self.settings)
        self.assertFalse(result["windows"])

    def test_missing_midpoint_reuses_ten_way_midpoint(self):
        rows=table(row(0),row(30,state="uncertain"),row(60,a=1,d=200))
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual(len(result["requests"]),8)
        self.assertNotIn(30,[r["time"] for r in result["requests"]])

    def test_sparse_recovery_windows_capped_without_more_pictures(self):
        rows=table(row(0),row(60,a=1,d=200))
        for t in range(6,60,6):
            rows[key(t)]=row(t,state="missing")
        result=evaluate(rows,[0,60],self.settings)
        self.assertEqual(result["requests"],[])
        self.assertEqual([(w["start"],w["end"]) for w in result["windows"]],[(0,30),(30,60)])

    def test_tail_and_last_real_frame(self):
        times=timeline({"duration":125,"fps":30})
        self.assertEqual(times[:3],[0,60,120])
        self.assertAlmostEqual(times[-1],124.966667)
        result=evaluate(table(row(120),row(times[-1],a=1,d=150)),[120,times[-1]],self.settings)
        self.assertFalse(result["requests"])
        self.assertEqual(len(result["windows"]),1)


class AssistTests(unittest.TestCase):
    def event(self,t,kind):
        return dict(class_="unused",**{"class":"A"},spectate=False,anchor=t,event_type=kind,
            own_view_start=0,own_view_end=100,ammo="unknown",fire_start=None,
            evidence="本人击倒；助攻，消灭 可变ID",evidence_frames=[],reviewed_start=0,reviewed_end=100)

    def test_assist_chains_with_knockdown_and_separate_gap(self):
        segments=plan_segments([self.event(20,"double_banner"),self.event(27,"assist_elimination"),
                               self.event(35,"assist_elimination")],100)
        self.assertEqual([s["anchors"] for s in segments],[[20,27],[35]])
        self.assertEqual([s["end"] for s in segments],[29,37])
        self.assertEqual(segments[0]["event_types"],["assist_elimination","double_banner"])


class HUDMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            binary("ffmpeg");binary("ffprobe")
        except ValueError as exc:
            raise unittest.SkipTest(str(exc))

    def test_cli_hud_local_scan_assist_export_and_invalidation(self):
        with tempfile.TemporaryDirectory(prefix="apex_hud_中文_") as temp:
            root=Path(temp)
            source=root/"fixture.mp4"
            run([binary("ffmpeg"),"-y","-v","error","-f","lavfi","-i","color=black:size=640x360:rate=10",
                 "-t","62","-c:v","libx264","-preset","ultrafast",str(source)])
            config=load_config(ROOT/"profiles/speed.json",use_local=False)
            config["scan"]["ocr"]="off"
            settings=root/"settings.json";save_json(settings,config)
            work=root/"work"
            def scan():
                if (work/"hud_state.json").exists() and not (work/"match_registry.json").exists():
                    from test_guard_support import registry_fixture
                    registry_fixture(work,62)
                result=cli("scan","--source",source,"--workdir",work,"--config",settings)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return json.loads((work/"hud_state.json").read_text(encoding="utf-8"))
            state=scan()
            self.assertEqual(len(state["requests"]),3)
            self.assertFalse((work/"scan.json").exists())
            self.assertNotEqual(cli("plan","--workdir",work).returncode,0)
            readings=Path(state["readings_path"])
            data=json.loads(readings.read_text(encoding="utf-8"))
            for r in data["readings"]:
                r.update(row(r["time"],a=int(r["time"]>=60),d=200 if r["time"]>=60 else 0))
            data=import_fixture_readings(work,data["readings"])
            state=scan()
            self.assertEqual([r["time"] for r in state["requests"]],[30])
            self.assertEqual(json.loads(cli("status","--workdir",work).stdout)["stage"],"awaiting_hud_review")
            data=json.loads(readings.read_text(encoding="utf-8"))
            incoming=[row(r["time"],d=200) for r in data["readings"] if r["state"]=="pending"]
            data=import_fixture_readings(work,incoming)
            state=scan()
            self.assertFalse(state["requests"])
            index=json.loads((work/"scan.json").read_text(encoding="utf-8"))
            self.assertEqual(index["mode"],"hud_prefiltered_scan")
            self.assertEqual(index["prefilter"]["ranges"],[[29,61]])
            self.assertEqual(index["frame_count"],64)
            validate_coverage(index,probe(source))
            decisions=root/"decisions.json";save_json(decisions,{"events":[]})
            result=cli("mark","--workdir",work,"--pages",*[p["id"] for p in index["pages"]],
                       "--decisions",decisions,"--reviewer","synthetic engineering fixture")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            draft=json.loads((work/"confirmed_events.json").read_text(encoding="utf-8"))
            draft["events"]=[dict(**{"class":"A"},spectate=False,anchor=40,event_type="assist_elimination",
                own_view_start=0,own_view_end=62,ammo="unknown",fire_start=None,
                evidence="SYNTHETIC ENGINEERING TEST: 本人助攻，消灭 ID",evidence_frames=[index["pages"][0]["path"]],
                reviewed_start=0,reviewed_end=62)]
            save_json(work/"confirmed_events.json",draft)
            from test_guard_support import ammo_fixture,interval_fixture,damage_fixture
            ammo_fixture(work,config)
            result=cli("plan","--workdir",work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            interval_fixture(work,config)
            damage_fixture(work,config)
            result=cli("export","--workdir",work,"--validate-only")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=cli("export","--workdir",work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertAlmostEqual(probe(work/"compilation_FULL.mp4")["duration"],5,delta=.15)
            original=copy.deepcopy(data)
            data["readings"][0]["damage"]=1
            save_json(readings,data)
            self.assertNotEqual(cli("export","--workdir",work,"--validate-only").returncode,0)
            save_json(readings,original)
            # A gap in the aggregate local scan cannot pass coverage validation.
            broken=copy.deepcopy(index);broken["frames"].pop()
            with self.assertRaises(ValueError):validate_coverage(broken,probe(source))

    def test_fixed_ten_way_cli_recovery_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/"recovery.mp4";work=root/"work"
            run([binary("ffmpeg"),"-y","-v","error","-f","lavfi","-i","color=black:size=320x180:rate=10",
                 "-t","60.1","-c:v","libx264","-preset","ultrafast",str(source)])
            cfg=load_config(ROOT/"profiles/speed.json",use_local=False);cfg["scan"]["ocr"]="off"
            settings=root/"config.json";save_json(settings,cfg)
            def scan():
                if (work/"hud_state.json").exists() and not (work/"match_registry.json").exists():
                    from test_guard_support import registry_fixture
                    registry_fixture(work,60.1)
                result=cli("scan","--source",source,"--workdir",work,"--config",settings)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return json.loads((work/"hud_state.json").read_text(encoding="utf-8"))
            state=scan();self.assertEqual([r["time"] for r in state["requests"]],[0,60])
            readings=Path(state["readings_path"])
            data=json.loads(readings.read_text(encoding="utf-8"))
            incoming=[row(r["time"],state="valid" if r["time"]==0 else "missing") for r in data["readings"]]
            data=import_fixture_readings(work,incoming)
            state=scan();self.assertEqual(len(state["requests"]),9)
            self.assertEqual([r["time"] for r in state["requests"]],list(range(6,60,6)))
            data=json.loads(readings.read_text(encoding="utf-8"))
            incoming=[]
            for r in data["readings"]:
                if r["state"]=="pending":
                    t=r["time"];incoming.append(row(t,a=int(t>=42),d=min(180,int(t/6)*30)))
            data=import_fixture_readings(work,incoming)
            state=scan();self.assertFalse(state["requests"])
            index=json.loads((work/"scan.json").read_text(encoding="utf-8"))
            self.assertEqual(index["prefilter"]["ranges"],[[35,43]])
            validate_coverage(index,probe(source))
            previous=index["cache_key"]
            state=scan()
            index=json.loads((work/"scan.json").read_text(encoding="utf-8"))
            self.assertEqual(index["cache_key"],previous)
            self.assertEqual(len(list(readings.parent.glob("source_*.png"))),11)

    def test_empty_gate_is_not_unreviewed_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/"quiet.mp4";work=root/"work"
            run([binary("ffmpeg"),"-y","-v","error","-f","lavfi","-i","color=black:size=320x180:rate=10",
                 "-t","3","-c:v","libx264","-preset","ultrafast",str(source)])
            cfg=load_config(ROOT/"profiles/speed.json",use_local=False);cfg["scan"]["ocr"]="off"
            settings=root/"config.json";save_json(settings,cfg)
            result=cli("scan","--source",source,"--workdir",work,"--config",settings)
            self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads((work/"hud_state.json").read_text(encoding="utf-8"))
            p=Path(state["readings_path"]);data=json.loads(p.read_text(encoding="utf-8"))
            data=import_fixture_readings(work,[row(r["time"]) for r in data["readings"]])
            from test_guard_support import registry_fixture
            registry_fixture(work,3)
            result=cli("scan","--source",source,"--workdir",work,"--config",settings)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=cli("plan","--workdir",work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=cli("export","--workdir",work,"--validate-only")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            final=json.loads((work/"final.json").read_text(encoding="utf-8"))
            self.assertEqual(final["segments"],[])


if __name__=="__main__":
    unittest.main(verbosity=2)
