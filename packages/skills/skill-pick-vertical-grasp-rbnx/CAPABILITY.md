---
description: S10 抓取技能：抓起来并保持握持（grab，目标不在桌面时返回 "not detected"）、把已握持的物体放进盒子（put_in_box）、就地松手（put_down）。纯臂能力（pick / 通用检测抓取 / 侧摆 / demo）已存档在纯臂副本。
---

# Pick skill (S10)

This skill grasps a table-top object with a top-down gripper. It is the **S10
variant** of `skill-pick-vertical-grasp-rbnx`: only the capabilities the S10
demo needs are registered — `driver` (lifecycle), `grab` (grasp and hold),
`put_in_box` (drop a held object into the box) and `put_down` (release in
place). The piper-only capabilities (`pick`, `pick_general`, `grab_general`,
`move_arm_aside`, `demo`) live in the archived pure-arm copy.

## `grab`

Call when the user wants the object picked up and HELD — "grab it / pick it up /
hand it to me / hold it", described by name, color or shape in ANY language
(西红柿 / 红色物体 / the red one / 蓝色方块 / toy carrot / potato...).
Pass the user's description as `object_name`:

```json
{"object_name": "红色物体"}
```

The arm grasps the matched object and keeps holding it, then parks at the zero
pose with the gripper closed. If the target is NOT on the table, the tool
reports "object not detected" — relay that to the user as-is (e.g. "没有蓝色
方块") and grasp nothing.

Do NOT infer what is on the table from this manual: only the call result says
what is actually there.

The object stays in the gripper until the user asks for it to be released, so
follow up with `put_in_box` (put it in the box) or `put_down` (let go).

## `put_in_box`

Call after a successful `grab` when the user wants the held object put away:
"把东西放到盒子里 / 放进盒子里 / 收起来 / put it in the box". Carries the
held object over the box, releases it there, and returns home empty-handed.
Request: `{}`.

## `put_down`

Call when the user wants the held object released WHERE IT IS — "put it down /
release it / let go / 放下 / 松手". Opens the gripper in place with zero arm
motion (reads the live arm joint feedback and republishes it unchanged, only the
fingers widen). Request: `{}`.
