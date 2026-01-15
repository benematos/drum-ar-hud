-- Drum AR HUD Reaper bridge
-- Sends transport state and project selection to server via HTTP
-- Uses curl via reaper.ExecProcess. Ensure curl is installed and accessible.

local server_host = "localhost"
local server_port = 8765

-- Marker prefix used to select project. Use markers named 'TABID:<projectId>'
local prefix = "TABID:"

-- Last project id sent
local last_project_id = nil

-- Send a POST request to the server. Escapes quotes in body.
local function send_request(path, body)
  -- Escape double quotes for shell
  local safe_body = body:gsub("\"", "\\\"")
  local url = string.format("http://%s:%d%s", server_host, server_port, path)
  local cmd = string.format('curl -s -X POST "%s" -H "Content-Type: application/json" -d "%s"', url, safe_body)
  reaper.ExecProcess(cmd, -1)
end

-- Returns the current projectId from markers (if any).
local function get_current_project_id()
  local retval, num_markers, num_regions = reaper.CountProjectMarkers(0)
  for i = 0, num_markers + num_regions - 1 do
    local ok, isrgn, pos, rgnend, name, markrgnindex = reaper.EnumProjectMarkers(i)
    if ok and not isrgn and name and name:sub(1, #prefix) == prefix then
      return name:sub(#prefix + 1)
    end
  end
  return nil
end

-- Main loop: gathers state and sends to server
function main_loop()
  -- Get playing state
  local play_state = reaper.GetPlayState()
  local playing = ((play_state & 1) == 1)

  -- Current position in seconds
  local pos = reaper.GetPlayPosition()

  -- Convert to quarter note position
  local qn = reaper.TimeMap2_timeToQN(0, pos)
  -- Time signature at this position
  local ts_num, ts_den = reaper.TimeMap_GetTimeSigAtTime(0, pos)
  ts_num = ts_num or 4
  ts_den = ts_den or 4

  -- Compute bar and beat numbers (1-based)
  local bar = math.floor(qn / ts_num) + 1
  local beat = math.floor(qn % ts_num) + 1

  -- PPQ position (pulses per quarter note; using 960 ticks per quarter)
  local ppq = qn * 960

  -- Current tempo
  local bpm = reaper.Master_GetTempo()

  -- Check for project selection marker
  local project_id = get_current_project_id()
  if project_id and project_id ~= last_project_id then
    last_project_id = project_id
    local sel_body = string.format('{"projectId":"%s"}', project_id)
    send_request("/api/select", sel_body)
  end

  -- Build state JSON body (no quotes inside values)
  local state_body = string.format('{"playing":%s,"bar":%d,"beat":%d,"bpm":%.6f,"ppq":%.3f,"ts_num":%d,"ts_den":%d}', tostring(playing), bar, beat, bpm, ppq, ts_num, ts_den)
  send_request("/api/state", state_body)

  -- Defer loop
  reaper.defer(main_loop)
end

-- Start loop
main_loop()
