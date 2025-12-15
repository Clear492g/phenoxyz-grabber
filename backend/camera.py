"""
Camera control integration (UVC) with MJPEG streaming and basic controls.
Configuration is loaded from config/camera_config.json; if missing, falls back to uvc-rgb/focus_config.json.
"""

from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
UVCRGB_DIR = BASE_DIR / "uvc-rgb"

MAIN_CFG = CONFIG_DIR / "camera_config.json"
FALLBACK_CFG = UVCRGB_DIR / "focus_config.json"


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_cfg() -> Dict[str, Any]:
    default_cfg = {
        "autofocus": True,
        "focus_value": 50,
        "interval_sec": 1.0,
        "cam_name_keyword": "FicVideo",
        "cam_preview_width": 1920,
        "cam_preview_height": 1080,
        "cam_focus_max": 127,
        "save_dir": str(CONFIG_DIR / "camera_images"),
        "png_compression": 3,
        "save_format": "jpg",
        "jpeg_quality": 92,
        "ui_preview_width": 960,
        "ui_preview_height": 540,
        "save_workers": 2,
    }
    data = _load_json(MAIN_CFG) or _load_json(FALLBACK_CFG)
    cfg = {**default_cfg, **data}
    cfg["autofocus"] = bool(cfg.get("autofocus"))
    cfg["focus_value"] = int(cfg.get("focus_value"))
    cfg["interval_sec"] = float(cfg.get("interval_sec"))
    cfg["cam_name_keyword"] = str(cfg.get("cam_name_keyword"))
    cfg["cam_preview_width"] = int(cfg.get("cam_preview_width"))
    cfg["cam_preview_height"] = int(cfg.get("cam_preview_height"))
    cfg["cam_focus_max"] = int(cfg.get("cam_focus_max"))
    cfg["save_dir"] = str(cfg.get("save_dir"))
    cfg["png_compression"] = max(0, min(9, int(cfg.get("png_compression", 3))))
    fmt = str(cfg.get("save_format", "jpg")).lower()
    cfg["save_format"] = "jpg" if fmt not in ("jpg", "png") else fmt
    cfg["jpeg_quality"] = max(10, min(100, int(cfg.get("jpeg_quality", 92))))
    cfg["ui_preview_width"] = int(cfg.get("ui_preview_width", 960))
    cfg["ui_preview_height"] = int(cfg.get("ui_preview_height", 540))
    cfg["save_workers"] = max(1, int(cfg.get("save_workers", 2)))
    os.makedirs(cfg["save_dir"], exist_ok=True)
    return cfg


def save_cfg(partial: dict) -> Dict[str, Any]:
    cfg = load_cfg()
    cfg.update(partial or {})
    cfg["png_compression"] = max(0, min(9, int(cfg.get("png_compression", 3))))
    cfg["jpeg_quality"] = max(10, min(100, int(cfg.get("jpeg_quality", 92))))
    cfg["save_workers"] = max(1, int(cfg.get("save_workers", 2)))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(MAIN_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


MULTISPEC_KEYWORDS = ("yerei", "yerui", "ms602", "yerui-ms602", "yerui ms602", "yerui-ms602", "multispec")


def _is_multispec_name(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in MULTISPEC_KEYWORDS)


def find_device_by_name(keyword: str) -> Optional[str]:
    os_type = platform.system()
    keyword = keyword or ""
    if os_type == "Windows":
        try:
            from PyCameraList.camera_device import list_video_devices  # type: ignore

            devices = list_video_devices()
            for device in devices:
                if len(device) >= 2 and device[1] and keyword.lower() in device[1].lower():
                    return str(device[0])
        except Exception:
            pass
        if keyword:
            return None
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"], stdout=subprocess.PIPE, text=True, check=False
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if keyword.lower() in line.lower():
                j = i + 1
                while j < len(lines) and lines[j].startswith("\t"):
                    dev_path = lines[j].strip()
                    if dev_path.startswith("/dev/video"):
                        return dev_path
                    j += 1
    except Exception:
        pass
    # Strict: no fallback when keyword given
    return None


class SavePool(threading.Thread):
    def __init__(self, png_compression=3, jpeg_quality=92, notify_q=None, num_workers=2):
        super().__init__(daemon=True)
        self.png_compression = max(0, min(9, int(png_compression)))
        self.jpeg_quality = max(10, min(100, int(jpeg_quality)))
        self.notify_q = notify_q
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self._running = threading.Event()
        self._running.set()
        self.num_workers = max(1, int(num_workers))
        self._workers = []

    def submit(self, img, filename):
        try:
            if self.q.full():
                try:
                    self.q.get_nowait()
                    self.q.task_done()
                except queue.Empty:
                    pass
            self.q.put_nowait((img, filename))
        except queue.Full:
            pass

    def _encode_write(self, img, filename):
        parent = os.path.dirname(filename)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        ext = os.path.splitext(filename)[1].lower() or ".png"
        if ext in (".jpg", ".jpeg"):
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        elif ext == ".png":
            ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, self.png_compression])
        else:
            ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, self.png_compression])
            if ext not in (".png", ".jpg", ".jpeg"):
                filename = filename + ".png"
        if not ok:
            return False, filename
        with open(filename, "wb") as f:
            f.write(buf.tobytes())
        return True, filename

    def _worker(self, wid):
        while self._running.is_set():
            try:
                img, filename = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                ok, fname = self._encode_write(img, filename)
                if ok and self.notify_q is not None:
                    try:
                        self.notify_q.put_nowait(("saved", fname))
                    except queue.Full:
                        pass
            except Exception as exc:  # noqa: BLE001
                print(f"[save-{wid}] error: {exc}")
            finally:
                self.q.task_done()

    def run(self):
        for i in range(self.num_workers):
            t = threading.Thread(target=self._worker, args=(i,), daemon=True)
            t.start()
            self._workers.append(t)
        while self._running.is_set():
            time.sleep(0.2)

    def stop(self):
        self._running.clear()
        while not self.q.empty():
            try:
                self.q.get_nowait()
                self.q.task_done()
            except queue.Empty:
                break
        for t in self._workers:
            t.join(timeout=0.5)


