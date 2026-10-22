import json,os,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parent.parent
local=root/'.local/config.json'
cfg=json.loads(local.read_text(encoding='utf-8')) if local.is_file() else {'tools':{}}
for name,path in cfg['tools'].items():
 if path:os.environ[name.upper()+'_BINARY']=path
os.environ['APEX_NO_VENV']='1'
os.environ['PYTHONUTF8']='1'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'scripts'))
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in ('test_pipeline','test_public','test_hud_prefilter','test_luna_safety','test_delivery_guard'))
r=unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not r.wasSuccessful())
