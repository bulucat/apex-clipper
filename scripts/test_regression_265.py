import unittest,tempfile,json
from pathlib import Path
from hud_prefilter import evaluate,key,growth_state
from damage_review import _validate_review,check_admission_reviews
from common import identity
class RepairRegression(unittest.TestCase):
 def rows(self,counts=True):
  return [dict(time=t,state='valid' if counts else 'partial',own_view=True,match_id='m',kills=k if counts else None,assists=a if counts else None,participation=None,damage=d) for t,k,a,d in [(6840,0,0,449),(6870,0,0,449),(6900,1,0,845)]]
 def settings(self):return dict(min_damage=150,max_window=30,fallback_divisions=10)
 def test_hidden_positive_half_only(self):
  r=self.rows(False);o=evaluate({key(x['time']):x for x in r},[6840,6900],self.settings());self.assertEqual([(w['start'],w['end']) for w in o['windows']],[(6870,6900)]);self.assertEqual(o['windows'][0]['method'],'hidden_counters_damage_probe');self.assertTrue(any(u['reason']=='counter_growth_unknown' for u in o['unresolved']));self.assertFalse(o['skipped'])
 def test_known_no_growth_stays_excluded(self):
  r=self.rows();r[-1]['kills']=0;o=evaluate({key(x['time']):x for x in r},[6840,6900],self.settings());self.assertFalse(o['windows']);self.assertTrue(o['skipped'])
 def test_unknown_not_false(self):
  r=self.rows(False);self.assertIsNone(growth_state(r[0],r[-1]))
 def test_rollback_cannot_prove_growth(self):
  a,b=self.rows()[0],self.rows()[-1];a['kills']=2;b['kills']=1;b['assists']=1;self.assertIsNone(growth_state(a,b))
 def fixture(self,root,delta):
  s=dict(anchors=[15],start=10,end=20);obs=[]
  for role,t,d in [('before',9.5,100),('start',10,100),('end',20-1/30,100+delta)]:
   p=root/(role+'.png');p.write_bytes(role.encode());obs.append(dict(role=role,time=t,damage=d,path=str(p),own_view=True,match_id='m',reading_method='visual',source_sha256='s'))
  rev=dict(anchors=[15],segment_start=10,segment_end=20,source_sha256='s',reviewer='regression fixture',reason='exact endpoints',match_id='m',observations=obs,damage_delta=delta,evidence_identities=[identity(o['path']) for o in obs]);return s,rev
 def test_42_rejected_even_admitted_minute(self):
  with tempfile.TemporaryDirectory() as td:
   s,r=self.fixture(Path(td),42)
   with self.assertRaisesRegex(ValueError,'below the required 150'):_validate_review(r,s,'s',[dict(match_id='m',start=0,end=100)],1/30)
 def test_exact_150_accepted(self):
  with tempfile.TemporaryDirectory() as td:
   s,r=self.fixture(Path(td),150);self.assertEqual(_validate_review(r,s,'s',[dict(match_id='m',start=0,end=100)],1/30)['damage_delta'],150)
 def test_exclusive_end_cannot_add_outside_damage(self):
  with tempfile.TemporaryDirectory() as td:
   s,r=self.fixture(Path(td),150);r['observations'][-1]['time']=20
   with self.assertRaisesRegex(ValueError,'last included frame'):_validate_review(r,s,'s',[dict(match_id='m',start=0,end=100)],1/30)
if __name__=='__main__':unittest.main()
