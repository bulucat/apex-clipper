"""Failure-path tests with OBS's actual LuaJIT; frontend events are simulated."""
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=Path(os.environ.get('APEX_MARKER_SCRIPT',str(ROOT/'obs/apex-marker.lua')))
OBS_BIN=Path(os.environ['OBS_BIN_DIR']) if os.environ.get('OBS_BIN_DIR') else None
LIBRARY=os.environ.get('APEX_LUA_LIBRARY') or (str(OBS_BIN/'lua51.dll') if OBS_BIN else None)
if not LIBRARY:
    LIBRARY=ctypes.util.find_library('luajit-5.1') or ctypes.util.find_library('luajit') or ctypes.util.find_library('lua5.1')
if not LIBRARY:
    if os.environ.get('APEX_REQUIRE_LUA_TESTS')=='1':
        raise RuntimeError('LuaJIT/Lua 5.1 is required; set APEX_LUA_LIBRARY or OBS_BIN_DIR')
    raise unittest.SkipTest('LuaJIT/Lua 5.1 unavailable; set APEX_LUA_LIBRARY or OBS_BIN_DIR')
DLL_HANDLE=os.add_dll_directory(str(OBS_BIN)) if os.name=='nt' and OBS_BIN else None
LUA=C.CDLL(LIBRARY)
LUA.luaL_newstate.restype=C.c_void_p
LUA.luaL_openlibs.argtypes=[C.c_void_p]
LUA.luaL_loadbuffer.argtypes=[C.c_void_p,C.c_char_p,C.c_size_t,C.c_char_p]
LUA.lua_pcall.argtypes=[C.c_void_p,C.c_int,C.c_int,C.c_int]
LUA.lua_tolstring.argtypes=[C.c_void_p,C.c_int,C.c_void_p]
LUA.lua_tolstring.restype=C.c_char_p
LUA.lua_close.argtypes=[C.c_void_p]

MOCK=r'''
frames=0; active=false; paused=false; current='E:/录制/边界.mp4'
saves={}; exists={}; logs={}; released=0; fail_write=false; fail_encode=false
obslua={OBS_INVALID_HOTKEY_ID=-1,LOG_INFO=200,LOG_ERROR=100,
 OBS_FRONTEND_EVENT_RECORDING_STARTED=1,OBS_FRONTEND_EVENT_RECORDING_STOPPED=2}
local o=obslua
o.script_log=function(level,message) table.insert(logs,message) end
o.obs_data_create_from_json=function(s) if fail_encode then return nil end return s end
o.obs_data_save_json_safe=function(data,p) if fail_write then return false end saves[p]=data; exists[p]=true; return true end
o.obs_data_release=function() end
o.obs_data_create_from_json_file=function(p) if corrupt_existing then return nil end return saves[p] end
o.os_file_exists=function(p) return exists[p] or saves[p]~=nil end
o.obs_output_get_total_frames=function() return frames end
o.obs_frontend_get_current_record_output_path=function() return current end
o.obs_frontend_get_recording_output=function() return {} end
o.obs_video_info=function() return {fps_num=30,fps_den=1} end
o.obs_get_video_info=function() return true end
o.obs_output_release=function() released=released+1 end
o.obs_encoder_get_frame_rate_divisor=function() return 1 end
o.obs_output_get_video_encoder=function() return {} end
o.obs_frontend_recording_active=function() return active end
o.obs_frontend_recording_paused=function() return paused end
o.obs_data_get_int=function() return 60 end
o.obs_hotkey_register_frontend=function(id,label,cb) mark=cb; return 1 end
o.obs_data_get_array=function() return {} end
o.obs_hotkey_load=function() end
o.obs_data_array_release=function() end
o.obs_frontend_add_event_callback=function(cb) event=cb end
o.obs_frontend_remove_event_callback=function() end
o.timer_add=function(cb) poll=cb end
o.timer_remove=function() end
o.obs_hotkey_save=function() return {} end
o.obs_data_set_array=function() end
function saved(p) return saves[(p or current)..'.apex-markers.json'] or '' end
function countmarks(s) local _,n=string.gsub(s or saved(),'time_seconds',''); return n end
function start() active=true;frames=0;event(1) end
function stop() active=false;event(2) end
'''


