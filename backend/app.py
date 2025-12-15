import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, Response, jsonify, request, send_from_directory

from .modbus_io import COIL_ADDR, get_state, handle_coil, write_coords, write_speeds
from .camera import (
    camera_stream,
    get_camera,
    load_cfg as load_cam_cfg,
    save_cfg as save_cam_cfg,
    update_camera_config,
)
from .multispec import (
    get_multispec_config,
    get_multispec_manager,
    list_multispec_channels,
    multispec_stream,
    multispec_stream_all,
    update_multispec_config,
)

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
CONFIG_DIR = BASE_DIR / "config"
BOUNDS_FILE = CONFIG_DIR / "bounds.json"
AUTORUN_FILE = CONFIG_DIR / "autorun.json"

DEFAULT_BOUNDS: Dict[str, Any] = {
    "x_min": 0,
    "x_max": 2000,
    "y_min": 0,
    "y_max": 2000,
    "cols": 10,
    "rows": 10,
}

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIR),
    static_url_path="",
)


def empty_state() -> dict:
    return {
        "current": {
            "speed": {"x": 0.0, "y": 0.0, "z": 0.0},
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "set_speed": {"x": 0.0, "y": 0.0, "z": 0.0},
        "set_coord": {"x": 0.0, "y": 0.0, "z": 0.0},
        "coils": {k: False for k in COIL_ADDR},
    }


@app.route("/")
def index() -> Response:
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/state")
def api_state():
    try:
        return jsonify(get_state())
    except Exception as exc:  # noqa: BLE001
        print("[api_state error]", exc)
        return jsonify(empty_state())


def _load_bounds() -> Dict[str, Any]:
    try:
        with open(BOUNDS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 合并默认值，缺省字段用默认
        merged = {**DEFAULT_BOUNDS, **(cfg or {})}
        return merged
    except FileNotFoundError:
        print(f"[bounds] config not found, using default: {BOUNDS_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"[bounds] load error: {exc}, using default")
    return DEFAULT_BOUNDS


@app.route("/api/bounds")
def api_bounds():
    return jsonify(_load_bounds())


def _load_autorun_config() -> Dict[str, Any]:
    try:
        with open(AUTORUN_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        routes = cfg.get("routes") or []
        return {"routes": routes}
    except FileNotFoundError:
        print(f"[autorun] config not found, create a file at: {AUTORUN_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"[autorun] load error: {exc}")
    return {"routes": []}


class AutoRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._home_after_stop = False
        self._state: Dict[str, Any] = {
            "running": False,
            "route": None,
            "index": None,
            "total": None,
            "paused": False,
            "error": None,
        }

    def state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self, route: Dict[str, Any]) -> None:
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("autorun already running")
            self._stop.clear()
            self._pause.clear()
            self._home_after_stop = False
            self._state = {
                "running": True,
                "route": route.get("name") or "custom",
                "index": 0,
                "total": len(route.get("points") or []),
                "paused": False,
                "error": None,
            }
        self._thread = threading.Thread(target=self._run_route, args=(route,), daemon=True)
        self._thread.start()

    def stop(self, home: bool = True) -> None:
        self._home_after_stop = home
        self._stop.set()

    def pause(self, value: bool) -> None:
        if not self._state["running"]:
            return
        if value:
            self._pause.set()
        else:
            self._pause.clear()
        with self._lock:
            self._state["paused"] = bool(value)

    def _sleep_with_check(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.is_set():
                return
            while self._pause.is_set() and not self._stop.is_set():
                time.sleep(0.1)
            time.sleep(0.05)

    def _run_route(self, route: Dict[str, Any]):
        try:
            pts = route.get("points") or []
            speed = route.get("speed") or {}
            dwell = float(route.get("dwell", 1.0))

            # Set speed once if provided
            if speed:
                write_speeds(speed)

            for idx, pt in enumerate(pts):
                if self._stop.is_set():
                    break
                # write coords
                write_coords(pt)
                # trigger move (pulse XY/Z go target)
                try:
                    handle_coil("xy_go_target", pulse=True)
                except Exception:
                    pass
                try:
                    handle_coil("z_go_target", pulse=True)
                except Exception:
                    pass

                with self._lock:
                    self._state["index"] = idx + 1

                self._sleep_with_check(dwell)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state["running"] = False
                self._state["paused"] = False
            self._stop.clear()
            self._pause.clear()
            if self._home_after_stop:
                try:
                    handle_coil("xy_home", pulse=True)
                except Exception:
                    pass
                try:
                    handle_coil("z_home", pulse=True)
                except Exception:
                    pass
            self._home_after_stop = False


auto_runner = AutoRunner()


@app.route("/api/autorun/config")
def api_autorun_config():
    return jsonify(_load_autorun_config())


@app.route("/api/autorun/start", methods=["POST"])
def api_autorun_start():
    try:
        data = request.get_json(force=True) or {}
        route = data.get("route")
        # 支持 route 为名称或完整对象
        if isinstance(route, str):
            cfg = _load_autorun_config()
            routes = cfg.get("routes") or []
            target = next((r for r in routes if r.get("name") == route), None)
            if not target:
                return jsonify({"error": "未知路径"}), 400
            route = target
        elif isinstance(route, dict):
            pass
        else:
            route = {}

        points = route.get("points") or data.get("points")
        if not points:
            return jsonify({"error": "需要提供至少一个点位"}), 400
        if "points" not in route:
            route["points"] = points
        auto_runner.start(route)
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/autorun/stop", methods=["POST"])
def api_autorun_stop():
    auto_runner.stop(home=True)
    return jsonify({"ok": True})


@app.route("/api/autorun/pause", methods=["POST"])
def api_autorun_pause():
    data = request.get_json(force=True) or {}
    pause_val = bool(data.get("pause", True))
    auto_runner.pause(pause_val)
    return jsonify({"ok": True})


@app.route("/api/autorun/state")
def api_autorun_state():
    return jsonify(auto_runner.state())


# ================= Camera =================
@app.route("/camera/stream")
def camera_stream_route():
    return Response(camera_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/camera/config")
def api_camera_config():
    try:
        cam = get_camera()
        return jsonify(cam.cfg)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/autofocus", methods=["POST"])
def api_camera_autofocus():
    try:
        cam = get_camera()
        data = request.get_json(force=True) or {}
        enabled = bool(data.get("enabled", True))
        ok = cam.set_autofocus(enabled)
        if ok and not enabled:
            cam.set_focus(cam.cfg["focus_value"])
        return jsonify({"ok": ok})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/focus", methods=["POST"])
def api_camera_focus():
    try:
        cam = get_camera()
        data = request.get_json(force=True) or {}
        v = int(data.get("value", 50))
        ok = cam.set_focus(v)
        return jsonify({"ok": ok, "value": cam.cfg["focus_value"]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/save", methods=["POST"])
def api_camera_save():
    try:
        cam = get_camera()
        fn = cam.save_current()
        return jsonify({"ok": bool(fn), "filename": fn})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/timed/start", methods=["POST"])
def api_camera_timed_start():
    try:
        cam = get_camera()
        ok = cam.start_timed()
        return jsonify({"ok": ok})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/timed/stop", methods=["POST"])
def api_camera_timed_stop():
    try:
        cam = get_camera()
        ok = cam.stop_timed()
        return jsonify({"ok": ok})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/save_dir", methods=["POST"])
def api_camera_save_dir():
    try:
        cam = get_camera()
        data = request.get_json(force=True) or {}
        path = str(data.get("path", cam.cfg["save_dir"]))
        os.makedirs(path, exist_ok=True)
        new_cfg = save_cam_cfg({"save_dir": path})
        cam.cfg = new_cfg
        return jsonify({"ok": True, "save_dir": path})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/save_params", methods=["POST"])
def api_camera_save_params():
    try:
        cam = get_camera()
        body = request.get_json(force=True) or {}
        to_update = {}
        if "save_format" in body:
            to_update["save_format"] = "jpg" if str(body["save_format"]).lower() not in ("jpg", "png") else str(body["save_format"]).lower()
        if "jpeg_quality" in body:
            to_update["jpeg_quality"] = max(10, min(100, int(body["jpeg_quality"])))
        if "png_compression" in body:
            to_update["png_compression"] = max(0, min(9, int(body["png_compression"])))
        if "interval_sec" in body:
            to_update["interval_sec"] = float(body["interval_sec"])
        if "save_workers" in body:
            to_update["save_workers"] = max(1, int(body["save_workers"]))

        new_cfg = save_cam_cfg(to_update)
        cam.cfg = new_cfg
        cam.pool.png_compression = new_cfg["png_compression"]
        cam.pool.jpeg_quality = new_cfg["jpeg_quality"]
        return jsonify({"ok": True, "cfg": new_cfg})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/camera/config/update", methods=["POST"])
def api_camera_config_update():
    data = request.get_json(force=True) or {}
    cfg = update_camera_config(data)
    return jsonify({"ok": True, "cfg": cfg})


# ================= Multispectral Camera =================
@app.route("/multispec/stream")
def multispec_stream_route():
    ch = request.args.get("ch") or "rgb"
    return Response(multispec_stream(ch), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/multispec/stream_all")
def multispec_stream_all_route():
    return Response(multispec_stream_all(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/multispec/config")
def api_multispec_config():
    return jsonify(get_multispec_config())


@app.route("/api/multispec/config/update", methods=["POST"])
def api_multispec_config_update():
    data = request.get_json(force=True) or {}
    cfg = update_multispec_config(data)
    return jsonify({"ok": True, "cfg": cfg})


@app.route("/api/multispec/channels")
def api_multispec_channels():
    return jsonify({"channels": list_multispec_channels()})


@app.route("/api/multispec/refresh", methods=["POST"])
def api_multispec_refresh():
    cfg = get_multispec_manager().refresh_index()
    return jsonify({"ok": True, "cfg": cfg})


@app.route("/api/speeds", methods=["POST"])
def api_speeds():
    try:
        data = request.get_json(force=True) or {}
        ok = write_speeds(data)
        if not ok:
            return jsonify({"error": "需要提供至少一个 x/y/z 速度"}), 400
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/coords", methods=["POST"])
def api_coords():
    try:
        data = request.get_json(force=True) or {}
        ok = write_coords(data)
        if not ok:
            return jsonify({"error": "需要提供至少一个 x/y/z 坐标"}), 400
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/coil", methods=["POST"])
def api_coil():
    try:
        data = request.get_json(force=True) or {}
        action = data.get("action")
        if action not in COIL_ADDR:
            return jsonify({"error": "未知线圈动作"}), 400
        pulse = bool(data.get("pulse", True))
        handle_coil(action, pulse=pulse, value=data.get("value", True))
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    print("Web 页面: http://0.0.0.0:5000  |  Modbus: 127.0.0.1:502")
    app.run(host="0.0.0.0", port=5000, debug=False)
