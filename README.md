# 🔬 Raspberry Pi Web-Controlled Motorized Microscope

A DIY digital microscope built around a **Raspberry Pi 4B**. The Pi streams the live microscope view to a web dashboard and drives three stepper motors, so anyone on the same network can move along **X, Y and Z** from a browser, with no software to install on the client.

| Hardware | Web dashboard |
|---|---|
| ![Microscope hardware](docs/hardware.jpg) | ![Web dashboard](docs/dashboard.jpg) |

## ✨ Features

- **Live video** at 1280×720, 30 fps, using `rpicam-vid` and [MediaMTX](https://github.com/bluenviron/mediamtx) (WebRTC in the browser, RTSP for players such as VLC)
- **3-axis motor control** from the browser
  - Click for a fixed number of steps, or hold (over 250 ms) to move continuously
  - 1× and 4× modes on X and Y
  - Adjustable step count and RPM for each axis
- **Image capture** that downloads a timestamped JPEG to your computer
- **Capture adjustments** for brightness, contrast, sharpness and saturation
- **White-background calibration** that estimates red and blue white-balance gains (experimental, see [Known limitations](#-known-limitations))
- **Automatic IP display**: the server prints its address in the terminal on startup

## 🧰 Hardware

| Component | Details |
|---|---|
| Computer | Raspberry Pi 4B |
| Camera | Raspberry Pi CSI camera (OV5647, 5 MP, in the demo build) |
| Objective | Magnus 40×, NA 0.65 |
| Motors | 3 × 28BYJ-48 stepper motors (X, Y, Z) |
| Motor drivers | 3 × ULN2003 driver boards |
| Structure | 3D-printed microscope body and stage |
| Other | Light source, prepared slide, power supply |

## 🔌 Wiring

Pin numbers in the code use **BCM** numbering.

| Axis | Driver input | BCM GPIO | Physical pin |
|---|---|---|---|
| X | IN1, IN2, IN3, IN4 | 17, 27, 22, 23 | 11, 13, 15, 16 |
| Y | IN1, IN2, IN3, IN4 | 24, 25, 20, 21 | 18, 22, 38, 40 |
| Z | IN1, IN2, IN3, IN4 | 5, 6, 12, 13 | 29, 31, 32, 33 |

> ⚠️ Power the motors from a suitable external 5 V supply and connect its ground to the Pi's ground.

## 🏗️ How it works

```
Camera ──► rpicam-vid ──► MediaMTX ──► WebRTC :8889 ──► Browser (live view)
                              └──────► RTSP   :8554 ──► VLC / image capture

Browser ──► Flask :5000 ──► gpiozero (lgpio) ──► ULN2003 ──► X / Y / Z motors
```

## 🚀 Installation

Requires Raspberry Pi OS **Bookworm** or later with the camera connected and detected.

**1. Install dependencies**

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-flask python3-opencv \
                    python3-numpy python3-gpiozero python3-lgpio rpicam-apps
```

**2. Check the camera**

```bash
rpicam-hello --list-cameras
```

**3. Clone this repository**

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

**4. Add MediaMTX**

Download the Linux ARM64 build from the [MediaMTX releases page](https://github.com/bluenviron/mediamtx/releases) (use the ARMv7 build on a 32-bit OS). Extract the `mediamtx` binary and `mediamtx.yml` into the project folder.

```bash
chmod +x mediamtx
```

**5. Run the server**

```bash
python3 host.py
```

Run it from the project folder, because the script starts `./mediamtx` by relative path. It also uses `sudo pkill` to restart the camera processes, so your user needs `sudo` rights (passwordless is recommended).

## 🖥️ Usage

1. After startup the terminal shows:
   ```
   Server active: http://<pi-ip>:5000
   ```
2. On any device connected to the same network, open that address in a browser.
3. The live microscope view appears with the controls beside it.
4. Use the X/Y pad to move across the slide and the Z buttons to focus.
5. Click **Capture Image** to download the current frame.

| Service | Address |
|---|---|
| Dashboard | `http://<pi-ip>:5000` |
| WebRTC stream | `http://<pi-ip>:8889/cam` |
| RTSP stream | `rtsp://<pi-ip>:8554/cam` |

### Default motion settings

| Axis | Steps | RPM |
|---|---|---|
| X | 500 | 5 |
| Y | 500 | 5 |
| Z | 100 | 5 |

4× mode moves four times the entered step count at a higher speed. The code's minimum step delay limits 1× speed to about 12 RPM.

## 🎛️ Image controls

All sliders range from −10 to +10.

| Control | Status |
|---|---|
| Brightness, Contrast, Sharpness, Saturation | Applied to **captured images** |
| Exposure, Highlights, Shadows, Whites, Blacks, Clarity, Dehaze, Vignette | In the interface, processing not yet implemented |

The sliders do not currently change the live stream.

## 📡 API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| POST | `/jog?axis=X&steps=500&rpm=5&mult=1` | Move a fixed number of steps (negative reverses) |
| POST | `/continuous_start?axis=X&dir=1&rpm=5&mult=1` | Start continuous movement |
| POST | `/continuous_stop?axis=X` | Stop continuous movement |
| GET | `/get_params` | Read image settings |
| POST | `/update_param?key=brightness&val=3` | Change an image setting |
| POST | `/reset_params` | Reset image settings to 0 |
| GET, POST | `/capture` | Download a JPEG |
| POST | `/calibrate_openflexure` | Calibrate and save `custom_calibration.json` |
| POST | `/calibrate_awb` | Recalculate white-balance gains |
| POST | `/calibrate_ffc` | Same routine as `/calibrate_openflexure` |
| POST | `/restart_stream` | Restart the camera stream |

## ⚠️ Known limitations

- **Calibration is experimental.** The raw-frame processing was written for the IMX219 (Camera v2.1). On the OV5647, the packed 10-bit raw data and different Bayer order are not handled, so the calculated gains may be inaccurate.
- The lens shading table is saved to `custom_calibration.json` but not yet applied to the stream or captures.
- Captures come from the 720p video stream, not the sensor's full resolution.
- There are no limit switches or software travel limits. Watch the objective when moving Z to avoid hitting the slide.
- There is **no authentication**. Anyone who can reach the Pi can control the microscope, so use it only on a trusted network and do not expose it to the internet.
- Flask's development server is used.

## 🗺️ Roadmap

- [ ] Apply image sliders to the live view
- [ ] Implement the remaining image controls
- [ ] Fix raw calibration for the OV5647 and apply lens shading correction
- [ ] Full-resolution still capture
- [ ] Travel limits and login protection
