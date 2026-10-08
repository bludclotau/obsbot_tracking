"""High-level Tiny 3 Lite controls: AI tracking, image, PTZ."""

from __future__ import annotations

import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import xu
from .xu import XuDevice

AI_MODES = {
    "off": (0, 0),
    "stop": (0, 0),
    "none": (0, 0),
    "group": (1, 0),
    "human": (2, 0),
    "normal": (2, 0),
    "upper": (2, 1),
    "upperbody": (2, 1),
    "closeup": (2, 2),
    "headless": (2, 3),
    "lower": (2, 4),
    "lowerbody": (2, 4),
    "hand": (3, 0),
    "whiteboard": (4, 0),
    "desk": (5, 0),
}

AI_NAMES = {
    (0, 0): "off",
    (1, 0): "group",
    (2, 0): "human",
    (2, 1): "upper",
    (2, 2): "closeup",
    (2, 3): "headless",
    (2, 4): "lower",
    (3, 0): "hand",
    (4, 0): "whiteboard",
    (5, 0): "desk",
}


@dataclass
class Status:
    device: str
    running: bool
    ai: str
    ai_code: tuple[int, int]
    hdr: bool
    fov: int
    zoom_pct: int
    tracker_speed: int
    serial: str | None = None

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "running": self.running,
            "ai": self.ai,
            "ai_code": list(self.ai_code),
            "hdr": self.hdr,
            "fov": self.fov,
            "zoom_pct": self.zoom_pct,
            "tracker_speed": self.tracker_speed,
            "serial": self.serial,
        }


def _decode_status(device: Path, block: bytes, serial: str | None = None) -> Status:
    code = (block[0x18], block[0x1C])
    return Status(
        device=str(device),
        running=block[0x09] == 1,
        ai=AI_NAMES.get(code, f"unknown-{code[0]}-{code[1]}"),
        ai_code=code,
        hdr=block[0x06] != 0,
        fov=block[0x11],
        zoom_pct=int.from_bytes(block[0x04:0x06], "little"),
        tracker_speed=block[0x24] if len(block) > 0x24 else 0,
        serial=serial,
    )


def crc16_usb(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def build_frame(flags: int, seq: int, cmd: int, receiver: int, payload: bytes = b"") -> bytes:
    frame = bytearray(60)
    frame[0] = 0xAA
    frame[1] = flags
    struct.pack_into("<H", frame, 2, seq)
    struct.pack_into("<H", frame, 4, 12)
    frame[8] = 0x0A
    frame[9] = receiver
    struct.pack_into("<H", frame, 10, cmd)
    struct.pack_into("<H", frame, 6, crc16_usb(bytes(frame[0:12])))
    if payload:
        struct.pack_into("<H", frame, 12, len(payload))
        frame[16 : 16 + len(payload)] = payload
        tmp = bytearray(frame[12 : 16 + len(payload)])
        tmp[2:4] = b"\x00\x00"
        struct.pack_into("<H", frame, 14, crc16_usb(bytes(tmp)))
    return bytes(frame)


class Camera:
    def __init__(self, path: Path | str | None = None):
        self.dev = XuDevice(Path(path) if path else None)
        self._seq = 1

    @property
    def path(self) -> Path:
        return self.dev.path

    def close(self) -> None:
        self.dev.close()

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def status(self, with_serial: bool = False) -> Status:
        block = self.dev.get_block(xu.XU_SEL_STATUS)
        serial = self.serial() if with_serial else None
        return _decode_status(self.path, block, serial)

    def set_ai(self, mode: str) -> Status:
        key = mode.strip().lower()
        if key not in AI_MODES:
            raise ValueError(f"unknown AI mode {mode!r}; choose from {sorted(set(AI_MODES))}")
        m, n = AI_MODES[key]
        self.dev.send_status_cmd(bytes([xu.TAG_AI_MODE, 0x02, m, n]))
        time.sleep(0.25)
        return self.status()

    def set_hdr(self, on: bool) -> None:
        self.dev.send_status_cmd(bytes([xu.TAG_HDR, 0x01, 1 if on else 0]))

    def set_fov(self, mode: int) -> None:
        if mode not in (0, 1, 2):
            raise ValueError("fov must be 0=wide 1=medium 2=narrow")
        self.dev.send_status_cmd(bytes([xu.TAG_FOV, 0x01, mode]))

    def send_frame(self, flags: int, cmd: int, receiver: int, payload: bytes = b"") -> bytes:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        frame = build_frame(flags, seq, cmd, receiver, payload)
        self.dev.set_block(frame, xu.XU_SEL_FRAME)
        time.sleep(0.08)
        return bytes(self.dev.get_block(xu.XU_SEL_FRAME))

    def serial(self) -> str | None:
        try:
            rep = self.send_frame(0x01, 0x18C8, 0x0D, b"")
            if rep[0] != 0xAA:
                return None
            plen = int.from_bytes(rep[12:14], "little")
            return rep[16 : 16 + plen].split(b"\x00", 1)[0].decode("ascii", "replace")
        except OSError:
            return None

    def recenter(self) -> None:
        self.send_frame(0x25, 0x00C3, 0x03, bytes(6))
        self._v4l("pan_absolute=0,tilt_absolute=0")

    def set_pan_tilt_deg(self, pan: float, tilt: float) -> None:
        # UVC pan/tilt are arc-seconds. Tiny 2 family: ±130° pan, ±90° tilt.
        pan_asec = int(max(-130, min(130, pan)) * 3600)
        tilt_asec = int(max(-90, min(90, tilt)) * 3600)
        self._v4l(f"pan_absolute={pan_asec},tilt_absolute={tilt_asec}")

    def nudge(self, pan_speed: int = 0, tilt_speed: int = 0, ms: int = 350) -> None:
        self._v4l(f"pan_speed={pan_speed},tilt_speed={tilt_speed}")
        time.sleep(ms / 1000)
        self._v4l("pan_speed=0,tilt_speed=0")

    def set_zoom(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        self._v4l(f"zoom_absolute={percent}")

    def _v4l(self, ctrls: str) -> None:
        subprocess.run(
            ["v4l2-ctl", "-d", str(self.path), f"--set-ctrl={ctrls}"],
            check=False,
            capture_output=True,
        )
