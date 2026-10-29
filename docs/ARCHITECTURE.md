# 架构与部署形态

本文记录**结构性决策**（为什么这样组织、为什么这样部署），供维护者和手册读者理解设计意图。逐条的原始讨论不在此复述，只留结论与理由。

## 1. 容器形态：robonix 烤进镜像，packages 挂载

**决策（2026-09-10）**：容器分两层来源——`vendor/robonix`（框架源码）在**镜像构建时 COPY 进去并编译**；`packages/`（能力包源码）与 `deploy/`（配置）通过 **bind mount** 挂进容器。

**理由**：按**变更频率**分配重建成本。

- `vendor/robonix` 在本项目历史上从未更新过，预期更新频率极低 → 适合烤进镜像（一次性成本）。改造前它是**只读挂载**，某些包因此需要本地适配（PEP 517 安装要写 egg-info，只读挂载下会失败）；烤进镜像后源码在容器内可写，这个问题就不存在了。
- `packages/` 变更频繁 → 挂载，改完 `rbnx boot` 即生效，不必每次走 `docker build` + 重建容器的循环。
- 包构建产物（`packages/*/rbnx-build/`）落在挂载目录里，因此**重建容器不需要重新编译**。
- 配置与密钥（`deploy/**`）、运行状态与日志（`rbnx-boot/`）留在宿主 → **密钥永远不进镜像层**，日志宿主可直接看。

**注意**：容器内看到的是 robonix 的**镜像内副本**。若有人想在宿主上改 `vendor/robonix` 做实验，容器不会实时反映——临时加一个 `-v` 覆盖即可（仅开发用）。

**踩过的坑（实现时务必守住）**：烤进镜像的路径**必须在运行时挂载点之外**。运行时会执行 `<repo> → /root/workspace` 的挂载，任何烤在 `/root/workspace/...` 下的内容都会被整体覆盖，而且现象容易误判：`PATH` 里有那个目录、`docker inspect` 显示环境变量也正确，但二进制就是"不存在"。因此 robonix 源码与编译产物烤在 **`/opt/robonix`**（源码 `/opt/robonix/src`、产物 `/opt/robonix/build/target`），与挂载点没有交集。

**被否决的替代方案**：

- *全部烤进镜像*：packages 变更频繁，改一次就要走一次镜像构建 + 重建容器；且 `docker build` 里的依赖下载默认不命中缓存（除非专门做预热层）。收益仅是"镜像自包含"，与本项目"仓库即交付物"的形态不符。
- *全部挂载*：省不了编译时间（编译在两种方案下都要做），却要长期承受只读挂载带来的适配分叉，且容器永远依赖宿主目录结构。

**落地待办**（实现时逐条勾掉）：

- [x] `containers/*/Dockerfile`：加 `COPY vendor/robonix` + 编译 rbnx；构建上下文改为仓库根并配 `.dockerignore`（2026-09-10 完成，烤到 `/opt/robonix`，见下方「踩过的坑」）
- [x] `containers/*/run.sh`：删掉 robonix 只读挂载那一行（2026-09-10 完成）
- [x] `packages/primitives/primitive-audio-client-bridge-rbnx/scripts/build.sh`：恢复上游原版（2026-09-10 完成，该包现与上游 17/17 逐字节一致）；`packages/UPSTREAMS.md` 已同步

## 2. S10 容器的例外：packages 也烤进镜像

**决策（2026-09-11）**：`containers/s10/` 把 `packages/` 与 `deploy/` 烤进镜像的末尾几层，容器不再挂载仓库。`containers/piper/` 仍是第 1 节的挂载形态。

**理由**：运行时会生成 `rbnx-build/`（每个包一个）与 `rbnx-boot/`，这两样都属于运行产物。烤进镜像后它们落在容器里，宿主机上的仓库只保留源码，不再混入运行产物。代价是改一次 packages 要重建镜像，但 packages 排在 Dockerfile 最后几层，前面装 ROS、编 robonix 的层都命中缓存。
