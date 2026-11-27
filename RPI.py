import os
import cv2
import time
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
from picamera2 import Picamera2

# Ensure the folder exists
photo_folder = "photos"
if not os.path.exists(photo_folder):
    os.makedirs(photo_folder)

picam2 = Picamera2()

# FIXED: Added '=' and moved main inside the parentheses
preview_config = picam2.create_preview_configuration(main={"size": (1920, 1080), "format": "BGR888"})

picam2.configure(preview_config)
picam2.start()

root = tk.Tk()
root.title("Raspberry PI Camera UI")
root.attributes("-fullscreen", True)

# FIXED: Changed 'R' to 'root' and fixed the lambda string
root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))

camera_label = tk.Label(root)
camera_label.pack()

def update_frame():
    # FIXED: Added '='
    frame = picam2.capture_array()

    # Convert BGR to RGB for Tkinter
    frame = frame[:, :, ::-1]

    img = Image.fromarray(frame)
    imgtk = ImageTk.PhotoImage(image=img)

    # FIXED: Added '='
    camera_label.imgtk = imgtk
    camera_label.configure(image=imgtk)
    camera_label.after(10, update_frame)

def capture_photo():
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    # FIXED: Fixed the filename curly braces
    filename = f"{photo_folder}/photo_{timestamp}.jpg"
    
    picam2.capture_file(filename)
    messagebox.showinfo("Saved", f"Photo saved:\n{filename}")

# FIXED: Added '='
capture_btn = tk.Button(root, command=capture_photo, text="Capture", font=("Arial", 16), bg="blue", fg="white")
capture_btn.pack(pady=20)

update_frame()
root.mainloop()

# Stop camera on exit
picam2.stop()