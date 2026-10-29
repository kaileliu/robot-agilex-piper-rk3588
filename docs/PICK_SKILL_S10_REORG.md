# pick 技能整理方案（S10 版）

本文记录 `skill-pick-vertical-grasp-rbnx` 的整理决策与实施方案。该技能经过多次演示迭代，能力随当天需求增删，代码已经零散；纯臂与 S10 的演示内容差别大，需要**分成两套**：纯臂那套**存档**（近期不再修改），本文只讨论 **S10**。

现状规模：`pick_skill/atlas_bridge.py` 单文件 **2848 行**（上游 1298 行）；对外声明 9 个能力。

## 一、能力保留决策（S10）

| 能力 | 决定 | 说明 |
|---|---|---|
| `driver` | **保留** | 生命周期，其他能力都依赖它（解析 atlas 端点、启动 ROS 夹爪/相机订阅） |
| `grab` | **保留** | S10 主力：抓起来并保持握持，结束停在零位 |
| `put_in_box` | **保留** | 与 grab 配套：把已握持物体搬运入盒 |
| `put_down` | **待定** | 就地松手（无臂运动，直接发 `/arm/joint_command`） |
| `pick` | **删** | 分拣流程（grasp→搬运→放置→回家）；S10 不用 |
| `pick_general` | **删** | 与 `pick` 行为完全相同（`backend` 参数无人读），无意义 |
| `grab_general` | **删** | 同上，与 `grab` 完全相同 |
| `move_arm_aside` | **删** | 为让开俯视相机视野设计；S10 的相机在臂上，转开等于把视野一起转走 |
| `demo` | **删** | 两点之间往返搬运演示；它内部仍调用 `move_arm_aside`，在 S10 上不成立 |

## 二、为什么不使用 `service-object-detect-rbnx`（重要）

现状是**技能自己实现了一整套检测**（抓帧 → 去畸变 → 直连宿主 YOLO :8770 / 直连 VLM → 两帧过滤 → 物品名匹配 → 盒内排除），而 `detect_object` 服务是同一件事的另一份实现，技能**从不调用它**（唯一的调用点是从未被调用的 `_stage_detect_object`），却把它列为**激活必需依赖**。

曾评估把技能内检测换成调用该服务，结论是**服务当前支撑不了需求**，因此**不使用**，原因如下（都有代码依据）：

1. **服务不去畸变**。技能每次检测都 `_undistort_frame`，因为手眼 homography 是在"去畸变图"上采点的（技能注释：不去畸变角落像素可偏 ~30px ≈ 3cm）。服务的 yolo 路径是把原图直接 JPEG 编码后 POST，且没有 `camera_matrix`/`dist_coeffs` 配置。→ 直接切换会导致 S10 抓偏。
2. **服务不做"物品名 → 类名"匹配**。宿主 YOLO 服务按 `object_name` 做**子串过滤**（`yolo_detect_server.py:215-218`：`key in d["object_name"].lower()`，空则不滤），要求用户说的词是英文类名的子串；技能则是"不带名字全量检测 + 文本 LLM 把中文说法匹配到类名"（`_llm_match_object`），正是为处理"红色的那个/胡萝卜"这类说法。→ 直接切换会让中文说法一律"未检测到"。
3. **服务没有两帧一致性**。技能有（间隔 0.7s、同类且 IoU>0.3 才保留，滤掉闪烁误检）；服务是单次请求。顺带说明：S10 清单里给 `llm_detect` 配的 `yolo_two_frame_interval_s` / `yolo_two_frame_iou` 是**没人读的配置**（服务代码不看它们）。
4. **契约只返回 top-1**。`detect_object` 返回单个 bbox；技能若要自己挑选/匹配，需要服务返回全部检测（要动 `DetectObject.srv`）。

因此：**S10 不部署 `service-object-detect-rbnx`**；技能里那两处残留一并清理（`REQUIRED_INPUTS` 里的 `detect_object`、未被调用的 `_stage_detect_object`、没人读的 `backend` 参数）。
若将来检测要收归服务，需先补齐上述 1–3 项能力，再考虑是否扩展契约。

**附带结论**：`backend` 那套"每次调用选后端"的设计（服务里已实现）与技能里的 `detect_scheme` 互不相干；S10 里**只保留 `detect_scheme`**。

## 三、技能内检测的整理方案（抽取共性方法）

目标：把散在技能里的检测实现收成**一个入口 + 两条内部路径**，调用方式**与检测服务的接口保持一致**。

**统一入口（模仿 `DetectObject` 契约）**：

```python
def detect(object_name: str, backend: str = "") -> dict:
    """返回 {success, bbox_2d, object_center_3d, confidence, message}
    backend: "yolo" | "vlm" | ""（空 = 用 detect_scheme_now()）"""
```

- 与服务的 `DetectObject(object_name, backend) → bbox_2d / object_center_3d / confidence / success / message` **同形**——将来若要换回服务，只需替换这一个入口的实现。
- `backend` 在这里**真正生效**（与现状不同：现在 `_do_pick`/`_do_grab` 的参数没人读）；空值时回落到 `detect_scheme`（配置 + 运行时开关文件，行为不变）。

**内部两条路径（保留现有全部能力）**：

| 路径 | 步骤（保持现状语义） |
|---|---|
| `yolo` | 静置 2s（等臂稳定，S10 必需）→ 取帧 + 新鲜度检查 → 去畸变 → POST 宿主 YOLO（不带名字，全量）→ 两帧一致性过滤 → `_drop_sorted_in_box` 盒内排除 → 文本 LLM 匹配物品名 → 返回匹配项 bbox |
| `vlm` | 静置 2s → 取帧 + 新鲜度检查 → 去畸变 → 一次多模态调用 → bbox → 盒内排除（homography 投影判定）→ 返回 |

**同时清理**：

- 删掉未被调用的 `_stage_detect_object`（1117）与 `REQUIRED_INPUTS` 里的 `detect_object`；
- 删四处同名能力（pick/pick_general/grab_general/move_arm_aside）与 `demo` 的调用链；
- 删/整理演示残留：检测路径里写死的 `sleep`、调试图落盘（`rbnx-boot/logs/skill-frames`）、不可配置的常量；
- 竖直抓取三段式（悬停/下探/抬起）+ 夹爪校验在 `_do_pick`/`_do_grab`/`_do_demo` 里各有一份 → 收成一个共享函数（S10 只剩 grab 一个调用点，仍保留这个共享函数，便于将来扩展）；
- 统一复位语义：`reset`（回 init 观察位 + 开爪）与 `teach_safe`（回 teach-safe 位 ± 保持握持）在代码与清单里写清各自用途，避免再混用。

## 四、待定事项

1. **`put_down` 留不留**（S10）？它是就地松手、无臂运动，与 `put_in_box`（搬运入盒）用途不同。
2. **两套代码的物理形态**：纯臂能力"存档"具体怎么做——独立成一个不再维护的包？同一包内分区标注？还是移到归档目录/分支？
3. 检测抽取完成后，grab 的端到端流程需要**实机验证**（S10 上抓一个物体 → 入盒）。
