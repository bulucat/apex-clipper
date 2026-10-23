"""Regression checks for review corrections and accurate resumable stages."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import identity, save_json
from config import digest, load_config
from hud_import import merge, recheck
from test_guard_support import observed_fixture_row


class WorkflowRegressionTests(unittest.TestCase):
    def make_hud(self, root):
        cfg = load_config(use_local=False)
        image = root / 'stats_0.000000.png'
        image.write_bytes(b'ENGINEERING FIXTURE ONLY')
        row = observed_fixture_row(dict(time=0, state='valid', own_view=True,
            match_id='one', kills=1, assists=2, participation=3, damage=450), root)
        source = {'path': str(root/'source'), 'size': 1, 'sha256': 'fixture'}
        save_json(root/'readings.json', {'cache_key': 'fixture', 'readings': [row]})
        hud = dict(cache_key='fixture', config_digest=digest(cfg),
            readings_path=str(root/'readings.json'), requests=[], pages=[],
            source=source, media={'duration':60,'fps':30}, minute_times=[0],
            settings=cfg['prefilter'], windows=[], scan_ranges=[])
        save_json(root/'hud_state.json', hud)
        save_json(root/'match_registry.json', dict(source_sha256='fixture', matches=[dict(
            match_id='one', session_id='fixture-session', start=0, end=60,
            reviewer='ENGINEERING FIXTURE', reason='Known generated one-session input',
            evidence_frames=[str(image)], evidence_identities=[identity(image)])]))
        save_json(root/'job.json', dict(source=source, scan_index=str(root/'scan.json')))
        return cfg, row, hud

    def test_partial_correction_does_not_rebind_omitted_old_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cfg, old, hud=self.make_hud(root)
            corrected=dict(time=0, state='partial', own_view=True, match_id='one', kills=1,
                reviewer='ENGINEERING FIXTURE', reading_method='visual',
                note='Only the kill counter is readable in this generated correction case')
            save_json(root/'correction.json', dict(cache_key='fixture', source_sha256='fixture', readings=[corrected]))
            recheck(root/'hud_state.json', root/'correction.json')
            actual=json.loads((root/'readings.json').read_text())['readings'][0]
            self.assertEqual(actual['kills'],1)
            for field in ('assists','participation','damage'):
                self.assertIsNone(actual.get(field), field)
                self.assertIsNone(actual['observation']['values'][field],field)
            self.assertEqual(actual['previous_decisions'][-1]['damage'],450)

    def test_negative_correction_clears_prior_own_view_and_counters(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cfg, old, hud=self.make_hud(root)
            corrected=dict(time=0,state='uncertain',reviewer='ENGINEERING FIXTURE',
                note='Generated case has no readable own-view counter')
            save_json(root/'correction.json',dict(cache_key='fixture',source_sha256='fixture',readings=[corrected]))
            recheck(root/'hud_state.json',root/'correction.json')
            actual=json.loads((root/'readings.json').read_text())['readings'][0]
            self.assertIsNone(actual.get('own_view'))
            self.assertIsNone(actual.get('match_id'))
            self.assertIsNone(actual.get('damage'))
            self.assertNotIn('observation',actual)

    def test_status_reports_missing_registry_instead_of_unstarted_scan(self):
        import apex
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cfg, old, hud=self.make_hud(root)
            (root/'match_registry.json').unlink()
            actual=apex.status(root,cfg)
            self.assertEqual(actual['stage'],'awaiting_match_review')
            self.assertIn('match_registry_missing',[e['code'] for e in actual['errors']])

    def test_status_reports_local_scan_ready_after_hud_gate(self):
        import apex
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cfg, old, hud=self.make_hud(root)
            self.assertEqual(apex.status(root,cfg)['stage'],'awaiting_local_scan')


if __name__=='__main__':
    unittest.main()
