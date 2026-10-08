# obsbot_tracking

Linux controller for the **OBSBOT Tiny 3 Lite** (PW105, USB `3564:ff04`).

Live preview, OBS virtual camera, on-camera AI tracking, and digital zoom. No vendor SDK.

The official OBSBOT Linux SDK does not list Tiny 3 Lite. This project talks to the camera over the same UVC extension unit the Tiny 2 family uses.

## Requirements

- Linux, Python 3.11+
- `ffmpeg` (preview and virtual camera)
- `v4l2-ctl` (PTZ)
- `v4l2loopback` (OBS virtual camera)
- User in group `video`

Fedora:

```bash
sudo dnf install ffmpeg v4l-utils v4l2loopback
sudo usermod -aG video "$USER"
```

This host already has `/dev/video42` labeled **OBSBOT Virtual Camera**.

## Install

```bash
git clone git@github.com:bludclotau/obsbot_tracking.git
cd obsbot_tracking
pip install --user -e .
```

Override the capture node with `-d /dev/video1` or `OBSBOT_DEVICE=/dev/video1`. Override the loopback node with `--virtual-camera /dev/video42` or `OBSBOT_VIRTUAL`.

## OBS Studio

1. Stop anything else holding the real camera (lan-share, another preview).
2. Run `obsbot-tiny3 ui`.
3. In OBS, add **Video Capture Device** → **OBSBOT Virtual Camera** (`/dev/video42`). Use 1920×1080.

The app writes 1080p30 into the loopback device. OBS must not open the real Tiny 3 Lite node; that would steal the camera from tracking.

## Usage

```bash
obsbot-tiny3 status
obsbot-tiny3 track on          # object tracking locked on belly / hips
obsbot-tiny3 track off
obsbot-tiny3 zoom 0            # 1.0x  (100 = 4.0x)
obsbot-tiny3 home
obsbot-tiny3 nudge left
obsbot-tiny3 ui                # preview + virtual camera
```

`track on` / `track waist` turns on Tiny 3 Lite **object tracking** (AI mode 7,2) and auto-selects the **belly / hips** as the tracked object (tap at mid-frame, slightly below center, plus a hips bounding box). The gimbal is left to that tracker. A previous hold-loop that tilted down against face/upper-body tracking is gone — that fight was the pause, dip, and snap-back.

## Zoom

Tiny 3 Lite ignores UVC `zoom_absolute`. Zoom is a framed XU command, **1.0x–4.0x**.

While AI tracking is on, the camera owns sensor zoom (so the waist shot stays composed). The UI zoom slider then crops the **virtual camera and preview** instead, so zoom still changes what OBS sees.

## How tracking works

Tiny 3 Lite exposes the Tiny 2 vendor XU:

| | |
|---|---|
| GUID | `{9a1e7291-6843-4683-6d92-39bc7906ee49}` |
| Unit | 2 |
| Selector 6 | 60-byte AI / image status and command block |
| Selector 2 | checksummed V3 mailbox (serial, gimbal, zoom frames) |

Selector-6 writes must overlay the command on the **current** status block. Zero-padded 60-byte writes are ignored.

AI command: `[0x16, 0x02, mode, submode]`.

| mode, sub | name |
|---|---|
| 0, 0 | off |
| 1, 0 | group |
| 2, 0 | human (full body) |
| 2, 1 | upper body (thigh to eyebrows) |
| 2, 2 | close-up |
| 2, 3 | headless |
| 2, 4 | lower body |
| 3, 0 | hand |
| 4, 0 | whiteboard |
| 5, 0 | desk |
| 7, 2 | object (Tiny 3 Lite AI Tracking 2.0) |

Waist mode sends the object-tracking select-target packets (click / box / biggest / center) at the bellybutton. Tiny 3 Lite often does not ACK those frames; they are still the SDK tap-to-track commands.

Zoom frame: command `0x1942`, payload `[speed u32][ratio×100 u32]` with ratio 100–400.

## Credits

Protocol details from [cgevans/tiny2](https://github.com/cgevans/tiny2) and [lxman/obsbot-mcp](https://github.com/lxman/obsbot-mcp). Probed on a Tiny 3 Lite (SN `RMOWUHHC131OXY`).