class CameraManager:
    def __init__(self):
        self.cfg = load_cfg()
        self.device = find_device_by_name(self.cfg["cam_name_keyword"])
        if self.device is None:
            raise RuntimeError(f"未找到匹配的可见光相机: {self.cfg['cam_name_keyword']}")

        self.cap: Optional[cv2.VideoCapture] = None
        self._fail_count = 0
        opened = False
        if platform.system() == "Windows":
            if str(self.device).isdigit():
                idx = int(self.device)
                self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                opened = self.cap.isOpened()
                if not opened:
                    if self.cap:
                        self.cap.release()
                    self.cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
                    opened = self.cap.isOpened()
            else:
                self.cap = cv2.VideoCapture(self.device)
                opened = self.cap.isOpened()
            if not opened:
                if self.cap:
                    self.cap.release()
                for idx in range(0, 6):
                    cap_try = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap_try.isOpened():
                        self.cap = cap_try
                        opened = True
                        break
                    cap_try.release()
        else:
            self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            opened = self.cap.isOpened()
            if not opened:
                if self.cap:
                    self.cap.release()
                self.cap = cv2.VideoCapture(self.device)
                opened = self.cap.isOpened()

        if not opened or self.cap is None:
            raise RuntimeError("无法打开摄像头")

        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["cam_preview_width"])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["cam_preview_height"])

        self.set_autofocus(self.cfg["autofocus"])
        if not self.cfg["autofocus"]:
            self.set_focus(self.cfg["focus_value"])

        self._lock = threading.Lock()
        self.curr_frame = None
        self.curr_ts = None
        self._run = True
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

        self.notify_q: queue.Queue = queue.Queue(maxsize=100)
        self.pool = SavePool(
            self.cfg["png_compression"],
            self.cfg["jpeg_quality"],
            notify_q=self.notify_q,
            num_workers=self.cfg["save_workers"],
        )
        self.pool.start()

        self._timed_stop = threading.Event()
        self._timed_thread = None
        self.current_session_dir = None

    def _grab_loop(self):
        while self._run:
            ret, frame = self.cap.read()
            if ret:
                self._fail_count = 0
                with self._lock:
                    self.curr_frame = frame
                    self.curr_ts = datetime.now()
            else:
                self._fail_count += 1
                if self._fail_count >= 10:
                    self._reconnect()
                time.sleep(0.03)

    def _reconnect(self):
        try:
            with self._lock:
                if self.cap:
                    self.cap.release()
                self.cap = None
                self.curr_frame = None
                self.curr_ts = None
            self.device = find_device_by_name(self.cfg["cam_name_keyword"])
            cap = None
            opened = False
            if platform.system() == "Windows":
                if str(self.device).isdigit():
                    idx = int(self.device)
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    opened = cap.isOpened()
                    if not opened:
                        if cap:
                            cap.release()
                        cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
                        opened = cap.isOpened()
                else:
                    cap = cv2.VideoCapture(self.device)
                    opened = cap.isOpened()
                if not opened:
                    if cap:
                        cap.release()
                    for idx in range(0, 6):
                        cap_try = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                        if cap_try.isOpened():
                            cap = cap_try
                            opened = True
                            break
                        cap_try.release()
            else:
                cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                opened = cap.isOpened()
                if not opened:
                    if cap:
                        cap.release()
                    cap = cv2.VideoCapture(self.device)
                    opened = cap.isOpened()
            if not opened or cap is None:
                return False
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["cam_preview_width"])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["cam_preview_height"])
            self.cap = cap
            self.set_autofocus(self.cfg["autofocus"])
            if not self.cfg["autofocus"]:
                self.set_focus(self.cfg["focus_value"])
            self._fail_count = 0
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[camera-reconnect] {exc}")
            return False

    def get_frame(self) -> Tuple[Optional[Any], Optional[datetime]]:
        with self._lock:
            if self.curr_frame is None:
                return None, None
            return self.curr_frame.copy(), self.curr_ts

    def set_autofocus(self, enabled: bool) -> bool:
        try:
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if enabled else 0)
            self.cfg["autofocus"] = bool(enabled)
            save_cfg({"autofocus": self.cfg["autofocus"]})
            return True
        except Exception:
            return False

    def set_focus(self, value: int) -> bool:
        try:
            v = max(0, min(self.cfg["cam_focus_max"], int(value)))
            self.cap.set(cv2.CAP_PROP_FOCUS, float(v))
            self.cap.grab()
            self.cfg["focus_value"] = v
            save_cfg({"focus_value": v})
            return True
        except Exception:
            return False

    def save_current(self, target_dir=None) -> Optional[str]:
        frame, ts = self.get_frame()
        if frame is None:
            return None
        ts = ts or datetime.now()
        ts_str = ts.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        ext = ".jpg" if self.cfg["save_format"].lower() == "jpg" else ".png"
        save_dir = target_dir or self.cfg["save_dir"]
        os.makedirs(save_dir, exist_ok=True)
        filename = os.path.join(save_dir, f"{ts_str}{ext}")
        self.pool.submit(frame, filename)
        return filename

    def start_timed(self):
        if self._timed_thread and self._timed_thread.is_alive():
            return False
        self._timed_stop.clear()
        tsdir = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_session_dir = os.path.join(self.cfg["save_dir"], tsdir)
        os.makedirs(self.current_session_dir, exist_ok=True)
        interval = max(0.05, float(self.cfg.get("interval_sec", 1.0)))

        def loop():
            while not self._timed_stop.is_set():
                self.save_current(target_dir=self.current_session_dir)
                self._timed_stop.wait(interval)

        self._timed_thread = threading.Thread(target=loop, daemon=True)
        self._timed_thread.start()
        return True

    def stop_timed(self):
        if self._timed_thread and self._timed_thread.is_alive():
            self._timed_stop.set()
            self._timed_thread.join(timeout=2.0)
        self._timed_thread = None
        self.current_session_dir = None
        return True

    def resize_for_ui(self, frame):
        ui_w = int(self.cfg["ui_preview_width"])
        ui_h = int(self.cfg["ui_preview_height"])
        h, w = frame.shape[:2]
        scale = min(ui_w / float(w), ui_h / float(h), 1.0)
        tw, th = int(w * scale), int(h * scale)
        return cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)

    def stop(self):
        self._run = False
        try:
            self.stop_timed()
        except Exception:
            pass
        self.pool.stop()
        if self.cap:
            self.cap.release()


camera_singleton: Optional[CameraManager] = None
camera_lock = threading.Lock()


def get_camera() -> CameraManager:
    global camera_singleton
    with camera_lock:
        if camera_singleton is None:
            camera_singleton = CameraManager()
        return camera_singleton


def update_camera_config(new_cfg: Dict[str, Any]) -> Dict[str, Any]:
    cam = get_camera()
    cfg = save_cfg(new_cfg)
    cam.cfg = cfg
    # apply key params
    cam.pool.png_compression = cfg["png_compression"]
    cam.pool.jpeg_quality = cfg["jpeg_quality"]
    if "save_workers" in cfg and cfg["save_workers"] != cam.pool.num_workers:
        cam.pool.stop()
        cam.pool = SavePool(
            cfg["png_compression"],
            cfg["jpeg_quality"],
            notify_q=cam.notify_q,
            num_workers=cfg["save_workers"],
        )
        cam.pool.start()
    # interval/autofocus/focus can be applied on next operations
    return cfg


def camera_stream():
    cam = get_camera()
    while True:
        frame, _ts = cam.get_frame()
        if frame is None:
            time.sleep(0.03)
            continue
        preview = cam.resize_for_ui(frame)
        ret, jpg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
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
