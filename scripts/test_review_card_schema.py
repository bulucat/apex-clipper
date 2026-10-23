import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from common import identity
from config import load_config
from quality_guard import fingerprint
from workflow import _issue,object_schema,array_schema,enum_schema

class ReviewCardSchemaTests(unittest.TestCase):
    def test_detail_source_images_are_selectable_as_match_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=root/'frame.png';image.write_bytes(b'ENGINEERING IMAGE IDENTITY')
            context={'review_times':[0,60],'boundary_times':[0,60],'duration':60}
            state={'detail':{fingerprint(['matches',context]):1}}
            schema=object_schema({'matches':array_schema(object_schema({'start':enum_schema([0]),'end':enum_schema([60]),
                'evidence_frame_ids':array_schema(enum_schema(['match_0','match_1']))}))})
            images=[{'frame_id':'match_'+str(n),'time':t,'path':str(image),'identity':identity(image)} for n,t in enumerate([0,60])]
            def frame(_root,_job,_config,t,kind='full'):
                return {'frame_id':'source_'+str(t),'time':t,'path':str(image),'identity':identity(image)}
            with patch('workflow._frame',side_effect=frame):
                task=_issue(root,{'source':{'sha256':'fixture','path':str(image)}},load_config(use_local=False),state,'matches','fixture',schema,images,context)['task']
            allowed=set(task['result_schema']['properties']['matches']['items']['properties']['evidence_frame_ids']['items']['enum'])
            self.assertTrue({i['frame_id'] for i in task['images']}<=allowed,'Detail card advertises source images that its answer schema rejects')

    def test_detail_event_anchor_accepts_displayed_full_source_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=root/'frame.png';image.write_bytes(b'ENGINEERING IMAGE IDENTITY')
            context={'proposed_time':10}
            state={'detail':{fingerprint(['event',context]):1}}
            schema=object_schema({'anchor_frame_id':enum_schema(['event_0']),
                'evidence_frame_ids':array_schema(enum_schema(['event_0']))})
            images=[{'frame_id':'event_0','time':10.123,'path':str(image),'identity':identity(image)}]
            def frame(_root,_job,_config,t,kind='full'):
                return {'frame_id':'source_'+str(t),'time':t,'path':str(image),'identity':identity(image)}
            with patch('workflow._frame',side_effect=frame),patch('workflow.probe',return_value={'duration':30,'fps':60}):
                task=_issue(root,{'source':{'sha256':'fixture','path':str(image)}},load_config(use_local=False),state,'event','fixture',schema,images,context)['task']
            props=task['result_schema']['properties']
            for field in ['anchor_frame_id','evidence_frame_ids']:
                enum=props[field]['enum'] if field=='anchor_frame_id' else props[field]['items']['enum']
                self.assertTrue({i['frame_id'] for i in task['images']}<=set(enum))

if __name__=='__main__':unittest.main()
