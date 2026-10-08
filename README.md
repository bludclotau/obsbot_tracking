# obsbot_tracking

Linux controller for the **OBSBOT Tiny 3 Lite** (PW105, USB `3564:ff04`).

Live MJPEG preview, on-camera AI tracking, and UVC pan/tilt/zoom. No vendor SDK.

The official OBSBOT Linux SDK does not list Tiny 3 Lite. This project talks to the camera over the same UVC extension unit the Tiny 2 family uses.

## Requirements

- Linux, Python 3.11+
- `ffmpeg` (preview)
- `v4l2-ctl` (PTZ)
- User in group `video` (and a udev rule so `/dev/videoN` is writable)

Fedora:

```bash
sudo dnf install ffmpeg v4l-utils
sudo usermod -aG video "$USER"
```

## Install

```bash
git clone git@github.com:bludclotau/obsbot_tracking.git
cd obsbot_tracking
pip install --user -e .
```

That installs the `obsbot-tiny3` command. You can also run without installing:

```bash
python3 -m obsbot_tiny3 status
```

Override the capture node with `-d /dev/video1` or `OBSBOT_DEVICE=/dev/video1`.

## Usage

```bash
obsbot-tiny3 status
obsbot-tiny3 track human
obsbot-tiny3 track off
obsbot-tiny3 home
obsbot-tiny3 nudge left
obsbot-tiny3 ui            # http://127.0.0.1:8765/
```

AI modes: `off`, `human`, `upper`, `closeup`, `headless`, `lower`, `group`, `hand`, `desk`, `whiteboard`.

The UI binds to localhost. ffmpeg copies MJPEG from the capture node; XU ioctls share that same node, so tracking still works while the preview is open.

The camera is exclusive. Stop anything else holding `/dev/videoN` (OBS, lan-share, another preview) first.

## How tracking works

Tiny 3 Lite exposes the Tiny 2 vendor XU:

| | |
|---|---|
| GUID | `{9a1e7291-6843-4683-6d92-39bc7906ee49}` |
| Unit | 2 |
| Selector 6 | 60-byte AI / image status and command block |
| Selector 2 | checksummed V3 mailbox (serial, gimbal frames) |

Selector-6 writes must overlay the command on the **current** status block. Zero-padded 60-byte writes are ignored.

AI command: `[0x16, 0x02, mode, submode]`.

| mode, sub | name |
|---|---|
| 0, 0 | off |
| 1, 0 | group |
| 2, 0 | human (normal) |
| 2, 1 | upper body |
| 2, 2 | close-up |
| 2, 3 | headless |
| 2, 4 | lower body |
| 3, 0 | hand |
| 4, 0 | whiteboard |
| 5, 0 | desk |

Pan/tilt/zoom use standard UVC controls (`pan_absolute`, `tilt_absolute`, `pan_speed`, `tilt_speed`, `zoom_absolute`).

## Credits

Protocol details from [cgevans/tiny2](https://github.com/cgevans/tiny2) and [lxman/obsbot-mcp](https://github.com/lxman/obsbot-mcp). Probed on a Tiny 3 Lite (SN `RMOWUHHC131OXY`).
