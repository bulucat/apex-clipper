import json
from pathlib import Path
import tempfile
import unittest
import shutil
from common import binary,identity,run
from marked_export import export_marked

class MarkedExportTests(unittest.TestCase):
    def test_marked_exports_are_explicitly_unreviewed_and_preserve_source(self):
        if not shutil.which('ffmpeg') and not __import__('os').environ.get('FFMPEG_BINARY'):self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);src=p/'source.mp4'
            run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','color=black:s=320x180:r=10','-t','4','-c:v','libx264',str(src)])
            original=identity(src)
            sidecar={'schema':'apex-obs-markers/v1','clock':'obs_output_frames','status':'complete',
                'recording_path':str(src.resolve()),'lookback_seconds':2,'duration_seconds':4,'markers':[{'time_seconds':3}]}
            Path(str(src)+'.apex-markers.json').write_text(json.dumps(sidecar))
            result=export_marked(src,p/'out')
            self.assertEqual(identity(src),original)
            self.assertFalse(result['reviewed_for_knockdown']);self.assertFalse(result['damage_threshold_applied'])
            self.assertEqual(len(result['outputs']),1)
            self.assertTrue(Path(result['outputs'][0]['path']).is_file())
            with self.assertRaises(ValueError):export_marked(src,p/'out')
            sidecar['markers']=[];Path(str(src)+'.apex-markers.json').write_text(json.dumps(sidecar))
            with self.assertRaisesRegex(ValueError,'no marks'):export_marked(src,p/'empty')
            self.assertFalse((p/'empty').exists())

if __name__=='__main__':unittest.main()
