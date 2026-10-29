# InternVLA Navigation capability

## `robonix/skill/internvla_navigation/navigate`

Use this capability when the user asks the robot to navigate toward a place or
object described in natural language and InternVLA should control the local
closed-loop motion.

Input:

- `instruction`: required natural-language navigation goal.
- `max_steps`: optional per-call iteration limit; `0` uses deployment config.
- `timeout_sec`: optional per-call total timeout; `0` uses deployment config.

Output:

- `status`: `completed`, `failed`, `busy`, `cancelled`, `timeout`, or
  `max_steps`.
- `reason`: human-readable terminal reason.
- `steps`: number of chassis move commands completed.

The skill discovers `robonix/primitive/camera/snapshot` over MCP and
`robonix/primitive/chassis/move` over gRPC through Atlas. Each iteration is:

1. capture one JPEG RGB snapshot;
2. upload it to the InternVLA `/eval_dual` endpoint;
3. convert the returned trajectory or discrete action into one bounded move;
4. capture a new image before issuing another move.

The first release intentionally sends no depth image. The InternVLA server must
fill missing depth with zeros. Do not use `camera/depth_snapshot` as metric
depth because it is an 8-bit visualization JPEG.

