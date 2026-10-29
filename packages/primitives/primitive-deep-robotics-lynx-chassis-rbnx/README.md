# DeepRobotics Lynx S10 Chassis Primitive for Robonix

This repository provides a Robonix chassis Primitive for the DeepRobotics Lynx S10 wheeled quadruped. It converts standard `robonix/primitive/chassis/move` requests into Lynx UDP control commands and selects ratio-based or direct-value control through deployment configuration.

Upper-layer Skills always send generic distance or angle commands and do not need to know the Lynx control protocol or calibration parameters.

## Features

- Provides the standard `robonix/primitive/chassis/move` gRPC capability.
- Supports `forward_m`, `rotate_deg`, and direct three-axis control values.
- Selects the underlying Lynx control method at startup through `control_mode`:
  - `ratio`: uses ratio-based `Motion_Ctrl`.
  - `direct`: uses direct-value `Real_Motion_Ctrl`.
- Sends UDP control packets periodically at `ctl_hz` for the requested duration.
- Supports `dry_run` for inspecting converted actions without connecting to the robot.
- Makes the robot stand during initialization and sit during normal shutdown before releasing UDP resources.

## Capability

| Contract | Transport | Description |
| --- | --- | --- |
| `robonix/primitive/chassis/move` | gRPC | Executes one time-bounded chassis motion |

The request uses the standard Robonix `MoveCommand` and supports these fields:

| Field | Unit or range | Description |
| --- | --- | --- |
| `forward_m` | metres | Open-loop forward or backward distance |
| `rotate_deg` | degrees | Open-loop in-place rotation; direction can be adjusted with `yaw_sign` |
| `linear_x` | Depends on `control_mode` | Direct X-axis control value |
| `linear_y` | Depends on `control_mode` | Direct Y-axis control value |
| `angular_z` | Depends on `control_mode` | Direct yaw control value |
| `duration_sec` | seconds | Duration for direct control values; a non-positive value uses `default_dur` |

`forward_m` and `rotate_deg` cannot both be non-zero. Distance and angle fields take precedence over direct control fields. If all motion fields are zero, the request is treated as a `noop` and no UDP packet is sent.

The response field `status.data` contains a JSON string. For example:

```json
{
  "status": "done",
  "dry_run": false,
  "udp_sent": true,
  "action": {
    "mode": "forward",
    "mc_type": "Motion_Ctrl",
    "x": 0.3,
    "y": 0.0,
    "yaw": 0.0,
    "dur": 1.0
  }
}
```

## Control modes

### `ratio`

The `ratio` mode uses Lynx `Motion_Ctrl`:

- `forward_m` uses `forward_ratio` as the X-axis ratio and calculates duration with `forward_sec_per_meter`.
- `rotate_deg` uses `turn_ratio` as the yaw ratio and calculates duration with `turn_sec_per_degree`.
- Direct `linear_x`, `linear_y`, and `angular_z` values are interpreted as ratios in `[-1, 1]`.

With the default configuration, `forward_m: 0.25` is converted to:

```text
Motion_Ctrl: X=0.30, Y=0.00, Yaw=0.00, duration=1.00s
```

### `direct`

The `direct` mode uses Lynx `Real_Motion_Ctrl`:

- `forward_m` uses `move_v` as the open-loop linear velocity. Duration is `abs(forward_m) / move_v`.
- `rotate_deg` uses `turn_w` as the open-loop angular velocity. Duration is `abs(radians(rotate_deg)) / turn_w`.
- Direct three-axis values are passed unchanged as `Real_Motion_Ctrl` values.

With the default configuration, `forward_m: 0.25` is converted to:

```text
Real_Motion_Ctrl: X=0.20, Y=0.00, Yaw=0.00, duration=1.25s
```

## Requirements

- A DeepRobotics Lynx S10 configured to accept SDK control.
- Network connectivity between the host running the Primitive and the robot's UDP control endpoint.
- Python 3.10+.
- Robonix Python API and code-generated modules.
- The Lynx UDP client and protocol-packing utilities included in this repository.

Before starting, verify connectivity:

```bash
ping <robot_ip>
```

Also verify the robot IP address, UDP port, and vendor SDK control permissions.

## Build

Run the following commands from the Primitive repository root:

```bash
rbnx validate .
bash scripts/build.sh
```

