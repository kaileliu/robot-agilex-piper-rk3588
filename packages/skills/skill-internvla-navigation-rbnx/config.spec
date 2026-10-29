# Runtime config for this skill. Add these values under the skill's `config:`
# entry in robonix_manifest.yaml.

config:
  # InternVLA inference server.
  # string, default: http://127.0.0.1:5801
  server_url: http://127.0.0.1:5801

  # Client identifier sent to InternVLA.
  # string, default: lynx-01
  client_id: lynx-01

  # Timeout for one InternVLA HTTP request, including model inference.
  # float seconds, default: 120.0, must be > 0
  request_timeout_sec: 120.0

  # Timeout for one camera snapshot RPC.
  # float seconds, default: 10.0, must be > 0
  camera_timeout_sec: 10.0

  # Timeout for one chassis move RPC.
  # float seconds, default: 10.0, must be > 0
  chassis_timeout_sec: 10.0

  # Navigation loop limits.
  # int, default: 100, must be > 0
  max_steps: 100

  # float seconds, default: 300.0, must be > 0
  timeout_sec: 300.0

  # Delay before capturing the next frame after a wait result.
  # float seconds, default: 0.30, must be >= 0
  wait_interval_sec: 0.30

  # Model-output interpretation. These values describe generic distance and
  # angle commands; Lynx-specific speed and ratio conversion belongs to the
  # chassis primitive.
  # float metres, default: 0.30, must be > 0
  lookahead_m: 0.30

  # Distance represented by one InternNav forward action.
  # float metres, default: 0.25, must be > 0
  discrete_forward_m: 0.25

  # A trajectory heading error larger than this produces a rotate command.
  # float degrees, default: 15.0, must be >= 0
  turn_threshold_deg: 15.0

  # Angle represented by one InternNav left/right action.
  # float degrees, default: 15.0, must be > 0
  discrete_step_deg: 15.0
