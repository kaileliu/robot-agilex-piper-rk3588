# Description: piper 这台机器的取图——Orbbec Gemini 336L，pyorbbecsdk 直连（宿主）。
#
# 与相机原语用的是同一个 SDK、同一档彩色流（1280x720），只是不经 ROS：标定没必要
# 为了拿一帧画面去起一整套栈。**像素口径必须与运行时一致**（分辨率、彩色流），
# 所以档位写死在这里，不跟着默认档漂。
import cv2
import numpy as np
from pyorbbecsdk import Config, OBFormat, OBSensorType, Pipeline

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 10
# 优先 MJPG（这台机的彩色流在 SDK 下跑 MJPG 最稳），没有就依次退回
PREFERRED_FORMATS = (OBFormat.MJPG, OBFormat.YUYV, OBFormat.RGB)


class OrbCameraError(RuntimeError):
    pass


def _to_bgr(frame) -> np.ndarray | None:
    """把 Orbbec 的一帧彩色转成 BGR。

    像素格式分支取自 Orbbec 官方示例的 utils.frame_to_bgr_image（Apache-2.0），
    只留彩色流会用到的几种。
    """
    width, height = frame.get_width(), frame.get_height()
    fmt = frame.get_format()
    data = np.frombuffer(frame.get_data(), dtype=np.uint8)
    if fmt == OBFormat.RGB:
        return cv2.cvtColor(data.reshape((height, width, 3)), cv2.COLOR_RGB2BGR)
    if fmt == OBFormat.BGR:
        return data.reshape((height, width, 3)).copy()
    if fmt == OBFormat.YUYV:
        yuyv = data.reshape((height, width, 2))
        return cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUYV)
    if fmt == OBFormat.UYVY:
        uyvy = data.reshape((height, width, 2))
        return cv2.cvtColor(uyvy, cv2.COLOR_YUV2BGR_UYVY)
    if fmt == OBFormat.MJPG:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    print(f"[OrbCamera] 不支持的彩色像素格式: {fmt}", flush=True)
    return None


class OrbCamera:
    """Gemini 336L 彩色流：get_frames() 返回 {"color": bgr|None}（接口与 S10 的相机一致）。"""

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
                 fps: int = DEFAULT_FPS):
        self.last_error: str | None = None
        self._pipeline = Pipeline()
        config = Config()
        profile_list = self._pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)

        profile = None
        for fmt in PREFERRED_FORMATS:
            try:
                profile = profile_list.get_video_stream_profile(width, height, fmt, fps)
                break
            except Exception:
                continue
        if profile is None:
            raise OrbCameraError(f"没有 {width}x{height}@{fps} 的彩色流，不能换分辨率标定")
        config.enable_stream(profile)
        self._pipeline.start(config)
        print("[OrbCamera] 彩色流档位: %dx%d %s @%dfps"
              % (profile.get_width(), profile.get_height(),
                 profile.get_format(), profile.get_fps()), flush=True)
        self._profile = profile

    def get_frames(self) -> dict:
        # 出错时把原因记在 last_error 里（页面会显示），别只是默默返回 None
        try:
            frames = self._pipeline.wait_for_frames(1000)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"取帧失败：{type(e).__name__}: {e}"
            return {"color": None}
        if frames is None:
            self.last_error = "取帧超时：1 秒内没有新帧（相机被别的进程占着？插拔一下 USB？）"
            return {"color": None}
        color_frame = frames.get_color_frame()
        if color_frame is None:
            self.last_error = "这一帧没有彩色图"
            return {"color": None}
        self.last_error = None
        return {"color": _to_bgr(color_frame)}

    def close(self) -> None:
        try:
            self._pipeline.stop()
        except Exception:
            pass
