import json
from pathlib import Path
import tempfile
import unittest
from evaluate_reviews import evaluate

class EvaluationTests(unittest.TestCase):
    def test_unresolved_missing_and_wrong_answers_are_not_counted_as_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            labels=[dict(case_id='hud',kind='hud',expected={'damage':450}),dict(case_id='view',kind='view',expected={'own_view':True}),dict(case_id='absent',kind='event',expected={'event_type':'double_banner'})]
            answers=[dict(case_id='hud',model='declared-test-model',run_id='one',outcome='answered',result={'damage':450}),dict(case_id='view',model='declared-test-model',run_id='one',outcome='uncertain',result=None)]
            for name,rows in [('labels',labels),('answers',answers)]:
                (p/(name+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf8')
            result=evaluate(p/'labels.jsonl',p/'answers.jsonl',p/'report.json')['runs'][0]
            self.assertEqual(result['passed'],1);self.assertEqual(result['total'],3)
            self.assertIsNone(result['reported_elapsed_seconds'])
            self.assertEqual(result['cases'][2]['errors'],['missing_answer'])

    def test_empty_labels_and_duplicate_answers_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'labels').write_text('');(p/'answers').write_text('')
            with self.assertRaises(ValueError):evaluate(p/'labels',p/'answers',p/'out')
            (p/'labels').write_text(json.dumps(dict(case_id='a',expected={'v':1})))
            answer=dict(case_id='a',model='fixture',run_id='one',outcome='answered',result={'v':1})
            (p/'answers').write_text(json.dumps(answer)+'\n'+json.dumps(answer))
            with self.assertRaisesRegex(ValueError,'Duplicate'):evaluate(p/'labels',p/'answers',p/'out')

if __name__=='__main__':unittest.main()
