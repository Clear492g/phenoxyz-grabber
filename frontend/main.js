const addrLabels = {
  x_speed_cur: "D 0x0042",
  y_speed_cur: "D 0x0044",
  z_speed_cur: "D 0x0046",
  x_pos_cur: "D 0x0052",
  y_pos_cur: "D 0x0054",
  z_pos_cur: "D 0x0056",
};

// 外设映射
const peripheralMap = [
  { key: "machine", label: "电机", on: "machine_on", off: "machine_off", onAddr: "M 0x0036", offAddr: "M 0x0037" },
  { key: "light", label: "照明", on: "light_on", off: "light_off", onAddr: "M 0x0038", offAddr: "M 0x0039" },
  { key: "pump", label: "水泵", on: "pump_on", off: "pump_off", onAddr: "M 0x003A", offAddr: "M 0x003B" },
  { key: "dc12", label: "DC12V", on: "dc12_on", off: "dc12_off", onAddr: "M 0x003C", offAddr: "M 0x003D" },
  { key: "dc24", label: "DC24V", on: "dc24_on", off: "dc24_off", onAddr: "M 0x003E", offAddr: "M 0x003F" },
  { key: "ac220", label: "AC220V", on: "ac220_on", off: "ac220_off", onAddr: "M 0x0040", offAddr: "M 0x0041" },
];

let latestCoils = {};
const boundsDefault = { x_min: 0, x_max: 2000, y_min: 0, y_max: 2000, cols: 10, rows: 10 };
let boundsCfg = { ...boundsDefault };
let routes = [];
let autorunTimer = null;
let editingRouteName = null;
let lastPosition = { x: 0, y: 0, z: 0 };
let camTimed = false;
let camCfgCache = null;
let msCfgCache = null;
let msChannels = ["480", "550", "660", "720", "840", "rgb"];
let msSeq = 0;

function fmt(v) {
  return v === null || v === undefined || Number.isNaN(v) ? "--" : Number(v).toFixed(3);
}

function defaultState() {
  const zeros = { x: 0, y: 0, z: 0 };
  return {
    current: { speed: { ...zeros }, position: { ...zeros } },
    set_speed: { ...zeros },
    set_coord: { ...zeros },
    coils: peripheralMap.reduce((acc, cfg) => {
      acc[cfg.on] = false;
      acc[cfg.off] = false;
      return acc;
    }, {}),
  };
}

async function api(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "请求失败");
  }
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
  try {
    await api("/api/speeds", payload);
    refresh();
  } catch (e) {
    alert(e.message);
  }
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
  try {
    await api("/api/coords", payload);
    refresh();
  } catch (e) {
    alert(e.message);
  }
}

async function pulse(action) {
  try {
    await api("/api/coil", { action, pulse: true });
    refresh();
  } catch (e) {
    alert(e.message);
  }
}

// 外设单按键切换
async function togglePeripheral(key) {
  const cfg = peripheralMap.find((x) => x.key === key);
  if (!cfg) return;
  const isOn = latestCoils?.[cfg.on] === true;
  const action = isOn ? cfg.off : cfg.on;
  try {
    await api("/api/coil", { action, pulse: true });
    refresh();
  } catch (e) {
    alert(e.message);
  }
}

