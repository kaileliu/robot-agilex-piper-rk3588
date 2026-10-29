# skill-pick-vertical-grasp-rbnx (S10 variant)

Robonix user-facing grasp skill for the S10 robot (arm on the dog, D435i camera
mounted ON the arm). It owns `robonix/skill/pick/*`.

Catalog name: `robonix.skill.pick.vertical_grasp`.

This package is the **S10 variant**. It keeps only the capabilities the S10 demo
needs (`driver`, `grab`, `put_in_box`, `put_down`); the piper-only capabilities
(`pick`, `pick_general`, `grab_general`, `move_arm_aside`, `demo`) were removed
here and live in the archived pure-arm copy. Decisions behind the split:
`docs/PICK_SKILL_S10_REORG.md`.

## Capability surface

| Contract | Transport | Purpose |
| --- | --- | --- |
| `robonix/skill/pick/driver` | gRPC | Lifecycle (`CMD_INIT` / lazy `CMD_ACTIVATE`) |
| `robonix/skill/pick/grab` | MCP | Grasp-and-hold entry — any description, matched against an in-skill YOLO detect-all via LLM; parks at the teach-safe pose still holding |
| `robonix/skill/pick/put_in_box` | MCP | Carry the held object over the box, release, home |
| `robonix/skill/pick/put_down` | MCP | Open the gripper where the arm is (no arm motion) |

`grab` accepts ANY user description as `object_name`:

```json
{"object_name": "红色物体"}
```

It takes the latest camera frame itself, POSTs it to the host GPU YOLO server
(:8770, no `object_name` filter -> every detection on the table), then an LLM
(`grab_match_llm_*`, same provider as the pilot planner) matches the request
against the detections. No match -> "not detected" error, nothing grasped. The
call is synchronous, uses a fixed 60 s budget, and makes one grasp attempt.

## Runtime paths

```text
pilot / executor
  -> pick_skill.grab(object_name)                     MCP
  -> reset to the observation pose (arm/base_link homography is only
     valid from that repeatable pose) — stage 0
  -> in-skill detection (no object_detect service):
       camera frame -> undistort -> host GPU YOLO detect-all (:8770,
       no object_name filter) -> 2-frame consistency -> box exclusion
       -> LLM match (grab_match_llm_*) request vs detections
       (or the VLM path when detect_scheme=vlm)
  -> grasp_pose.grasp_request                        gRPC
  -> roboarm_ik.execute_grasp ×3 (hover / descend+close / lift)
  -> gripper feedback check on /arm/joint_states_single
  -> teach_safe(hold_gripper=True): park at the zero pose, still holding

pilot / executor
  -> pick_skill.put_in_box()                         MCP
  -> carry held object over the box, release, home empty-handed

pilot / executor
  -> pick_skill.put_down()                           MCP
  -> replay the live joints on /arm/joint_command with only the gripper
     opened — zero arm motion
```

Atlas resolves all endpoints. There is no legacy ROS-service or
`/graspnet` fallback in this skill.

## Lifecycle and dependencies

At boot, `CMD_INIT` only parses configuration and the skill remains
`INITIALIZED`. On the first tool call, `CMD_ACTIVATE` resolves upstream
endpoints.

Required:

- `service/perception/grasp_pose/grasp_request` over gRPC;
- `service/manipulation/execute_grasp` over gRPC.

Optional during activation (missing -> warning, degraded behaviour):

- `reset`: observation-pose setup and failure recovery;
- `teach_safe`: zero-pose parking for `grab` (gripper held) and for every exit
  path.

`service/perception/object_detect/detect_object` is deliberately **not** a
dependency: S10 does not deploy that service, and the skill does its own
detection (undistort + 2-frame filter + wording match + box exclusion are not
available from the service today). Rationale: `docs/PICK_SKILL_S10_REORG.md`.

## Grab sequence

1. If `reset` is available, move to the calibrated observation pose.
2. Detect the target in-skill (see above).
3. Compute one vertical grasp pose with `grasp_pose` (`retry=0`).
4. `_grasp_and_verify`: hover (pre_pose, gripper open) -> descend to the grasp
   pose and close -> lift back to pre_pose still closed -> check fresh gripper
   feedback on `/arm/joint_states_single`. A failed approach or descend aborts;
   a failed lift only warns (the object may already be in the gripper).
