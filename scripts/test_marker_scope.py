"""Marked scheduling and source binding, including a real-video CLI smoke test."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import binary, identity, run, save_json
from config import load_config, digest
from hud_prefilter import timeline, evaluate, key, validate_coverage
from marker_scope import load_sidecar, validate_scope, assert_source, check_duration, SUFFIX
from test_public import cli


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root/'录像.mp4'
        self.video.write_bytes(b'synthetic identity fixture')
        self.source = identity(self.video)
        self.sidecar = Path(str(self.video)+SUFFIX)
        self.data = dict(schema='apex-obs-markers/v1', clock='obs_output_frames',
                         recording_path=str(self.video), status='complete',
                         duration_seconds=600, lookback_seconds=60,
                         markers=[dict(time_seconds=t) for t in [100, 140, 140, 500]])

    def load(self):
        save_json(self.sidecar, self.data)
        return load_sidecar(self.sidecar, self.source)

    def test_union_dedup_and_video_identity(self):
        scope = self.load()
        self.assertEqual(scope['ranges'], [[40, 140], [440, 500]])
        self.assertEqual(scope['marker_times'], [100, 140, 500])
        assert_source(scope, self.source)
        other = {**self.source, 'sha256': 'foreign'}
        with self.assertRaises(ValueError):
            assert_source(scope, other)

    def test_recording_under_one_minute_clamps_to_zero(self):
        self.data['markers'] = [dict(time_seconds=12)]
        self.assertEqual(self.load()['ranges'], [[0, 12]])

    def test_disjoint_ranges_never_create_a_gap_gate(self):
        scope = self.load()
        media = dict(duration=600, fps=30)
        times = timeline(media, 60, scope)
        self.assertEqual(times, [40, 100, 140, 440, 500])
        settings = load_config(use_local=False)['prefilter']
        settings['marker_scope'] = scope
        def row(t, kills, damage):
            return dict(time=t, state='valid', own_view=True, match_id='one',
                        kills=kills, assists=0, damage=damage)
        # Both actual windows fail the damage gate, but the unmarked gap grows by 900.
        rows = {key(t): row(t,k,d) for t,k,d in [(40,0,0),(100,0,0),(140,0,0),(440,9,900),(500,9,900)]}
        result = evaluate(rows, times, settings)
        self.assertFalse(result['requests'])
        self.assertFalse(result['windows'])
        self.assertFalse(result['unresolved'])
        self.assertNotIn([140,440], [x['interval'] for x in result['skipped']])

    def test_mark_is_not_automatic_150_damage_admission(self):
        scope = self.load()
        settings = load_config(use_local=False)['prefilter']
        settings['marker_scope'] = scope
        rows = {key(t): dict(time=t,state='valid',own_view=True,match_id='one',
                            kills=k,assists=0,damage=d) for t,k,d in [(440,0,0),(500,1,149)]}
        self.assertFalse(evaluate(rows,[440,500],settings)['windows'])

    def test_invalid_marker_files_never_fall_back(self):
        original = copy.deepcopy(self.data)
        for patch in [dict(status='recording'), dict(status='interrupted'),
                      dict(status='unsupported_split'), dict(markers=[]),
                      dict(markers=[dict(time_seconds=-1)]),
                      dict(markers=[dict(time_seconds=float('nan'))]),
                      dict(markers=[dict(time_seconds=700)]),
                      dict(recording_path=str(self.root/'wrong.mp4')),
                      dict(lookback_seconds=True), dict(duration_seconds=0)]:
            with self.subTest(patch=patch):
                self.data = {**original, **patch}
                with self.assertRaises(ValueError):
                    self.load()

    def test_empty_overlap_nan_and_boolean_ranges_rejected(self):
        scope = self.load()
        for ranges in [[], [[1,1]], [[-1,2]], [[True,2]], [[1,float('inf')]], [[1,3],[2,4]]]:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                validate_scope({**scope,'ranges':ranges})

    def test_duration_drift_is_rejected(self):
        scope = self.load()
        check_duration(scope, dict(duration=600.1, fps=30))
        with self.assertRaises(ValueError):
            check_duration(scope, dict(duration=610, fps=30))

    def test_changing_marks_invalidates_configuration(self):
        config = load_config(use_local=False)
        config['prefilter']['marker_scope'] = self.load()
        before = digest(config)
        self.data['markers'].append(dict(time_seconds=590))
        config['prefilter']['marker_scope'] = self.load()
        self.assertNotEqual(before, digest(config))

    def test_real_video_cli_auto_discovery_and_resume(self):
        self.video.unlink()
        run([binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','color=c=black:s=320x180:r=30:d=12',
             '-c:v','libx264','-pix_fmt','yuv420p',str(self.video)])
        self.source = identity(self.video)
        self.data.update(duration_seconds=12,lookback_seconds=3,
                         markers=[dict(time_seconds=4),dict(time_seconds=10)])
        self.load()
        work = self.root/'job'
        result = cli('scan','--source',self.video,'--workdir',work)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        hud = json.loads((work/'hud_state.json').read_text(encoding='utf8'))
        self.assertEqual(hud['minute_times'], [1,4,7,10])
        self.assertEqual([r['time'] for r in hud['requests']], [1,4,7,10])
        result = cli('scan','--source',self.video,'--workdir',work)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result = cli('status','--workdir',work)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('awaiting_hud_review',result.stdout)
        # Synthetic engineering observations exercise the real import and delivery guards.
        # These blank frames are deliberately not claimed to contain actual Apex events.
        from test_guard_support import registry_fixture
        registry_fixture(work,12)
        readings = []
        for t,k,d in [(1,0,0),(4,1,180),(7,1,180),(10,2,360)]:
            readings.append(dict(time=t,state='valid',own_view=True,match_id='one',
                                 kills=k,assists=0,participation=k,damage=d,
                                 reading_method='visual',reviewer='ENGINEERING TEST FIXTURE'))
        submission=self.root/'synthetic_observations.json'
        save_json(submission,dict(cache_key=hud['cache_key'],readings=readings))
        result=cli('hud-import','--workdir',work,'--submission',submission)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=cli('scan','--source',self.video,'--workdir',work)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        index=json.loads((work/'scan.json').read_text(encoding='utf8'))
        self.assertEqual(index['prefilter']['ranges'],[[0,5],[6,11]])
        validate_coverage(index,index['media'])
        self.assertFalse(any(5 <= f['time'] < 6 for f in index['frames']))
        decisions=self.root/'synthetic_decisions.json'
        save_json(decisions,dict(events=[]))
        result=cli('mark','--workdir',work,'--pages',*[p['id'] for p in index['pages']],
                   '--decisions',decisions,'--reviewer','SYNTHETIC ENGINEERING FIXTURE')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=cli('plan','--workdir',work)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=cli('export','--workdir',work,'--validate-only')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        quality=json.loads((work/'quality_report.json').read_text(encoding='utf8'))
        self.assertEqual(quality['marked_seconds'],6)
        self.assertFalse(quality['unmarked_footage_reviewed'])
        ordinary = self.root/'ordinary'
        result = cli('scan','--source',self.video,'--workdir',ordinary,'--ignore-markers')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        hud = json.loads((ordinary/'hud_state.json').read_text(encoding='utf8'))
        self.assertEqual(hud['minute_times'][0],0)
        self.assertAlmostEqual(hud['minute_times'][-1],12-1/30,places=5)


if __name__ == '__main__':
    unittest.main()
