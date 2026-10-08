"""Local preview + control UI. ffmpeg reads V4L2; XU ioctls share the node."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .camera import AI_MODES, Camera

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
  .status { font-size:12px; color:#a1a1aa; margin-bottom:12px; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
  button { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:10px 8px; cursor:pointer; font-size:13px; }
  button:hover { background:#3f3f46; }
  button.active { background:#2563eb; border-color:#3b82f6; }
  .row { display:flex; gap:8px; margin-top:8px; }
  .row button { flex:1; }
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
const modes = ["off","human","upper","closeup","group","hand","desk","whiteboard"];
const box = document.getElementById("modes");
modes.forEach(m => {
  const b = document.createElement("button");
  b.textContent = m;
  b.dataset.mode = m;
  b.onclick = () => fetch("/api/track?mode="+m, {method:"POST"}).then(refresh);
  box.appendChild(b);
});
document.querySelectorAll("[data-ptz]").forEach(b => {
  b.onclick = () => fetch("/api/ptz?dir="+b.dataset.ptz, {method:"POST"}).then(refresh);
});
async function refresh() {
  const s = await (await fetch("/api/status")).json();
  document.getElementById("status").textContent =
    (s.serial ? s.serial+" · " : "") + s.device + " · AI " + s.ai + (s.running ? " · run" : " · sleep");
  document.querySelectorAll("#modes button").forEach(b => {
    b.classList.toggle("active", b.dataset.mode === s.ai || (s.ai==="human" && b.dataset.mode==="normal"));
  });
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class PreviewServer:
    def __init__(self, camera: Camera, host: str = "127.0.0.1", port: int = 8765):
        self.camera = camera
        self.host = host
        self.port = port
        self._ffmpeg: subprocess.Popen | None = None
        self._clients: list = []
        self._lock = threading.Lock()
        handler = self._handler()
        self.httpd = ThreadingHTTPServer((host, port), handler)

    def _start_ffmpeg(self) -> None:
        if self._ffmpeg and self._ffmpeg.poll() is None:
            return
        self._ffmpeg = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "v4l2", "-input_format", "mjpeg",
                "-video_size", "1280x720", "-framerate", "30",
                "-i", str(self.camera.path),
                "-c:v", "copy", "-f", "mjpeg", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        soi, eoi = b"\xff\xd8", b"\xff\xd9"
        buf = b""
        assert self._ffmpeg and self._ffmpeg.stdout
        while self._ffmpeg.poll() is None:
            chunk = self._ffmpeg.stdout.read(4096)
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
                    body = json.dumps(server.camera.status(with_serial=True).as_dict()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
                    mode = (qs.get("mode") or ["human"])[0]
                    if mode not in AI_MODES:
                        self.send_error(400, "bad mode")
                        return
                    st = server.camera.set_ai(mode)
                    body = json.dumps(st.as_dict()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
                    body = json.dumps(server.camera.status().as_dict()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

        return Handler

    def serve(self) -> None:
        print(f"Tiny 3 Lite UI  http://{self.host}:{self.port}/")
        print(f"capture node    {self.camera.path}")
        try:
            self.httpd.serve_forever()
        finally:
            self.close()

    def close(self) -> None:
        if self._ffmpeg:
            self._ffmpeg.terminate()
            self._ffmpeg = None
        self.httpd.server_close()
