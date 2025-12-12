# -*- coding: utf-8 -*-
"""
简易 Modbus-TCP 上位机页面

需求对应：
1. 读数区（D 寄存器，32 位浮点）：X/Y/Z 当前速度；X/Y/Z 当前坐标。
2. 速度设置（D 寄存器，可读写）：X/Y/Z 设置速度。
3. 坐标与指令（D 寄存器 + M 线圈）：X/Y/Z 指定坐标可读写；按钮脉冲对应线圈：
   - 下达指令到 X/Y 指定位置：M 0x0033
   - 下达指令到 Z 指定位置：M 0x004C
   - X/Y 回原点：M 0x0047
   - Z 回原点：M 0x004D
   - X/Y 停止：M 0x004B
   - Z 停止：M 0x0053
   - 下达指令暂停：M 0x004E

各组成独立，无附加逻辑，仅按按钮/输入时读写对应地址。
"""

import struct
import threading
import time
from typing import Dict, List

from flask import Flask, Response, jsonify, request
import modbus_tk.defines as md
import modbus_tk.modbus_tcp as mt

# PLC 连接
PLC_HOST = "192.168.1.88"
PLC_PORT = 502
SLAVE_ID = 1

# D 寄存器（32 位浮点，低字在前）
REG_ADDR: Dict[str, int] = {
    "x_speed_cur": 0x0042,
    "y_speed_cur": 0x0044,
    "z_speed_cur": 0x0046,
    "x_pos_cur": 0x0052,
    "y_pos_cur": 0x0054,
    "z_pos_cur": 0x0056,
    "x_speed_set": 0x0048,
    "y_speed_set": 0x004E,
    "z_speed_set": 0x0050,
    "x_coord_set": 0x0058,
    "y_coord_set": 0x005A,
    "z_coord_set": 0x005C,
}

# M 线圈
COIL_ADDR: Dict[str, int] = {
    "xy_go_target": 0x0033,
    "z_go_target": 0x004C,
    "xy_home": 0x0047,
    "z_home": 0x004D,
    "xy_stop": 0x004B,
    "z_stop": 0x0053,
    "cmd_pause": 0x004E,
    "machine_on": 0x0036,
    "machine_off": 0x0037,
    "light_on": 0x0038,
    "light_off": 0x0039,
    "pump_on": 0x003A,
    "pump_off": 0x003B,
    "dc12_on": 0x003C,
    "dc12_off": 0x003D,
    "dc24_on": 0x003E,
    "dc24_off": 0x003F,
    "ac220_on": 0x0040,
    "ac220_off": 0x0041,
}

plc_master = mt.TcpMaster(PLC_HOST, PLC_PORT)
plc_master.set_timeout(2.0)
_lock = threading.Lock()


def encode_ieee(value: float) -> List[int]:
    """float -> 低字在前的两个 16 位寄存器。"""
    bs = struct.pack(">f", float(value))
    return [
        int.from_bytes(bs[2:4], "big"),  # 低字
        int.from_bytes(bs[0:2], "big"),  # 高字
    ]


def decode_ieee(regs) -> float:
    """低字在前的两个 16 位寄存器 -> float。"""
    if regs is None or len(regs) < 2:
        raise ValueError("需要两个寄存器解码为浮点")
    bs = bytes(
        [
            (int(regs[1]) >> 8) & 0xFF,
            int(regs[1]) & 0xFF,
            (int(regs[0]) >> 8) & 0xFF,
            int(regs[0]) & 0xFF,
        ]
    )
    return struct.unpack(">f", bs)[0]


def _read_float(addr: int) -> float:
    with _lock:
        regs = plc_master.execute(SLAVE_ID, md.READ_HOLDING_REGISTERS, addr, 2)
    return decode_ieee(regs)


def _write_float(addr: int, value: float) -> None:
    payload = encode_ieee(float(value))
    with _lock:
        plc_master.execute(
            SLAVE_ID, md.WRITE_MULTIPLE_REGISTERS, addr, output_value=payload
        )


def _read_coil(addr: int) -> bool:
    with _lock:
        return bool(plc_master.execute(SLAVE_ID, md.READ_COILS, addr, 1)[0])


def _write_coil(addr: int, value: bool) -> None:
    with _lock:
        plc_master.execute(
            SLAVE_ID, md.WRITE_SINGLE_COIL, addr, output_value=int(bool(value))
        )


def _pulse_coil(addr: int, width: float = 0.15) -> None:
    """脉冲线圈：写 1 后写 0。"""
    _write_coil(addr, True)
    time.sleep(width)
    _write_coil(addr, False)


def _safe_read_float(label: str, addr: int):
    try:
        return _read_float(addr)
    except Exception as exc:  # noqa: BLE001
        print(f"[read-float-error] {label}@0x{addr:04X}: {exc}")
        return None


