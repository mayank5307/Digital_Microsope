import json
import subprocess
import threading
import time
import os
import io
import cv2
import numpy as np
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, send_file
from gpiozero import OutputDevice, Device
from gpiozero.pins.lgpio import LGPIOFactory

# Set default GPIO pin factory for RPi 4B / OS Bookworm
Device.pin_factory = LGPIOFactory()

# Global Image Processing Parameters (-10 to +10 range)
image_edit_params = {
    "brightness": 0,
    "sharpness": 0,
    "exposure": 0,
    "contrast": 0,
    "saturation": 0,
    "vignette": 0,
    "highlights": 0,
    "shadows": 0,
    "whites": 0,
    "blacks": 0,
    "clarity": 0,
    "dehaze": 0
}

# ==============================================================================
# --- RPI CAMERA V2.1 (IMX219) UNIFORM WHITE BACKGROUND & LST ALGORITHMS ---
# ==============================================================================

def calculate_white_background_calibration(raw_bayer):
    b  = raw_bayer[0::2, 0::2].astype(float)
    g1 = raw_bayer[0::2, 1::2].astype(float)
    g2 = raw_bayer[1::2, 0::2].astype(float)
    r  = raw_bayer[1::2, 1::2].astype(float)

    g = (g1 + g2) / 2.0

    brightness_map = g
    bright_threshold = np.percentile(brightness_map, 90)
    bg_mask = brightness_map >= bright_threshold

    mean_r_bg = np.mean(r[bg_mask])
    mean_g_bg = np.mean(g[bg_mask])
    mean_b_bg = np.mean(b[bg_mask])

    red_gain = float(mean_g_bg / np.clip(mean_r_bg, 1e-5, None))
    blue_gain = float(mean_g_bg / np.clip(mean_b_bg, 1e-5, None))

    grid_w, grid_h = 64, 48
    r_grid = cv2.resize(r.astype(np.float32), (grid_w, grid_h), interpolation=cv2.INTER_AREA)
    g_grid = cv2.resize(g.astype(np.float32), (grid_w, grid_h), interpolation=cv2.INTER_AREA)
    b_grid = cv2.resize(b.astype(np.float32), (grid_w, grid_h), interpolation=cv2.INTER_AREA)

    blur_kernel = (7, 7)
    r_grid = cv2.GaussianBlur(r_grid, blur_kernel, 0)
    g_grid = cv2.GaussianBlur(g_grid, blur_kernel, 0)
    b_grid = cv2.GaussianBlur(b_grid, blur_kernel, 0)

    r_ref = np.percentile(r_grid, 98)
    g_ref = np.percentile(g_grid, 98)
    b_ref = np.percentile(b_grid, 98)

    r_ls = r_ref / np.clip(r_grid, 1.0, None)
    g_ls = g_ref / np.clip(g_grid, 1.0, None)
    b_ls = b_ref / np.clip(b_grid, 1.0, None)

    r_ls *= (red_gain / max(red_gain, blue_gain, 1.0))
    b_ls *= (blue_gain / max(red_gain, blue_gain, 1.0))

    ls_table = np.clip(np.stack([r_ls, g_ls, b_ls], axis=-1), 1.0, 4.0)

    return red_gain, blue_gain, ls_table


def openflexure_recalibrate():
    from picamera2 import Picamera2

    print("[OpenFlexure] Capturing IMX219 raw frame for Full White Calibration...")
    picam2 = Picamera2()
    
    config = picam2.create_still_configuration(
        main={"size": (1920, 1080)},
        raw={"size": (3280, 2464)}
    )
    picam2.configure(config)
    picam2.start()
    
    raw_bayer = picam2.capture_array("raw")
    picam2.stop()
    picam2.close()

    red_gain, blue_gain, ls_table = calculate_white_background_calibration(raw_bayer)

    calibration_data = {
        "calibration_type": "Pi Camera v2.1 (IMX219) Full White Background Calibration",
        "timestamp": datetime.now().isoformat(),
        "camera_settings": {
            "awb_gains": {
                "red": red_gain,
                "blue": blue_gain
            },
            "exposure_setpoint": 230,
            "lens_shading_table": ls_table.tolist()
        }
    }

    with open("custom_calibration.json", "w") as f:
        json.dump(calibration_data, f, indent=4)

    return red_gain, blue_gain


