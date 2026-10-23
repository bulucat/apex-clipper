-- Apex OBS Marker 1.1.0. Load through OBS Tools -> Scripts; no Python required.
-- Marker data stays beside the recording. Video/audio are never modified.
obs = obslua
local state = nil
local pending_writes = {}
local lookback = 60
local hotkey = obs.OBS_INVALID_HOTKEY_ID
local last_press = -10
local audible_feedback = false
local last_status = "尚未开始录制"
local function feedback()
    if not audible_feedback then return end
    local ok, ffi = pcall(require, "ffi")
    if not ok or ffi.os ~= "Windows" then return end
    -- Optional system sound; failure must never affect saving a marker.
    pcall(function()
        ffi.cdef("int MessageBeep(unsigned int uType);")
        ffi.load("user32").MessageBeep(0x40)
    end)
end

local function log(level, text)
    obs.script_log(level, "[Apex Marker] " .. text)
end

local function quote(s)
    return '"' .. tostring(s):gsub('[%z\1-\31\\"]', function(c)
        local escapes = {['"']='\\"', ['\\']='\\\\', ['\n']='\\n', ['\r']='\\r', ['\t']='\\t'}
        return escapes[c] or string.format('\\u%04x', string.byte(c))
    end) .. '"'
end

local function persist(snapshot)
    local state = snapshot or state
    if not state then return false end
    -- Keep dirty even if JSON encoding itself fails, before reaching the filesystem.
    state.dirty = true
    local marks = {}
    for i, t in ipairs(state.markers) do
        marks[i] = string.format('{"time_seconds":%.6f}', t)
    end
    local json = '{"schema":"apex-obs-markers/v1","clock":"obs_output_frames",' ..
        '"recording_path":' .. quote(state.path) .. ',"status":' .. quote(state.status) ..
        ',"lookback_seconds":' .. state.lookback .. ',"duration_seconds":' ..
        string.format('%.6f', state.duration) .. ',"markers":[' .. table.concat(marks, ',') .. ']}'
    local data = obs.obs_data_create_from_json(json)
    if not data then
        log(obs.LOG_ERROR, "Cannot encode marker data; marks remain in memory.")
        return false
    end
    -- OBS filesystem helpers support Windows Unicode paths and an atomic safe save.
    local ok = obs.obs_data_save_json_safe(data, state.sidecar, "tmp", "bak")
    obs.obs_data_release(data)
    state.dirty = not ok
    if not ok and not state.write_error then
        log(obs.LOG_ERROR, "Cannot save " .. state.sidecar .. "; check folder permissions/free space.")
    end
    state.write_error = not ok
    return ok
end

local function seconds()
    if not state or not state.output then return 0 end
    local frames = obs.obs_output_get_total_frames(state.output)
    return math.max(0, frames * state.fps_den * state.divisor / state.fps_num)
end

local function begin_recording()
    if state then return end
    local filename = obs.obs_frontend_get_current_record_output_path()
    if not filename or filename == "" then
        log(obs.LOG_ERROR, "OBS has no recording filename yet; mark again after recording starts.")
        return
    end
    local output = obs.obs_frontend_get_recording_output()
    if not output then return end
    local info = obs.obs_video_info()
    if not obs.obs_get_video_info(info) or info.fps_num <= 0 or info.fps_den <= 0 then
        obs.obs_output_release(output)
        log(obs.LOG_ERROR, "Cannot read recording frame rate.")
        return
    end
    local sidecar = filename .. ".apex-markers.json"
    local blocked = obs.os_file_exists(sidecar) or obs.os_file_exists(sidecar .. ".bak") or
        obs.os_file_exists(sidecar .. ".tmp")
    for _, snapshot in ipairs(pending_writes) do
        if snapshot.sidecar == sidecar then blocked = true end
    end
    if blocked then
        obs.obs_output_release(output)
        log(obs.LOG_ERROR, "Marker file already exists; use a new recording filename: " .. sidecar)
        return
    end
    local divisor = 1
    if obs.obs_encoder_get_frame_rate_divisor then
        local encoder = obs.obs_output_get_video_encoder(output)
        if encoder then divisor = obs.obs_encoder_get_frame_rate_divisor(encoder) end
    end
    state = {path=filename, sidecar=sidecar, output=output, markers={}, duration=0,
        status="recording", lookback=lookback, fps_num=info.fps_num,
        fps_den=info.fps_den, divisor=math.max(1, divisor), dirty=true}
    last_press = -10
    last_status = "录制中：0 个标记"
    persist()
    log(obs.LOG_INFO, "Ready. Markers will be saved to " .. sidecar)
end

local function check_path()
    if not state then return end
    local current = obs.obs_frontend_get_current_record_output_path()
    if current and current ~= "" and current ~= state.path and state.status ~= "unsupported_split" then
        state.status = "unsupported_split"
        state.dirty = true
        log(obs.LOG_ERROR, "Split recording detected. This version requires one file per recording; these marks cannot be imported.")
    end
end

local function flush_pending()
    for i = #pending_writes, 1, -1 do
        if persist(pending_writes[i]) then table.remove(pending_writes, i) end
    end
