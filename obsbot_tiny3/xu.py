"""UVC extension-unit ioctl for OBSBOT Tiny 3 Lite (and Tiny 2 family).

The camera's vendor XU is unit 2, GUID {9a1e7291-6843-4683-6d92-39bc7906ee49}.
Selector 6 is the 60-byte command/status block used for AI tracking.
Selector 2 is the checksummed V3 frame mailbox (gimbal, serial, sleep).

Tiny 3 Lite ignores zero-padded selector-6 writes. Overlay the command on
the current status block (same trick the kernel uses for UVCIOC_CTRL_MAP).
"""

from __future__ import annotations

import ctypes
import fcntl
import os
from pathlib import Path

UVCIOC_CTRL_QUERY = 0xC0107521
UVC_SET_CUR = 0x01
UVC_GET_CUR = 0x81
UVC_GET_LEN = 0x85

XU_UNIT = 2
XU_SEL_STATUS = 6
XU_SEL_FRAME = 2
BLOCK = 60

TAG_AI_MODE = 0x16
TAG_HDR = 0x01
TAG_FOV = 0x04
TAG_FACE_AE = 0x03
TAG_MIRROR = 0x14


class _Query(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


def find_loopback_device() -> Path | None:
    """v4l2loopback node OBS can capture (written by ffmpeg)."""
    preferred = os.environ.get("OBSBOT_VIRTUAL")
    if preferred and Path(preferred).exists():
        return Path(preferred)
    sysfs = Path("/sys/class/video4linux")
    if not sysfs.exists():
        return None
    for name in sorted(p.name for p in sysfs.iterdir()):
        card = (sysfs / name / "name").read_text(encoding="utf-8", errors="replace").strip().lower()
        if "virtual" in card or "loopback" in card:
            return Path("/dev") / name
    return None


def find_capture_device() -> Path:
    preferred = os.environ.get("OBSBOT_DEVICE")
    if preferred and Path(preferred).exists():
        return Path(preferred)
    sysfs = Path("/sys/class/video4linux")
    if not sysfs.exists():
        raise FileNotFoundError("no video4linux devices")
    for name in sorted(p.name for p in sysfs.iterdir()):
        card = (sysfs / name / "name").read_text(encoding="utf-8", errors="replace").strip()
        lower = card.lower()
        if "virtual" in lower or "loopback" in lower:
            continue
        index_path = sysfs / name / "index"
        if index_path.exists() and index_path.read_text().strip() != "0":
            continue
        if "obsbot" in lower or "pw105" in lower or "tiny" in lower:
            return Path("/dev") / name
    if Path("/dev/video1").exists():
        return Path("/dev/video1")
    raise FileNotFoundError("OBSBOT capture node not found")


class XuDevice:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else find_capture_device()
        self.fd = os.open(self.path, os.O_RDWR)

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "XuDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ioctl(self, selector: int, query: int, buf: ctypes.Array) -> None:
        q = _Query(XU_UNIT, selector, query, len(buf), ctypes.addressof(buf))
        fcntl.ioctl(self.fd, UVCIOC_CTRL_QUERY, q)

    def get_len(self, selector: int) -> int:
        buf = (ctypes.c_uint8 * 2)()
        self._ioctl(selector, UVC_GET_LEN, buf)
        return int.from_bytes(bytes(buf), "little")

    def get_block(self, selector: int = XU_SEL_STATUS) -> bytearray:
        n = self.get_len(selector)
        buf = (ctypes.c_uint8 * n)()
        self._ioctl(selector, UVC_GET_CUR, buf)
        return bytearray(buf)

    def set_block(self, data: bytes | bytearray, selector: int = XU_SEL_STATUS) -> None:
        n = self.get_len(selector)
        raw = bytes(data)
        if len(raw) < n:
            raw = raw + bytes(n - len(raw))
        buf = (ctypes.c_uint8 * n).from_buffer_copy(raw[:n])
        self._ioctl(selector, UVC_SET_CUR, buf)

    def send_status_cmd(self, cmd: bytes) -> bytearray:
        """Write a selector-6 command by overlaying it on the live status block."""
        block = self.get_block(XU_SEL_STATUS)
        block[: len(cmd)] = cmd
        self.set_block(block, XU_SEL_STATUS)
        return self.get_block(XU_SEL_STATUS)