5. On success, park at the teach-safe pose with the gripper closed (object
   still held).

The grasp z comes from `grasp_pose` (its own configured grasp height); every
in-air height the skill commands comes from the height model below
(`desktop_height` + `travel_offset`). The skill does not perform camera
calibration or IK itself.

## Configuration

```yaml
gripper_open_width: 0.08              # metres
gripper_close_width: 0.0              # metres
gripper_grasp_threshold_width: 0.005  # measured width above this means held
gripper_feedback_timeout_s: 2.0
gripper_joint_states_topic: /arm/joint_states_single
desktop_height: 0.115                 # desktop height in arm/base_link (m).
                                      # PER DEPLOY: set it from your own
                                      # calibration; the frame travels with the
                                      # arm mount, so piper and S10 differ.
grasp_offset: 0.02                    # grasp/place height = desktop + this
travel_offset: 0.10                   # every in-air height = desktop + this
                                      # (grasp hover, lift, carry, drop release)
camera_topic: /camera_435i/color/image_raw  # S10: camera on the arm
yolo_http_url: http://127.0.0.1:8770/detect  # host GPU YOLO (detect-all)
yolo_conf: 0.6                        # confidence floor for detect-all
yolo_iou: 0.45
yolo_two_frame_interval_s: 0.7        # two-frame consistency gap
yolo_two_frame_iou: 0.3               # ... and its same-object IoU
detect_scheme: yolo                   # yolo | vlm
grab_vlm_base_url / _api_key / _model # used when detect_scheme=vlm
grab_match_llm_base_url / _api_key / _model  # wording matcher (text LLM)
homography_matrix / camera_matrix / dist_coeffs  # pixel→arm XY + undistort
box_xy / place_positions / place_fallback_xy     # the single box drop point
# (heights: see desktop_height / grasp_offset / travel_offset above)
place_go_down_before_open: true       # true: descend before releasing; false:
                                      # drop mode — open at the hover pose
place_jitter_enabled: false
place_jitter_m: 0.03
runtime_config_file: ""               # live config file (place mode +
                                      # detect_scheme), re-read before each
                                      # action; absent = use the values above.
                                      # Legacy key name: place_mode_override_file
place_exclusion_radius_m: 0.20        # detections this close to the box are
                                      # treated as already sorted
```

## Layout

```text
skill-pick-vertical-grasp-rbnx/
├── package_manifest.yaml
├── capabilities/
│   ├── driver.v1.toml
│   ├── grab.v1.toml
│   ├── put_down.v1.toml
│   ├── put_in_box.v1.toml
│   ├── lib/{pick,grasp,manipulation}/srv/
│   └── service/{perception,manipulation}/
├── pick_skill/
│   ├── atlas_bridge.py   编排：能力声明、生命周期、三个工具的流程、
│   │                     SkillConfig / HeightConfig / PlaceConfig
│   ├── motion.py         运动：Arm 类（move_pose / go_observe / go_teach_safe /
│   │                     open_gripper_in_place）+ GripperConfig，失败抛 MotionError
│   ├── grpc_clients.py   传输：Clients 类（上游通道/桩 + 四个契约调用）
│   ├── poses.py          位姿：构造、平移、dict/protobuf/MCP 三种表示转换
│   ├── camera.py         取图：帧缓存、新鲜度、去畸变（含图像订阅）+ CameraConfig
│   ├── geometry.py       像素 → 臂基座 XY + 落盒点排除判定
│   ├── detect.py         检测：yolo/vlm 统一入口 + DetectConfig
│   └── config.py         配置读取：num/text/flag/matrix/xy… 统一类型与坏值处理
└── scripts/{build,start}.sh
```

This is a pure-Python orchestration package. Its build is codegen-only; it
does not vendor a ROS package.

分工原则：`atlas_bridge.py` 只做业务编排，只调其余模块的公开函数；运动、传输、
位姿、取图、检测、配置各自成模块，不互相穿透。每个模块用 `from_cfg` 解析自己
那份配置（谁消费谁定义键名与默认值），`init` 只负责组装。

## Build and verification

```bash
bash scripts/build.sh

# With the deploy running:
rbnx caps | grep pick
rbnx ask "请把胡萝卜抓起来"
```

## License

MulanPSL-2.0.