Run the tests and Python compilation check with:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile lynx_chassis/main.py lynx_chassis/robot_control.py
```

## Deployment configuration

Add the Primitive to the robot deployment repository's `robonix_manifest.yaml`:

```yaml
primitive:
  - name: lynx_chassis
    path: ../primitive-deep-robotics-lynx-chassis-rbnx
    config:
      dry_run: true

      robot_ip: 10.21.33.103
      robot_port: 30004
      timeout: 3.0
      ctl_hz: 20.0

      # ratio: Motion_Ctrl
      # direct: Real_Motion_Ctrl
      control_mode: ratio

      # Ratio-mode parameters
      forward_ratio: 0.30
      turn_ratio: 0.40
      forward_sec_per_meter: 4.0
      turn_sec_per_degree: 0.0666667

      # Direct-mode parameters
      move_v: 0.20
      turn_w: 0.50

      default_dur: 0.50
      yaw_sign: 1.0
```

Configuration fields:

| Field | Default | Description |
| --- | ---: | --- |
| `dry_run` | `false` | Logs the converted action without sending UDP packets |
| `robot_ip` | `10.21.33.103` | IP address of the Lynx control endpoint |
| `robot_port` | `30004` | Lynx UDP control port |
| `timeout` | `3.0` | UDP socket timeout, in seconds |
| `ctl_hz` | `20.0` | Motion-control packet frequency, in Hz |
| `control_mode` | `ratio` | Control mode: `ratio` or `direct` |
| `forward_ratio` | `0.30` | Forward magnitude in ratio mode, in the range `(0, 1]` |
| `turn_ratio` | `0.40` | Turning magnitude in ratio mode, in the range `(0, 1]` |
| `forward_sec_per_meter` | `4.0` | Open-loop forward travel time per metre in ratio mode |
| `turn_sec_per_degree` | `0.0666667` | Open-loop turning time per degree in ratio mode |
| `move_v` | `0.20` | Forward velocity in direct mode |
| `turn_w` | `0.50` | Angular velocity in direct mode, in rad/s |
| `default_dur` | `0.50` | Default duration when a direct control request omits a positive duration |
| `yaw_sign` | `1.0` | Yaw-direction correction; must be `1.0` or `-1.0` |

Parameters for the inactive control mode are still validated during initialization and must therefore remain valid positive values.

## Calibration

The relationship between control ratios, speed, and displacement depends on the robot state, floor surface, battery level, and payload. Calibrate the open-loop parameters in a clear area:

1. Set `dry_run: true` and verify the command fields and directions.
2. Set `dry_run: false` and begin with small `forward_ratio` and `turn_ratio` values.
3. Send a known `forward_m` command, measure the actual distance, and adjust `forward_sec_per_meter`.
4. Send a known `rotate_deg` command, measure the actual angle, and adjust `turn_sec_per_degree`.
5. If the yaw direction is reversed, change `yaw_sign` from `1.0` to `-1.0`.

Increasing `forward_ratio` or `turn_ratio` increases instantaneous speed. Changing the per-metre or per-degree duration changes only the motion duration.

## Start and verify

Run the following commands from the robot deployment repository:

```bash
rbnx build -f robonix_manifest.yaml
rbnx boot -v -f robonix_manifest.yaml
rbnx caps -v
```

Confirm that the capability list contains:

```text
robonix/primitive/chassis/move
```

Keep the following setting for the first end-to-end test:

```yaml
dry_run: true
```

The logs show the converted `mc_type`, X/Y/Yaw values, and duration. Disable `dry_run` and connect to the physical robot only after confirming that these values are correct.

## Runtime behavior and safety

- After successful initialization, the Primitive sends the motion-state command that makes the Lynx stand.
- During shutdown, it attempts to make the Lynx sit before closing the UDP client.
- When a time-bounded motion ends, the Primitive stops periodic transmission and does not send an additional zero-valued motion packet.
- An all-zero `move` request is a `noop`, not an emergency-stop command.
- Distance and angle control are currently open-loop estimates based on time and do not use odometry feedback. Actual motion will contain error.
- The Primitive does not provide obstacle avoidance, collision detection, or a hardware emergency stop. Test in an open area and ensure an operator can take over at any time with the manufacturer's remote controller or emergency-stop mechanism.

## License

Apache-2.0
