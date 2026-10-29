# InternVLA Navigation Skill for Robonix

This repository provides a closed-loop vision-language navigation Skill for Robonix based on InternVLA. The Skill accepts a natural-language navigation goal, dynamically discovers camera and chassis capabilities through Atlas, captures RGB images, requests InternVLA inference, and converts model outputs into generic chassis distance or angle commands.

This repository is responsible **only** for interpreting model outputs and managing the navigation loop. 

## Features

- Accepts natural-language navigation instructions, such as "Move next to the red chair."
- Dynamically discovers camera and chassis capabilities through Atlas without hard-coding service addresses.
- Supports both `discrete_action` and `trajectory` outputs from InternVLA.
- Executes discrete actions in the original model output order without merging or splitting them.
- Captures a new image and performs inference in each iteration for closed-loop visual navigation.
- Supports maximum step count, task timeout, RPC timeouts, and mutual exclusion between navigation tasks.
- Attempts to stop the navigation workflow when a task completes, fails, is cancelled, or the Skill is deactivated.

## Workflow

1. Pilot or another client calls `robonix/skill/internvla_navigation/navigate`.
2. The Skill calls the camera `snapshot` capability to obtain a JPEG image.
3. The Skill sends the image and task information to a separately running InternVLA HTTP server.
4. The Skill converts the model result into `forward_m` or `rotate_deg`.
5. The Skill calls the standard `robonix/primitive/chassis/move` capability.
6. The task ends when the model returns a stop action, the step limit is reached, or an error occurs.

## Capability

The Skill provides the following capability:

| Contract | Transport | Description |
| --- | --- | --- |
| `robonix/skill/internvla_navigation/navigate` | MCP | Executes one natural-language visual navigation task |

Request fields:

| Field | Type | Description |
| --- | --- | --- |
| `instruction` | `string` | Natural-language navigation goal |
| `max_steps` | `int32` | Maximum number of motion steps for this task; `0` uses the deployment configuration |
| `timeout_sec` | `float64` | Total timeout for this task; `0` uses the deployment configuration |

Response fields:

| Field | Type | Description |
| --- | --- | --- |
| `status` | `string` | `completed`, `failed`, `busy`, `cancelled`, `timeout`, or `max_steps` |
| `reason` | `string` | Completion reason or error message |
| `steps` | `int32` | Number of chassis commands executed |

## Requirements

### Robonix capabilities

The following capabilities must be discoverable when the Skill is activated:

| Contract | Transport | Purpose |
| --- | --- | --- |
| `robonix/primitive/camera/snapshot` | MCP | Captures a JPEG-encoded RGB image |
| `robonix/primitive/chassis/move` | gRPC | Executes a generic distance or angle command |

The camera response must contain fields compatible with `sensor_msgs/Image`. Its `encoding` must be `jpeg` or `jpg`, and `data` must contain valid JPEG bytes.

### InternVLA HTTP server

