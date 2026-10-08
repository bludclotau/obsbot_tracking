#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from .camera import AI_MODES, Camera
from .web import PreviewServer


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="obsbot-tiny3", description="OBSBOT Tiny 3 Lite Linux controller (no SDK)")
    p.add_argument("-d", "--device", help="V4L2 capture node (default: autodetect)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print AI / HDR / FOV / serial")
    tr = sub.add_parser("track", help="set on-camera AI tracking mode")
    tr.add_argument("mode", choices=sorted(set(AI_MODES)))
    sub.add_parser("home", help="recenter gimbal")
    pt = sub.add_parser("nudge", help="jog pan/tilt")
    pt.add_argument("dir", choices=["left", "right", "up", "down"])
    ui = sub.add_parser("ui", help="open live preview + tracking UI")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")

    args = p.parse_args(argv)
    cam = Camera(args.device)

    try:
        if args.cmd == "status":
            print(json.dumps(cam.status(with_serial=True).as_dict(), indent=2))
            return 0
        if args.cmd == "track":
            st = cam.set_ai(args.mode)
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
            url = f"http://127.0.0.1:{args.port}/"
            if not args.no_browser:
                webbrowser.open(url)
            PreviewServer(cam, port=args.port).serve()
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
