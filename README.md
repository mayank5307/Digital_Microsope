# 🔬 OpenFlexure Motorized Raspberry Pi Microscope

An automated, open-source digital microscope powered by a **Raspberry Pi 4B**, high-resolution camera sensor, a high-power microscope objective lens, and 28BYJ-48 stepper motors for precision 3-axis motion control. 

The onboard Flask web server streams low-latency live video over the local network and provides an interactive web dashboard for real-time stage motion (X/Y/Z), live image parameter grading, auto white-balance calibration, and direct frame captures.

---

## 📸 Key Features

- **3-Axis Motorized Control (X, Y, Z):** Fine-grained stage movement and focus adjustment using 28BYJ-48 stepper motors driven via ULN2003 drivers. Supports discrete stepping (1x / 4x stride) and variable RPM.
- **Local Network Web Dashboard:** Lightweight Flask web application accessible from any browser on the local subnet (e.g., `http://<raspberry-pi-ip>:5000`).
- **Low-Latency Streaming:** Hardware-accelerated H.264/RTSP stream via `rpicam-vid` and MediaMTX.
- **Live Image Adjustments:** In-browser sliders for 12 image parameters:
  - Brightness, Contrast, Exposure, Sharpness, Saturation
  - Highlights, Shadows, Whites, Blacks
  - Clarity, Dehaze, and Vignette
- **Microscopy Calibration:**
  - Automated White Balance (AWB) background calibration to neutralize condenser/LED tint.
  - Flat-Field Lens Shading Table (LST) compensation.
- **Direct Snapshot Capture:** One-click full-resolution image capture downloaded directly to the client browser.

---

## 🛠️ Hardware Components

| Component | Description |
| :--- | :--- |
| **SBC** | Raspberry Pi 4 Model B (Raspberry Pi OS Bookworm) |
| **Camera Module** | High-resolution Pi Camera / 12MP Arducam |
| **Objective Lens** | Magnus 40X / 0.65 NA Achromatic Objective |
| **Microscope Body** | 3D Printed Flexure Stage (OpenFlexure compatible) |
| **Actuators** | 3x 28BYJ-48 5V Stepper Motors (X, Y, Z axes) |
| **Motor Drivers** | 3x ULN2003 Driver Boards |
| **Illumination** | Under-stage white LED with diffuser / condenser |

---

## 🔌 GPIO Pin Configuration

The default pin mappings configured in the motor driver class:

| Axis | IN1 | IN2 | IN3 | IN4 |
| :---: | :---: | :---: | :---: | :---: |
| **X-Axis** | GPIO 17 | GPIO 27 | GPIO 22 | GPIO 23 |
| **Y-Axis** | GPIO 24 | GPIO 25 | GPIO 20 | GPIO 21 |
| **Z-Axis (Focus)** | GPIO 5 | GPIO 6 | GPIO 12 | GPIO 13 |

*Note: Uses BCM pin numbering with `gpiozero` and `lgpio`.*

---

## 💻 Software Prerequisites

Ensure your Raspberry Pi is updated and has the required media libraries installed:

```bash
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-opencv \
    python3-numpy \
    libcamera-tools \
    python3-picamera2
```

Install Python dependencies:

```bash
pip3 install flask gpiozero lgpio
```

Download and configure **MediaMTX** (RTSP media server) in your project directory:

```bash
wget [https://github.com/bluenviron/mediamtx/releases/latest/download/mediamtx_linux_arm64v8.tar.gz](https://github.com/bluenviron/mediamtx/releases/latest/download/mediamtx_linux_arm64v8.tar.gz)
tar -xvzf mediamtx_linux_arm64v8.tar.gz
chmod +x mediamtx
```

---

## 🚀 Getting Started

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/<your-username>/raspberry-pi-microscope.git
   cd raspberry-pi-microscope
   ```

2. **Start the Microscope Web Server:**
   ```bash
   python3 host.py
   ```
   *(Or run `python3 web.py` depending on your entry script filename).*

3. **Check Terminal Output:**
   Upon launch, the script:
   - Performs white background sensor auto-calibration.
   - Spawns the MediaMTX RTSP pipeline and starts video broadcast via `rpicam-vid`.
   - Displays the active LAN IP in the terminal:
     ```text
     * Running on all addresses (0.0.0.0)
     * Running on http://127.0.0.1:5000
     * Running on http://192.168.x.x:5000
     ```

4. **Access the Dashboard:**
   Open any web browser on a device connected to the same Wi-Fi/LAN and navigate to:
   ```text
   http://<RPi_IP_ADDRESS>:5000
   ```

---

## 🖥️ Web Interface Overview

- **Live Stream Preview:** Real-time low-latency video feed of the microscope slide.
- **Stage Navigation:**
  - **X/Y Controls:** Directional keypad with speed presets (1x, 4x) and customizable RPM steps.
  - **Z-Axis Controls:** Dedicated **Z+ (Up)** and **Z- (Down)** buttons for fine optical focusing.
- **Image Processing Panel:** Real-time CSS and canvas-based shaders for contrast, sharpness, and brightness enhancement.
- **Calibration & Capture:**
  - **Auto Calib (White BG):** Recalculates white-balance gains against background light.
  - **Capture Image:** Triggers a full-quality capture saved to the client machine.
