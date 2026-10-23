"""Synthetic controller tests, not model/gameplay accuracy measurements."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import binary, identity, run, save_json
from config import load_config
from workflow import start, next_task, submit, validate_answer, object_schema


class QueueTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('FFMPEG_BINARY') and not __import__('shutil').which('ffmpeg'):
            self.skipTest('FFmpeg required for generated-media controller validation')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source.mp4'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','color=black:s=640x360:r=10',
            '-t','12','-c:v','libx264','-pix_fmt','yuv420p',str(self.source)])
        cfg=load_config(use_local=False);cfg['scan'].update(ocr='off',mode='full')
        for tool in ('ffmpeg','ffprobe'):cfg['tools'][tool]=binary(tool)
        save_json(self.root/'config.json',cfg)
        self.result=start(self.source,self.root/'job',self.root/'config.json')

    def answer(self,result):
        save_json(self.root/'answer.json',dict(task_id=self.result['task_id'],outcome='answered',result=result))
        self.result=submit(self.root/'job',self.root/'answer.json','ENGINEERING TEST FIXTURE')
        return self.result

    def bootstrap(self):
        self.assertEqual(self.result['kind'],'calibration')
        self.answer({'aligned':True,'reason':'Generated fixture verifies controller routing only'})
        self.assertEqual(self.result['kind'],'matches')
        task=self.result['task'];images=task['images']
        self.answer({'matches':[dict(start=0,end=12,continue_match_id=None,session_id='fixture',reason='Generated one-session fixture',
            evidence_frame_ids=[i['frame_id'] for i in images])], 'reason':'Known generated single-session source'})
        while self.result.get('kind')=='hud':
            rows=[]
            for item in self.result['task']['context']['items']:
                t=item['time'];rows.append(dict(item_id=item['item_id'],state='valid',own_view=True,
                    kills=0 if t==0 else 1,assists=0,participation=0,damage=100 if t==0 else 400,
                    own_downed=False,spectator_terms=[],reason='Injected counter values for generated-media routing, not actual Apex reading'))
            self.answer({'readings':rows})

    def test_next_is_idempotent_and_foreign_answer_does_not_change_state(self):
        self.assertEqual(next_task(self.root/'job')['task_id'],self.result['task_id'])
        before=(self.root/'job/workflow/state.json').read_bytes() if (self.root/'job/workflow/state.json').exists() else None
        save_json(self.root/'bad.json',dict(task_id='foreign',outcome='answered',result={'aligned':True,'reason':'bad'}))
        with self.assertRaisesRegex(ValueError,'Stale or foreign'):submit(self.root/'job',self.root/'bad.json','fixture')
        self.assertEqual(next_task(self.root/'job')['task_id'],self.result['task_id'])

    def test_editing_task_metadata_cannot_bypass_the_answer_schema(self):
        card=self.root/'job/workflow/current.json'
        changed=json.loads(card.read_text());changed['context']['injected']='must be rejected'
        save_json(card,changed)
        with self.assertRaisesRegex(ValueError,'metadata changed'):
            self.answer({'aligned':True,'reason':'Should not approve an edited task'})

    def test_source_and_image_changes_reject_submission(self):
        image=Path(self.result['task']['images'][0]['path']);image.write_bytes(b'CHANGED ENGINEERING FIXTURE')
        with self.assertRaisesRegex(ValueError,'image changed'):
            self.answer({'aligned':True,'reason':'Must not approve altered image'})

    def test_false_or_extra_fields_cannot_approve_calibration(self):
        with self.assertRaisesRegex(ValueError,'exactly these fields'):
            self.answer({'aligned':True,'reason':'fixture','source_sha256':'injected'})
        with self.assertRaisesRegex(ValueError,'not confirmed'):
            self.answer({'aligned':False,'reason':'The ROI is not aligned'})
        self.assertEqual(next_task(self.root/'job')['kind'],'calibration')

    def test_uncertain_is_paused_not_marked_done(self):
        save_json(self.root/'answer.json',dict(task_id=self.result['task_id'],outcome='uncertain',result=None))
        result=submit(self.root/'job',self.root/'answer.json','fixture')
        self.assertEqual(result['stage'],'needs_attention')
        self.assertEqual(next_task(self.root/'job')['stage'],'needs_attention')
        self.assertFalse((self.root/'job/scan.json').exists())

    def test_generated_negative_review_finishes_without_fake_video(self):
        self.bootstrap()
        while self.result.get('kind')=='page':
            self.answer({'all_frames_checked':True,'candidates':[],'reason':'Generated black fixture has no event'})
        self.assertEqual(self.result['stage'],'complete_no_events')
        self.assertFalse((self.root/'job/compilation_FULL.mp4').exists())

    def drive_event(self, hidden=False, final_damage=300):
        self.bootstrap_hidden() if hidden else self.bootstrap()
        selected=False
        for _ in range(30):
            kind=self.result.get('kind')
            if not kind:break
            task=self.result['task'];ctx=task['context']
            if kind=='page':
                candidates=[]
                if not selected:
                    available=[f for f in ctx['frames'] if 5<=f['time']<=8]
                    if available:candidates=[available[0]['frame_id']];selected=True
                self.answer(dict(all_frames_checked=True,candidates=candidates,reason='Engineering event routing fixture only'))
            elif kind=='event':
                chosen=min(task['images'],key=lambda i:abs(i['time']-ctx['proposed_time']))
                self.answer(dict(classification='A',event_type='double_banner',anchor_frame_id=chosen['frame_id'],
                    own_view_start=0,own_view_end=12,evidence_frame_ids=[chosen['frame_id']],duplicate_anchor=None,
                    reason='Injected double-banner engineering label; no real model claim'))
            elif kind=='ammo':
                self.answer(dict(decision='unknown',fire_frame_id=None,same_weapon_confirmed=False,
                    reason='Known generated blank video has no visible weapon, engineering test'))
            elif kind=='interval':
                self.answer(dict(whole_interval_checked=True,own_view_throughout=True,firing_at_start=False,
                    omitted_lead_in=False,boundary_reason=None,reason='Known synthetic blank interval'))
            elif kind=='damage':
                self.answer(dict(own_view=True,before=100,start=100,end=final_damage,bound_frame_id=None,bound_damage=None,
                    reason='Injected final counter readings for export routing test only'))
            else:self.fail('Unexpected task '+str(self.result))
        self.assertTrue(selected)

    def test_generated_event_passes_every_guard_and_exports(self):
        self.drive_event()
        self.assertEqual(self.result['stage'],'complete',str(self.result))
        self.assertTrue(self.result['quality']['ready'])
        self.assertTrue((self.root/'job/compilation_FULL.mp4').is_file())
        self.assertEqual(next_task(self.root/'job')['stage'],'complete')

    def test_final_damage_below_threshold_is_excluded_not_exported(self):
        self.drive_event(final_damage=200)
        self.assertEqual(self.result['stage'],'complete_no_events',str(self.result))
        events=json.loads((self.root/'job/confirmed_events.json').read_text())['events']
        self.assertTrue(any(e.get('exclusion_reason')=='final_segment_hud_damage_below_150' for e in events))
        self.assertFalse((self.root/'job/compilation_FULL.mp4').exists())

    def bootstrap_hidden(self):
        self.answer({'aligned':True,'reason':'Generated controller fixture'})
        task=self.result['task']
        self.answer({'matches':[dict(start=0,end=12,continue_match_id=None,session_id='fixture',reason='Generated source',evidence_frame_ids=[i['frame_id'] for i in task['images']])], 'reason':'Generated one session'})
        while self.result.get('kind')=='hud':
            rows=[]
            for item in self.result['task']['context']['items']:
                rows.append(dict(item_id=item['item_id'],state='partial',own_view=True,
                    kills=None,assists=None,participation=None,damage=100 if item['time']==0 else 400,
                    own_downed=False,spectator_terms=[],reason='Generated hidden-counter engineering routing case'))
            self.answer({'readings':rows})

    def test_hidden_counter_event_uses_formal_admission_import(self):
        self.drive_event(hidden=True)
        self.assertEqual(self.result['stage'],'needs_attention',str(self.result))
        self.assertIn('hud_unresolved_review_required',[e['code'] for e in self.result['errors']])
        events=json.loads((self.root/'job/confirmed_events.json').read_text())['events']
        self.assertEqual(events[0]['admission_review']['basis'],'own_knockdown_for_hidden_counters')
        self.assertFalse((self.root/'job/USER_SKIP_UNRESOLVED.json').exists())

    def test_duplicate_answer_is_idempotent_and_does_not_overwrite(self):
        task_id=self.result['task_id']
        result={'aligned':True,'reason':'Generated calibration protocol test'}
        self.answer(result);next_id=self.result['task_id']
        save_json(self.root/'again.json',dict(task_id=task_id,outcome='answered',result=result))
        actual=submit(self.root/'job',self.root/'again.json','ENGINEERING TEST FIXTURE')
        self.assertTrue(actual['already_accepted']);self.assertEqual(actual['task_id'],next_id)
        save_json(self.root/'again.json',dict(task_id=task_id,outcome='answered',result={**result,'aligned':False}))
        with self.assertRaisesRegex(ValueError,'cannot be overwritten'):submit(self.root/'job',self.root/'again.json','ENGINEERING TEST FIXTURE')

    def test_detail_is_bounded_and_does_not_approve(self):
        for n in range(3):
            save_json(self.root/'detail.json',dict(task_id=self.result['task_id'],outcome='need_detail',result=None))
            self.result=submit(self.root/'job',self.root/'detail.json','ENGINEERING TEST FIXTURE')
        self.assertEqual(self.result['stage'],'needs_attention')
        self.assertFalse((self.root/'job/scan.json').exists())

    def test_schema_distinguishes_booleans_from_numbers(self):
        with self.assertRaises(ValueError):validate_answer(True,{'enum':[1]})
        validate_answer(1.0,{'enum':[1]})
        with self.assertRaises(ValueError):validate_answer({'v':True},object_schema({'v':{'type':'integer'}}))


if __name__=='__main__':unittest.main()