function renderState(data) {
  const readings = document.getElementById("readings");
  const c = data.current ?? { speed: {}, position: {} };
  const speed = c.speed ?? {};
  const pos = c.position ?? {};

  readings.innerHTML = `
    <div class="pill row-line">
      <div>
        <div class="label">当前位置 (D 0x0052 / 0x0054 / 0x0056)</div>
      </div>
      <div class="triple">
        <span>X: ${fmt(pos.x)}</span>
        <span>Y: ${fmt(pos.y)}</span>
        <span>Z: ${fmt(pos.z)}</span>
      </div>
    </div>
    <div class="pill row-line">
      <div>
        <div class="label">当前速度 (D 0x0042 / 0x0044 / 0x0046)</div>
      </div>
      <div class="triple">
        <span>X: ${fmt(speed.x)}</span>
        <span>Y: ${fmt(speed.y)}</span>
        <span>Z: ${fmt(speed.z)}</span>
      </div>
    </div>
  `;

  lastPosition = { x: pos.x ?? 0, y: pos.y ?? 0, z: pos.z ?? 0 };
  renderPreview(lastPosition);

  // 填入设置值（显示用）
  const ss = data.set_speed || {};
  if (ss.x !== undefined) document.getElementById("vx").placeholder = `${fmt(ss.x)}（当前）`;
  if (ss.y !== undefined) document.getElementById("vy").placeholder = `${fmt(ss.y)}（当前）`;
  if (ss.z !== undefined) document.getElementById("vz").placeholder = `${fmt(ss.z)}（当前）`;
  const sc = data.set_coord || {};
  if (sc.x !== undefined) document.getElementById("cx").placeholder = `${fmt(sc.x)}（当前）`;
  if (sc.y !== undefined) document.getElementById("cy").placeholder = `${fmt(sc.y)}（当前）`;
  if (sc.z !== undefined) document.getElementById("cz").placeholder = `${fmt(sc.z)}（当前）`;

  // 渲染外设
  latestCoils = data.coils ?? {};
  const box = document.getElementById("peripherals");
  if (box) {
    box.innerHTML = peripheralMap
      .map((cfg) => {
        const raw = latestCoils[cfg.on];
        const isKnown = raw === true || raw === false;
        const isOn = raw === true;

        const stateText = isKnown ? (isOn ? "上电" : "下电") : "NA";
        const tagClass = isKnown ? (isOn ? "tag on" : "tag off") : "tag off";
        const btnText = isOn ? "下电" : "上电";
        const btnClass = isOn ? "" : "muted-btn";

        return `
          <div class="pill">
            <div>
              <div class="label">
                ${cfg.label}
                <span class="${tagClass}">${stateText}</span>
              </div>
              <div class="addr">${cfg.onAddr} / ${cfg.offAddr}</div>
            </div>
            <button class="${btnClass}" onclick="togglePeripheral('${cfg.key}')">
              ${btnText}
            </button>
          </div>
        `;
      })
      .join("");
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderState(data);
  } catch (e) {
    console.warn(e);
    renderState(defaultState());
  }
}

async function loadBounds() {
  try {
    const res = await fetch("/api/bounds");
    if (!res.ok) throw new Error(await res.text());
    const cfg = await res.json();
    boundsCfg = { ...boundsDefault, ...cfg };
  } catch (e) {
    console.warn(e);
    boundsCfg = { ...boundsDefault };
  }
}

async function loadRoutes() {
  try {
    const res = await fetch("/api/autorun/config");
    if (!res.ok) throw new Error(await res.text());
    const cfg = await res.json();
    routes = cfg.routes || [];
  } catch (e) {
    console.warn(e);
  }
  if (routes.length === 0) {
    routes.push({ name: "custom", speed: { x: 300, y: 300, z: 150 }, dwell: 1, points: [] });
  }
  renderRouteOptions();
  onRouteChange();
  renderPreview(lastPosition);
}

async function toggleAutorun() {
  const btn = document.getElementById("start-run");
  const running = btn?.dataset.running === "1";
  if (running) {
    try {
      await api("/api/autorun/stop");
      await updateAutorunState();
    } catch (e) {
      alert(e.message);
    }
    return;
  }

  const route = getCurrentRoute();
  if (!route) {
    alert("没有可用路径");
    return;
  }
  try {
    await api("/api/autorun/start", { route });
    await updateAutorunState();
  } catch (e) {
    alert(e.message);
  }
}

async function togglePause() {
  const pauseBtn = document.getElementById("pause-run");
  const paused = pauseBtn?.dataset.paused === "1";
  try {
    await api("/api/autorun/pause", { pause: !paused });
    await updateAutorunState();
  } catch (e) {
    alert(e.message);
  }
}