end

local function poll()
    flush_pending()
    if not state then return end
    if obs.obs_frontend_recording_active() then check_path() end
    state.duration = seconds()
    if state.dirty then persist() end
end

local function add_mark(pressed)
    if not pressed then return end
    if not obs.obs_frontend_recording_active() then
        log(obs.LOG_INFO, "No active recording; mark ignored.")
        return
    end
    if obs.obs_frontend_recording_paused() then
        log(obs.LOG_INFO, "Recording is paused; mark ignored.")
        return
    end
    if not state then begin_recording() end
    if not state then return end
    check_path()
    if state.status ~= "recording" then return end
    local t = seconds()
    if t <= 0 or t-last_press < 2 then return end
    last_press = t
    state.duration = t
    table.insert(state.markers, t)
    if persist() then
        last_status = string.format("已保存 %d 个标记；最后一次 %.3f 秒", #state.markers, t)
        feedback()
        log(obs.LOG_INFO, string.format("Saved mark #%d at %.3fs; review %.3f..%.3fs.",
            #state.markers, t, math.max(0,t-state.lookback), t))
    end
end

local function finish(status)
    if not state then return end
    check_path()
    state.duration = seconds()
    if state.status ~= "unsupported_split" then state.status = status end
    local finished = state
    local saved = persist(finished)
    obs.obs_output_release(state.output)
    finished.output = nil
    if not saved then
        table.insert(pending_writes, finished)
        log(obs.LOG_ERROR, "Final marker save failed; retrying while this script remains loaded: " .. finished.sidecar)
    end
    last_status = string.format("本次 %d 个标记；录制已结束%s", #finished.markers, saved and "，已保存" or "，等待保存重试")
    state = nil
end

local function on_event(event)
    if event == obs.OBS_FRONTEND_EVENT_RECORDING_STARTED then begin_recording()
    elseif event == obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED then finish("complete") end
end

function script_description()
    return [[<h3>Apex 精彩标记助手 1.1.0</h3>
    <p>录制时按快捷键，剪辑时只检查标记前的内容。默认 60 秒。</p>
    <p>加载后到「设置 → 热键」搜索 <b>Apex：标记精彩操作</b>，自行设置 F8 等不冲突的键。
    暂停录制和未录制时不接受标记；连续两秒内重复按键只记一次。</p>
    <p>标记文件保存在录像旁，扩展名为 .apex-markers.json。请与原录像一起保留。
    本版请关闭 OBS 自动分割文件；正常停止录制后再剪辑。</p>]]
end

function script_defaults(settings)
    obs.obs_data_set_default_int(settings, "lookback_seconds", 60)
    if obs.obs_data_set_default_bool then obs.obs_data_set_default_bool(settings, "audible_feedback", false) end
end

function script_properties()
    local props = obs.obs_properties_create()
    if obs.obs_properties_add_bool then
        obs.obs_properties_add_bool(props, "audible_feedback", "标记保存成功时播放Windows提示音（可能被桌面音频录入）")
    end
    if obs.obs_properties_add_text and obs.OBS_TEXT_INFO then
        obs.obs_properties_add_text(props, "marker_status", last_status, obs.OBS_TEXT_INFO)
        obs.obs_properties_add_button(props, "refresh_status", "刷新标记状态", function(properties)
            local property = obs.obs_properties_get(properties, "marker_status")
            if property then obs.obs_property_set_description(property, last_status) end
            return true
        end)
    end
    obs.obs_properties_add_int(props, "lookback_seconds", "标记前检查多少秒（下次录制生效）", 1, 600, 1)
    obs.obs_properties_add_button(props, "mark_now", "立即标记（测试）", function()
        add_mark(true)
        return false
    end)
    return props
end

function script_update(settings)
    lookback = math.max(1, math.min(600, obs.obs_data_get_int(settings, "lookback_seconds")))
    audible_feedback = obs.obs_data_get_bool and obs.obs_data_get_bool(settings, "audible_feedback") or false
end

function script_load(settings)
    script_update(settings)
    hotkey = obs.obs_hotkey_register_frontend("apex_marker.mark", "Apex：标记精彩操作", add_mark)
    local saved = obs.obs_data_get_array(settings, "mark_hotkey")
    obs.obs_hotkey_load(hotkey, saved)
    obs.obs_data_array_release(saved)
    obs.obs_frontend_add_event_callback(on_event)
    obs.timer_add(poll, 1000)
    if obs.obs_frontend_recording_active() then begin_recording() end
end

function script_save(settings)
    local saved = obs.obs_hotkey_save(hotkey)
    obs.obs_data_set_array(settings, "mark_hotkey", saved)
    obs.obs_data_array_release(saved)
end

function script_unload()
    obs.timer_remove(poll)
    obs.obs_frontend_remove_event_callback(on_event)
    finish("interrupted")
    flush_pending()
    if #pending_writes > 0 then
        log(obs.LOG_ERROR, "Some marker files remain unsaved; check the script log and filesystem before closing OBS.")
    end
end
