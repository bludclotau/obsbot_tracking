#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from .camera import AI_MODES, FULL_BODY_MODES, Camera
from .web import PreviewServer
from .xu import find_loopback_device


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="obsbot-tiny3", description="OBSBOT Tiny 3 Lite Linux controller (no SDK)")
    p.add_argument("-d", "--device", help="V4L2 capture node (default: autodetect)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print AI / HDR / FOV / serial")
    tr = sub.add_parser("track", help="set on-camera AI tracking mode")
    tr.add_argument("mode", choices=sorted(set(AI_MODES)))
    zm = sub.add_parser("zoom", help="digital zoom 0=1.0x … 100=4.0x")
    zm.add_argument("percent", type=int)
    sub.add_parser("home", help="recenter gimbal")
    pt = sub.add_parser("nudge", help="jog pan/tilt")
    pt.add_argument("dir", choices=["left", "right", "up", "down"])
    ui = sub.add_parser("ui", help="preview + OBS virtual camera + tracking")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--virtual-camera", help="v4l2loopback node (default: autodetect)")
    ui.add_argument("--no-virtual-camera", action="store_true")

    args = p.parse_args(argv)
    cam = Camera(args.device)

    try:
        if args.cmd == "status":
            print(json.dumps(cam.status(with_serial=True).as_dict(), indent=2))
            return 0
        if args.cmd == "track":
            if args.mode in FULL_BODY_MODES:
                st = cam.apply_full_body()
            elif args.mode in {"human", "normal", "upper", "upperbody", "closeup"}:
                cam.set_ai(args.mode)
                cam.set_auto_zoom(True)
                st = cam.status()
            else:
                st = cam.set_ai(args.mode)
            print(json.dumps(st.as_dict(), indent=2))
            return 0
        if args.cmd == "zoom":
            st = cam.set_zoom(args.percent)
            print(json.dumps(st.as_dict(), indent=2))
            return 0
        if args.cmd == "home":
            cam.recenter()
            print(json.dumps(cam.status().as_dict(), indent=2))
            return 0
        if args.cmd == "nudge":
            mapping = {
                "left": (-40, 0),
                "right": (40, 0),
                "up": (0, 40),
                "down": (0, -40),
            }
            pan, tilt = mapping[args.dir]
            cam.nudge(pan, tilt)
            print(json.dumps(cam.status().as_dict(), indent=2))
            return 0
        if args.cmd == "ui":
            loopback = None if args.no_virtual_camera else (args.virtual_camera or find_loopback_device())
            url = f"http://127.0.0.1:{args.port}/"
            if not args.no_browser:
                webbrowser.open(url)
            PreviewServer(cam, port=args.port, loopback=loopback).serve()
            return 0
    except (OSError, ValueError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        if args.cmd != "ui":
            cam.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