async function updateAutorunState() {
  try {
    const res = await fetch("/api/autorun/state");
    if (!res.ok) throw new Error(await res.text());
    const s = await res.json();
    const text = document.getElementById("autorun-text");
    if (text) {
      if (s.running) {
        const pausedTxt = s.paused ? "（已暂停）" : "";
        text.textContent = `运行中：${s.route || ""} (${s.index || 0}/${s.total || 0})${pausedTxt}`;
      } else if (s.error) {
        text.textContent = `错误：${s.error}`;
      } else {
        text.textContent = "空闲";
      }
    }
    const startBtn = document.getElementById("start-run");
    const pauseBtn = document.getElementById("pause-run");
    if (startBtn) {
      if (s.running) {
        startBtn.textContent = "结束";
        startBtn.dataset.running = "1";
      } else {
        startBtn.textContent = "开始";
        startBtn.dataset.running = "0";
      }
    }
    if (pauseBtn) {
      pauseBtn.disabled = !s.running;
      if (s.running) {
        if (s.paused) {
          pauseBtn.textContent = "恢复";
          pauseBtn.dataset.paused = "1";
        } else {
          pauseBtn.textContent = "暂停";
          pauseBtn.dataset.paused = "0";
        }
      } else {
        pauseBtn.textContent = "暂停";
        pauseBtn.dataset.paused = "0";
      }
    }
  } catch (e) {
    console.warn(e);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadBounds();
  await loadRoutes();
  await camInit();
  await msInit();
  // 先渲染一份默认状态，避免初始空白
  renderState(defaultState());
  refresh();
  setInterval(refresh, 1000);
  autorunTimer = setInterval(updateAutorunState, 1000);
});

// Expose autorun control
window.toggleAutorun = toggleAutorun;
window.togglePause = togglePause;

function getCurrentRoute() {
  const select = document.getElementById("route-select");
  if (!select) return null;
  const name = select.value;
  const found = routes.find((r, idx) => (r.name || `route-${idx}`) === name);
  return found || null;
}

function onRouteChange() {
  const route = getCurrentRoute();
  const info = document.getElementById("route-summary-text");
  if (info) {
    if (route) {
      const pts = route.points?.length || 0;
      info.textContent = `${route.name || "未命名"} | 点数: ${pts} | 速度: ${JSON.stringify(route.speed || {})} | dwell: ${route.dwell ?? 1}s`;
    } else {
      info.textContent = "--";
    }
  }
  renderPreview(lastPosition);
}

function renderRouteOptions() {
  const select = document.getElementById("route-select");
  if (!select) return;
  const prev = select.value;
  const options = routes.map((r, idx) => {
    const val = r.name || `route-${idx}`;
    const label = r.name || `路线${idx + 1}`;
    return `<option value="${val}">${label}</option>`;
  });
  select.innerHTML = options.join("");
  if (routes.length > 0) {
    const found = routes.find((r, idx) => (r.name || `route-${idx}`) === prev);
    select.value = found ? (found.name || prev) : (routes[0].name || `route-0`);
  }
}

function openRouteEditor() {
  const route = getCurrentRoute();
  const modal = document.getElementById("modal");
  const textarea = document.getElementById("route-json");
  if (!modal || !textarea) return;
  editingRouteName = route?.name || null;
  textarea.value = JSON.stringify(route || { name: "custom", speed: { x: 300, y: 300, z: 150 }, dwell: 1, points: [] }, null, 2);
  modal.classList.remove("hidden");
}

function closeRouteEditor() {
  const modal = document.getElementById("modal");
  if (modal) modal.classList.add("hidden");
}

function saveRouteEdit() {
  const textarea = document.getElementById("route-json");
  if (!textarea) return;
  let updated;
  try {
    updated = JSON.parse(textarea.value);
  } catch (e) {
    alert("JSON 解析失败: " + e.message);
    return;
  }
  const name = updated.name || editingRouteName || "custom";
  updated.name = name;

  const idx = routes.findIndex((r) => r.name === name);
  if (idx >= 0) {
    routes[idx] = updated;
  } else {
    routes.push(updated);
  }

  renderRouteOptions();
  const select = document.getElementById("route-select");
  if (select) select.value = name;
  onRouteChange();
  closeRouteEditor();
}

function addRoute() {
  const base = "route";
  let idx = routes.length + 1;
  let name = `${base}-${idx}`;
  const used = new Set(routes.map((r) => r.name));
  while (used.has(name)) {
    idx += 1;
    name = `${base}-${idx}`;
  }
  const newRoute = { name, speed: { x: 300, y: 300, z: 150 }, dwell: 1, points: [] };
  routes.push(newRoute);
  renderRouteOptions();
  const select = document.getElementById("route-select");
  if (select) select.value = name;
  onRouteChange();
}