We have provided a docker image [internvla-server](https://hub.docker.com/repository/docker/mujue37/internvla-server/general) which includes InternVLA-N1-DualVLN source code and environment, while the **model params are not included**, and you have to download the model params by youself and provides the path to the model in `.env`.(`cp .env.example ,env` and configure in `.env`). The model url on huggingface: [InternVLA-N1-DualVLN](https://huggingface.co/InternRobotics/InternVLA-N1-DualVLN/tree/main). 

**NOTE**: It would be a better choice to pull the image before you start Robonix, otherwise it will take a LONG TIME to pull the image and the Robonix may seems not work well.

When this skill is activated by Robonix, you need to wait about 20-40 seconds to make sure the model is completely loaded and ready for inference. More details can refer to `scripts/start.sh` and `compose.yaml`(which passes in args Internnav needs).

The InternVLA inference server must run separately and provide these endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Checks whether the model is ready |
| `POST` | `/task/start` | Creates a navigation task |
| `POST` | `/eval_dual` | Uploads the current RGB image and runs inference |
| `POST` | `/task/report` | Reports completion, failure, or cancellation |

A successful `/eval_dual` response must contain either `discrete_action` or `trajectory`. The current implementation uploads RGB JPEG images only and does not upload depth images.

### Python dependencies

- Python 3.10+
- `requests`
- `mcp`
- `grpcio`
- Robonix Python API and code-generated modules

Install the dependencies with:

```bash
python3 -m pip install -r requirements.txt
```

## Usage

Run the following commands from the Skill repository root:

```bash
rbnx validate .
bash scripts/build.sh
```

`scripts/build.sh` checks the Python dependencies and runs Robonix code generation.

If you want to start the skill, run:

```bash
set -a
source .env
set +a
bash scripts/start.sh
```

Since a docker will be created to be the runtime of Internnav, you can run:

```bash
bash scripts/stop.sh
```
to exit gracefully.


## Deployment configuration

Add the Skill to the robot deployment repository's `robonix_manifest.yaml`:

```yaml
skill:
  - name: internvla_navigation
    path: ../skill-internvla-navigation-rbnx
    config:
      server_url: http://127.0.0.1:5801
      client_id: XXX

      request_timeout_sec: 120.0
      camera_timeout_sec: 10.0
      chassis_timeout_sec: 10.0

      max_steps: 100
      timeout_sec: 300.0
      wait_interval_sec: 0.30

      lookahead_m: 0.30
      discrete_forward_m: 0.25
      turn_threshold_deg: 15.0
      discrete_step_deg: 15.0
```

Configuration fields:

| Field | Default | Description |
| --- | ---: | --- |
| `server_url` | `http://127.0.0.1:5801` | Base URL of the InternVLA server |
| `client_id` | `XXX` | Client identifier sent to InternVLA |
| `request_timeout_sec` | `120.0` | Timeout for one InternVLA HTTP request, in seconds |
| `camera_timeout_sec` | `10.0` | Timeout for one camera snapshot RPC, in seconds |
| `chassis_timeout_sec` | `10.0` | Timeout for one chassis movement RPC, in seconds |
| `max_steps` | `100` | Default maximum number of chassis commands |
| `timeout_sec` | `300.0` | Default total task timeout, in seconds |
| `wait_interval_sec` | `0.30` | Delay before capturing another image after a wait result |
| `lookahead_m` | `0.30` | Look-ahead distance used for trajectory output, in metres |
| `discrete_forward_m` | `0.25` | Distance represented by one discrete forward action, in metres |
| `turn_threshold_deg` | `15.0` | Heading threshold above which a trajectory command turns in place first |
| `discrete_step_deg` | `15.0` | Angle represented by one discrete left or right turn action |

`forward_ratio`, `turn_ratio`, motion duration, and `control_mode` are not Skill configuration fields. They belong to the chassis-specific Primitive.

## Model action mapping

Discrete actions are converted one by one in the original model output order:

| InternVLA action | Skill behavior |
| ---: | --- |
| `0` | No operation; if the result contains only `0`, the task ends |
| `1` | `forward_m = discrete_forward_m` |
| `2` | `rotate_deg = +discrete_step_deg` |
| `3` | `rotate_deg = -discrete_step_deg` |
| `5`, `9` | Camera-related actions ignored by the chassis |

If both `discrete_action` and `trajectory` are present, `discrete_action` takes precedence.

For `trajectory`, the coordinate convention is positive X forward and positive Y left. The Skill selects the first trajectory point that reaches `lookahead_m`. If its heading error exceeds `turn_threshold_deg`, the Skill sends one `rotate_deg` command; otherwise, it sends one `forward_m` command no longer than the look-ahead distance. This is a local closed-loop fallback for operation without odometry or MPC, not a complete trajectory tracker.

## Start and use

Start the camera Primitive, chassis Primitive, and InternVLA server first. Then run the following commands from the deployment repository:

```bash
rbnx build -f robonix_manifest.yaml
rbnx boot -v -f robonix_manifest.yaml
rbnx caps -v
```

You can then submit a navigation request through Pilot, for example:

```bash
rbnx ask "Use InternVLA to navigate next to the red chair."
```

## Notes and safety

- For initial integration, set the chassis Primitive to `dry_run: true`. Confirm command direction and magnitude before connecting to the physical robot.
- The current implementation permits only one active navigation task. Concurrent requests return `busy`.
- A complete discrete action sequence is executed in order before the next image is captured. If immediate perception is required after every action, configure the inference server to return only one action at a time.
- If the camera, chassis, or InternVLA server is unavailable, activation is delayed or the Skill returns a failure response.
- This Skill does not provide obstacle avoidance, localization, or emergency stopping. Keep manual control and the manufacturer's safety mechanisms available during physical robot operation.

## License

MIT