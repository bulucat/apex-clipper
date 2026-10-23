"""Failure-path regressions for 2.7 review; all media are generated fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import binary,identity,probe,run,save_json
from cut_full import cut_one,concatenate
from evaluate_reviews import evaluate
from marked_export import export_marked
import test_workflow as fixtures
import workflow


class RecoveryTests(unittest.TestCase):
    setUp=fixtures.QueueTests.setUp
    answer=fixtures.QueueTests.answer
    bootstrap=fixtures.QueueTests.bootstrap
    def test_interrupted_page_commit_cannot_discard_its_candidates(self):
        self.bootstrap()
        task=self.result['task'];page_id=task['context']['page_id']
        fid=task['context']['frames'][0]['frame_id']
        save_json(self.root/'answer.json',dict(task_id=task['task_id'],outcome='answered',
            result=dict(all_frames_checked=True,candidates=[fid],reason='Generated interruption fixture')))
        with patch('workflow._save_state',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                workflow.submit(self.root/'job',self.root/'answer.json','ENGINEERING TEST FIXTURE')
        resumed=workflow.next_task(self.root/'job')
        state=json.loads((self.root/'job/workflow/state.json').read_text())
        candidate_retained=bool(state['queue'])
        original_page_pending=resumed.get('kind')=='page' and resumed['task']['context']['page_id']==page_id
        self.assertTrue(candidate_retained or original_page_pending,'Interrupted page approval lost its candidate and advanced to a later page')

    def test_process_exit_during_page_commit_recovers_the_original_task(self):
        import os
        import subprocess
        import sys
        self.bootstrap();task_id=self.result['task_id']
        fid=self.result['task']['context']['frames'][0]['frame_id']
        save_json(self.root/'answer.json',dict(task_id=task_id,outcome='answered',
            result=dict(all_frames_checked=True,candidates=[fid],reason='Generated process-exit case')))
        code="import os,sys;sys.path[:0]=[sys.argv[1]+'/scripts',sys.argv[1]];import workflow;workflow._save_state=lambda *args:os._exit(73);workflow.submit(sys.argv[2],sys.argv[3],'ENGINEERING TEST FIXTURE')"
        process=subprocess.Popen([sys.executable,'-X','utf8','-c',code,
            str(Path(workflow.__file__).resolve().parent.parent),str(self.root/'job'),str(self.root/'answer.json')],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            stdout,stderr=process.communicate(timeout=30)
        except BaseException:
            process.kill();process.communicate();raise
        self.assertEqual(process.returncode,73,stderr.decode('utf8',errors='replace'))
        # This child is known to have exited; never clear a lock held by a live process.
        lock=self.root/'job/workflow/writer.lock'
        self.assertEqual(json.loads(lock.read_text())['pid'],process.pid)
        lock.unlink()
        resumed=workflow.next_task(self.root/'job')
        self.assertEqual(resumed['task_id'],task_id)
        self.assertEqual(resumed['kind'],'page')
        self.assertFalse((self.root/'job/compilation_FULL.mp4').exists())

    def test_paused_task_keeps_a_reusable_card_path(self):
        task_id=self.result['task_id']
        save_json(self.root/'answer.json',dict(task_id=task_id,outcome='uncertain',result=None))
        paused=workflow.submit(self.root/'job',self.root/'answer.json','ENGINEERING TEST FIXTURE')
        self.assertEqual(paused['stage'],'needs_attention')
        self.assertEqual(paused.get('workdir'),str((self.root/'job').resolve()))
        self.assertTrue(Path(paused.get('task_path','missing')).is_file())
        resumed=workflow.next_task(self.root/'job')
        self.assertEqual(resumed['task_id'],task_id)
        self.assertEqual(resumed['task_path'],paused['task_path'])


class AudioTests(unittest.TestCase):
    def test_obs_audio_tracks_survive_clips_compilation_and_marked_export(self):
        if not __import__('shutil').which('ffmpeg') and not __import__('os').environ.get('FFMPEG_BINARY'):
            self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);src=p/'source.mp4'
            run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','color=black:s=320x180:r=10',
                 '-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                 '-f','lavfi','-i','sine=frequency=880:sample_rate=48000',
                 '-t','3','-map','0:v:0','-map','1:a:0','-map','2:a:0',
                 '-c:v','libx264','-c:a','aac',str(src)])
            def tracks(file):
                data=json.loads(run([binary('ffprobe'),'-v','error','-show_streams','-of','json',str(file)]).stdout)
                return sum(s['codec_type']=='audio' for s in data['streams'])
            self.assertEqual(tracks(src),2)
            media=probe(src);source_id=identity(src);clip=p/'clip.mp4'
            cut_one(src,clip,{'start':0,'end':1.5},source_id,media,binary('ffmpeg'))
            self.assertEqual(tracks(clip),2,'Strict clip dropped a separate OBS audio track')
            compilation=p/'compilation.mp4'
            concatenate([clip],compilation,media,1.5)
            self.assertEqual(tracks(compilation),2,'Compilation dropped a separate OBS audio track')
            sidecar={'schema':'apex-obs-markers/v1','clock':'obs_output_frames','status':'complete',
                'recording_path':str(src.resolve()),'lookback_seconds':1,'duration_seconds':3,'markers':[{'time_seconds':2}]}
            Path(str(src)+'.apex-markers.json').write_text(json.dumps(sidecar))
            marked=export_marked(src,p/'marked')
            self.assertEqual(tracks(marked['outputs'][0]['path']),2,'Marked export dropped a separate OBS audio track')


class EvaluationTests(unittest.TestCase):
    def test_missing_expected_unknown_field_is_not_a_correct_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'labels').write_text(json.dumps(dict(case_id='hidden',expected={'damage':None})))
            (p/'answers').write_text(json.dumps(dict(case_id='hidden',model='fixture',run_id='one',outcome='answered',result={})))
            result=evaluate(p/'labels',p/'answers',p/'report')['runs'][0]
            self.assertEqual(result['passed'],0,'An omitted field was treated as an explicitly observed unknown')



class JournalTests(unittest.TestCase):
    def test_corrupt_journal_does_not_overwrite_metadata(self):
        from workflow_transaction import begin,recover
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'workflow').mkdir();target=root/'state.json';target.write_text('original')
            begin(root,[target]);target.write_text('current')
            journal=root/'workflow/transaction.json';data=json.loads(journal.read_text());data['files'][0]['data']='corrupt'
            save_json(journal,data)
            with self.assertRaisesRegex(ValueError,'journal is invalid'):recover(root)
            self.assertEqual(target.read_text(),'current')

    def test_journal_refuses_metadata_outside_task(self):
        from workflow_transaction import begin
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'task';root.mkdir();(root/'workflow').mkdir()
            outside=Path(tmp)/'outside.json';outside.write_text('preserve')
            with self.assertRaisesRegex(ValueError,'inside this task'):begin(root,[outside])
            self.assertEqual(outside.read_text(),'preserve')

if __name__=='__main__':unittest.main()
