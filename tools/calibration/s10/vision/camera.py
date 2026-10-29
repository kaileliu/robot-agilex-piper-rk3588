# Description: S10 上的取图——pyrealsense2 直连 D435i + 手动自动曝光。
#
# 为什么只有这一条路：相机原语（primitive-intel-d435i-manual-ae-camera-rbnx）就是
# 这么取的——pyrealsense2 直连（不走 UVC；UVC 彩色流在这台机上不可靠：节点多路复用，
# 打开后常常是 IR/暗帧）+ 手动 AE（官方驱动的自动曝光失效，改成 EMA 平滑中位数 +
# 高光占比惩罚）。标定必须与原语看到同一份画面，所以这里用同一套驱动、同一档位
# （1280x720x30 bgr8），标定出来的内参/单应才能直接给运行时用。
#
# 去畸变用 common/camera.py 的 UndistortCamera 包一层（K/D 取 artifacts/intrinsics
# 的标定产物）：标定输入必须与运行时输入一致（运行时也是先按清单内参去畸变再检测）。
import numpy as np


class S10CameraError(RuntimeError):
    pass


class S10Camera:
    """pyrealsense2 后端 + EMA 自动曝光（UVC 那条路已废弃，见文件头）。

    接口：get_frames() 返回 {"color": bgr_frame}。
    自动曝光：关闭硬件 AE（失效），用 EMA 平滑中位数亮度 + 高光占比惩罚调 exposure。
    已验证（rs_autoexp3 收敛：phigh 10%→0.02%, med 197→136）。
    """

    def __init__(self, width=1280, height=720, fps=30, exposure_start=1000.0,
                 med_target=150.0, ph_stop1=0.02, ph_stop2=0.01,
                 ema_alpha=0.02, adjust_every=5):
        import pyrealsense2 as rs
        self.last_error: str | None = None
        self._rs = rs
        self.width = width
        self.height = height
        self.fps = fps
        self.med_target = med_target
        self.ph_stop1 = ph_stop1
        self.ph_stop2 = ph_stop2
        self.ema_alpha = ema_alpha
        self.adjust_every = adjust_every
        self._exposure = exposure_start

        self._p = rs.pipeline()
        conf = rs.config()
        conf.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        self._prof = self._p.start(conf)
        self._sensor = None
        for s in self._prof.get_device().query_sensors():
            if "RGB" in s.name:
                self._sensor = s
        if self._sensor is None:
            self._p.stop()
            raise S10CameraError("未找到 RGB sensor")
        try:
            self._sensor.set_option(rs.option.enable_auto_exposure, 0)
            self._sensor.set_option(rs.option.exposure, int(self._exposure))
        except Exception as e:
            print("[S10Camera] set exposure failed: %s" % e, flush=True)

        # EMA 状态
        self._med_e = None
        self._ph_e = None
        self._frame_i = 0
        self._last_adj = -10

    def get_frames(self):
        """取一帧彩色（BGR）；内部做 EMA 自动曝光。返回 {"color": bgr|None}"""
        try:
            frames = self._p.wait_for_frames()
        except Exception as e:  # noqa: BLE001
            # 出错原因记下来（页面会显示），别只是默默返回 None
            self.last_error = f"取帧失败：{type(e).__name__}: {e}"
            return {"color": None}
        cf = frames.get_color_frame()
        if not cf:
            self.last_error = "这一帧没有彩色图"
            return {"color": None}
        self.last_error = None
        frame = np.asanyarray(cf.get_data())
        self._frame_i += 1

        # EMA 自动曝光（高光优先）
        gray = frame.mean(axis=2)
        med = float(np.median(gray))
        ph = float((gray > 250).mean())
        if self._med_e is None:
            self._med_e, self._ph_e = med, ph
        else:
            a = self.ema_alpha
            self._med_e = a * med + (1 - a) * self._med_e
            self._ph_e = a * ph + (1 - a) * self._ph_e
        if self._frame_i - self._last_adj >= self.adjust_every:
            ne = self._exposure
            if self._ph_e > self.ph_stop1:
                ne = self._exposure * 0.90
            elif self._ph_e > self.ph_stop2:
                ne = self._exposure * 0.95
            else:
                if abs(self._med_e - self.med_target) > 15:
                    err = (self._med_e - self.med_target) / self.med_target
                    ne = self._exposure * (1.0 - err * 0.08)
            ne = max(100, min(8000, ne))
            if abs(ne - self._exposure) > 5:
                self._exposure = ne
                self._last_adj = self._frame_i
                try:
                    self._sensor.set_option(self._rs.option.exposure,
                                            int(self._exposure))
                except Exception as e:
                    print("[S10Camera] set exposure failed: %s" % e, flush=True)
        return {"color": frame}

    def close(self):
        try:
            self._p.stop()
        except Exception:
            pass


