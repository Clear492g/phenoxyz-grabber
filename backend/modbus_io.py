"""
Shared Modbus helpers and address maps.
"""

import struct
import threading
import time
from typing import Dict, Iterable, List, Optional

import modbus_tk.defines as md
import modbus_tk.modbus_tcp as mt

# PLC connection
PLC_HOST = "192.168.1.88"
PLC_PORT = 502
SLAVE_ID = 1

# D registers (32-bit float, low word first)
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

# M coils
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
    """float -> two 16-bit registers (low word first)."""
    bs = struct.pack(">f", float(value))
    return [
        int.from_bytes(bs[2:4], "big"),  # low word
        int.from_bytes(bs[0:2], "big"),  # high word
    ]


def decode_ieee(regs) -> float:
    """Two 16-bit registers (low word first) -> float."""
    if regs is None or len(regs) < 2:
        raise ValueError("need two registers to decode float")
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
    """Pulse a coil: write 1 then 0."""
    _write_coil(addr, True)
    time.sleep(width)
    _write_coil(addr, False)


def _safe_read_float(label: str, addr: int) -> Optional[float]:
    try:
        return _read_float(addr)
    except Exception as exc:  # noqa: BLE001
        print(f"[read-float-error] {label}@0x{addr:04X}: {exc}")
        return None


def _safe_read_coil(label: str, addr: int) -> Optional[bool]:
    try:
        return _read_coil(addr)
    except Exception as exc:  # noqa: BLE001
        print(f"[read-coil-error] {label}@0x{addr:04X}: {exc}")
        return None


def get_state() -> dict:
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


def _write_optional(
    payload: dict, keys: Iterable[str], target_map: Dict[str, int]
) -> bool:
    wrote = False
    for key in keys:
        if key in payload:
            _write_float(target_map[key], float(payload[key]))
            wrote = True
    return wrote


def write_speeds(data: dict) -> bool:
    return _write_optional(
        data,
        ("x", "y", "z"),
        {
            "x": REG_ADDR["x_speed_set"],
            "y": REG_ADDR["y_speed_set"],
            "z": REG_ADDR["z_speed_set"],
        },
    )


def write_coords(data: dict) -> bool:
    return _write_optional(
        data,
        ("x", "y", "z"),
        {
            "x": REG_ADDR["x_coord_set"],
            "y": REG_ADDR["y_coord_set"],
            "z": REG_ADDR["z_coord_set"],
        },
    )


def handle_coil(action: str, pulse: bool = True, value: Optional[bool] = None) -> None:
    if action not in COIL_ADDR:
        raise KeyError("unknown coil action")

    if pulse:
        _pulse_coil(COIL_ADDR[action])
        return

    if value is None:
        raise ValueError("value required when pulse is False")
    _write_coil(COIL_ADDR[action], bool(value))
