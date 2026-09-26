"""
calibrate.py

Run this ONCE (or whenever your resolution/UI scale changes) while the
Mario Kart World VR results screen is on screen.

It grabs one full frame from your chosen capture source (desktop screen, or
a camera source like the OBS Virtual Camera for a capture-card setup), lets
you click the top-left corner and bottom-right corner of the whole name/VR
grid (both columns, all rows), and saves that region + grid size to
config.json.

Controls:
  - Left click: place top-left corner, then bottom-right corner
  - r: reset the two points and start over
  - s: save and quit (only works once both points are placed)
  - q / ESC: quit without saving
"""

import json
import sys
import time

import cv2

import vr_tracker as vt

CONFIG_PATH = "config.json"

points = []
preview = None
base_img = None


def mouse_callback(event, x, y, flags, param):
    global points, preview
    if event == cv2.EVENT_LBUTTONDOWN and len(points) < 2:
        points.append((x, y))
        redraw()


def redraw():
    global preview
    preview = base_img.copy()
    for p in points:
        cv2.circle(preview, p, 6, (0, 0, 255), -1)
    if len(points) == 2:
        cv2.rectangle(preview, points[0], points[1], (0, 255, 0), 2)
    cv2.imshow("Calibrate - click top-left then bottom-right of the grid", preview)


def select_camera_index():
    """Cycle through camera indices 0-5, previewing each, until the user
    confirms the one showing the OBS Virtual Camera / capture card feed."""
    print("Checking camera indices... a preview window will pop up for each one found.")
    for idx in range(6):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        cap.release()
        if not ok:
            continue
        preview_frame = frame.copy()
        cv2.putText(
            preview_frame,
            f"Camera index {idx} -- press 'y' if this is right, any other key to try the next",
            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
        )
        cv2.imshow("Camera preview", preview_frame)
        key = cv2.waitKey(0) & 0xFF
        cv2.destroyWindow("Camera preview")
        if key == ord('y'):
            return idx
    return None


def choose_source_config():
    print("Capture source:")
    print("  1) Desktop screen -- the game runs directly on this PC")
    print("  2) Camera / capture card feed (e.g. OBS Virtual Camera) -- you're capturing a console")
    choice = (input("Choose 1 or 2 [default 1]: ").strip() or "1")

    if choice != "2":
        return {"source": "screen"}

    print("Make sure OBS Virtual Camera is already started (Controls > Start Virtual Camera).")
    idx = select_camera_index()
    if idx is None:
        print("No camera confirmed. Exiting.")
        sys.exit(1)
    try:
        width = int(input("Capture width [default 1920]: ") or 1920)
    except ValueError:
        width = 1920
    try:
        height = int(input("Capture height [default 1080]: ") or 1080)
    except ValueError:
        height = 1080
    return {"source": "camera", "camera_index": idx, "capture_width": width, "capture_height": height}


def main():
    global base_img, points

    source_config = choose_source_config()

    print("Switch to the game (VR screen showing) now.")
    for remaining in range(5, 0, -1):
        print(f"Capturing in {remaining}...")
        time.sleep(1)
    print("Capturing now.")

    source = vt.FrameSource(source_config)
    try:
        base_img = source.grab_full()
    finally:
        source.close()

    cv2.namedWindow("Calibrate - click top-left then bottom-right of the grid", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Calibrate - click top-left then bottom-right of the grid", mouse_callback)
    redraw()

    print("Click the TOP-LEFT corner of the grid (just outside the first name pill),")
    print("then the BOTTOM-RIGHT corner (just outside the last pill, bottom-right column).")
    print("Press 's' to save once both points are placed, 'r' to reset, 'q' to quit.")

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == ord('r'):
            points = []
            redraw()
        elif key == ord('s'):
            if len(points) != 2:
                print("Place both points first.")
                continue
            break
        elif key == ord('q') or key == 27:
            cv2.destroyAllWindows()
            sys.exit(0)

    cv2.destroyAllWindows()

    (x1, y1), (x2, y2) = points
    left, top = min(x1, x2), min(y1, y2)
    width, height = abs(x2 - x1), abs(y2 - y1)

    try:
        rows = int(input("How many rows in the grid? [default 12]: ") or 12)
    except ValueError:
        rows = 12
    try:
        cols = int(input("How many columns in the grid? [default 2]: ") or 2)
    except ValueError:
        cols = 2

    config = {
        **source_config,
        "region": {"left": left, "top": top, "width": width, "height": height},
        "rows": rows,
        "cols": cols,
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Saved config to {CONFIG_PATH}: {config}")


if __name__ == "__main__":
    main()