def _safe_read_coil(label: str, addr: int):
    try:
        return _read_coil(addr)
    except Exception as exc:  # noqa: BLE001
        print(f"[read-coil-error] {label}@0x{addr:04X}: {exc}")
        return None


def get_state():
    return {
        "current": {
            "speed": {
                "x": _safe_read_float("x_speed_cur", REG_ADDR["x_speed_cur"]),
                "y": _safe_read_float("y_speed_cur", REG_ADDR["y_speed_cur"]),
                "z": _safe_read_float("z_speed_cur", REG_ADDR["z_speed_cur"]),
            },
            "position": {
                "x": _safe_read_float("x_pos_cur", REG_ADDR["x_pos_cur"]),
                "y": _safe_read_float("y_pos_cur", REG_ADDR["y_pos_cur"]),
                "z": _safe_read_float("z_pos_cur", REG_ADDR["z_pos_cur"]),
            },
        },
        "set_speed": {
            "x": _safe_read_float("x_speed_set", REG_ADDR["x_speed_set"]),
            "y": _safe_read_float("y_speed_set", REG_ADDR["y_speed_set"]),
            "z": _safe_read_float("z_speed_set", REG_ADDR["z_speed_set"]),
        },
        "set_coord": {
            "x": _safe_read_float("x_coord_set", REG_ADDR["x_coord_set"]),
            "y": _safe_read_float("y_coord_set", REG_ADDR["y_coord_set"]),
            "z": _safe_read_float("z_coord_set", REG_ADDR["z_coord_set"]),
        },
        "coils": {k: _safe_read_coil(k, addr) for k, addr in COIL_ADDR.items()},
    }


app = Flask(__name__)


@app.route("/")
def index() -> Response:
    return Response(HTML_PAGE, mimetype="text/html")


@app.route("/api/state")
def api_state():
    try:
        return jsonify(get_state())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


def _write_optional(payload: dict, keys, target_map: Dict[str, int]):
    wrote = False
    for key in keys:
        if key in payload:
            _write_float(target_map[key], float(payload[key]))
            wrote = True
    return wrote


