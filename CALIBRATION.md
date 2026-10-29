# 当前桌面标定

入口：<http://192.168.137.20:8090/handeye>。当前已切换到标定模式，chat 抓取和 8088 检测预览暂停。启动入口会阻止同时启动 Robonix，避免抢占相机和 CAN。标定服务自身启动、显示画面、读取坐标均不发送运动指令。

已采集 13 个点并按用户要求拟合、写入当前本地配置。但独立验证误差为 30.28、12.84、44.72 mm，尚未通过。自动运行受 `run-logs/calibration.requires-validation` 标记阻止，退出标定服务也不会自动解除该标记。需要复核对点参考位置并重新验证。原数据和旧配置均已备份，详见 [拟合报告](reports/calibration_fit.md)。

当前 Gemini 336L 彩色流为 1280×720、MJPG、10 FPS。已从当前相机的 ROS CameraInfo 导入出厂内参，fx/fy 约为 609.77/609.91，旧部署配置为 710.05/709.00，两者不一致。导入来源保存在 `tools/calibration/piper/artifacts/intrinsics/source.json`。这不是一次新的棋盘格内参标定。新数据使用当前内参去畸变，内参已与当前拟合矩阵一起写入 pick 和 grasp_pose。

## 现场采点

1. 固定相机和机械臂底座。把抓取区域的杂物移开，在机械臂可达的同一桌面上贴 12 个细十字标记。前 9 个分散覆盖整个抓取区（可按 3×3 排布），最后 3 个放在该区域内部的其他位置。
2. 打开上述页面，在相机画面中点击第一个十字的交点。若夹爪遮挡标记，先手动移开夹爪再选点。相机不能随臂移动。
3. 按机械臂上的示教按钮进入拖动示教，将夹爪朝下，**两指尖之间的中心**对准该十字。每次都对齐同一桌面高度，不要把单侧指尖当作中心。页面显示末端 XYZ 和垂直偏差；要求偏差小于 8°，停稳后点「记录」。
4. 继续手动移开夹爪，选择下一个标记，重复到 12 点。第 10～12 点仅用于独立验证，不参与拟合。按错时可撤销最后一个点。
5. 点「拟合单应矩阵」。采点后可按示教按钮退出示教。把“12 点已采完”的消息发给 Codex，后续可直接读取保存的数据、检查误差并更新本地配置。

「摆到起始位姿」「接管臂 + 回起始位姿」会使机械臂自动运动。固定顶部相机的采点过程可全程手动移开夹爪，无需逐点自动回位。页面上的起始位姿继承原部署配置，未在本次准备中执行。

## 判定与后续

- 记录时等到 CAN 位姿、关节、状态均有新反馈；停稳检查需满足位置变化 ≤2 mm，并检查机械臂错误码及姿态。
- 拟合使用 5 mm 的 RANSAC 阈值（目标坐标单位是米）；前 9 点拟合 RMS ≤3 mm、最大误差 ≤5 mm，最后 3 点各自误差 ≤5 mm。采点需覆盖至少 10 cm × 10 cm，所有点的末端高度差 ≤5 mm。
- 结果为桌面平面的像素 → 机械臂 XY 映射，只对采点覆盖的同一平面有效。这不是完整三维相机外参；物体高度变化和实际夹持偏差仍需实物验证。当前未通过验证的矩阵另存为 `current_homography.unverified.npy`，不会生成可用于「移过去」的已验证矩阵文件。
- 原始点、XYZ/关节/姿态记录与报告保存在 `tools/calibration/piper/artifacts/handeye/`。`quality.json` 将训练误差与独立验证误差分开记录。尚未采点时，该目录没有新矩阵。
- 对点时记录的 Z 是运行时 `link6` 参考点的高度，不能直接当作所有物体的合爪高度。需核对实际桌面和目标高度、抓取配置中的偏置，再进行实机验证。
- 验证通过后，备份权限为 600 的 `deploy/piper/manifest.local.yaml`，同步更新 `skill.pick.config` 与 `service.grasp_pose.config` 的内参和单应矩阵；核对桌面高度及偏置。保持原始项目 `/root/robot-agilex-piper-ga` 不变。

## 服务操作

```bash
cd ~/robonix_yolo
bash calibrate_robonix_yolo.sh        # 进入标定模式；已运行时显示入口
bash calibrate_robonix_yolo.sh --stop # 退出标定服务；不会自动恢复抓取
```

完成标定、更新配置并退出示教后，再恢复：

```bash
bash run_robonix_yolo.sh --background
bash preview_robonix_yolo.sh
docker exec -it robonix_yolo_dev bash /docker-entrypoint.sh rbnx chat
```

整套 Robonix 启动仍会执行原项目的机械臂回安全位流程。标定服务没有配置开机自启，日志在 `run-logs/calibration.log`。