function deleteRoute() {
  const route = getCurrentRoute();
  if (!route) return;
  if (!confirm(`删除路径 ${route.name || ""}?`)) return;
  routes = routes.filter((r) => r !== route);
  if (routes.length === 0) {
    routes.push({ name: "custom", speed: { x: 300, y: 300, z: 150 }, dwell: 1, points: [] });
  }
  renderRouteOptions();
  onRouteChange();
}

function renameRoute() {
  const route = getCurrentRoute();
  if (!route) return;
  const newName = prompt("输入新名称", route.name || "");
  if (!newName) return;
  if (routes.some((r) => r !== route && r.name === newName)) {
    alert("名称已存在");
    return;
  }
  route.name = newName;
  renderRouteOptions();
  const select = document.getElementById("route-select");
  if (select) select.value = newName;
  onRouteChange();
}

// 预览：用矩形表示边界，用线+点表示路径，用不同颜色表示当前位置
function renderPreview(position = {}) {
  const canvas = document.getElementById("xy-canvas");
  const info = document.getElementById("bounds-info");
  if (!canvas) return;

  const x = Number(position.x ?? 0);
  const y = Number(position.y ?? 0);
  if (info) {
    info.textContent = `X: [${boundsCfg.x_min}, ${boundsCfg.x_max}]  Y: [${boundsCfg.y_min}, ${boundsCfg.y_max}]  当前位置: (${fmt(x)}, ${fmt(y)})`;
  }

  const rect = canvas.getBoundingClientRect();
  const width = Math.max(200, Math.floor(rect.width || canvas.clientWidth || 400));
  const height = Math.max(200, Math.floor(rect.height || canvas.clientHeight || 260));
  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);

  const pad = 26; // 留出空间避免与边框/图例重叠
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const xRange = Math.max(1e-6, boundsCfg.x_max - boundsCfg.x_min);
  const yRange = Math.max(1e-6, boundsCfg.y_max - boundsCfg.y_min);
  const toPx = (px, py) => {
    const sx = pad + ((px - boundsCfg.x_min) / xRange) * innerW;
    const sy = pad + ((boundsCfg.y_max - py) / yRange) * innerH; // y 轴向上
    return [sx, sy];
  };

  // 边界矩形
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = 1;
  ctx.strokeRect(pad, pad, innerW, innerH);

  // 路径线和点
  const route = getCurrentRoute();
  const pts = route?.points || [];
  if (pts.length > 0) {
    ctx.strokeStyle = "#a855f7";
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const [sx, sy] = toPx(Number(p.x ?? 0), Number(p.y ?? 0));
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    });
    ctx.stroke();

    ctx.fillStyle = "#38bdf8";
    pts.forEach((p) => {
      const [sx, sy] = toPx(Number(p.x ?? 0), Number(p.y ?? 0));
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // 当前点
  const [cx, cy] = toPx(x, y);
  ctx.fillStyle = "#ef4444";
  ctx.beginPath();
  ctx.arc(cx, cy, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(239, 68, 68, 0.4)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 10, 0, Math.PI * 2);
  ctx.stroke();
}

// Expose editor handlers
window.onRouteChange = onRouteChange;
window.openRouteEditor = openRouteEditor;
window.closeRouteEditor = closeRouteEditor;
window.saveRouteEdit = saveRouteEdit;
window.addRoute = addRoute;
window.deleteRoute = deleteRoute;
window.renameRoute = renameRoute;

// Expose functions to inline handlers in HTML
window.writeSpeeds = writeSpeeds;
window.writeCoords = writeCoords;
window.pulse = pulse;
window.togglePeripheral = togglePeripheral;

// ===== 相机控制 =====
async function camInit() {
  try {
    const cfg = await (await fetch("/api/camera/config")).json();
    camCfgCache = cfg;
    const img = document.getElementById("cam-stream");
    if (img) img.src = "/camera/stream";
    const info = document.getElementById("cam-info");
    if (info) info.textContent = `设备: ${cfg.cam_name_keyword} · ${cfg.cam_preview_width}x${cfg.cam_preview_height}`;
    const af = document.getElementById("cam-autofocus");
    const focus = document.getElementById("cam-focus");
    const fval = document.getElementById("cam-focus-val");
    if (af) {
      af.checked = cfg.autofocus;
      af.onchange = async () => {
        await api("/api/camera/autofocus", { enabled: af.checked });
        if (focus) focus.disabled = af.checked;
      };
    }
    if (focus) {
      focus.max = cfg.cam_focus_max;
      focus.value = cfg.focus_value;
      focus.disabled = cfg.autofocus;
      focus.oninput = () => {
        if (fval) fval.textContent = `Focus: ${focus.value}`;
      };
      focus.onchange = async () => {
        await api("/api/camera/focus", { value: parseInt(focus.value, 10) });
      };
    }
    const dir = document.getElementById("cam-save-dir");
    if (dir) dir.value = cfg.save_dir || "";
    const cfgTxt = document.getElementById("cam-config-json");
    if (cfgTxt) cfgTxt.value = JSON.stringify(cfg, null, 2);
  } catch (e) {
    console.warn("cam init error", e);
  }
}

async function camSave() {
  try {
    await api("/api/camera/save");
    const btn = document.getElementById("cam-save");
    if (btn) {
      btn.classList.add("muted-btn");
      setTimeout(() => btn.classList.remove("muted-btn"), 300);
    }
  } catch (e) {
    alert(e.message);
  }
}

async function camToggleTimed() {
  const btn = document.getElementById("cam-timed");
  try {
    if (!camTimed) {
      const r = await api("/api/camera/timed/start");
      if (r.ok) camTimed = true;
    } else {
      await api("/api/camera/timed/stop");
      camTimed = false;
    }
    if (btn) btn.textContent = camTimed ? "停止采集" : "定时采集";
  } catch (e) {
    alert(e.message);
  }
}

async function camApplyDir() {
  const dir = document.getElementById("cam-save-dir");
  if (!dir) return;
  try {
    await api("/api/camera/save_dir", { path: dir.value });
  } catch (e) {
    alert(e.message);
  }
}

async function camApplyConfig() {
  const cfgTxt = document.getElementById("cam-config-json");
  if (!cfgTxt) return;
  let cfg;
  try {
    cfg = JSON.parse(cfgTxt.value);
  } catch (e) {
    alert("JSON 解析失败: " + e.message);
    return;
  }
  try {
    const res = await api("/api/camera/config/update", cfg);
    camCfgCache = res.cfg;
    await camInit(); // refresh config and re-open stream
    closeCamConfig();
  } catch (e) {
    alert(e.message);
  }
}

window.camSave = camSave;
window.camToggleTimed = camToggleTimed;
window.camApplyDir = camApplyDir;
window.camApplyConfig = camApplyConfig;
window.openCamConfig = function openCamConfig() {
  const modal = document.getElementById("cam-modal");
  const txt = document.getElementById("cam-config-json");
  if (txt && camCfgCache) {
    txt.value = JSON.stringify(camCfgCache, null, 2);
  }
  if (modal) modal.classList.remove("hidden");
};
window.closeCamConfig = function closeCamConfig() {
  const modal = document.getElementById("cam-modal");
  if (modal) modal.classList.add("hidden");
};

// ===== 多光谱相机 =====
async function msInit() {
  try {
    const cfg = await (await fetch("/api/multispec/config")).json();
    msCfgCache = cfg;
    const chs = await (await fetch("/api/multispec/channels")).json();
    msChannels = chs.channels && chs.channels.length ? chs.channels : msChannels;
    msApplyStreams();
    const info = document.getElementById("ms-info");
    if (info) {
      info.textContent = `通道数: ${msChannels.length} · JPEG质量: ${cfg.jpeg_quality}`;
    }
  } catch (e) {
    console.warn("ms init error", e);
    alert("多光谱初始化失败: " + e.message);
  }
}

function msApplyStreams() {
  const img = document.getElementById("ms-stream-all");
  if (img) {
    msSeq += 1;
    img.src = "";
    img.src = `/multispec/stream_all?t=${Date.now()}&seq=${msSeq}`;
  }
}

window.msOnChannelChange = msOnChannelChange;
