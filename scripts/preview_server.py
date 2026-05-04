"""Headless camera preview over HTTP (MJPEG).

Runs the camera in a background thread, exposes the latest frame as an MJPEG
stream so any browser on the network can watch live without an X server. Built
for the lighting/positioning loop where the operator needs to see what the
camera sees while adjusting hardware over SSH.

No new dependencies — stdlib `http.server` + threading + cv2 JPEG encoding.

Endpoints:
  GET  /             minimal HTML page with <img src="/stream">
  GET  /stream       multipart/x-mixed-replace MJPEG, one capture-thread shared
  GET  /snapshot.jpg single JPEG of the latest frame
  GET  /healthz      "ok" + last frame age (ms)

Usage:
    .venv/bin/python scripts/preview_server.py
    .venv/bin/python scripts/preview_server.py --exposure 1500 --port 8080
    .venv/bin/python scripts/preview_server.py --max-width 800 --jpeg-quality 70

Open http://<this-host>:8080/ in a browser. Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pcb_inspection.camera import CameraConfig, create_camera

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MJPEG_BOUNDARY = "frame"


class FrameBuffer:
    """Holds the most recent JPEG-encoded frame, wakes waiters on update."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._jpeg: bytes | None = None
        self._captured_at: float = 0.0
        self._seq: int = 0

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._captured_at = time.monotonic()
            self._seq += 1
            self._cond.notify_all()

    def latest(self) -> tuple[bytes | None, float, int]:
        with self._lock:
            return self._jpeg, self._captured_at, self._seq

    def wait_for_next(self, last_seq: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        """Block until a frame newer than last_seq arrives (or timeout)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > last_seq, timeout=timeout)
            return self._jpeg, self._seq


class CaptureThread(threading.Thread):
    """Single owner of the camera. Grabs in a loop, encodes, publishes."""

    daemon = True

    def __init__(
        self,
        cam,
        buf: FrameBuffer,
        max_width: int,
        jpeg_quality: int,
        target_fps: float,
    ) -> None:
        super().__init__(name="capture")
        self.cam = cam
        self.buf = buf
        self.max_width = max_width
        self.jpeg_quality = jpeg_quality
        self.min_interval = 1.0 / max(target_fps, 0.1)
        self.stop_event = threading.Event()

    def run(self) -> None:
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)]
        next_due = time.monotonic()
        while not self.stop_event.is_set():
            try:
                img = self.cam.grab()
            except Exception:
                logger.exception("grab failed; pausing 0.5s")
                time.sleep(0.5)
                continue

            display = self._prepare(img)
            ok, encoded = cv2.imencode(".jpg", display, encode_params)
            if not ok:
                logger.warning("JPEG encode failed; skipping frame")
                continue
            self.buf.publish(encoded.tobytes())

            # Pace so we don't pin the CPU on huge sensor frames.
            next_due += self.min_interval
            sleep = next_due - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                # Fell behind — reset schedule rather than burst-catch-up.
                next_due = time.monotonic()

    def _prepare(self, img: np.ndarray) -> np.ndarray:
        """Convert to 8-bit BGR, downscale to max_width for browser bandwidth."""
        if img.dtype != np.uint8:
            img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        h, w = img.shape[:2]
        if w > self.max_width:
            scale = self.max_width / w
            img = cv2.resize(img, (self.max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return img


def _index_html(host_hint: str, port: int) -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>PCB camera preview</title>"
        "<style>body{margin:0;background:#111;color:#ddd;font-family:sans-serif}"
        "header{padding:8px 12px;font-size:13px;background:#222}"
        "img{display:block;max-width:100vw;max-height:calc(100vh - 32px);margin:auto}"
        "</style></head><body>"
        f"<header>preview · {host_hint}:{port} · "
        "<a style='color:#8af' href='/snapshot.jpg'>snapshot</a> · "
        "<a style='color:#8af' href='/healthz'>health</a></header>"
        "<img src='/stream' alt='live'/>"
        "</body></html>"
    ).encode("utf-8")


def make_handler(buf: FrameBuffer, host_hint: str, port: int):
    class Handler(BaseHTTPRequestHandler):
        # Suppress per-request log lines — too noisy for an MJPEG stream.
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send_bytes(200, "text/html; charset=utf-8", _index_html(host_hint, port))
            elif self.path == "/snapshot.jpg":
                jpeg, _, _ = buf.latest()
                if jpeg is None:
                    self.send_error(503, "No frame yet")
                    return
                self._send_bytes(200, "image/jpeg", jpeg)
            elif self.path == "/stream":
                self._stream(buf)
            elif self.path == "/healthz":
                _, captured_at, seq = buf.latest()
                age_ms = int((time.monotonic() - captured_at) * 1000) if captured_at else -1
                body = f"ok seq={seq} last_frame_age_ms={age_ms}\n".encode()
                self._send_bytes(200, "text/plain; charset=utf-8", body)
            else:
                self.send_error(404)

        def _send_bytes(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, buf: FrameBuffer) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last_seq = -1
            try:
                while True:
                    jpeg, last_seq = buf.wait_for_next(last_seq)
                    if jpeg is None:
                        continue
                    chunk = (
                        f"--{MJPEG_BOUNDARY}\r\n"
                        f"Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n"
                    ).encode("ascii")
                    self.wfile.write(chunk)
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                # Browser closed the tab — normal.
                return

    return Handler


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MJPEG preview server for the camera")
    p.add_argument("--host", default="0.0.0.0", help="Bind address (default: all interfaces)")
    p.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    p.add_argument("--device", type=int, default=0, help="Camera device index")
    p.add_argument("--backend", default="crevis", choices=("crevis", "mock", "auto"))
    p.add_argument("--exposure", type=float, default=1500.0, help="Exposure (us)")
    p.add_argument("--gain", type=float, default=0.0, help="Gain (dB)")
    p.add_argument("--pixel-format", default="Mono8")
    p.add_argument("--width", type=int, default=None, help="Sensor ROI width (default sensor max)")
    p.add_argument("--height", type=int, default=None, help="Sensor ROI height (default sensor max)")
    p.add_argument("--max-width", type=int, default=1024,
                   help="Downscale frames above this width before sending. Default 1024.")
    p.add_argument("--jpeg-quality", type=int, default=80, help="JPEG quality 1-100. Default 80.")
    p.add_argument("--fps", type=float, default=10.0, help="Target preview FPS. Default 10.")
    return p.parse_args()


def _local_hint() -> str:
    """Best-effort 'what URL should I open' hint for the log line."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


def main() -> None:
    args = parse_args()

    cfg = CameraConfig(
        exposure_us=args.exposure,
        gain=args.gain,
        pixel_format=args.pixel_format,
        width=args.width,
        height=args.height,
    )
    cam = create_camera(backend=args.backend, config=cfg, device_index=args.device)
    cam.open()

    buf = FrameBuffer()
    capture = CaptureThread(cam, buf, args.max_width, args.jpeg_quality, args.fps)
    capture.start()

    host_hint = _local_hint()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(buf, host_hint, args.port))
    logger.info("Preview ready: http://%s:%d/  (Ctrl-C to stop)", host_hint, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        capture.stop_event.set()
        capture.join(timeout=2.0)
        server.server_close()
        try:
            cam.close()
        except Exception:
            logger.exception("camera close failed")


if __name__ == "__main__":
    main()
