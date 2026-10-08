"""Local preview + OBS virtual camera. ffmpeg reads V4L2; XU ioctls share the node."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .camera import AI_MODES, WAIST_MODES, Camera
from .xu import find_loopback_device

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OBSBOT Tiny 3 Lite</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: Inter, system-ui, sans-serif; background:#0a0a0c; color:#f4f4f5; }
  main { display:grid; grid-template-columns: 1.4fr 320px; gap:16px; padding:16px; min-height:100vh; box-sizing:border-box; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .panel { background:rgba(23,23,26,.75); border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:16px; }
  img#preview { width:100%; height:auto; background:#000; border-radius:12px; display:block; }
  h1 { font-size:16px; margin:0 0 12px; font-weight:600; }
  .status { font-size:12px; color:#a1a1aa; margin-bottom:12px; line-height:1.45; white-space:pre-wrap; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
  button { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:10px 8px; cursor:pointer; font-size:13px; }
  button:hover { background:#3f3f46; }
  button.active { background:#2563eb; border-color:#3b82f6; }
  .row { display:flex; gap:8px; margin-top:8px; }
  .row button { flex:1; }
  label.zoom { display:flex; justify-content:space-between; font-size:13px; margin-top:16px; }
  input[type=range] { width:100%; margin-top:8px; }
  .hint { font-size:11px; color:#71717a; margin-top:8px; }
</style>
</head>
<body>
<main>
  <section class="panel">
    <h1>Live preview</h1>
    <img id="preview" alt="camera" src="/stream.mjpg">
  </section>
  <aside class="panel">
    <h1>AI tracking</h1>
    <div class="status" id="status">loading…</div>
    <div class="grid" id="modes"></div>
    <p class="hint">Waist mode: human tracking with dynamic zoom, gimbal tilted down so the waist is centered and the top of the head meets the top of the frame.</p>
    <label class="zoom">Zoom <span id="zlab">1.0x</span></label>
    <input id="zoom" type="range" min="0" max="100" value="0" step="1">
    <h1 style="margin-top:20px">PTZ</h1>
    <div class="row">
      <button data-ptz="up">Tilt up</button>
    </div>
    <div class="row">
      <button data-ptz="left">Pan left</button>
      <button data-ptz="home">Home</button>
      <button data-ptz="right">Pan right</button>
    </div>
    <div class="row">
      <button data-ptz="down">Tilt down</button>
    </div>
  </aside>
</main>
<script>
const modes = [
  ["off","Off"],
  ["waist","Waist"],
  ["human","Full body"],
  ["upper","Upper"],
  ["closeup","Close-up"],
  ["group","Group"],
  ["hand","Hand"],
  ["desk","Desk"],
  ["whiteboard","Board"]
];
const box = document.getElementById("modes");
modes.forEach(([id, label]) => {
  const b = document.createElement("button");
  b.textContent = label;
  b.dataset.mode = id;
  b.onclick = () => fetch("/api/track?mode="+id, {method:"POST"}).then(refresh);
  box.appendChild(b);
});
document.querySelectorAll("[data-ptz]").forEach(b => {
  b.onclick = () => fetch("/api/ptz?dir="+b.dataset.ptz, {method:"POST"}).then(refresh);
});
const zoom = document.getElementById("zoom");
function zoomLabel(pct) { return (1 + 3 * pct / 100).toFixed(1) + "x"; }
zoom.oninput = () => { document.getElementById("zlab").textContent = zoomLabel(+zoom.value); };
zoom.onchange = () => {
  fetch("/api/zoom?pct="+zoom.value, {method:"POST"}).then(() => {
    document.getElementById("preview").src = "/stream.mjpg?t="+Date.now();
    refresh();
  });
};
async function refresh() {
  const s = await (await fetch("/api/status")).json();
  const vcam = s.virtual_camera ? ("\\nOBS  " + s.virtual_camera) : "\\nOBS  virtual camera missing";
  document.getElementById("status").textContent =
    (s.serial ? s.serial+" · " : "") + s.device +
    "\\nAI " + s.ai + " · " + (s.framing || "custom") +
    " · zoom " + (s.zoom_x || "1.0") + "x" +
    (s.running ? " · run" : " · sleep") + vcam;
  document.querySelectorAll("#modes button").forEach(b => {
    const waist = s.framing === "waist";
    b.classList.toggle("active",
      (b.dataset.mode === "waist" && waist) ||
      (b.dataset.mode === s.ai && !(waist && b.dataset.mode === "human"))
    );
  });
  if (typeof s.zoom_pct === "number" && document.activeElement !== zoom) {
    zoom.value = s.zoom_pct;
    document.getElementById("zlab").textContent = zoomLabel(s.zoom_pct);
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class PreviewServer:
    def __init__(
        self,
        camera: Camera,
        host: str = "127.0.0.1",
        port: int = 8765,
        loopback: Path | str | None = None,
    ):
        self.camera = camera
        self.host = host
        self.port = port
        self.loopback = Path(loopback) if loopback else find_loopback_device()
        self._ffmpeg: subprocess.Popen | None = None
        self._clients: list = []
        self._lock = threading.Lock()
        self._zoom_pct = 0
        self._waist_hold = False
        self._pitch_target: float | None = None
        self._pump_stop = threading.Event()
        self._ffmpeg_log = open("/tmp/obsbot-tiny3-ffmpeg.log", "ab")
        handler = self._handler()
        self.httpd = ThreadingHTTPServer((host, port), handler)

    def public_status(self) -> dict:
        st = self.camera.status(with_serial=False).as_dict()
        st["zoom_pct"] = self._zoom_pct
        st["zoom_x"] = round(1 + 3 * self._zoom_pct / 100, 2)
        st["virtual_camera"] = str(self.loopback) if self.loopback else None
        pitch = self.camera.gimbal_pitch_deg()
        st["pitch_deg"] = round(pitch, 1) if pitch is not None else None
        st["framing"] = "waist" if self._waist_hold else st["ai"]
        return st

    def _ffmpeg_zoom(self) -> float:
        # Tracking owns sensor zoom; crop the OBS/preview feed instead.
        if self.camera.status().ai in {"off", "none", "stop"}:
            return 1.0
        return 1.0 + 3.0 * (self._zoom_pct / 100.0)

    def _vf(self, width: int, height: int, pix_fmt: str | None = None) -> str:
        parts: list[str] = []
        z = self._ffmpeg_zoom()
        if z > 1.02:
            parts.append(f"crop=trunc(iw/{z:.4f}/2)*2:trunc(ih/{z:.4f}/2)*2")
        parts.append(f"scale={width}:{height}")
        if pix_fmt:
            parts.append(f"format={pix_fmt}")
        return ",".join(parts)

    def _set_waist_hold(self, on: bool) -> None:
        was = self._waist_hold
        self._waist_hold = on
        if on:
            self._pitch_target = self.camera.waist_pitch_target
            if self._pitch_target is None:
                pitch = self.camera.gimbal_pitch_deg()
                if pitch is not None:
                    self._pitch_target = pitch
            if not was:
                threading.Thread(target=self._hold_waist_loop, daemon=True).start()
        else:
            self._pitch_target = None
            self.camera.gimbal_stop()

    def _hold_waist_loop(self) -> None:
        while self._waist_hold:
            target = self._pitch_target
            if target is not None:
                try:
                    self.camera.hold_waist_pitch(target)
                except OSError:
                    pass
            for _ in range(12):
                if not self._waist_hold:
                    return
                time.sleep(0.1)

    def _build_ffmpeg_cmd(self) -> list[str]:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", "1920x1080", "-framerate", "30",
            "-i", str(self.camera.path),
        ]
        if self.loopback:
            obs = self._vf(1920, 1080, "yuv420p")
            web = self._vf(1280, 720)
            cmd += [
                "-filter_complex",
                f"[0:v]split=2[v1][v2];[v1]{obs}[obs];[v2]{web}[web]",
                "-map", "[obs]", "-f", "v4l2", "-pix_fmt", "yuv420p", str(self.loopback),
                "-map", "[web]", "-c:v", "mjpeg", "-q:v", "5", "-f", "mjpeg", "pipe:1",
            ]
        else:
            cmd += [
                "-vf", self._vf(1280, 720),
                "-c:v", "mjpeg", "-q:v", "5", "-f", "mjpeg", "pipe:1",
            ]
        return cmd

    def _stop_ffmpeg(self) -> None:
        self._pump_stop.set()
        proc = self._ffmpeg
        self._ffmpeg = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def _start_ffmpeg(self) -> None:
        if self._ffmpeg and self._ffmpeg.poll() is None:
            return
        self._pump_stop.clear()
        self._ffmpeg = subprocess.Popen(
            self._build_ffmpeg_cmd(),
            stdout=subprocess.PIPE,
            stderr=self._ffmpeg_log,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _restart_ffmpeg(self) -> None:
        with self._lock:
            self._clients.clear()
        self._stop_ffmpeg()
        self._start_ffmpeg()

    def set_output_zoom(self, percent: int) -> dict:
        self._zoom_pct = max(0, min(100, int(percent)))
        self.camera.set_zoom(self._zoom_pct)
        self._restart_ffmpeg()
        return self.public_status()

    def _pump(self) -> None:
        soi, eoi = b"\xff\xd8", b"\xff\xd9"
        buf = b""
        proc = self._ffmpeg
        if not proc or not proc.stdout:
            return
        while not self._pump_stop.is_set() and proc.poll() is None:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                a = buf.find(soi)
                if a < 0:
                    buf = b""
                    break
                b = buf.find(eoi, a + 2)
                if b < 0:
                    buf = buf[a:]
                    break
                frame = buf[a : b + 2]
                buf = buf[b + 2 :]
                header = (
                    b"--ffmpeg\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                )
                packet = header + frame + b"\r\n"
                with self._lock:
                    dead = []
                    for wfile in self._clients:
                        try:
                            wfile.write(packet)
                            wfile.flush()
                        except OSError:
                            dead.append(wfile)
                    for d in dead:
                        self._clients.remove(d)

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _json(self, payload: dict, code: int = 200) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/":
                    body = HTML.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/status":
                    self._json(server.public_status())
                    return
                if path == "/stream.mjpg":
                    server._start_ffmpeg()
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=ffmpeg")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    with server._lock:
                        server._clients.append(self.wfile)
                    try:
                        while True:
                            threading.Event().wait(60)
                    except OSError:
                        pass
                    return
                self.send_error(404)

            def do_POST(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                if parsed.path == "/api/track":
                    mode = (qs.get("mode") or ["waist"])[0]
                    if mode not in AI_MODES:
                        self.send_error(400, "bad mode")
                        return
                    if mode in WAIST_MODES:
                        server.camera.apply_subject_framing()
                        server._set_waist_hold(True)
                    else:
                        server._set_waist_hold(False)
                        server.camera.set_ai(mode)
                    server._restart_ffmpeg()
                    self._json(server.public_status())
                    return
                if parsed.path == "/api/zoom":
                    try:
                        pct = int((qs.get("pct") or ["0"])[0])
                    except ValueError:
                        self.send_error(400, "bad zoom")
                        return
                    self._json(server.set_output_zoom(pct))
                    return
                if parsed.path == "/api/ptz":
                    direction = (qs.get("dir") or ["home"])[0]
                    if direction == "home":
                        server.camera.recenter()
                    elif direction == "left":
                        server.camera.nudge(pan_speed=-40)
                    elif direction == "right":
                        server.camera.nudge(pan_speed=40)
                    elif direction == "up":
                        server.camera.nudge(tilt_speed=40)
                    elif direction == "down":
                        server.camera.nudge(tilt_speed=-40)
                    else:
                        self.send_error(400)
                        return
                    self._json(server.public_status())
                    return
                self.send_error(404)

        return Handler

    def serve(self) -> None:
        self.camera.apply_subject_framing()
        self._set_waist_hold(True)
        self._start_ffmpeg()
        print(f"Tiny 3 Lite UI     http://{self.host}:{self.port}/")
        print(f"capture node       {self.camera.path}")
        if self.loopback:
            print(f"OBS virtual camera {self.loopback}  (Video Capture Device)")
        else:
            print("OBS virtual camera  not found — load v4l2loopback")
        print("tracking           human, waist-centered, gimbal tilted down")
        try:
            self.httpd.serve_forever()
        finally:
            self.close()

    def close(self) -> None:
        self._set_waist_hold(False)
        self._stop_ffmpeg()
        self.httpd.server_close()
