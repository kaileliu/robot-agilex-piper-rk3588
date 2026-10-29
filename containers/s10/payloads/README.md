# containers/s10/payloads

## pyrealsense2/ —— Intel RealSense 的 Python SDK（aarch64, cp310）

- **谁用**：`packages/primitives/camera-grasp-d435i`（D435i 手动曝光相机原语）在容器内 `import pyrealsense2` 取流。Dockerfile 把它拷进 `/usr/local/lib/python3.10/dist-packages/pyrealsense2/`。
- **版本**：2.58.4。
- **静态链接**：`ldd` 只依赖 `libusb / udev / libstdc++`，所以直接拷进 `dist-packages` 就能用，镜像里不用再装 librealsense2 系统包。

### 什么情况下会失效

| 触发条件 | 现象 |
|---|---|
| 基础镜像换 Python（3.10 → 3.12，例如升到 Ubuntu 24.04 / ROS Jazzy） | `import pyrealsense2` 失败（`.so` 是 `cpython-310` 编译的） |
| 换到 x86 主机 | 二进制无效（只有 `aarch64`） |
| 想要更新的 RealSense 特性 / 新固件兼容 | 需要用更新的 SDK |
| 文件丢失 | 需要重新获取（见下） |

### 需要重新获取时

1. **从另一台有它的机器拷**：在装有该 SDK 的 Jetson 上
   `python3 -c "import pyrealsense2, os; print(os.path.dirname(pyrealsense2.__file__))"`，把整个目录拷进 `containers/s10/payloads/pyrealsense2/`；或用`docker cp <容器>:/usr/local/lib/python3.10/dist-packages/pyrealsense2 .`。
2. **装一个 aarch64 的 wheel 包**：NVIDIA Jetson 的 wheel 源或 Intel 的预编译包里找 `pyrealsense2`，装到某台机器的 py3.10 环境后，把 site-packages 里的 `pyrealsense2/` 目录取出（注意：要确认它是静态链接版，否则镜像里还得补 librealsense2 系统包）。
3. **源码编译**：`librealsense`（Intel 官方）+ `pyrealsense2` 的 Python 绑定，交叉/本机编译出 aarch64 的 `.so`。最慢但最可控。
