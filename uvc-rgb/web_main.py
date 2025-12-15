# web_main.py
import os
import cv2
import json
import time
import queue
import threading
import platform
import subprocess
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ========== 配置 ==========
BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "focus_config.json")

def load_cfg():
    default_cfg = {
        "autofocus": True,
        "focus_value": 50,
        "interval_sec": 1.0,
        "cam_name_keyword": "FicVideo",
        "cam_preview_width": 4080,
        "cam_preview_height": 3060,
        "cam_focus_max": 127,
        "save_dir": os.path.join(BASE_DIR, "images"),
        "png_compression": 3,
        "save_format": "jpg",     # jpg / png
        "jpeg_quality": 92,
        "ui_preview_width": 1280,
        "ui_preview_height": 720,
        "save_workers": 3
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
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
    cfg["ui_preview_width"] = int(cfg.get("ui_preview_width", 1280))
    cfg["ui_preview_height"] = int(cfg.get("ui_preview_height", 720))
    cfg["save_workers"] = max(1, int(cfg.get("save_workers", 3)))
    os.makedirs(cfg["save_dir"], exist_ok=True)
    return cfg

def save_cfg(partial: dict):
    cfg = load_cfg()
    cfg.update(partial or {})
    cfg["png_compression"] = max(0, min(9, int(cfg.get("png_compression", 3))))
    cfg["jpeg_quality"] = max(10, min(100, int(cfg.get("jpeg_quality", 92))))
    cfg["save_workers"] = max(1, int(cfg.get("save_workers", 3)))
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

def find_device_by_name(keyword: str) -> Optional[str]:
    os_type = platform.system()
    if os_type == 'Windows':
        try:
            from PyCameraList.camera_device import list_video_devices
            cameras = list_video_devices()
            for device in cameras:
                # device: (index, name)
                if len(device) >= 2 and device[1] and keyword.lower() in device[1].lower():
                    return str(device[0])
        except Exception:
            pass
        # fallback: 探测索引
        for i in range(6):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                cap.release()
                return str(i)
            cap.release()
        return "0"
    else:
        try:
            result = subprocess.run(['v4l2-ctl', '--list-devices'], stdout=subprocess.PIPE, text=True)
            lines = result.stdout.splitlines()
            for i in range(len(lines)):
                if keyword.lower() in lines[i].lower():
                    j = i + 1
                    while j < len(lines) and lines[j].startswith('\t'):
                        dev_path = lines[j].strip()
                        if dev_path.startswith('/dev/video'):
                            return dev_path
                        j += 1
        except Exception:
            pass
        return "/dev/video0"

# ========== 保存工作池 ==========
class SavePool(threading.Thread):
    def __init__(self, png_compression=3, jpeg_quality=92, notify_q=None, num_workers=3):
        super().__init__(daemon=True)
        self.png_compression = max(0, min(9, int(png_compression)))
        self.jpeg_quality = max(10, min(100, int(jpeg_quality)))
        self.notify_q = notify_q
        self.q = queue.Queue(maxsize=200)
        self._running = threading.Event()
        self._running.set()
        self.num_workers = max(1, int(num_workers))
        self._workers = []

    def submit(self, img, filename):
        try:
            if self.q.full():
                try:
                    self.q.get_nowait(); self.q.task_done()
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
            print(f"[保存失败] imencode failed: {filename}")
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
                if ok:
                    print(f"[{wid}] saved: {fname}")
            except Exception as e:
                print(f"[{wid}] save err: {e}")
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
                self.q.get_nowait(); self.q.task_done()
            except queue.Empty:
                break
        for t in self._workers:
            t.join(timeout=0.5)

# ========== 摄像头管理 ==========
class CameraManager:
    def __init__(self):
        self.cfg = load_cfg()
        self.device = find_device_by_name(self.cfg["cam_name_keyword"])

        opened = False
        self.cap = None

        if platform.system() == "Windows":
            if str(self.device).isdigit():
                idx = int(self.device)
                self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                opened = self.cap.isOpened()
                if not opened:
                    if self.cap: self.cap.release()
                    self.cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
                    opened = self.cap.isOpened()
            else:
                self.cap = cv2.VideoCapture(self.device)
                opened = self.cap.isOpened()
            if not opened:
                if self.cap: self.cap.release()
                for idx in range(0, 6):
                    cap_try = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap_try.isOpened():
                        self.cap = cap_try; opened = True; break
                    cap_try.release()
                if not opened:
                    for idx in range(0, 6):
                        cap_try = cv2.VideoCapture(idx, cv2.CAP_MSMF)
                        if cap_try.isOpened():
                            self.cap = cap_try; opened = True; break
                        cap_try.release()
        else:
            self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            opened = self.cap.isOpened()
            if not opened:
                if self.cap: self.cap.release()
                self.cap = cv2.VideoCapture(self.device)
                opened = self.cap.isOpened()

        if not opened or self.cap is None:
            raise RuntimeError("打开摄像头失败")

        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.cfg["cam_preview_width"])
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

        self.notify_q = queue.Queue(maxsize=100)
        self.pool = SavePool(self.cfg["png_compression"], self.cfg["jpeg_quality"],
                             notify_q=self.notify_q, num_workers=self.cfg["save_workers"])
        self.pool.start()

        self._timed_stop = threading.Event()
        self._timed_thread = None
        self.current_session_dir = None

    def _grab_loop(self):
        while self._run:
            ret, frame = self.cap.read()
            if ret:
                with self._lock:
                    self.curr_frame = frame
                    self.curr_ts = datetime.now()
            else:
                time.sleep(0.03)

    def get_frame(self):
        with self._lock:
            if self.curr_frame is None:
                return None, None
            return self.curr_frame.copy(), self.curr_ts

    def set_autofocus(self, enabled: bool):
        try:
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if enabled else 0)
            self.cfg["autofocus"] = bool(enabled)
            save_cfg({"autofocus": self.cfg["autofocus"]})
            return True
        except Exception:
            return False

    def set_focus(self, value: int):
        try:
            v = max(0, min(self.cfg["cam_focus_max"], int(value)))
            self.cap.set(cv2.CAP_PROP_FOCUS, float(v))
            self.cap.grab()
            self.cfg["focus_value"] = v
            save_cfg({"focus_value": v})
            return True
        except Exception:
            return False

    def save_current(self, target_dir=None):
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

