from typing import Optional
from enum import Enum
import numpy as np


class TriggerMode(Enum):
    CONTINUOUS = 0
    SOFT_TRIGGER = 1
    EXTERNAL_TRIGGER = 2


class CamCurrentFrame:
    def __init__(self):
        self.cv_img: Optional[np.ndarray] = None
        self.exposure_time: Optional[int] = None  # 默认最大曝光时间 1-33000
        self.iso: Optional[int] = None  # 默认 ISO  # 100..1600
        self.fps: float = 0.0
        self.cam_name: Optional[str] = None  # ‘cam_480’ ‘cam_rgb’

    def update(
            self,
            cv_img: Optional[np.ndarray] = None,
            exposure_time: Optional[int] = None,
            iso: Optional[int] = None,
            fps: Optional[float] = None,
            cam_name: Optional[str] = None
    ):
        """更新帧数据"""
        if cv_img is not None:
            self.cv_img = cv_img
        if exposure_time is not None:
            self.exposure_time = exposure_time
        if iso is not None:
            self.iso = iso
        if fps is not None:
            self.fps = fps
        if cam_name is not None:
            self.cam_name = cam_name

    def reset(self):
        """重置帧数据"""
        self.cv_img = None
        self.exposure_time = None
        self.iso = None
        self.fps = 0.0
        self.cam_name = None

    def is_valid(self) -> bool:
        """检查帧数据是否有效"""
        return self.cv_img is not None and self.exposure_time is not None and self.iso is not None and self.fps is not None and self.cam_name is not None

    def __str__(self):
        return f"Camera: {self.cam_name}, Exposure: {self.exposure_time}, ISO: {self.iso}, FPS: {self.fps}"


class AllCamCurrentFrames:
    def __init__(self):
        self.frames = {name: CamCurrentFrame() for name in
                       ["cam_480", "cam_550", "cam_660", "cam_720", "cam_840", "cam_rgb"]}

    def reset_frame(self, cam_name: str):
        """重置特定相机帧数据"""
        if cam_name in self.frames:
            self.frames[cam_name].reset()
        else:
            raise ValueError(f"Camera '{cam_name}' not found in frames.")

    def reset_all(self):
        """重置所有相机帧数据"""
        for frame in self.frames.values():
            frame.reset()

    def get_frame(self, cam_name: str) -> CamCurrentFrame:
        """获取特定相机帧数据"""
        if cam_name in self.frames:
            return self.frames[cam_name]
        else:
            raise ValueError(f"Camera '{cam_name}' not found in frames.")

    def __getitem__(self, cam_name: str) -> CamCurrentFrame:
        return self.get_frame(cam_name)

    def __setitem__(self, cam_name: str, frame: CamCurrentFrame):
        if cam_name in self.frames:
            self.frames[cam_name] = frame
        else:
            raise ValueError(f"Camera '{cam_name}' not found in frames.")

    def __str__(self):
        return "\n".join(f"{name}: {frame}" for name, frame in self.frames.items())


OpenCV_exposure = {
    -1: 640.0,
    -2: 320.0,
    -3: 160.0,
    -4: 80.0,
    -5: 40.0,
    -6: 20.0,
    -7: 10.0,
    -8: 5.0,
    -9: 2.5,
    -10: 1.25,
    -11: 0.625,
    -12: 0.3125,
    -13: 0.15625,
    -14: 0.078125
}