class LuaTests(unittest.TestCase):
    def setUp(self):
        self.vm=LUA.luaL_newstate()
        LUA.luaL_openlibs(self.vm)
        self.run_lua(MOCK)
        self.run_lua(SCRIPT.read_text(encoding='utf8'))
        self.run_lua('script_load({})')

    def tearDown(self):
        LUA.lua_close(self.vm)

    def run_lua(self,code):
        data=code.encode('utf8')
        rc=LUA.luaL_loadbuffer(self.vm,data,len(data),b'edge-test')
        if not rc:
            rc=LUA.lua_pcall(self.vm,0,0,0)
        if rc:
            self.fail(LUA.lua_tolstring(self.vm,-1,None).decode('utf8'))

    def test_corrupted_existing_file_is_never_overwritten(self):
        self.run_lua('''
local file=current..'.apex-markers.json'
exists[file]=true; saves[file]='corrupted but recoverable evidence';corrupt_existing=true
start();frames=300;mark(true);stop()
assert(saves[file]=='corrupted but recoverable evidence','BUG: corrupt existing marker file was overwritten')
''')

    def test_leftover_backup_file_is_preserved(self):
        self.run_lua('''
exists[current..'.apex-markers.json.bak']=true
start();frames=300;mark(true);stop()
assert(saved()=='','BUG: recording reused a path with a recoverable backup')
''')

    def test_failed_final_save_retries_after_recording_stops(self):
        self.run_lua('''
start();frames=300;mark(true)
fail_write=true;frames=600;stop()
fail_write=false;poll()
assert(saved():find('"status":"complete"'),'BUG: failed final save lost the complete snapshot')
assert(saved():find('20.000000'));assert(countmarks()==1);assert(released==1)
''')

    def test_failed_final_save_preserves_multiple_sessions(self):
        self.run_lua('''
local first=current
start();frames=300;mark(true);fail_write=true;frames=600;stop()
current='E:/录制/另一个文件.mp4'
start();frames=900;mark(true);frames=1200;stop()
fail_write=false;poll()
assert(saved(first):find('"status":"complete"'),'BUG: first pending session was lost')
assert(saved():find('"status":"complete"'),'BUG: second pending session was lost')
assert(countmarks(saved(first))==1);assert(countmarks()==1);assert(released==2)
''')

    def test_failed_json_encoding_keeps_dirty_marker_for_retry(self):
        self.run_lua('''
start();frames=300;fail_encode=true;mark(true)
fail_encode=false;poll()
assert(countmarks()==1,'BUG: serialization failure dropped an unsaved marker')
stop()
''')

    def test_normal_pause_dedupe_and_stop(self):
        self.run_lua('''
mark(true);assert(saved()=='');start()
frames=300;mark(true);mark(false);frames=301;mark(true);assert(countmarks()==1)
paused=true;frames=600;mark(true);assert(countmarks()==1)
paused=false;mark(true);assert(countmarks()==2);stop()
assert(saved():find('"status":"complete"'))
''')

    def test_pending_unsaved_recording_blocks_reusing_its_filename(self):
        self.run_lua('''
fail_write=true;start();frames=300;mark(true);frames=600;stop()
start();frames=900;mark(true);stop()
fail_write=false;poll()
assert(countmarks()==1,'BUG: a new session overwrote a pending recording')
assert(saved():find('20.000000'));assert(saved():find('"status":"complete"'))
''')

    def test_real_obs_lua_json_and_unicode_filesystem(self):
        # Actual SWIG functions and obs.dll file I/O, with frontend/encoder still simulated.
        if os.name!='nt' or OBS_BIN is None:
            self.skipTest('Native OBS SWIG/file I/O requires Windows and OBS_BIN_DIR')
        binding=C.CDLL(str(OBS_BIN.parent.parent/'data/obs-scripting/64bit/obslua.dll'))
        binding.luaopen_obslua.argtypes=[C.c_void_p]
        binding.luaopen_obslua(self.vm)
        self.run_lua('''
local native=obslua;obslua=obs
obs.obs_data_create_from_json=native.obs_data_create_from_json
obs.obs_data_save_json_safe=native.obs_data_save_json_safe
obs.obs_data_release=native.obs_data_release
obs.os_file_exists=native.os_file_exists
''')
        with tempfile.TemporaryDirectory(prefix='apex_真实接口_') as folder:
            movie=Path(folder)/'中文录像.mp4'
            self.run_lua('current='+json.dumps(str(movie),ensure_ascii=False))
            self.run_lua('start();frames=300;mark(true);frames=600;mark(true);frames=900;stop()')
            target=Path(str(movie)+'.apex-markers.json')
            data=json.loads(target.read_text(encoding='utf8'))
            self.assertEqual(data['recording_path'],str(movie))
            self.assertEqual(data['status'],'complete')
            self.assertEqual([m['time_seconds'] for m in data['markers']],[10,20])
            self.assertEqual(data['duration_seconds'],30)
            self.run_lua('start();frames=1200;mark(true);stop()')
            self.assertEqual(json.loads(target.read_text(encoding='utf8')),data)


if __name__=='__main__':
    unittest.main(verbosity=2)
