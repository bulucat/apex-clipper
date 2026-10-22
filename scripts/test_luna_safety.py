import json
from pathlib import Path
import tempfile
import unittest
from ammo_diff import ammo_value
from common import identity, job_snapshot, save_json
from hud_prefilter import evaluate, key, validate_readings
from hud_import import merge
from config import load_config
from test_hud_prefilter import row, table

class SafetyTests(unittest.TestCase):
    def test_partial_negative_gate_and_unknown_not_zero(self):
        a=row(0,d=100);b=row(60,d=200)
        for r in (a,b):r.update(state='partial',kills=None,assists=None,participation=None)
        settings=load_config(use_local=False)['prefilter']
        data={'cache_key':'x','readings':[a,b]}
        rows=validate_readings(data,'x')
        result=evaluate(rows,[0,60],settings)
        self.assertFalse(result['requests']);self.assertFalse(result['windows'])
        b['damage']=300
        self.assertEqual(len(evaluate(rows,[0,60],settings)['requests']),9)

    def test_missing_boss_does_not_trigger_recovery(self):
        a=row(0);b=row(60,k=1,d=200);m=row(30)
        for r in (a,b,m):r.update(state='partial',participation=None)
        result=evaluate(table(a,b,m),[0,60],load_config(use_local=False)['prefilter'])
        self.assertFalse(result['requests'])
        self.assertEqual([(w['start'],w['end']) for w in result['windows']],[(30,60)])

    def test_real_uncertain_magazine_never_falls_to_reserve(self):
        fixtures=json.loads((Path(__file__).resolve().parent.parent/'assets/regression/ammo-ocr.json').read_text())
        self.assertEqual([ammo_value(r['ocr']) for r in fixtures],[32,30,None,None])

    def test_worker_merge_rejects_foreign_duplicate_and_unsupported_spectator(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data={'cache_key':'x','readings':[row(0,state='pending')]}
            save_json(p/'readings.json',data)
            report={'cache_key':'x','readings_path':str(p/'readings.json'),'requests':[{'time':0}],
                    'source':{'sha256':'synthetic'},'media':{'duration':60}}
            evidence=p/'synthetic.png';evidence.write_bytes(b'SYNTHETIC ONLY')
            save_json(p/'match_registry.json',{'source_sha256':'synthetic','matches':[{
                'match_id':'one','session_id':'test','start':0,'end':60,'reviewer':'synthetic fixture',
                'reason':'Synthetic single session','evidence_frames':[str(evidence)],
                'evidence_identities':[identity(evidence)]}]})
            save_json(p/'report.json',report)
            sub={'cache_key':'y','readings':[row(0)]}
            save_json(p/'sub.json',sub)
            with self.assertRaises(ValueError):merge(p/'report.json',p/'sub.json')
            sub['cache_key']='x';sub['readings']=[row(0,state='spectator')]
            save_json(p/'sub.json',sub)
            with self.assertRaises(ValueError):merge(p/'report.json',p/'sub.json')
            sub['readings']=[row(0)];save_json(p/'sub.json',sub)
            self.assertEqual(merge(p/'report.json',p/'sub.json')['imported'],1)
            with self.assertRaises(ValueError):merge(p/'report.json',p/'sub.json')

    def test_snapshot_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'video';p.write_bytes(b'123')
            stat=p.stat();job={'source':identity(p),'source_stat':{'path':str(p.resolve()),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}}
            self.assertEqual(job_snapshot(job),job['source'])
            p.write_bytes(b'1234')
            with self.assertRaises(ValueError):job_snapshot(job)

if __name__=='__main__':unittest.main()
