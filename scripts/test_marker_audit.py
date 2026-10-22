"""Regression tests for explicit marker selection and malformed sidecar failures."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import apex
from common import identity, save_json
from config import load_config
from marker_scope import load_sidecar


class MarkerAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source.mp4'
        self.source.write_bytes(b'unit source identity')
        self.source_id=identity(self.source)
        self.custom=self.root/'chosen.json'
        self.default=Path(str(self.source)+'.apex-markers.json')
        self.work=self.root/'job'
        self.data=dict(schema='apex-obs-markers/v1',clock='obs_output_frames',
                       recording_path=str(self.source),status='complete',duration_seconds=600,
                       lookback_seconds=60,markers=[dict(time_seconds=100)])
        save_json(self.custom,self.data)
        self.prepare=patch('hud_prefilter.prepare',return_value=dict(requests=[dict(time=40)],
                           stage='awaiting_hud_review',readings_path='fixture',pages=[]))
        self.prepare.start()
        self.addCleanup(self.prepare.stop)

    def scan(self,*,markers=None,ignore=False):
        # Re-passing an original ROI config must not silently discard a task's selected scope.
        args=argparse.Namespace(source=str(self.source),workdir=str(self.work),
                                markers=str(markers) if markers else None,
                                ignore_markers=ignore,verbose=False)
        with contextlib.redirect_stdout(io.StringIO()):
            apex.start_scan(args,load_config(use_local=False),self.root/'original-roi.json')
        return json.loads((self.work/'effective-config.json').read_text(encoding='utf8'))

    def test_resume_original_config_preserves_selected_custom_scope(self):
        first=self.scan(markers=self.custom)
        resumed=self.scan()
        self.assertEqual(resumed['prefilter']['marker_scope'],first['prefilter']['marker_scope'])

    def test_resume_does_not_replace_custom_selection_with_default_sidecar(self):
        first=self.scan(markers=self.custom)
        other={**self.data,'markers':[dict(time_seconds=500)]}
        save_json(self.default,other)
        resumed=self.scan()
        self.assertEqual(resumed['prefilter']['marker_scope'],first['prefilter']['marker_scope'])

    def test_ignore_choice_persists_for_the_same_job(self):
        save_json(self.default,self.data)
        self.scan(ignore=True)
        resumed=self.scan()
        self.assertIsNone(resumed['prefilter']['marker_scope'])

    def test_missing_sidecar_on_resume_keeps_saved_marked_scope(self):
        first=self.scan(markers=self.custom)
        self.custom.unlink()
        resumed=self.scan()
        self.assertEqual(resumed['prefilter']['marker_scope'],first['prefilter']['marker_scope'])

    def test_explicit_markers_override_a_previous_ignore_choice(self):
        self.scan(ignore=True)
        selected=self.scan(markers=self.custom)
        self.assertEqual(selected['prefilter']['marker_scope']['marker_times'],[100])

    def test_non_object_json_is_a_controlled_validation_error(self):
        for data in [[],[self.data],None,42,'bad']:
            with self.subTest(data=data):
                save_json(self.custom,data)
                with self.assertRaises(ValueError):
                    load_sidecar(self.custom,self.source_id)


if __name__=='__main__':
    unittest.main()
