"""Behavioral regressions; generated media tests engineering, not Apex accuracy."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import importlib.util

from PIL import Image, ImageDraw, ImageFont

from common import binary, expanded_start, identity, media_check, probe, roi_box, run, save_json
from cut_full import validate_manifest
from detect_highlights import scan
from plan_clips import plan_segments
from review_progress import update as review_update
from review_event import review as review_event
from vision import LocalOCR, representatives, visual_proposal, hints, glyph_signature
from ammo_diff import bursts, ammo_value


ROOT = Path(__file__).resolve().parent


def test_font(size):
    for name in ("DejaVuSans.ttf","arial.ttf"):
        try:
            return ImageFont.truetype(name,size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apex_manifest_")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        image = self.path / "evidence.png"
        Image.new("RGB", (100, 50), "red").save(image)
        self.source = {"size": 10, "sha256": "abc", "path": "synthetic.mp4"}
        self.media = {"duration": 10}
        self.scan_path = self.path / "scan.json"
        self.index = {"complete": True, "mode": "full_scan", "source": self.source, "cache_key": "test",
            "review_scope": "all_samples", "review_state": str(self.path / "reviews.json"),
            "fps": 4, "start": 0, "end": 10, "frame_count": 40,
            "frames": [{"seq": n, "time": n/4, "selected": True} for n in range(40)],
            "pages": [{"id": 1, "path": str(image), "frames": [{"seq": n, "time": n / 4} for n in range(40)]}]}
        save_json(self.scan_path, self.index)
        self.segment = {"class": "A", "spectate": False, "event_type": "double_banner",
            "start": 1, "end": 8, "original_start": 3, "original_end": 8, "start_floor": 0,
            "anchor": 6, "anchors": [6], "own_view_start": 0, "own_view_end": 10,
            "reviewed_start": 1, "reviewed_end": 8, "fire_start": 4, "ammo": "found",
            "evidence": "Synthetic fixture; not actual kill evidence", "evidence_frames": [str(image)]}
        self.data = {"schema_version": "2.4", "source": self.source, "policy": {"post_roll": 2, "chain_gap": 7},
            "coverage": {"scan_index": str(self.scan_path), "reviewed_pages": [1], "reviewer": "test fixture", "review_scope": "all_samples"},
            "segments": [self.segment]}

    def validate(self):
        return validate_manifest(self.data, self.source, self.media)

    def test_valid(self):
        self.assertEqual(len(self.validate()), 1)

    def test_no_shortening(self):
        self.assertEqual(expanded_start(100, 110), 100)
        self.assertEqual(expanded_start(108, 103), 100)
        self.assertEqual(expanded_start(1, 2), 0)
        self.assertEqual(expanded_start(100, None), 100)

    def test_reject_missing_spectate(self):
        del self.segment["spectate"]
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.validate()

    def test_unknown_null(self):
        self.segment.update(ammo="unknown", fire_start=None, start=3)
        self.assertEqual(len(self.validate()), 1)
        self.segment["fire_start"] = 4
        with self.assertRaisesRegex(ValueError, "null"):
            self.validate()

    def test_spectator_tail_does_not_veto_earlier_event(self):
        self.segment.update(own_view_end=7, end=7, original_end=7, reviewed_end=7)
        self.assertEqual(len(self.validate()), 1)
        self.segment["end"] = 8
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.validate()

    def test_expanded_interval_requires_review(self):
        self.segment["reviewed_start"] = 3
        with self.assertRaisesRegex(ValueError, "not been reviewed"):
            self.validate()

    def test_bad_formula_rejected(self):
        self.segment.update(original_start=.5, start=1)
        with self.assertRaisesRegex(ValueError, "Incorrect extension"):
            self.validate()

    def test_overlap_and_multikill(self):
        self.segment["anchors"] = [6, 7]
        self.segment.update(end=9, original_end=9, reviewed_end=9)
        self.assertEqual(len(self.validate()), 1)
        self.data["segments"].append(copy.deepcopy(self.segment))
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_page_review(self):
        self.data["coverage"]["reviewed_pages"] = []
        with self.assertRaisesRegex(ValueError, "Every scan page"):
            self.validate()

    def test_incomplete_scan(self):
        self.index["complete"] = False
        save_json(self.scan_path, self.index)
        with self.assertRaisesRegex(ValueError, "must complete"):
            self.validate()

    def test_gap_in_scan(self):
        self.index["frames"][4]["seq"] = 100
        save_json(self.scan_path, self.index)
        with self.assertRaisesRegex(ValueError, "missing or reordered"):
            self.validate()

    def test_source_mismatch(self):
        self.data["source"] = {"size": 10, "sha256": "other"}
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.validate()

    def test_roi_bounds(self):
        x, y, right, bottom = roi_box(1920, 1080, "ammo")
        self.assertTrue(0 <= x < right <= 1920 and 0 <= y < bottom <= 1080)
        with self.assertRaises(ValueError):
            roi_box(1920, 1080, "low", [.8, .5, .5, .3])

    def test_tail_over_two_seconds_rejected(self):
        self.segment.update(end=9, original_end=9, reviewed_end=9)
        with self.assertRaisesRegex(ValueError, "last kill"):
            self.validate()

    def test_economy_scope_must_be_honest(self):
        self.index["review_scope"] = "candidates_and_audits"
        save_json(self.scan_path, self.index)
        with self.assertRaisesRegex(ValueError, "actual economy/full"):
            self.validate()
        self.data["coverage"]["review_scope"] = "candidates_and_audits"
        self.assertEqual(len(self.validate()), 1)

    def test_review_resume_and_invalidation(self):
        decision = self.path / "decisions.json"
        save_json(decision, {"events": []})
        result = review_update(self.scan_path, [1], str(decision), "test reviewer")
        self.assertEqual(result["reviewed_pages"], [1])
        self.assertEqual(review_update(self.scan_path)["unread_pages"], [])
        save_json(decision, {"events": [{"anchor": 1}]})
        self.assertEqual(review_update(self.scan_path)["unread_pages"], [1])


class PlanTests(unittest.TestCase):
    def event(self, t, **values):
        result = {"anchor": t, "class": "A", "spectate": False, "event_type": "double_banner",
            "own_view_start": 0, "own_view_end": 200, "ammo": "unknown", "fire_start": None,
            "evidence": "fixture", "evidence_frames": [], "reviewed_start": max(0,t-3), "reviewed_end": min(200,t+2)}
        result.update(values)
        return result

    def test_single_two_second_tail(self):
        result = plan_segments([self.event(100)], 200)
        self.assertEqual((result[0]["start"], result[0]["end"]), (97,102))

    def test_chain_transitive(self):
        result = plan_segments([self.event(t) for t in (100,106,112)], 200)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["end"], 114)
        self.assertEqual(result[0]["anchors"], [100,106,112])
        self.assertTrue(result[0]["review_required"])

    def test_inclusive_seven_second_boundary(self):
        self.assertEqual(len(plan_segments([self.event(100),self.event(107)],200)), 1)
        self.assertEqual(len(plan_segments([self.event(100),self.event(107.0001)],200)), 2)

    def test_more_than_seven_does_not_merge_countback_overlap(self):
        result = plan_segments([self.event(100), self.event(108, ammo="found", fire_start=90)], 200)
        self.assertEqual(len(result), 2)
        self.assertEqual((result[0]["end"], result[1]["start"], result[1]["end"]), (102,102,110))

    def test_view_switch_breaks_chain(self):
        result = plan_segments([self.event(100, own_view_end=103),self.event(106,own_view_start=104)],200)
        self.assertEqual(len(result),2)

    def test_weak_reward_chains_and_source_end(self):
        result = plan_segments([self.event(194,event_type="weak_reward",evidence="弱者奖励"),self.event(199)],200)
        self.assertEqual((len(result),result[0]["event_type"],result[0]["end"]), (1,"mixed",200))


class VisionTests(unittest.TestCase):
    def test_mask_dedup_preserves_changes_and_span(self):
        records = [{"seq": n, "time": n/2, "signature": "same"} for n in range(10)]
        chosen = representatives(records)
        self.assertEqual(len(chosen),3)
        self.assertEqual({n for r in chosen for n in r["represented_sequences"]}, set(range(10)))
        records[5]["signature"] = "new-name-or-spectator-ui"
        self.assertIn(5,[r["seq"] for r in representatives(records)])

    def test_visual_gate_and_spectator_concept_dedup(self):
        self.assertFalse(visual_proposal(Image.new("RGB",(300,100),"black"))["candidate"])
        with Image.open(ROOT.parent / "assets/reference/1792065412039.png") as image:
            self.assertTrue(visual_proposal(image)["candidate"])
        self.assertFalse(hints([{"text":"即时 即時"}],"high")["spectate_hint"])
        self.assertTrue(hints([{"text":"即时 觀戰"}],"high")["spectate_hint"])

    def test_ocr_failure_preserves_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            ocr = LocalOCR(temp,"off")
            result = ocr.read(Image.new("RGB",(100,50),"white"))
            self.assertEqual(result["status"],"unavailable")

    def test_ammo_bursts_do_not_bridge_reload_or_unknown(self):
        trace = [{"time":n/2,"value":v,"path":str(n)} for n,v in enumerate([30,29,28,30,27,None,24,23])]
        result = bursts(trace)
        self.assertEqual(len(result),3)
        self.assertEqual(result[0]["first_time"],0)

    def test_local_ocr_backend_and_cache(self):
        if importlib.util.find_spec("rapidocr_onnxruntime") is None:
            self.skipTest("Install requirements-ocr.txt for real local OCR smoke test")
        with tempfile.TemporaryDirectory() as temp:
            engine = LocalOCR(temp,"required")
            image = Image.new("RGB",(240,100),"black")
            ImageDraw.Draw(image).text((20,10),"30",font=test_font(64),fill="white")
            result = engine.read(image,"ammo")
            self.assertEqual(result["status"],"ok")
            self.assertEqual(ammo_value(result),30)
            self.assertEqual(engine.read(image,"ammo"),result)
            self.assertEqual(engine.hits,1)


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            binary("ffmpeg")
            binary("ffprobe")
        except ValueError as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apex_test_中文_'_")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def make_video(self, name, audio=True, duration=12):
        path = self.path / name
        args = [binary("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30"]
        if audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        args += ["-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if audio:
            args += ["-c:a", "aac"]
        run(args + [str(path)])
        return path

    def manifest(self, src, audio=True):
        index_path = self.path / "scan.json"
        index = scan(src, index_path, self.path / (src.stem + "_sheets"),mode="full",ocr_backend="off")
        evidence = index["pages"][0]["path"]
        segments = []
        for start, end, anchor, floor in [(0, 3, 1, 0), (7, 12, 10, 3)]:
            segments.append({"class": "A", "spectate": False, "event_type": "double_banner", "start": start,
                "end": end, "anchor": anchor, "anchors": [anchor], "original_start": start, "original_end": end,"start_floor":floor,
                "own_view_start": 0, "own_view_end": 12, "reviewed_start": start, "reviewed_end": end,
                "ammo": "unknown", "fire_start": None, "evidence": "SYNTHETIC ENGINEERING TEST ONLY",
                "evidence_frames": [evidence]})
        data = {"schema_version": "2.4", "source": index["source"],"policy":{"post_roll":2,"chain_gap":7},
            "coverage": {"scan_index": str(index_path), "reviewed_pages": [p["id"] for p in index["pages"]],
                         "reviewer": "synthetic engineering fixture, not Apex review","review_scope":index["review_scope"]}, "segments": segments}
        path = self.path / "final.json"
        from config import load_config,digest
        from review_progress import update
        from test_guard_support import ammo_fixture,interval_fixture,registry_fixture,damage_fixture
        config=load_config(use_local=False)
        config["scan"].update(fps=2,mode="full",ocr="off")
        config["prefilter"]["enabled"]=False
        save_json(self.path/"guard_config.json",config)
        save_json(self.path/"job.json",{"source":index["source"],"scan_index":str(index_path),"config_path":str(self.path/"guard_config.json")})
        events={"source":index["source"],"events":[dict(s) for s in segments]}
        save_json(self.path/"confirmed_events.json",events)
        decisions=self.path/"synthetic_decisions.json";save_json(decisions,{"events":[]})
        update(index_path,[p["id"] for p in index["pages"]],decisions,"synthetic engineering fixture")
        registry_fixture(self.path,12,evidence=[evidence])
        ammo_fixture(self.path,config)
        data["events_identity"]=identity(self.path/"confirmed_events.json")
        data["config_digest"]=digest(load_config(self.path/"guard_config.json"))
        save_json(path,data)
        interval_fixture(self.path,config)
        damage_fixture(self.path,config)
        return path, index

    def command(self, script, *args):
        env = dict(os.environ, PYTHONUTF8="1")
        if script=="cut_full.py":
            args=(*args,"--config",self.path/"guard_config.json")
        return subprocess.run([sys.executable, str(ROOT / script), *map(str, args)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)

    def test_all_frames_tail_and_local_review(self):
        src = self.make_video("scan.mp4", audio=False, duration=6.2)
        index = scan(src, self.path / "scan.json", self.path / "sheets",mode="full",ocr_backend="off")
        frames = [f for p in index["pages"] for f in p["frames"]]
        self.assertTrue(index["complete"])
        self.assertEqual([f["seq"] for f in frames], list(range(len(frames))))
        self.assertGreaterEqual(frames[-1]["time"], 5.75)
        self.assertEqual(len(index["pages"]), 2)
        local = scan(src, self.path / "local.json", self.path / "local", fps=12, start=1, end=2,mode="full",ocr_backend="off")
        self.assertEqual(local["mode"], "local_review")
        self.assertEqual(local["frame_count"], 12)

    def test_cut_compile_cache_and_failure(self):
        src = self.make_video("audio.mp4")
        manifest, index = self.manifest(src)
        out = self.path / "out"
        comp = self.path / "compilation.mp4"
        args = ("--src", src, "--json", manifest, "--outdir", out, "--compilation", comp,"--verbose")
        result = self.command("cut_full.py", *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(out.glob("*_FULL.mp4"))), 2)
        media_check(comp, probe(src), 8)
        result = self.command("cut_full.py", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERIFIED CACHE", result.stdout)
        # Even an output with a valid old receipt is regenerated after tampering.
        clip = next(out.glob("*_FULL.mp4"))
        clip.write_bytes(b"corrupt" * 10000)
        result = self.command("cut_full.py", *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(probe(clip)["duration"], 1)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        del data["segments"][0]["spectate"]
        save_json(manifest, data)
        result = self.command("cut_full.py", *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("DONE", result.stdout)

    def test_silent_input_and_ammo(self):
        src = self.make_video("silent.mp4", audio=False)
        manifest, _ = self.manifest(src)
        result = self.command("cut_full.py", "--src", src, "--json", manifest, "--outdir", self.path / "silent_out")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(probe(next((self.path / "silent_out").glob("*_FULL.mp4")))["audio"])
        result = self.command("ammo_diff.py", src, 0, 2,"--ocr","off","--outdir",self.path/"ammo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HINTS ONLY", result.stdout)

    def test_resume_cache_config_invalidation_and_sparse_review(self):
        src = self.make_video("resume.mp4",audio=False,duration=4)
        root, output = self.path/"cache",self.path/"scan.json"
        first = scan(src,output,root,ocr_backend="off",stop_after=3)
        self.assertFalse(first["complete"])
        result = scan(src,output,root,ocr_backend="off")
        self.assertEqual(result["frame_count"],8)
        self.assertEqual([f["seq"] for f in result["frames"]],list(range(8)))
        self.assertEqual(scan(src,output,root,ocr_backend="off")["cache_key"],result["cache_key"])
        changed = scan(src,output,root,fps=4,ocr_backend="off")
        self.assertNotEqual(changed["cache_key"],result["cache_key"])
        sparse = review_event(src,1,self.path/"review")
        self.assertEqual(len(sparse["frames"]),3)
        self.assertEqual(review_event(src,1,self.path/"review"),sparse)


if __name__ == "__main__":
    unittest.main(verbosity=2)