@app.route("/api/speeds", methods=["POST"])
def api_speeds():
    try:
        data = request.get_json(force=True) or {}
        ok = _write_optional(
            data,
            ("x", "y", "z"),
            {
                "x": REG_ADDR["x_speed_set"],
                "y": REG_ADDR["y_speed_set"],
                "z": REG_ADDR["z_speed_set"],
            },
        )
        if not ok:
            return jsonify({"error": "需要提供 x/y/z 中至少一个速度"}), 400
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/coords", methods=["POST"])
def api_coords():
    try:
        data = request.get_json(force=True) or {}
        ok = _write_optional(
            data,
            ("x", "y", "z"),
            {
                "x": REG_ADDR["x_coord_set"],
                "y": REG_ADDR["y_coord_set"],
                "z": REG_ADDR["z_coord_set"],
            },
        )
        if not ok:
            return jsonify({"error": "需要提供 x/y/z 中至少一个坐标"}), 400
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
        if pulse:
            _pulse_coil(COIL_ADDR[action])
        else:
            _write_coil(COIL_ADDR[action], bool(data.get("value", True)))
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Modbus 上位机控制页面</title>
  <style>
    :root {
      --bg: #0c111b;
      --card: #111827;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #38bdf8;
      --accent2: #a855f7;
      --border: #1f2937;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; }
    body { margin:0; padding:18px; font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; background: radial-gradient(circle at 20% 20%, rgba(56,189,248,0.08), transparent 35%), radial-gradient(circle at 80% 0%, rgba(168,85,247,0.08), transparent 30%), var(--bg); color: var(--text); }
    h1 { margin: 0 0 12px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap:14px; }
    .card { background: var(--card); border:1px solid var(--border); border-radius: 14px; padding: 14px 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); }
    .card h2 { margin: 0 0 12px; font-size:18px; color: var(--accent); display:flex; gap:8px; align-items:center; }
    .pill { border:1px solid var(--border); background: rgba(255,255,255,0.03); border-radius: 12px; padding:10px 12px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; }
    .label { color: var(--muted); font-size:12px; }
    .value { font-variant-numeric: tabular-nums; font-weight:600; }
    label { display:block; font-size:12px; color: var(--muted); margin-bottom:4px; }
    input { width:100%; padding:9px 10px; border:1px solid var(--border); border-radius:10px; background: rgba(255,255,255,0.04); color: var(--text); }
    button { padding:10px 12px; border-radius:12px; border:1px solid var(--border); background: linear-gradient(135deg, var(--accent), var(--accent2)); color:#0d1117; font-weight:700; cursor:pointer; transition: transform 120ms ease, box-shadow 120ms ease; }
    button:hover { transform: translateY(-1px); box-shadow: 0 10px 18px rgba(56,189,248,0.25); }
    .muted-btn { background: rgba(255,255,255,0.05); color: var(--text); }
    .danger { background: var(--danger); color:#0d1117; border-color: var(--danger); }
    .row { display:flex; gap:10px; flex-wrap:wrap; }
    .row + .row { margin-top:8px; }
    .addr { font-size:12px; color: var(--muted); }
    .tag { padding:4px 8px; border-radius:999px; font-size:12px; }
    .on { background: rgba(56,189,248,0.14); color: #38bdf8; }
    .off { background: rgba(248,113,113,0.14); color: #f87171; }
  </style>
</head>
<body>
  <h1>Modbus 上位机控制页面</h1>
  <div class="grid">
    <div class="card">
      <h2>读数区（当前速度 / 当前位置）</h2>
      <div id="readings"></div>
    </div>

    <div class="card">
      <h2>坐标与指令（读写寄存器 + 脉冲线圈）</h2>
      <div class="row">
        <div style="flex:1;">
          <label for="cx">X 指定坐标（0x0058）</label>
          <input id="cx" type="number" placeholder="1-1989">
        </div>
        <div style="flex:1;">
          <label for="cy">Y 指定坐标（0x005A）</label>
          <input id="cy" type="number" placeholder="1-1989">
        </div>
      </div>
      <div class="row">
        <div style="flex:1;">
          <label for="cz">Z 指定坐标（0x005C）</label>
          <input id="cz" type="number" placeholder="0-192">
        </div>
      </div>
      <div class="row" style="margin-top:10px;">
        <button class="muted-btn" onclick="writeCoords()">写入坐标</button>
      </div>
      <div class="row" style="margin-top:6px;">
        <button onclick="pulse('xy_go_target')">下达指令到 X/Y 指定位置 (M 0x0033)</button>
        <button onclick="pulse('z_go_target')">下达指令到 Z 指定位置 (M 0x004C)</button>
      </div>
      <div class="row">
        <button class="muted-btn" onclick="pulse('xy_home')">X/Y 回原点 (M 0x0047)</button>
        <button class="muted-btn" onclick="pulse('z_home')">Z 回原点 (M 0x004D)</button>
      </div>
      <div class="row">
        <button class="danger" onclick="pulse('xy_stop')">X/Y 停止 (M 0x004B)</button>
        <button class="danger" onclick="pulse('z_stop')">Z 停止 (M 0x0053)</button>
      </div>
      <div class="row">
        <button class="muted-btn" onclick="setPause()">下达指令暂停 (M 0x004E 置位)</button>
        <button class="muted-btn" onclick="resumeCmd()">下达指令恢复 (M 0x004E 写 0)</button>
      </div>
    </div>

    <div class="card">
      <h2>速度设置（读写 D 寄存器）</h2>
      <div class="row">
        <div style="flex:1;">
          <label for="vx">X 设置速度（0x0048）</label>
          <input id="vx" type="number" placeholder="1-1000，建议 ≤500">
        </div>
        <div style="flex:1;">
          <label for="vy">Y 设置速度（0x004E）</label>
          <input id="vy" type="number" placeholder="1-1000，建议 ≤500">
        </div>
      </div>
      <div class="row">
        <div style="flex:1;">
          <label for="vz">Z 设置速度（0x0050）</label>
          <input id="vz" type="number" placeholder="1-1000，建议 ≤500">
        </div>
      </div>
      <div class="row" style="margin-top:10px;">
        <button onclick="writeSpeeds()">写入速度</button>
      </div>
    </div>

    <div class="card">
      <h2>外设控制（线圈脉冲）</h2>
      <div class="row">
        <button onclick="pulse('machine_on')">电机上电 (M 0x0036)</button>
        <button class="muted-btn" onclick="pulse('machine_off')">电机断电 (M 0x0037)</button>
      </div>
      <div class="row">
        <button onclick="pulse('light_on')">灯上电 (M 0x0038)</button>
        <button class="muted-btn" onclick="pulse('light_off')">灯断电 (M 0x0039)</button>
      </div>
      <div class="row">
        <button onclick="pulse('pump_on')">水泵上电 (M 0x003A)</button>
        <button class="muted-btn" onclick="pulse('pump_off')">水泵断电 (M 0x003B)</button>
      </div>
      <div class="row">
        <button onclick="pulse('dc12_on')">DC12V 上电 (M 0x003C)</button>
        <button class="muted-btn" onclick="pulse('dc12_off')">DC12V 断电 (M 0x003D)</button>
      </div>
      <div class="row">
        <button onclick="pulse('dc24_on')">DC24V 上电 (M 0x003E)</button>
        <button class="muted-btn" onclick="pulse('dc24_off')">DC24V 断电 (M 0x003F)</button>
      </div>
      <div class="row">
        <button onclick="pulse('ac220_on')">AC220V 上电 (M 0x0040)</button>
        <button class="muted-btn" onclick="pulse('ac220_off')">AC220V 断电 (M 0x0041)</button>
      </div>
    </div>
  </div>

  <script>
    const addrLabels = {
      x_speed_cur: "D 0x0042",
      y_speed_cur: "D 0x0044",
      z_speed_cur: "D 0x0046",
      x_pos_cur: "D 0x0052",
      y_pos_cur: "D 0x0054",
      z_pos_cur: "D 0x0056",
    };

    function fmt(v) {
      return v === null || v === undefined || Number.isNaN(v) ? "--" : Number(v).toFixed(3);
    }

    async function api(path, payload) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      if (!res.ok) throw new Error((await res.text()) || "请求失败");
      return res.json();
    }

    async function writeSpeeds() {
      const payload = {};
      const vx = document.getElementById("vx").value;
      const vy = document.getElementById("vy").value;
      const vz = document.getElementById("vz").value;
      if (vx) payload.x = Number(vx);
      if (vy) payload.y = Number(vy);
      if (vz) payload.z = Number(vz);
      if (!Object.keys(payload).length) return alert("请输入速度值");
      try { await api("/api/speeds", payload); refresh(); } catch (e) { alert(e.message); }
    }

    async function writeCoords() {
      const payload = {};
      const cx = document.getElementById("cx").value;
      const cy = document.getElementById("cy").value;
      const cz = document.getElementById("cz").value;
      if (cx) payload.x = Number(cx);
      if (cy) payload.y = Number(cy);
      if (cz) payload.z = Number(cz);
      if (!Object.keys(payload).length) return alert("请输入坐标值");
      try { await api("/api/coords", payload); refresh(); } catch (e) { alert(e.message); }
    }

    async function pulse(action) {
      try { await api("/api/coil", { action, pulse: true }); refresh(); }
      catch (e) { alert(e.message); }
    }

    async function setPause() {
      try { await api("/api/coil", { action: "cmd_pause", pulse: false, value: true }); refresh(); }
      catch (e) { alert(e.message); }
    }

    async function resumeCmd() {
      try { await api("/api/coil", { action: "cmd_pause", pulse: false, value: false }); refresh(); }
      catch (e) { alert(e.message); }
    }

    async function refresh() {
      try {
        const res = await fetch("/api/state");
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        const readings = document.getElementById("readings");
        const c = data.current || {};
        const speed = c.speed || {};
        const pos = c.position || {};
        readings.innerHTML = `
          <div class="pill"><div><div class="label">X 当前速度 (${addrLabels.x_speed_cur})</div><div class="value">${fmt(speed.x)}</div></div></div>
          <div class="pill"><div><div class="label">Y 当前速度 (${addrLabels.y_speed_cur})</div><div class="value">${fmt(speed.y)}</div></div></div>
          <div class="pill"><div><div class="label">Z 当前速度 (${addrLabels.z_speed_cur})</div><div class="value">${fmt(speed.z)}</div></div></div>
          <div class="pill"><div><div class="label">X 当前位置 (${addrLabels.x_pos_cur})</div><div class="value">${fmt(pos.x)}</div></div></div>
          <div class="pill"><div><div class="label">Y 当前位置 (${addrLabels.y_pos_cur})</div><div class="value">${fmt(pos.y)}</div></div></div>
          <div class="pill"><div><div class="label">Z 当前位置 (${addrLabels.z_pos_cur})</div><div class="value">${fmt(pos.z)}</div></div></div>
        `;
        // 填入设置值（显示用途）
        const ss = data.set_speed || {};
        if (ss.x !== undefined) document.getElementById("vx").placeholder = `${fmt(ss.x)}（当前）`;
        if (ss.y !== undefined) document.getElementById("vy").placeholder = `${fmt(ss.y)}（当前）`;
        if (ss.z !== undefined) document.getElementById("vz").placeholder = `${fmt(ss.z)}（当前）`;
        const sc = data.set_coord || {};
        if (sc.x !== undefined) document.getElementById("cx").placeholder = `${fmt(sc.x)}（当前）`;
        if (sc.y !== undefined) document.getElementById("cy").placeholder = `${fmt(sc.y)}（当前）`;
        if (sc.z !== undefined) document.getElementById("cz").placeholder = `${fmt(sc.z)}（当前）`;

      } catch (e) {
        console.warn(e);
      }
    }

    setInterval(refresh, 1000);
    refresh();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    print("Web 页面: http://0.0.0.0:5000  |  Modbus: 127.0.0.1:502")
    app.run(host="0.0.0.0", port=5000, debug=False)
