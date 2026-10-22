"""Behavioral checks for public setup, shared settings, CLI workflow and packaging."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import apex
import test_pipeline as pipeline
from benchmark import compare
from common import binary,probe,run,save_json
from config import digest,load_config,policy_from_config,validate
from cut_full import validate_manifest
from environment import doctor,setup
from package_release import package
from plan_clips import plan_segments


def cli(*args,cwd=None,entry=None):
    return subprocess.run([sys.executable,str(entry or ROOT/"apex.py"),*map(str,args)],cwd=cwd,
        env=dict(os.environ,PYTHONUTF8="1",APEX_NO_VENV="1"),capture_output=True,text=True,encoding="utf-8",errors="replace")


class ConfigTests(unittest.TestCase):
    def test_portable_profile_retains_local_tool_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            save_json(root/"profiles/default.json",load_config(use_local=False))
            save_json(root/".local/config.json",{"tools":{"ffmpeg":"my-ffmpeg","ffprobe":"my-ffprobe"},"scan":{"fps":5}})
            save_json(root/"portable.json",{"scan":{"fps":4},"tools":{"ffmpeg":None,"ffprobe":None}})
            with patch("config.ROOT",root):
                config=load_config(root/"portable.json")
            self.assertEqual(config["tools"]["ffmpeg"],"my-ffmpeg")
            self.assertEqual(config["scan"]["fps"],4)

    def test_default_and_full_profile(self):
        default=load_config(use_local=False)
        full=load_config(ROOT/"profiles/full-review.json")
        self.assertEqual((default["timing"]["post_roll"],default["timing"]["chain_gap"]),(2,7))
        self.assertEqual((default["scan"]["fps"],full["scan"]["fps"],full["scan"]["mode"]),(4,4,"full"))
        speed=load_config(ROOT/"profiles/speed.json",use_local=False)
        self.assertNotEqual(digest(default),digest(speed))

    def test_bad_settings_rejected(self):
        for group,key,value in [("scan","fps",True),("scan","fps",1),("timing","post_roll",8),
                                ("roi","low",[.9,.5,.5,.3]),("timing","lookback",25)]:
            config=load_config(use_local=False)
            config[group][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                validate(config)

    def test_unknown_setting_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"config.json"
            save_json(path,{"scan":{"fpps":4}})
            with self.assertRaisesRegex(ValueError,"Unknown setting"):
                load_config(path)

    def test_custom_timing_reaches_plan_and_validator(self):
        fixture=pipeline.ManifestTests("test_valid")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        config=load_config(use_local=False)
        config["timing"].update(post_roll=3,chain_gap=8,pre_roll=2,fire_padding=1.5)
        policy=policy_from_config(validate(config))
        event={"anchor":6,"class":"A","spectate":False,"event_type":"double_banner",
               "own_view_start":0,"own_view_end":10,"ammo":"found","fire_start":4,
               "evidence":"SYNTHETIC CONFIG TEST","evidence_frames":fixture.segment["evidence_frames"],
               "reviewed_start":0,"reviewed_end":10}
        segments=plan_segments([event],10,policy)
        self.assertEqual((segments[0]["start"],segments[0]["end"],segments[0]["original_start"]),(2.5,9,4))
        fixture.data.update(policy=policy,segments=segments)
        self.assertEqual(len(validate_manifest(fixture.data,fixture.source,fixture.media)),1)
        fixture.data["policy"]["post_roll"]=2
        with self.assertRaisesRegex(ValueError,"last kill"):
            validate_manifest(fixture.data,fixture.source,fixture.media)

    def test_job_remembers_and_reloads_config_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            config=root/"settings.json"
            save_json(config,{"scan":{"fps":3}})
            save_json(root/"job.json",{"config_path":str(config)})
            args=argparse.Namespace(config=None,workdir=str(root))
            self.assertEqual(apex.configuration(args)[0]["scan"]["fps"],3)
            save_json(config,{"scan":{"fps":4}})
            self.assertEqual(apex.configuration(args)[0]["scan"]["fps"],4)

    def test_scan_config_change_requires_rescan(self):
        config=load_config(use_local=False)
        config["prefilter"]["enabled"]=False
        record={"config":{"fps":config["scan"]["fps"],"mode":config["scan"]["mode"],"audit_seconds":15,"ocr_backend":"auto",
                          "low_roi":config["roi"]["low"],"high_roi":config["roi"]["high"]}}
        apex.check_scan_config(record,config)
        changed=copy.deepcopy(config)
        changed["roi"]["low"][0]=.27
        with self.assertRaisesRegex(ValueError,"changed"):
            apex.check_scan_config(record,changed)


class PackagingTests(unittest.TestCase):
    def tiny_root(self,root):
        for name in ("SKILL.md","README.md","LICENSE","apex.py","profiles/default.json","scripts/main.py"):
            path=root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text("{}" if path.suffix==".json" else "fixture",encoding="utf-8")

    def test_private_artifacts_excluded_and_hashes_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"project"
            self.tiny_root(root)
            for name in (".local/config.json",".venv/secret.py","exports/audit.json","scripts/__pycache__/main.pyc","exports/video.mp4"):
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b"PRIVATE")
            output=Path(temp)/"release.zip"
            package(root,output)
            with zipfile.ZipFile(output) as archive:
                names=archive.namelist()
                self.assertFalse(any("PRIVATE" in archive.read(name).decode("utf-8",errors="ignore") for name in names))
                manifest=json.loads(archive.read("apex-clipper/release-manifest.json"))
                for item in manifest["files"]:
                    self.assertEqual(hashlib.sha256(archive.read("apex-clipper/"+item["path"])).hexdigest(),item["sha256"])

    def test_personal_path_stops_packaging(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"project"
            self.tiny_root(root)
            # Construct a private-path fixture without embedding such a path in the distributed test source.
            private="C:"+"/"+"Users"+"/"+"Someone"+"/"+"private.mp4"
            (root/"scripts/leak.py").write_text(repr(private),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"Personal absolute"):
                package(root,Path(temp)/"release.zip")

    def test_real_release_extracts_and_initializes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=root/"release.zip"
            result=package(ROOT,output)
            self.assertFalse(result["uploaded"])
            with zipfile.ZipFile(output) as archive:
                self.assertIn("apex-clipper/agents/openai.yaml",archive.namelist())
                archive.extractall(root/"unpacked")
            entry=root/"unpacked/apex-clipper/apex.py"
            check=cli("init","--output",root/"profile.json",entry=entry)
            self.assertEqual(check.returncode,0,check.stdout+check.stderr)
            self.assertEqual(json.loads((root/"profile.json").read_text())["timing"]["post_roll"],2)


class EnvironmentTests(unittest.TestCase):
    def test_help_and_init_work_without_ocr_startup(self):
        self.assertEqual(cli("--help").returncode,0)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"config.json"
            self.assertEqual(cli("init","--output",path).returncode,0)
            self.assertNotEqual(cli("init","--output",path).returncode,0)

    def test_missing_tools_are_reported(self):
        config=load_config(use_local=False)
        config["tools"]={"ffmpeg":"missing-apex-ffmpeg-executable","ffprobe":"missing-apex-ffprobe-executable"}
        with patch.dict(os.environ,{}):
            result=doctor(config)
        self.assertFalse(result["ready_for_local_scan"])
        self.assertTrue(all(item["fix"] for item in result["checks"] if not item["ok"]))

    def test_isolated_environment_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            python=setup(create_only=True,root=Path(temp))
            result=subprocess.run([str(python),"-c","import sys; print(sys.prefix)"],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(".venv",result.stdout)


class BenchmarkTests(unittest.TestCase):
    def test_misses_extras_and_duplicate_predictions(self):
        result=compare({"anchors":[100,106,112]},{"anchors":[100.2,100.3,106,150]},.5)
        self.assertEqual((result["matched"],result["missed"],result["extra"]),(2,[112],[100.3,150]))
        self.assertAlmostEqual(result["recall"],2/3)
        self.assertEqual(result["precision"],.5)

    def test_empty_labels_do_not_claim_perfect_recall(self):
        result=compare({"anchors":[]},{"anchors":[]})
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["precision"])


class PublicWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            binary("ffmpeg"); binary("ffprobe")
        except ValueError as exc:
            raise unittest.SkipTest(str(exc))

    def test_scan_review_plan_calibrate_export_and_stale_config(self):
        with tempfile.TemporaryDirectory(prefix="apex_public_中文_") as temp:
            root=Path(temp)
            source=root/"fixture.mp4"
            run([binary("ffmpeg"),"-y","-v","error","-f","lavfi","-i","color=black:size=640x360:rate=30",
                 "-t","8","-c:v","libx264","-preset","ultrafast",str(source)])
            config=load_config(use_local=False)
            config["scan"]["ocr"]="off"
            config["prefilter"]["enabled"]=False
            config["timing"].update(post_roll=3,chain_gap=8,pre_roll=2)
            path=root/"config.json"
            save_json(path,config)
            work=root/"work"
            result=cli("scan","--source",source,"--workdir",work,"--config",path)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotEqual(cli("plan","--workdir",work).returncode,0)
            scan=json.loads((work/"scan.json").read_text(encoding="utf-8"))
            decision=root/"decision.json"
            save_json(decision,{"events":[{"class":"A","anchor":3,"evidence":"SYNTHETIC ENGINEERING TEST ONLY"}]})
            pages=[str(p["id"]) for p in scan["pages"]]
            result=cli("mark","--workdir",work,"--pages",*pages,"--decisions",decision,"--reviewer","synthetic test fixture")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            draft=work/"confirmed_events.json"
            data=json.loads(draft.read_text(encoding="utf-8"))
            data["events"]=[{"class":"A","spectate":False,"anchor":3,"event_type":"double_banner",
                "own_view_start":0,"own_view_end":8,"ammo":"unknown","fire_start":None,
                "evidence":"SYNTHETIC ENGINEERING TEST, NOT A REAL KILL","evidence_frames":[scan["pages"][0]["path"]],
                "reviewed_start":0,"reviewed_end":8}]
            save_json(draft,data)
            from test_guard_support import ammo_fixture,interval_fixture
            result=cli("ammo","--workdir",work,"--all")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            ammo_fixture(work,config)
            result=cli("plan","--workdir",work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            final=json.loads((work/"final.json").read_text(encoding="utf-8"))
            self.assertEqual((final["segments"][0]["start"],final["segments"][0]["end"]),(1,6))
            interval_fixture(work,config)
            data=json.loads(draft.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(cli("status","--workdir",work).stdout)["stage"],"ready_to_export")
            result=cli("calibrate","--source",source,"--at","2","--outdir",root/"calibration","--config",path)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue((root/"calibration/overlay_00.png").is_file())
            self.assertEqual(json.loads((root/"calibration/config.json").read_text())["tools"],{"ffmpeg":None,"ffprobe":None})
            result=cli("export","--workdir",work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertAlmostEqual(probe(work/"compilation_FULL.mp4")["duration"],5,delta=.15)
            data["events"].append({"class":"B","anchor":7})
            save_json(draft,data)
            result=cli("export","--workdir",work)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Confirmed events changed",result.stdout+result.stderr)
            data["events"].pop()
            save_json(draft,data)
            config["timing"]["post_roll"]=2
            save_json(path,config)
            result=cli("export","--workdir",work)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Timing configuration changed",result.stdout+result.stderr)
            config["timing"]["post_roll"]=3
            config["timing"]["lookback"]=14
            save_json(path,config)
            result=cli("export","--workdir",work)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Configuration changed",result.stdout+result.stderr)
            config["timing"]["lookback"]=15
            save_json(path,config)
            save_json(decision,{"events":[]})
            self.assertNotEqual(cli("export","--workdir",work).returncode,0)


if __name__=="__main__":
    unittest.main(verbosity=2)
