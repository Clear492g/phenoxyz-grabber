"""
Multispectral (CropEye S3) camera support with simple MJPEG preview per channel.
Configuration is stored at config/multispec_config.json.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import importlib.util
import sys
import re
import numpy as np

# follow CropEye-S3 indexing rules
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "multispec_config.json"
INDEX_DIR = BASE_DIR / "CropEye-S3" / "project_core"


def _load_index_helper():
    mod_path = INDEX_DIR / "ms602_index_fixer.py"
    spec = importlib.util.spec_from_file_location("ms602_index_fixer", mod_path)
    if not spec or not spec.loader:
        raise ImportError(f"cannot load ms602_index_fixer from {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ms602_index_fixer"] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


ms_index = _load_index_helper()
get_os_type_and_cams_path = ms_index.get_os_type_and_cams_path

# Strict mapping: only YeRui-MS602-* devices
BAND_MAPPING = {
    "YeRui-MS602-1": "480",
    "YeRui-MS602-2": "550",
    "YeRui-MS602-3": "660",
    "YeRui-MS602-4": "720",
    "YeRui-MS602-5": "840",
    "YeRui-MS602-6": "rgb",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "channels": {},  # auto filled, not user edited
    "jpeg_quality": 80,
}


def load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    cfg = {**DEFAULT_CONFIG, **data}
    cfg["jpeg_quality"] = max(10, min(100, int(cfg.get("jpeg_quality", DEFAULT_CONFIG["jpeg_quality"]))))
    return cfg


def save_config(partial: dict) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update({"jpeg_quality": partial.get("jpeg_quality", cfg.get("jpeg_quality", 80))} if partial else {})
    cfg["jpeg_quality"] = max(10, min(100, int(cfg.get("jpeg_quality", DEFAULT_CONFIG["jpeg_quality"]))))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


class SingleCam:
    def __init__(self, name: str, device: Any, width: int, height: int, jpeg_quality: int):
        self.name = name
        self.device = device
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality

        self.cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self.curr_frame = None
        self.curr_ts = None
        self._run = True
        self._fail = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _open(self) -> bool:
        cap = None
        opened = False
        if platform.system() == "Windows" and str(self.device).isdigit():
            idx = int(self.device)
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            opened = cap.isOpened()
            if not opened:
                cap.release()
                cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
                opened = cap.isOpened()
        else:
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2 if platform.system() != "Windows" else cv2.CAP_ANY)
            opened = cap.isOpened()
        if not opened or cap is None:
            if cap:
                cap.release()
            return False
        # Follow CropEye-S3 sample: continuous mode only
        cap.set(cv2.CAP_PROP_BACKLIGHT, 0)  # 0 auto trigger
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if platform.system() == "Windows":
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
        else:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3.0)
        cap.set(cv2.CAP_PROP_GAIN, 16)
        self.cap = cap
        return True

    def _loop(self):
        if not self._open():
            print(f"[multispec] {self.name} open failed")
        while self._run:
            if self.cap is None:
                time.sleep(0.2)
                if not self._open():
                    continue
            ret, frame = self.cap.read()
            if ret:
                self._fail = 0
                with self._lock:
                    # For non-rgb channels the sample converts to gray; keep BGR for consistency with MJPEG
                    self.curr_frame = frame
            else:
                self._fail += 1
                if self._fail > 30:
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    self.curr_frame = None
                    self._fail = 0
                time.sleep(0.05)

    def get_frame(self):
        with self._lock:
            if self.curr_frame is None:
                return None
            return self.curr_frame.copy()

    def stop(self):
        self._run = False
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass


class MultiSpecManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.cfg = load_config()
        self.cams: Dict[str, SingleCam] = {}
        self._init_cams()

    def _auto_index(self) -> Dict[str, Dict[str, Any]]:
        channels: Dict[str, Dict[str, Any]] = {}

        # First try helper (band -> index)
        try:
            _os_type, idx_map = get_os_type_and_cams_path()
            for band in ["480", "550", "660", "720", "840", "rgb"]:
                dev = idx_map.get(band)
                if dev is None:
                    continue
                if band == "rgb":
                    channels[band] = {"device": dev, "width": 1600, "height": 1200}
                else:
                    channels[band] = {"device": dev, "width": 1280, "height": 800}
        except Exception as exc:  # noqa: BLE001
            print(f"[multispec] index helper failed: {exc}")

        # Windows: strict name contains YeRui-MS602-n
        if platform.system() == "Windows":
            try:
                from PyCameraList.camera_device import list_video_devices  # type: ignore

                devices = list_video_devices()
                for device in devices:
                    if len(device) < 2:
                        continue
                    idx, name = device[0], device[1] or ""
                    m = re.search(r"yerui-ms602-(\d)", name, re.IGNORECASE)
                    if not m:
                        continue
                    dev_name = f"YeRui-MS602-{m.group(1)}"
                    band = BAND_MAPPING.get(dev_name)
                    if not band or band in channels:
                        continue
                    if band == "rgb":
                        channels[band] = {"device": idx, "width": 1600, "height": 1200}
                    else:
                        channels[band] = {"device": idx, "width": 1280, "height": 800}
            except Exception as exc:  # noqa: BLE001
                print(f"[multispec] windows name scan failed: {exc}")
        else:
            # Linux name scan
            try:
                result = subprocess.run(
                    ["v4l2-ctl", "--list-devices"], stdout=subprocess.PIPE, text=True, check=False
                )
                lines = result.stdout.splitlines()
                for i, line in enumerate(lines):
                    for dev_name, band in BAND_MAPPING.items():
                        if dev_name.lower() in line.lower() and band not in channels:
                            j = i + 1
                            while j < len(lines) and lines[j].startswith("\t"):
                                dev_path = lines[j].strip()
                                if dev_path.startswith("/dev/video"):
                                    if band == "rgb":
                                        channels[band] = {"device": dev_path, "width": 1600, "height": 1200}
                                    else:
                                        channels[band] = {"device": dev_path, "width": 1280, "height": 800}
                                    break
                                j += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[multispec] v4l2 scan failed: {exc}")

        if not channels:
            print("[multispec] auto index found no devices")
        return channels

        if not channels:
            print("[multispec] auto index found no devices")
        return channels

    def _init_cams(self):
        with self._lock:
            for cam in self.cams.values():
                cam.stop()
            self.cams = {}
            # always auto index following mapping (no manual channels)
            chs = self._auto_index()
            self.cfg["channels"] = chs
            save_config({"jpeg_quality": self.cfg.get("jpeg_quality", 80), "channels": chs})
            for name, meta in chs.items():
                dev = meta.get("device")
                width = int(meta.get("width", 1280))
                height = int(meta.get("height", 800))
                self.cams[name] = SingleCam(name, dev, width, height, self.cfg["jpeg_quality"])

    def list_channels(self):
        with self._lock:
            return list(self.cams.keys())

    def get_cam(self, name: str) -> Optional[SingleCam]:
        with self._lock:
            return self.cams.get(name)

    def update_config(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        # only jpeg_quality is configurable; channels always auto indexed
        self.cfg = save_config(
            {
                "jpeg_quality": cfg.get("jpeg_quality", self.cfg.get("jpeg_quality", 80)),
                "channels": self.cfg.get("channels", {}),
            }
        )
        self._init_cams()
        return self.cfg

    def refresh_index(self):
        # force auto index and rebuild
        auto_cfg = {"channels": self._auto_index(), "jpeg_quality": self.cfg.get("jpeg_quality", 80)}
        self.cfg = save_config(auto_cfg)
        self._init_cams()
        return self.cfg


_multispec_manager: Optional[MultiSpecManager] = None


def get_multispec_manager() -> MultiSpecManager:
    global _multispec_manager
    if _multispec_manager is None:
        _multispec_manager = MultiSpecManager()
    return _multispec_manager


def list_multispec_channels():
    return get_multispec_manager().list_channels()


def get_multispec_config():
    return get_multispec_manager().cfg


def update_multispec_config(cfg: Dict[str, Any]):
    return get_multispec_manager().update_config(cfg)


def multispec_stream(channel: str):
    cam = get_multispec_manager().get_cam(channel)
    if cam is None:
        return
    while True:
        frame = cam.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        ret, jpg = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, get_multispec_manager().cfg["jpeg_quality"]]
        )
        if not ret:
            continue
        b = jpg.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: "
            + str(len(b)).encode()
            + b"\r\n\r\n"
            + b
            + b"\r\n"
        )


def multispec_stream_all():
    manager = get_multispec_manager()
    order = ["480", "550", "660", "720", "840", "rgb"]
    target_size: Tuple[int, int] = (640, 480)
    font = cv2.FONT_HERSHEY_SIMPLEX

    def _blank(band: str):
        w, h = target_size
        tile = 255 * np.ones((h, w, 3), dtype=np.uint8)
        cv2.putText(tile, f"{band}: no signal", (20, h // 2), font, 1.0, (0, 0, 255), 2, cv2.LINE_AA)
        return tile

    while True:
        try:
            tiles = []
            for band in order:
                cam = manager.get_cam(band)
                frame = cam.get_frame() if cam else None
                if frame is None:
                    tile = _blank(band)
                else:
                    # ensure 3 channel
                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    tile = cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)
                    cv2.putText(tile, band, (15, 35), font, 1.0, (0, 255, 0), 2, cv2.LINE_AA)
                tiles.append(tile)

            # align row sizes
            row1 = cv2.hconcat([tiles[0], tiles[1], tiles[2]])
            row2 = cv2.hconcat([tiles[3], tiles[4], tiles[5]])
            mosaic = cv2.vconcat([row1, row2])
            ret, jpg = cv2.imencode(".jpg", mosaic, [cv2.IMWRITE_JPEG_QUALITY, manager.cfg["jpeg_quality"]])
            if not ret:
                time.sleep(0.05)
                continue
            b = jpg.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(b)).encode()
                + b"\r\n\r\n"
                + b
                + b"\r\n"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[multispec mosaic] error: {exc}")
            time.sleep(0.1)