def run_awb_calibration():
    from picamera2 import Picamera2
    
    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": (1920, 1080)}, 
        raw={"size": (3280, 2464)}
    )
    picam2.configure(config)
    picam2.start()
    raw_bayer = picam2.capture_array("raw")
    picam2.stop()
    picam2.close()

    rg, bg, _ = calculate_white_background_calibration(raw_bayer)
    return rg, bg


def run_flat_field_calibration():
    return openflexure_recalibrate()


# ==============================================================================
# --- STEPPER MOTOR DRIVER ---
# ==============================================================================

class BYJ48Motor:
    def __init__(self, pins):
        self.devices = []
        for p in pins:
            try:
                self.devices.append(OutputDevice(p))
            except Exception as e:
                print(f"Pin {p} init warning: {e}")

        self.step_sequence = [
            [1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
            [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]
        ]
        self.is_moving_continuous = False
        self.continuous_thread = None

    def move_steps(self, target_steps, direction_positive, rpm=5, mult=1):
        seq = self.step_sequence if direction_positive else list(reversed(self.step_sequence))
        
        base_delay = max(0.0012, 60.0 / (4096.0 * max(1.0, float(rpm))))
        step_increment = 2 if mult == 4 else 1
        adjusted_delay = base_delay / 2.0 if mult == 4 else base_delay

        seq_len = len(seq)
        i = 0
        steps_done = 0

        while steps_done < target_steps:
            pattern = seq[i % seq_len]
            for dev, val in zip(self.devices, pattern):
                dev.value = val
            i += step_increment
            steps_done += step_increment
            time.sleep(adjusted_delay)

        self.stop()

    def start_continuous(self, direction_positive, rpm=5, mult=1):
        self.stop()
        self.is_moving_continuous = True

        def run_loop():
            seq = self.step_sequence if direction_positive else list(reversed(self.step_sequence))
            
            base_delay = max(0.0012, 60.0 / (4096.0 * max(1.0, float(rpm))))
            step_stride = 2 if mult == 4 else 1
            adjusted_delay = base_delay / 2.0 if mult == 4 else base_delay

            seq_len = len(seq)
            i = 0

            while self.is_moving_continuous:
                pattern = seq[i % seq_len]
                for dev, val in zip(self.devices, pattern):
                    dev.value = val
                i += step_stride
                time.sleep(adjusted_delay)
            
            for dev in self.devices:
                dev.off()

        self.continuous_thread = threading.Thread(target=run_loop, daemon=True)
        self.continuous_thread.start()

    def stop(self):
        self.is_moving_continuous = False
        if self.continuous_thread and self.continuous_thread.is_alive():
            self.continuous_thread.join(timeout=0.2)
        for dev in self.devices:
            dev.off()

    def close(self):
        self.stop()
        for dev in self.devices:
            dev.close()


try:
    motors = {
        'X': BYJ48Motor(pins=[17, 27, 22, 23]),
        'Y': BYJ48Motor(pins=[24, 25, 20, 21]),
        'Z': BYJ48Motor(pins=[5, 6, 12, 13])
    }
except Exception as err:
    print(f"Motor Initialization Warning: {err}")
    motors = {}

current_awb_gains = None

# --- Camera Pipeline Control ---
def start_pi_camera():
    global current_awb_gains
    subprocess.run(["sudo", "pkill", "-f", "rpicam-vid"], stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "pkill", "-f", "mediamtx"], stderr=subprocess.DEVNULL)
    time.sleep(0.5)

    subprocess.Popen(["./mediamtx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    cmd = [
        "rpicam-vid", "-n", "-t", "0",
        "--width", "1280", "--height", "720",
        "--framerate", "30",
        "--inline", "--intra", "1",
        "--codec", "libav", "--libav-format", "rtsp"
    ]

    if current_awb_gains:
        cmd.extend(["--awb", "custom", "--awbgains", current_awb_gains])

    cmd.extend(["-o", "rtsp://127.0.0.1:8554/cam"])
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


try:
    print("Performing Pi Camera v2.1 White Background Auto Calibration...")
    rg, bg = run_awb_calibration()
    current_awb_gains = f"{rg:.2f},{bg:.2f}"
except Exception as e:
    print(f"Startup Calibration Warning: {e}")

start_pi_camera()

# ==============================================================================
# --- FLASK DASHBOARD WITH SIDE-BY-SIDE REAL-TIME PARAMETER PANEL ---
# ==============================================================================

app = Flask(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Raspberry Pi 4B Web Control Center</title>
    <style>
        body { background-color: #1e1e1e; color: #ffffff; font-family: Arial, sans-serif; margin: 0; padding: 0; }
        header { background-color: #2d2d2d; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #333; }
        .ip-info { color: #00e676; font-family: monospace; font-size: 13px; font-weight: bold; }
        .main-container { display: flex; flex-wrap: wrap; gap: 15px; padding: 15px; max-width: 1500px; margin: 0 auto; }
        
        .video-panel { flex: 1 1 500px; display: flex; flex-direction: column; gap: 10px; }
        .preview-bar { background-color: #2d2d2d; padding: 8px 12px; border-radius: 6px; display: flex; align-items: center; justify-content: space-between; font-weight: bold; }
        .video-frame { background-color: #000000; border-radius: 8px; overflow: hidden; width: 100%; aspect-ratio: 16 / 9; display: flex; justify-content: center; align-items: center; }
        .video-frame iframe { width: 100%; height: 100%; border: none; }
        
        .edit-side-panel { flex: 0 0 320px; background-color: #2d2d2d; border-radius: 8px; padding: 12px; box-sizing: border-box; display: flex; flex-direction: column; gap: 10px; height: fit-content; border: 1px solid #3a3a3a; }
        .edit-header { font-weight: bold; font-size: 14px; color: #ff9800; border-bottom: 1px solid #444; padding-bottom: 6px; display: flex; justify-content: space-between; align-items: center; }
        .slider-grid { display: flex; flex-direction: column; gap: 8px; }
        .slider-row { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
        .slider-row label { width: 85px; font-weight: bold; }
        .slider-row input[type="range"] { flex: 1; margin: 0 8px; accent-color: #00e676; cursor: pointer; }
        .slider-val { width: 25px; text-align: right; font-family: monospace; font-weight: bold; color: #00e676; }
        .btn-reset-side { background: #f44336; border:none; padding: 6px 12px; border-radius:4px; color:white; font-weight:bold; cursor:pointer; font-size: 11px; margin-top: 5px; }
        .btn-reset-side:hover { background: #d32f2f; }

        .control-panel { flex: 0 0 340px; background-color: #2d2d2d; border-radius: 8px; padding: 15px; box-sizing: border-box; display: flex; flex-direction: column; gap: 12px; border: 1px solid #3a3a3a; }
        .section-box { border: 1px solid #424242; border-radius: 6px; padding: 10px; background-color: #252525; }
        .section-title { font-weight: bold; font-size: 12px; display: block; margin-bottom: 6px; }
        .input-row { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
        .input-row label { font-weight: bold; font-size: 12px; }
        .input-row input[type="number"] { width: 65px; padding: 4px; background-color: #1e1e1e; color: #ffffff; border: 1px solid #424242; border-radius: 4px; text-align: center; font-weight: bold; }
        
        .joystick-container { display: grid; grid-template-columns: 95px 90px 95px; grid-template-rows: auto auto auto; gap: 4px; align-items: center; justify-items: center; margin-top: 8px; }
        .joystick-center { color: #888; font-weight: bold; font-size: 13px; text-align: center; }
        .joy-branch { display: flex; flex-direction: column; align-items: center; width: 100%; }
        .branch-title { font-size: 10px; font-weight: bold; color: #2979ff; margin: 2px 0; }

        .segmented-btn-v { display: flex; flex-direction: column; width: 75px; border-radius: 6px; overflow: hidden; border: 1px solid #555; background-color: #333; }
        .segmented-btn-h { display: flex; flex-direction: row; width: 90px; border-radius: 6px; overflow: hidden; border: 1px solid #555; background-color: #333; }
        .part { background-color: #424242; color: #ffffff; border: none; padding: 7px 0; font-weight: bold; font-size: 11px; cursor: pointer; text-align: center; flex: 1; transition: background 0.15s; user-select: none; -webkit-user-select: none; }
        .segmented-btn-v .part:not(:last-child) { border-bottom: 1px solid #555; }
        .segmented-btn-h .part:not(:last-child) { border-right: 1px solid #555; }
        .part:hover { background-color: #616161; }
        .part:active { background-color: #00e676; color: #000; }

        .btn-capture { background-color: #e91e63; width: 100%; margin-top: 6px; font-size: 13px; padding: 9px 0; border:none; border-radius:4px; color:white; font-weight:bold; cursor:pointer; }
        .btn-capture:hover { background-color: #c2185b; }

        .calib-btn-group { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
        .calib-row-2 { display: flex; gap: 6px; }
        .btn-autocal { background-color: #2979ff; flex: 1; padding: 8px 0; border:none; border-radius:4px; color:white; font-weight:bold; font-size:11px; cursor:pointer; }
        .btn-autocal:hover { background-color: #1565c0; }
        .btn-ffc { background-color: #9c27b0; flex: 1; padding: 8px 0; border:none; border-radius:4px; color:white; font-weight:bold; font-size:11px; cursor:pointer; }
        .btn-ffc:hover { background-color: #7b1fa2; }
        .btn-openflexure { background-color: #00b0ff; width: 100%; padding: 9px 0; border:none; border-radius:4px; color:black; font-weight:bold; font-size:11px; cursor:pointer; }
        .btn-openflexure:hover { background-color: #0091ea; }
        .btn-z { background-color: #ff9800; width: 100%; margin-top: 4px; padding: 8px 0; border:none; border-radius:4px; color:white; font-weight:bold; cursor:pointer; user-select:none; -webkit-user-select: none; }
        .btn-z:hover { background-color: #f57c00; }
        .status-text { color: #00e676; font-style: italic; font-size: 12px; text-align: center; margin-top: 6px; }
    </style>
</head>
<body>
    <header>
        <span style="font-weight: bold; font-size: 16px;">Raspberry Pi 4B Camera v2.1 & Stepper Web Center</span>
        <span class="ip-info">RTSP: rtsp://{{ pi_ip }}:8554/cam | HTTP: http://{{ pi_ip }}:8889/cam</span>
    </header>

    <div class="main-container">
        <!-- Center Live Preview -->
        <div class="video-panel">
            <div class="preview-bar">
                <label><input type="checkbox" id="chk-preview" checked onchange="togglePreview()"> Live Stream Preview</label>
            </div>
            <div class="video-frame" id="video-container">
                <iframe id="video-iframe" src="http://{{ pi_ip }}:8889/cam"></iframe>
            </div>
        </div>

        <!-- Side-by-Side Live Parameters Drawer -->
        <div class="edit-side-panel">
            <div class="edit-header">
                <span>🎛️ Live Image Parameters</span>
                <button class="btn-reset-side" onclick="resetEditParams()">Reset 0</button>
            </div>
            
            <div class="slider-grid">
                <div class="slider-row"><label>Brightness:</label><input type="range" id="param_brightness" min="-10" max="10" value="0" oninput="updateParam('brightness', this.value)"><span class="slider-val" id="val_brightness">0</span></div>
                <div class="slider-row"><label>Sharpness:</label><input type="range" id="param_sharpness" min="-10" max="10" value="0" oninput="updateParam('sharpness', this.value)"><span class="slider-val" id="val_sharpness">0</span></div>
                <div class="slider-row"><label>Exposure:</label><input type="range" id="param_exposure" min="-10" max="10" value="0" oninput="updateParam('exposure', this.value)"><span class="slider-val" id="val_exposure">0</span></div>
                <div class="slider-row"><label>Contrast:</label><input type="range" id="param_contrast" min="-10" max="10" value="0" oninput="updateParam('contrast', this.value)"><span class="slider-val" id="val_contrast">0</span></div>
                <div class="slider-row"><label>Saturation:</label><input type="range" id="param_saturation" min="-10" max="10" value="0" oninput="updateParam('saturation', this.value)"><span class="slider-val" id="val_saturation">0</span></div>
                <div class="slider-row"><label>Highlights:</label><input type="range" id="param_highlights" min="-10" max="10" value="0" oninput="updateParam('highlights', this.value)"><span class="slider-val" id="val_highlights">0</span></div>
                <div class="slider-row"><label>Shadows:</label><input type="range" id="param_shadows" min="-10" max="10" value="0" oninput="updateParam('shadows', this.value)"><span class="slider-val" id="val_shadows">0</span></div>
                <div class="slider-row"><label>Whites:</label><input type="range" id="param_whites" min="-10" max="10" value="0" oninput="updateParam('whites', this.value)"><span class="slider-val" id="val_whites">0</span></div>
                <div class="slider-row"><label>Blacks:</label><input type="range" id="param_blacks" min="-10" max="10" value="0" oninput="updateParam('blacks', this.value)"><span class="slider-val" id="val_blacks">0</span></div>
                <div class="slider-row"><label>Clarity:</label><input type="range" id="param_clarity" min="-10" max="10" value="0" oninput="updateParam('clarity', this.value)"><span class="slider-val" id="val_clarity">0</span></div>
                <div class="slider-row"><label>Dehaze:</label><input type="range" id="param_dehaze" min="-10" max="10" value="0" oninput="updateParam('dehaze', this.value)"><span class="slider-val" id="val_dehaze">0</span></div>
                <div class="slider-row"><label>Vignette:</label><input type="range" id="param_vignette" min="-10" max="10" value="0" oninput="updateParam('vignette', this.value)"><span class="slider-val" id="val_vignette">0</span></div>
            </div>
        </div>

        <!-- Controls Section -->
        <div class="control-panel">
            <div class="section-box">
                <span class="section-title" style="color: #00e676;">Step Inputs & Speed (RPM)</span>
                
                <div class="input-row">
                    <label style="color: #2979ff;">X Step / RPM:</label>
                    <div>
                        <input type="number" id="step_x" value="500">
                        <input type="number" id="rpm_x" value="5">
                    </div>
                </div>

                <div class="input-row">
                    <label style="color: #2979ff;">Y Step / RPM:</label>
                    <div>
                        <input type="number" id="step_y" value="500">
                        <input type="number" id="rpm_y" value="5">
                    </div>
                </div>

                <div class="input-row">
                    <label style="color: #ffb74d;">Z Step / RPM:</label>
                    <div>
                        <input type="number" id="step_z" value="100">
                        <input type="number" id="rpm_z" value="5">
                    </div>
                </div>
            </div>

            <div class="section-box">
                <span class="section-title" style="color: #2979ff;">X / Y Joystick Controls</span>
                <div class="joystick-container">
                    <div></div>
                    <div class="joy-branch">
                        <span class="branch-title">Y+ ▲</span>
                        <div class="segmented-btn-v">
                            <button class="part move-btn" data-axis="Y" data-dir="1" data-mult="4">4x</button>
                            <button class="part move-btn" data-axis="Y" data-dir="1" data-mult="1">1x</button>
                        </div>
                    </div>
                    <div></div>

                    <div class="joy-branch">
                        <span class="branch-title">◄ X-</span>
                        <div class="segmented-btn-h">
                            <button class="part move-btn" data-axis="X" data-dir="1" data-mult="4">4x</button>
                            <button class="part move-btn" data-axis="X" data-dir="1" data-mult="1">1x</button>
                        </div>
                    </div>

                    <div class="joystick-center">X / Y</div>

                    <div class="joy-branch">
                        <span class="branch-title">X+ ►</span>
                        <div class="segmented-btn-h">
                            <button class="part move-btn" data-axis="X" data-dir="-1" data-mult="1">1x</button>
                            <button class="part move-btn" data-axis="X" data-dir="-1" data-mult="4">4x</button>
                        </div>
                    </div>

                    <div></div>
                    <div class="joy-branch">
                        <div class="segmented-btn-v">
                            <button class="part move-btn" data-axis="Y" data-dir="-1" data-mult="1">1x</button>
                            <button class="part move-btn" data-axis="Y" data-dir="-1" data-mult="4">4x</button>
                        </div>
                        <span class="branch-title">Y- ▼</span>
                    </div>
                    <div></div>
                </div>
            </div>

            <div class="section-box">
                <span class="section-title" style="color: #ffb74d;">Z Axis Controls</span>
                <button class="btn-z move-btn" data-axis="Z" data-dir="1" data-mult="1">Z+ ▲ (Up)</button>
                <button class="btn-z move-btn" style="background-color:#f57c00;" data-axis="Z" data-dir="-1" data-mult="1">Z- ▼ (Down)</button>
            </div>

            <div class="section-box">
                <span class="section-title" style="color: #ffffff;">Capture & Calibration</span>
                <button class="btn-capture" onclick="captureImage()">📸 Capture Image (Download to Windows)</button>
                
                <div class="calib-btn-group">
                    <button class="btn-openflexure" onclick="triggerOpenFlexureCalib()">🔬 OpenFlexure Calibration</button>
                    <div class="calib-row-2">
                        <button class="btn-autocal" onclick="triggerAutoCalib()">✨ Auto Calib (White BG)</button>
                        <button class="btn-ffc" onclick="triggerFFC()">✨ Flat Field (Lighting)</button>
                    </div>
                </div>

                <button class="part" style="width: 100%; margin-top: 6px; padding: 6px 0; border-radius:4px; background:#424242;" onclick="restartStream()">🔄 Refresh Stream</button>
                <div class="status-text" id="status-label">Status: Ready</div>
            </div>
        </div>
    </div>

    <script>
        const piIp = "{{ pi_ip }}";
        let holdTimer = null;
        let isHolding = false;
        let activeAxis = null;

        // Immediate slider parameter push
        function updateParam(key, value) {
            document.getElementById(`val_${key}`).innerText = value;
            fetch(`/update_param?key=${key}&val=${value}`, { method: 'POST' });
        }

        function resetEditParams() {
            const keys = ["brightness", "sharpness", "exposure", "contrast", "saturation", "highlights", "shadows", "whites", "blacks", "clarity", "dehaze", "vignette"];
            keys.forEach(k => {
                if (document.getElementById(`param_${k}`)) {
                    document.getElementById(`param_${k}`).value = 0;
                    document.getElementById(`val_${k}`).innerText = 0;
                }
            });
            fetch('/reset_params', { method: 'POST' });
        }

        function togglePreview() {
            const chk = document.getElementById('chk-preview');
            const container = document.getElementById('video-container');
            if (chk.checked) {
                container.innerHTML = `<iframe id="video-iframe" src="http://${piIp}:8889/cam"></iframe>`;
            } else {
                container.innerHTML = `<div style="color:#888;">Live Preview Paused</div>`;
            }
        }

        document.querySelectorAll('.move-btn').forEach(btn => {
            const handlePressStart = (e) => {
                if (e.type === 'mousedown' && e.button !== 0) return;
                
                e.preventDefault();
                const axis = btn.getAttribute('data-axis');
                const dirSign = parseInt(btn.getAttribute('data-dir'));
                const multiplier = parseInt(btn.getAttribute('data-mult'));
                
                activeAxis = axis;
                isHolding = false;

                let axisKey = axis.toLowerCase();
                let baseSteps = document.getElementById(`step_${axisKey}`).value;
                let baseRpm = document.getElementById(`rpm_${axisKey}`).value;
                let effectiveSteps = baseSteps * multiplier * dirSign;

                holdTimer = setTimeout(() => {
                    isHolding = true;
                    document.getElementById('status-label').innerText = `Status: Continuous Move ${axis} (${multiplier}x Speed)...`;
                    fetch(`/continuous_start?axis=${axis}&dir=${dirSign}&rpm=${baseRpm}&mult=${multiplier}`, { method: 'POST' });
                }, 250);

                btn._pressData = { axis, effectiveSteps, baseRpm, multiplier };
            };

            const handlePressEnd = (e) => {
                if (!activeAxis) return;
                e.preventDefault();
                
                clearTimeout(holdTimer);
                const axis = activeAxis;
                
                if (isHolding) {
                    isHolding = false;
                    document.getElementById('status-label').innerText = `Status: Stopping ${axis}...`;
                    fetch(`/continuous_stop?axis=${axis}`, { method: 'POST' })
                        .then(() => { document.getElementById('status-label').innerText = 'Status: Ready'; });
                } else if (btn._pressData) {
                    const data = btn._pressData;
                    document.getElementById('status-label').innerText = `Status: Moving ${data.axis} (${data.effectiveSteps} steps)...`;
                    fetch(`/jog?axis=${data.axis}&steps=${data.effectiveSteps}&rpm=${data.baseRpm}&mult=${data.multiplier}`, { method: 'POST' })
                        .then(res => res.json())
                        .then(d => { document.getElementById('status-label').innerText = 'Status: Ready'; });
                }
                activeAxis = null;
                btn._pressData = null;
            };

            btn.addEventListener('mousedown', handlePressStart);
            btn.addEventListener('mouseup', handlePressEnd);
            btn.addEventListener('mouseleave', (e) => { if (activeAxis) handlePressEnd(e); });

            btn.addEventListener('touchstart', handlePressStart, { passive: false });
            btn.addEventListener('touchend', handlePressEnd, { passive: false });
        });

        function captureImage() {
            document.getElementById('status-label').innerText = 'Status: Capturing & Downloading...';
            window.location.href = '/capture';
            setTimeout(() => { document.getElementById('status-label').innerText = 'Status: Image Saved to Windows Downloads'; }, 2000);
        }

        function triggerOpenFlexureCalib() {
            document.getElementById('status-label').innerText = 'Status: Running IMX219 Background Calibration...';
            fetch('/calibrate_openflexure', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    alert(`White Background Calibration Complete!\nRed Gain: ${data.red_gain}, Blue Gain: ${data.blue_gain}`);
                    document.getElementById('status-label').innerText = 'Status: Stream Ready';
                    togglePreview();
                })
                .catch(err => { alert('Calibration failed!'); document.getElementById('status-label').innerText = 'Status: Calibration Error'; });
        }

        function triggerAutoCalib() {
            document.getElementById('status-label').innerText = 'Status: Calibrating White Background AWB...';
            fetch('/calibrate_awb', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    alert(`White Background AWB Complete!\nRed Gain: ${data.red_gain}, Blue Gain: ${data.blue_gain}`);
                    document.getElementById('status-label').innerText = 'Status: Stream Ready';
                    togglePreview();
                })
                .catch(err => { alert('Auto Calibration failed!'); document.getElementById('status-label').innerText = 'Status: Calibration Error'; });
        }

        function triggerFFC() {
            document.getElementById('status-label').innerText = 'Status: Calibrating Flat Field Lighting...';
            fetch('/calibrate_ffc', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    alert(`Flat Field Correction Complete!\nRed Gain: ${data.red_gain}, Blue Gain: ${data.blue_gain}`);
                    document.getElementById('status-label').innerText = 'Status: Stream Ready';
                    togglePreview();
                })
                .catch(err => { alert('Flat Field Correction failed!'); document.getElementById('status-label').innerText = 'Status: Calibration Error'; });
        }

        function restartStream() {
            document.getElementById('status-label').innerText = 'Status: Restarting Stream...';
            fetch('/restart_stream', { method: 'POST' })
                .then(res => res.json())
                .then(data => { document.getElementById('status-label').innerText = 'Status: Ready'; togglePreview(); });
        }
    </script>
</body>
</html>
"""

import socket
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

PI_IP_ADDRESS = get_ip()

@app.route('/')
def index():
    return render_template_string(HTML_DASHBOARD, pi_ip=PI_IP_ADDRESS)

@app.route('/get_params', methods=['GET'])
def get_params():
    return jsonify(image_edit_params)

@app.route('/update_param', methods=['POST'])
def update_param():
    key = request.args.get('key', '')
    val = int(request.args.get('val', 0))
    if key in image_edit_params:
        image_edit_params[key] = val
        return jsonify({"status": "success", "key": key, "val": val})
    return jsonify({"status": "error"}), 400

@app.route('/reset_params', methods=['POST'])
def reset_params():
    for k in image_edit_params:
        image_edit_params[k] = 0
    return jsonify({"status": "success"})

@app.route('/jog', methods=['POST'])
def jog():
    axis = request.args.get('axis', '').upper()
    steps = int(request.args.get('steps', 0))
    rpm = float(request.args.get('rpm', 5))
    mult = int(request.args.get('mult', 1))

    if axis in motors:
        direction = steps > 0
        threading.Thread(target=motors[axis].move_steps, args=(abs(steps), direction, rpm, mult), daemon=True).start()
        return jsonify({"status": "success", "axis": axis, "steps": steps, "rpm": rpm, "mult": mult})
    return jsonify({"status": "error", "message": "Invalid axis"}), 400

@app.route('/continuous_start', methods=['POST'])
def continuous_start():
    axis = request.args.get('axis', '').upper()
    direction_sign = int(request.args.get('dir', 1))
    rpm = float(request.args.get('rpm', 5))
    mult = int(request.args.get('mult', 1))

    if axis in motors:
        direction = direction_sign > 0
        motors[axis].start_continuous(direction, rpm, mult)
        return jsonify({"status": "started", "axis": axis, "direction": direction_sign, "rpm": rpm, "mult": mult})
    return jsonify({"status": "error"}), 400

@app.route('/continuous_stop', methods=['POST'])
def continuous_stop():
    axis = request.args.get('axis', '').upper()
    if axis in motors:
        motors[axis].stop()
        return jsonify({"status": "stopped", "axis": axis})
    return jsonify({"status": "error"}), 400

@app.route('/capture', methods=['GET', 'POST'])
def capture():
    try:
        filename = datetime.now().strftime("capture_%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"

        cap = cv2.VideoCapture("rtsp://127.0.0.1:8554/cam", cv2.CAP_FFMPEG)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(5):
                ret, frame = cap.read()
            if ret and frame is not None:
                p = image_edit_params
                
                alpha = 1.0 + (p["contrast"] * 0.05)
                beta = p["brightness"] * 10
                frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)

                if p["sharpness"] != 0:
                    k = p["sharpness"] * 0.1
                    kernel = np.array([[0, -k, 0], [-k, 1 + 4*k, -k], [0, -k, 0]])
                    frame = cv2.filter2D(frame, -1, kernel)

                if p["saturation"] != 0:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
                    hsv[:, :, 1] *= (1.0 + p["saturation"] * 0.1)
                    hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
                    frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

                _, buffer = cv2.imencode('.jpg', frame)
                cap.release()

                return send_file(
                    io.BytesIO(buffer.tobytes()),
                    mimetype='image/jpeg',
                    as_attachment=True,
                    download_name=filename
                )
        if cap:
            cap.release()
        return jsonify({"status": "error", "message": "Could not grab frame"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/calibrate_openflexure', methods=['POST'])
def calibrate_openflexure():
    global current_awb_gains
    subprocess.run(["sudo", "pkill", "-f", "rpicam-vid"], stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "pkill", "-f", "mediamtx"], stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    try:
        rg, bg = openflexure_recalibrate()
        current_awb_gains = f"{rg:.2f},{bg:.2f}"
        
        image_edit_params["brightness"] = 0
        image_edit_params["exposure"] = 0

        start_pi_camera()
        return jsonify({"status": "success", "red_gain": f"{rg:.2f}", "blue_gain": f"{bg:.2f}"})
    except Exception as e:
        start_pi_camera()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/calibrate_awb', methods=['POST'])
def calibrate_awb():
    global current_awb_gains
    subprocess.run(["sudo", "pkill", "-f", "rpicam-vid"], stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "pkill", "-f", "mediamtx"], stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    try:
        rg, bg = run_awb_calibration()
        current_awb_gains = f"{rg:.2f},{bg:.2f}"
        start_pi_camera()
        return jsonify({"status": "success", "red_gain": f"{rg:.2f}", "blue_gain": f"{bg:.2f}"})
    except Exception as e:
        start_pi_camera()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/calibrate_ffc', methods=['POST'])
def calibrate_ffc():
    global current_awb_gains
    subprocess.run(["sudo", "pkill", "-f", "rpicam-vid"], stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "pkill", "-f", "mediamtx"], stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    try:
        rg, bg = run_flat_field_calibration()
        current_awb_gains = f"{rg:.2f},{bg:.2f}"
        start_pi_camera()
        return jsonify({"status": "success", "red_gain": f"{rg:.2f}", "blue_gain": f"{bg:.2f}"})
    except Exception as e:
        start_pi_camera()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/restart_stream', methods=['POST'])
def restart_stream():
    start_pi_camera()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    print(f"Server active: http://{PI_IP_ADDRESS}:5000")
    app.run(host='0.0.0.0', port=5000)
