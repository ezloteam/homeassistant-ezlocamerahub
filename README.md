# Ezlo Camera Hub — Home Assistant integration

A Home Assistant **custom integration** (`custom_components/ezlo_camerahub`) that turns Home Assistant into
an Ezlo Camera Hub. It runs the **`ha-camerasd`** engine — the same engine the Android / macOS / Windows /
Linux Camera Hub clients embed — as a supervised child process, registers it with your Ezlo account, and
records your cameras to the Ezlo cloud. Cameras, recording mode and upload state show up as Home Assistant
entities.

## How it runs

- **Engine binary:** each integration release pins one engine version and the SHA-256 of its static Linux
  build (x86_64 and aarch64 musl), published at `https://dl.mios.com/linux_firmware/build/custom_packages/ha-camerasd/standalone/<tag>/`. It's
  downloaded once into `.storage/ezlo_camerahub/bin/` and its hash is checked before every start.
- **Process:** started when the integration loads and stopped when it unloads or Home Assistant stops.
  - It listens only on a loopback port the integration picks, so its control plane is never on your network.
  - It keeps its identity and cameras in `.storage/ezlo_camerahub/<entry>/engine-cache/`.
  - It gets a clean environment.
  - It's restarted with backoff if it exits, and a repair issue appears if it keeps stopping.
  - An engine left running on the same cache after Home Assistant was killed is stopped first.
- **Identity:** setup asks for your Ezlo username and password and checks them with Ezlo. The engine
  registers a new Camera Hub on that account (its first provision), and from then on boots from its cache.
  The password is removed from Home Assistant as soon as registration succeeds. If Ezlo rejects it, or the
  engine ever needs to register again, Home Assistant asks you to sign in again.
- **Removing the integration** deletes the engine's cache (its identity and camera list).

Supported: Home Assistant OS, Container or Core on 64-bit Linux (x86_64 or ARM). Other systems are refused
at setup.

## Cameras

Add cameras from the integration's page with **Add camera**:
- **Stream URL:** use the camera's **H.264 sub stream**, for example Reolink `rtsp://<ip>:554/Preview_01_sub`.
  Main streams are often H.265, which the engine can't ingest and most browsers can't play.
- **Credentials:** a username and password go in their own fields. Credentials pasted into the URL are moved
  into those fields.
- **Transport:** automatic / TCP / UDP. Use TCP behind NAT.
- **Recording mode:** continuous or motion.

Each camera is a config subentry, so it can be edited (**Reconfigure**) or deleted on its own. The
subentries are the source of truth: the integration keeps the engine's camera list in line with them, and
re-adds a camera if the engine ever loses it.

Camera passwords are stored in Home Assistant's config entry storage (as for any device credential), shown
as password fields, never prefilled when editing, and never put in logs, entity attributes or diagnostics.

## Entities

Per camera (one device per camera, linked to the hub device):

| Entity | What it shows |
|---|---|
| `camera` | The camera's own RTSP stream (see below), snapshots via ffmpeg. Attributes: `codec`, `stream` (address with credentials removed). |
| `select` Recording mode | `continuous` / `motion` via `POST /app/standalone/cameras/{id}/mode`. Only created when the engine reports `capabilities.motion`; `motion` is offered only for cameras with `motion_capable`. |
| `sensor` Recording mode | `continuous` / `motion`. |
| `sensor` Status | `offline` (RTSP ingest down) / `online` / `recording` (continuous mode with live ingest; a motion-mode camera is `online`) / `armed` (motion mode, waiting for motion: the engine opens the stream only on motion, and the camera's ONVIF event service answers). |
| `sensor` Segments uploaded, Upload errors, Last upload | Per-camera counters from `/status` `uploads.per_camera`. The engine uploads each recording as HLS segments of about 4 s and counts every segment (`clips_ok`), so a 60 s clip adds about 15. |
| `sensor` Last clip source | `motion` / `continuous` of the newest clip in `/uploads`. That ring is global and bounded (ha-camerasd #33), so a busy continuous camera can push a motion camera's clips out; Segments uploaded is the authoritative counter. |

Hub device: Cloud connection (NMA), Uploads done / pending / failed.

Motion (only on engines that report `motion_active` / `last_motion_ts` per camera in `/status`; older
engines get no motion entities, and they appear without a reload once the engine is upgraded):

| Entity | What it shows |
|---|---|
| `binary_sensor` Motion | On while the camera's own ONVIF motion source reports motion (`motion_active`). A trip that starts and ends between two 5 s polls is still caught from `last_motion_ts` advancing, and shows as on for one poll. |
| `sensor` Last motion | Start time of the camera's last motion (`last_motion_ts`). |

The engine's `recording` flag is not used for motion: it means "recorder armed" and stays `true` between
motion windows.

## Live view

Camera Hub live view is cloud-only WebRTC over NMA; the engine serves no local player stream. The
`camera` entity therefore streams the RTSP URL the camera was added to the hub with, straight from the
camera on your LAN. Add cameras to the hub with their **H.264 sub stream** (for example Reolink
`Preview_01_sub`): main streams are often H.265, which the hub can't ingest and most browsers can't play.
Cameras the hub reaches over interleaved TCP (`transport: tcp`) are streamed over TCP by Home Assistant too.

RTSP URLs carry the camera's credentials (percent-encoded). The integration passes them to Home Assistant's
stream and ffmpeg exactly as stored on the engine (no re-encoding) and never puts them in state attributes, logs,
model `repr`s or diagnostics.

## Installation

1. In HACS, add `https://github.com/ezloteam/homeassistant-ezlocamerahub` as a custom repository
   (category: Integration) and install **Ezlo Camera Hub**, or copy `custom_components/ezlo_camerahub`
   into your Home Assistant `config/custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Ezlo Camera Hub**, and sign in with your Ezlo account.
4. On the integration's page, use **Add camera** for each camera.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/python -m pytest --cov
.venv/bin/ruff check custom_components tests && .venv/bin/ruff format --check custom_components tests
```