# ====== FastAPI app（lifespan 确保仅在真正进程里创建相机）======
cam = None  # 启动后由 lifespan 填充
_cfg0 = load_cfg()
os.makedirs(_cfg0["save_dir"], exist_ok=True)
static_files = StaticFiles(directory=_cfg0["save_dir"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cam
    cam = CameraManager()
    try:
        yield
    finally:
        if cam:
            cam.stop()
            cam = None

app = FastAPI(lifespan=lifespan, title="Camera Focus WebApp", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
app.mount("/images", static_files, name="images")

# ======= 前端（响应式 + 保持真实宽高比 + 预览缩放/拖拽/双指缩放）=======
INDEX_HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Camera Focus Web</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
:root {
  --bg:#0d1117; --panel:#151b23; --border:#263042; --txt:#e6edf3;
  --accent:#1f6feb; --accent-2:#2ea043; --warn:#dc3545;
}
* { box-sizing: border-box; }
html, body { height:100%; }
body{margin:0;background:var(--bg);color:var(--txt);font:14px system-ui,Segoe UI,Arial,sans-serif}
.container{max-width:1200px;margin:0 auto;padding:16px}
.grid{
  display:grid; gap:16px;
  grid-template-columns: 1.2fr 0.8fr;
}
@media (max-width: 1024px){
  .grid{grid-template-columns: 1fr;}
}
.card{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:16px}
h2{margin:6px 0 16px 0;font-weight:600}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.btn{
  padding:10px 14px;border-radius:12px;border:1px solid var(--border);
  background:#1b2230;color:var(--txt);cursor:pointer;touch-action:manipulation
}
.btn:active{transform:translateY(1px)}
.btn.success{background:#173b26;border-color:#2a6d49}
.btn.warn{background:#3b1720;border-color:#6d2a3a}
input[type=range]{width:260px}
input[type=number], input[type=text], select{
  background:#0f1622;border:1px solid var(--border);color:var(--txt);
  padding:8px 10px;border-radius:10px
}
.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#101826;border:1px solid var(--border)}
.hr{height:1px;background:var(--border);opacity:.5;margin:12px 0}
.preview-wrap{
  position:relative; overflow:hidden; border-radius:12px; border:1px solid var(--border);
  background:#0a0f16;
  width:100%;
  /* 关键：JS 会把这里替换为相机实际宽高比，例如 aspect-ratio: 4080 / 3060 */
  aspect-ratio: 16 / 9; /* 兜底 */
}
.viewport{
  position:absolute; left:0; top:0; right:0; bottom:0;
  touch-action:none; /* 允许自定义手势 */
}
.viewport img{
  user-select:none; -webkit-user-drag:none; pointer-events:none;
  transform-origin: 0 0;
  will-change: transform;
  width: 100%; height: 100%; object-fit: contain; /* 不拉伸变形 */
}
.toolbar{
  position:absolute; right:12px; bottom:12px; display:flex; gap:8px;
}
.toolbar .btn{padding:8px 10px;border-radius:10px}
.small{opacity:.8}
</style>
</head>
<body>
<div class="container">
  <h2>Camera Focus Web</h2>
  <div class="grid">
    <div class="card">
      <div id="previewWrap" class="preview-wrap">
        <div id="viewport" class="viewport">
          <img id="stream" alt="preview" src="/stream">
        </div>
        <div class="toolbar">
          <button class="btn" id="zoom_out">-</button>
          <button class="btn" id="zoom_reset">100%</button>
          <button class="btn" id="zoom_in">+</button>
        </div>
      </div>
      <div style="margin-top:10px" class="small">提示：滚轮/触控板缩放，拖拽平移；双击（双指双击）复位。移动端支持双指缩放与拖动。</div>
    </div>
    <div class="card">
      <div class="controls">
        <span class="badge" id="info">loading...</span>
      </div>
      <div class="hr"></div>
      <div class="controls">
        <label><input type="checkbox" id="autofocus"> 自动对焦</label>
        <input type="range" id="focus" min="0" max="127" step="1" value="50" disabled>
        <span id="focus_val" class="badge">Focus: 50</span>
      </div>
      <div class="controls">
        <button class="btn" id="save">保存图像</button>
        <button class="btn" id="timed">定时采集</button>
      </div>

      <div class="hr"></div>
      <div class="controls">
        <label>保存目录</label>
        <input id="save_dir" style="flex:1" placeholder="绝对路径">
        <button class="btn" id="apply_dir">应用</button>
      </div>

      <div class="controls">
        <label>保存格式</label>
        <select id="fmt"><option value="jpg">jpg</option><option value="png">png</option></select>
        <label>JPEG质量</label>
        <input type="number" id="jpgq" min="10" max="100" value="92" style="width:90px">
        <label>PNG压缩</label>
        <input type="number" id="pngc" min="0" max="9" value="3" style="width:80px">
      </div>

      <div class="controls">
        <label>采集间隔(s)</label>
        <input type="number" id="interval" step="0.05" value="1.0" style="width:110px">
        <label>保存并发</label>
        <input type="number" id="workers" min="1" max="16" value="3" style="width:90px">
        <button class="btn" id="apply_save">应用保存参数</button>
      </div>

      <div style="margin-top:6px" class="small">保存成功会闪烁“定时采集”按钮一下。</div>
    </div>
  </div>
</div>

<script>
async function GET(u){const r=await fetch(u);return r.json()}
async function POST(u, body){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return r.json()}

const info = document.getElementById('info')
const autofocus = document.getElementById('autofocus')
const focus = document.getElementById('focus')
const focusVal = document.getElementById('focus_val')
const saveBtn = document.getElementById('save')
const timedBtn = document.getElementById('timed')
const saveDir = document.getElementById('save_dir')
const applyDir = document.getElementById('apply_dir')
const fmt = document.getElementById('fmt')
const jpgq = document.getElementById('jpgq')
const pngc = document.getElementById('pngc')
const interval = document.getElementById('interval')
const workers = document.getElementById('workers')
const applySave = document.getElementById('apply_save')

// ====== 预览缩放/拖拽/双指缩放 ======
const previewWrap = document.getElementById('previewWrap')
const viewport = document.getElementById('viewport')
const img = document.getElementById('stream')
const btnIn = document.getElementById('zoom_in')
const btnOut = document.getElementById('zoom_out')
const btnReset = document.getElementById('zoom_reset')

// 状态
let scale = 1, minScale = 0.5, maxScale = 6
let tx = 0, ty = 0
let isPanning = false
let lastX = 0, lastY = 0

function applyTransform(){
  img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`
}
function screenToImageCoords(px, py){
  const rect = img.getBoundingClientRect()
  const ox = (px - rect.left - tx) / scale
  const oy = (py - rect.top - ty) / scale
  return {ox, oy}
}
function zoomAt(cx, cy, ds){
  const before = screenToImageCoords(cx, cy)
  scale = Math.min(maxScale, Math.max(minScale, scale * ds))
  const after = screenToImageCoords(cx, cy)
  tx += (cx - (after.ox*scale + tx)) - (cx - (before.ox*scale + tx))
  ty += (cy - (after.oy*scale + ty)) - (cy - (before.oy*scale + ty))
  applyTransform()
  btnReset.textContent = Math.round(scale*100) + '%'
}
viewport.addEventListener('wheel', (e)=>{
  e.preventDefault()
  const ds = e.deltaY < 0 ? 1.1 : 1/1.1
  const cx = e.clientX, cy = e.clientY
  zoomAt(cx, cy, ds)
}, {passive:false})

viewport.addEventListener('mousedown', (e)=>{
  isPanning = true; lastX = e.clientX; lastY = e.clientY
})
window.addEventListener('mousemove', (e)=>{
  if(!isPanning) return
  const dx = e.clientX - lastX, dy = e.clientY - lastY
  lastX = e.clientX; lastY = e.clientY
  tx += dx; ty += dy
  applyTransform()
})
window.addEventListener('mouseup', ()=>{ isPanning = false })

viewport.addEventListener('dblclick', ()=>{
  scale = 1; tx = 0; ty = 0; applyTransform(); btnReset.textContent='100%'
})

// 触控（双指缩放 + 单指拖动）
let pointers = new Map()
function distance(p1, p2){ const dx=p1.clientX-p2.clientX, dy=p1.clientY-p2.clientY; return Math.hypot(dx,dy) }
let lastDist = 0
viewport.addEventListener('pointerdown', (e)=>{ viewport.setPointerCapture(e.pointerId); pointers.set(e.pointerId, e) })
viewport.addEventListener('pointerup', (e)=>{ viewport.releasePointerCapture(e.pointerId); pointers.delete(e.pointerId); lastDist = 0 })
viewport.addEventListener('pointermove', (e)=>{
  if(!pointers.has(e.pointerId)) return
  pointers.set(e.pointerId, e)
  if(pointers.size === 1){
    // 单指拖动
    const p = e
    const dx = p.movementX || (p.clientX - lastX)
    const dy = p.movementY || (p.clientY - lastY)
    lastX = p.clientX; lastY = p.clientY
    tx += dx; ty += dy; applyTransform()
  }else if(pointers.size === 2){
    // 双指缩放（取两指）
    const [p1, p2] = Array.from(pointers.values())
    const cx = (p1.clientX + p2.clientX)/2
    const cy = (p1.clientY + p2.clientY)/2
    const dist = distance(p1, p2)
    if(lastDist === 0) { lastDist = dist; return }
    const ds = dist / lastDist
    zoomAt(cx, cy, ds)
    lastDist = dist
  }
})
// 工具栏按钮
btnIn.onclick  = ()=> zoomAt(viewport.clientWidth/2, viewport.clientHeight/2, 1.2)
btnOut.onclick = ()=> zoomAt(viewport.clientWidth/2, viewport.clientHeight/2, 1/1.2)
btnReset.onclick = ()=>{ scale=1; tx=0; ty=0; applyTransform(); btnReset.textContent='100%' }

// ======= 控制逻辑 =======
async function refreshCfg(){
  const cfg = await GET('/api/config')
  info.textContent = `Device OK · ${cfg.cam_preview_width}x${cfg.cam_preview_height} -> UI ${cfg.ui_preview_width}x${cfg.ui_preview_height}`

  // ★ 关键：预览容器按照相机实际宽高比约束
  const arW = Math.max(1, parseInt(cfg.cam_preview_width))
  const arH = Math.max(1, parseInt(cfg.cam_preview_height))
  previewWrap.style.aspectRatio = arW + ' / ' + arH

  autofocus.checked = cfg.autofocus
  focus.disabled = cfg.autofocus
  focus.max = cfg.cam_focus_max
  focus.value = cfg.focus_value
  focusVal.textContent = 'Focus: ' + focus.value
  saveDir.value = cfg.save_dir
  fmt.value = cfg.save_format
  jpgq.value = cfg.jpeg_quality
  pngc.value = cfg.png_compression
  interval.value = cfg.interval_sec
  workers.value = cfg.save_workers
}
// 用首帧天然尺寸再精修一次比例
img.addEventListener('load', ()=>{
  if (img.naturalWidth && img.naturalHeight) {
    previewWrap.style.aspectRatio = img.naturalWidth + ' / ' + img.naturalHeight
  }
}, { once: true })

autofocus.addEventListener('change', async ()=>{
  await POST('/api/autofocus', {enabled:autofocus.checked})
  focus.disabled = autofocus.checked
})
focus.addEventListener('input', ()=>{ focusVal.textContent = 'Focus: ' + focus.value })
focus.addEventListener('change', async ()=>{ await POST('/api/focus', {value:parseInt(focus.value)}) })
saveBtn.addEventListener('click', async ()=>{
  await POST('/api/save', {})
  saveBtn.classList.add('success')
  setTimeout(()=>saveBtn.classList.remove('success'), 220)
})
let timedOn = false
timedBtn.addEventListener('click', async ()=>{
  if(!timedOn){
    const r = await POST('/api/timed/start', {}); if(r.ok){ timedOn = true; timedBtn.textContent='停止采集' }
  }else{
    await POST('/api/timed/stop', {}); timedOn = false; timedBtn.textContent='定时采集'
  }
})
applyDir.addEventListener('click', async ()=>{
  await POST('/api/save_dir', {path: saveDir.value})
  await refreshCfg()
})
applySave.addEventListener('click', async ()=>{
  await POST('/api/save_params', {
    save_format: fmt.value,
    jpeg_quality: parseInt(jpgq.value),
    png_compression: parseInt(pngc.value),
    interval_sec: parseFloat(interval.value),
    save_workers: parseInt(workers.value)
  })
  await refreshCfg()
})
refreshCfg()
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)

def mjpeg_generator():
    while True:
        frame, ts = cam.get_frame()
        if frame is None:
            time.sleep(0.03)
            continue
        preview = cam.resize_for_ui(frame)
        ret, jpg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            continue
        b = jpg.tobytes()
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n"
               b"Content-Length: " + str(len(b)).encode() + b"\r\n\r\n" +
               b + b"\r\n")

@app.get("/stream")
def stream():
    return StreamingResponse(mjpeg_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

# ===== API =====
@app.get("/api/config")
def api_config():
    return cam.cfg

@app.post("/api/autofocus")
async def api_autofocus(body: dict):
    enabled = bool(body.get("enabled", True))
    ok = cam.set_autofocus(enabled)
    if ok and not enabled:
        cam.set_focus(cam.cfg["focus_value"])
    return {"ok": ok}

@app.post("/api/focus")
async def api_focus(body: dict):
    v = int(body.get("value", 50))
    ok = cam.set_focus(v)
    return {"ok": ok, "value": cam.cfg["focus_value"]}

@app.post("/api/save")
async def api_save():
    fn = cam.save_current()
    return {"ok": bool(fn), "filename": fn}

@app.post("/api/timed/start")
async def api_timed_start():
    ok = cam.start_timed()
    return {"ok": ok}

@app.post("/api/timed/stop")
async def api_timed_stop():
    ok = cam.stop_timed()
    return {"ok": ok}

@app.post("/api/save_dir")
async def api_save_dir(body: dict):
    path = str(body.get("path", cam.cfg["save_dir"]))
    os.makedirs(path, exist_ok=True)
    new_cfg = save_cfg({"save_dir": path})
    cam.cfg = new_cfg
    static_files.directory = new_cfg["save_dir"]  # 热更新静态目录
    return {"ok": True, "save_dir": path}

@app.post("/api/save_params")
async def api_save_params(body: dict):
    to_update = {}
    if "save_format" in body: to_update["save_format"] = "jpg" if str(body["save_format"]).lower() not in ("jpg","png") else str(body["save_format"]).lower()
    if "jpeg_quality" in body: to_update["jpeg_quality"] = max(10, min(100, int(body["jpeg_quality"])))
    if "png_compression" in body: to_update["png_compression"] = max(0, min(9, int(body["png_compression"])))
    if "interval_sec" in body: to_update["interval_sec"] = float(body["interval_sec"])
    if "save_workers" in body: to_update["save_workers"] = max(1, int(body["save_workers"]))

    new_cfg = save_cfg(to_update)
    cam.cfg = new_cfg
    cam.pool.png_compression = new_cfg["png_compression"]
    cam.pool.jpeg_quality = new_cfg["jpeg_quality"]
    if "save_workers" in body:
        cam.pool.stop()
        cam.pool = SavePool(new_cfg["png_compression"], new_cfg["jpeg_quality"],
                            notify_q=cam.notify_q, num_workers=new_cfg["save_workers"])
        cam.pool.start()
    return {"ok": True, "cfg": new_cfg}

# ===== 开发启动 =====
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web_main:app", host="0.0.0.0", port=8309, reload=True)
