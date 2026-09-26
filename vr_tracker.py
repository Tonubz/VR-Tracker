"""
vr_tracker.py  (v2 -- fixed number reading)

Watches the Mario Kart World VR results grid, finds YOUR row (it's the one
with bright white bold text -- everyone else's is dimmer/gray), OCRs your
VR number, and writes a live-updating OBS browser source overlay showing
your current VR and room stats.

Requires config.json from calibrate.py to already exist.

What changed in v2 (the "numbers were wrong" fix):
- Rows are no longer assumed to be perfectly equal slices of the calibrated
  box. The tracker now finds each player pill directly on screen by its
  brightness profile, so small calibration errors can't slide the reading
  boxes out of line with the rows.
- The VR number is read by isolating the actual digit shapes at the right
  end of each pill (connected components) instead of OCRing a fixed-size
  crop that could catch parts of the username or the pill edge.
- Any reading that isn't a plausible 4-5 digit VR number is thrown out, so
  a bad frame gets skipped instead of saved.

If Tesseract isn't on your PATH, edit the line below.
"""

import json
import re
import statistics
import time
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

CONFIG_PATH = "config.json"
HISTORY_PATH = "history.json"
OVERLAY_HTML_PATH = "overlay.html"

POLL_INTERVAL_SEC = 1.0
BRIGHTNESS_THRESHOLD = 170        # pixel value (0-255) counted as "bright text"
MIN_BRIGHT_PIXELS = 40            # a pill needs at least this many bright pixels to count as "your" row
MIN_ADVANTAGE_RATIO = 1.3         # top pill must beat 2nd place by this ratio to be trusted
CONFIRM_READS = 2                 # same OCR value must repeat this many times before accepted
MAX_PLAUSIBLE_JUMP = 500          # a single race shouldn't swing VR more than this
JUMP_CONFIRM_READS = 5            # if a reading jumps more than that, require this many repeats instead
MIN_PILL_HEIGHT = 15              # ignore brightness bands thinner than this (noise, dividers)
NUMBER_REGION_FRAC = 0.38         # rightmost fraction of a pill where the VR digits live
CONTENT_STD_THRESHOLD = 12        # pixel std-dev above which a pill is considered "occupied"
OVER_THRESHOLD = 9000             # VR threshold for the "how many people are over X" stat


def load_config():
    if not Path(CONFIG_PATH).exists():
        raise FileNotFoundError(
            f"'{CONFIG_PATH}' not found in this folder. Run calibrate.py first, "
            f"and make sure vr_tracker.py and config.json are in the SAME folder."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_history():
    if Path(HISTORY_PATH).exists():
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {"readings": []}


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


class FrameSource:
    """Grabs full frames from either your desktop (mss) or a video capture
    device (e.g. the OBS Virtual Camera showing a capture card feed)."""

    def __init__(self, source_config):
        self.mode = source_config.get("source", "screen")
        if self.mode == "camera":
            index = source_config["camera_index"]
            width = source_config.get("capture_width", 1920)
            height = source_config.get("capture_height", 1080)
            self.cap = cv2.VideoCapture(index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if not self.cap.isOpened():
                raise RuntimeError(
                    f"Could not open camera index {index}. Make sure OBS Virtual "
                    f"Camera is started (Controls > Start Virtual Camera in OBS)."
                )
        else:
            self.sct = mss.mss()
            self.monitor = self.sct.monitors[1]

    def grab_full(self):
        if self.mode == "camera":
            ok, frame = self.cap.read()
            if not ok:
                raise RuntimeError("Failed to read a frame from the camera source.")
            return frame
        shot = self.sct.grab(self.monitor)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    def close(self):
        if self.mode == "camera":
            self.cap.release()
        else:
            self.sct.close()


def crop_region(frame, region):
    x, y, w, h = region["left"], region["top"], region["width"], region["height"]
    return frame[y:y + h, x:x + w]


def find_pills(col_img):
    """Find the vertical bands of the player pills in one grid column by
    looking at the row-by-row brightness profile. Each pill is brighter than
    the gaps between pills, so rows group naturally -- this replaces the old
    'split the box into N equal slices' approach that drifted out of line
    whenever calibration was even slightly off."""
    gray = cv2.cvtColor(col_img, cv2.COLOR_BGR2GRAY)
    w = gray.shape[1]
    # Sample a strip in the middle-right of the column (inside the pills,
    # away from the left icons and the right edge).
    profile = gray[:, int(w * 0.55):int(w * 0.75)].mean(axis=1)
    lo, hi = np.percentile(profile, 10), np.percentile(profile, 90)
    if hi - lo < 8:
        return []  # flat image -- not on the VR screen
    thr = (lo + hi) / 2
    on = profile > thr
    bands = []
    start = None
    for y, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = y
        elif not v and start is not None:
            if y - start > MIN_PILL_HEIGHT:
                bands.append((start, y))
            start = None
    return bands


def find_all_pills(img, cols):
    """Split the calibrated region into its columns, then find the actual
    pill bands in each. Returns a list of pill images."""
    h, w = img.shape[:2]
    pills = []
    for c in range(cols):
        col = img[:, c * w // cols:(c + 1) * w // cols]
        for (y1, y2) in find_pills(col):
            pills.append(col[y1:y2])
    return pills


def brightness_score(pill_img):
    gray = cv2.cvtColor(pill_img, cv2.COLOR_BGR2GRAY)
    return int(np.sum(gray > BRIGHTNESS_THRESHOLD))


def find_own_pill(pills):
    scored = sorted(pills, key=brightness_score, reverse=True)
    if not scored:
        return None
    top = scored[0]
    top_score = brightness_score(top)
    if top_score < MIN_BRIGHT_PIXELS:
        return None  # nothing bright enough -- probably not on the VR screen
    if len(scored) > 1:
        second_score = max(brightness_score(scored[1]), 1)
        if top_score / second_score < MIN_ADVANTAGE_RATIO:
            return None  # not confidently distinguishable -- skip this frame
    return top


def read_pill_number(pill_img):
    """Read the VR number at the right end of a player pill.

    Instead of OCRing a fixed crop (which could catch username digits or
    pill-edge noise), this:
      1. thresholds the right portion of the pill against its own background,
      2. finds the individual bright shapes (connected components),
      3. keeps only the rightmost cluster of digit-sized shapes,
      4. OCRs just that tight digit group.
    Returns an int, or None if there are no digits (guest / disconnected)."""
    gray = cv2.cvtColor(pill_img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    roi = gray[:, int(w * (1 - NUMBER_REGION_FRAC)):]

    bg = np.median(roi)
    if float(np.std(roi)) < 6:
        return None  # flat -- no digits here
    _, bw = cv2.threshold(roi, bg + 35, 255, cv2.THRESH_BINARY)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw)
    boxes = [
        stats[i] for i in range(1, n)
        if h * 0.2 < stats[i][3] < h * 0.7 and stats[i][4] > 15
    ]
    if not boxes:
        return None

    # Walk left from the rightmost shape, keeping shapes that are close
    # together (the digit group). Stop at the first big gap (that's the
    # boundary between the number and the name/icon area).
    boxes.sort(key=lambda s: -s[0])
    keep = [boxes[0]]
    for s in boxes[1:]:
        if keep[-1][0] - (s[0] + s[2]) < h * 0.35:
            keep.append(s)
        else:
            break

    x1 = min(s[0] for s in keep)
    x2 = max(s[0] + s[2] for s in keep)
    y1 = min(s[1] for s in keep)
    y2 = max(s[1] + s[3] for s in keep)

    crop = 255 - bw[max(0, y1 - 4):y2 + 4, max(0, x1 - 6):x2 + 6]
    crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    text = pytesseract.image_to_string(
        crop, config="--psm 7 -c tessedit_char_whitelist=0123456789"
    )
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    value = int(digits)
    # Real VR values are always between MIN_VR and MAX_VR; anything else is a misread.
    if value < MIN_VR or value > MAX_VR:
        return None
    return value


def pill_has_player(pill_img):
    gray = cv2.cvtColor(pill_img, cv2.COLOR_BGR2GRAY)
    return float(np.std(gray)) > CONTENT_STD_THRESHOLD


MIN_VR = 3000            # lowest possible VR
MAX_VR = 13500           # highest possible VR; anything outside = misread
OUTLIER_DISTANCE = 3000 # ignore numbers this far from the room median


def compute_room_metrics(pills):
    occupied = 0
    numbers = []
    over_threshold = 0
    for p in pills:
        if not pill_has_player(p):
            continue
        occupied += 1
        val = read_pill_number(p)
        if val is not None:
            numbers.append(val)
            if val > OVER_THRESHOLD:
                over_threshold += 1
    if numbers:
        med0 = statistics.median(numbers)
        numbers = [n for n in numbers if abs(n - med0) <= OUTLIER_DISTANCE]
        over_threshold = sum(1 for n in numbers if n > OVER_THRESHOLD)
    avg = sum(numbers) / len(numbers) if numbers else None
    median = statistics.median(numbers) if numbers else None
    return {
        "occupied": occupied,
        "avg": avg,
        "median": median,
        "avg_count": len(numbers),
        "over_threshold": over_threshold,
    }


def write_overlay(current_vr, reading_count, room=None):
    room = room or {}
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<style>
  body {{
    margin: 0;
    background: transparent;
    font-family: 'Segoe UI', Arial, sans-serif;
    color: white;
  }}
  .box {{
    display: inline-block;
    padding: 12px 20px;
    background: rgba(20, 20, 30, 0.75);
    border-radius: 12px;
    box-shadow: 0 0 12px rgba(0,0,0,0.5);
  }}
  .label {{
    font-size: 16px;
    opacity: 0.75;
    text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .value {{
    font-size: 40px;
    font-weight: 800;
  }}
  .row {{
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
  }}
</style>
</head>
<body>
  <div class="box">
    <div class="row">
      <div>
        <div class="label">Current VR</div>
        <div class="value">{current_vr if current_vr is not None else "--"}</div>
      </div>
      <div>
        <div class="label">Players in Room</div>
        <div class="value">{room.get("occupied", "--")}</div>
      </div>
      <div>
        <div class="label">Room Avg ({room.get("avg_count", 0)})</div>
        <div class="value">{round(room["avg"]) if room.get("avg") is not None else "--"}</div>
      </div>
      <div>
        <div class="label">Room Median ({room.get("avg_count", 0)})</div>
        <div class="value">{round(room["median"]) if room.get("median") is not None else "--"}</div>
      </div>
      <div>
        <div class="label">{OVER_THRESHOLD}+ VR</div>
        <div class="value">{room.get("over_threshold", "--")}</div>
      </div>
    </div>
  </div>
</body>
</html>
"""
    Path(OVERLAY_HTML_PATH).write_text(html, encoding="utf-8")


def main():
    config = load_config()
    region, cols = config["region"], config["cols"]

    history = load_history()
    readings = history["readings"]

    pending_value = None
    pending_count = 0
    last_accepted_value = None

    print("Watching for the VR screen... (Ctrl+C to stop)")

    write_overlay(readings[-1] if readings else None, len(readings))

    source = FrameSource(config)
    try:
        while True:
            try:
                frame = source.grab_full()
                img = crop_region(frame, region)
                pills = find_all_pills(img, cols)
                own = find_own_pill(pills)

                if own is not None:
                    value = read_pill_number(own)
                    if value is not None:
                        if value == pending_value:
                            pending_count += 1
                        else:
                            pending_value = value
                            pending_count = 1

                        required = CONFIRM_READS
                        if last_accepted_value is not None and abs(value - last_accepted_value) > MAX_PLAUSIBLE_JUMP:
                            required = JUMP_CONFIRM_READS

                        if pending_count >= required and value != last_accepted_value:
                            last_accepted_value = value
                            readings.append(value)
                            save_history(history)
                            room = compute_room_metrics(pills)
                            write_overlay(value, len(readings), room)
                            if room["avg"] is not None:
                                print(
                                    f"New VR reading: {value}  "
                                    f"(room: {room['occupied']} players, avg {room['avg']:.1f}, "
                                    f"median {room['median']:.1f} over {room['avg_count']}, "
                                    f"{room['over_threshold']} over {OVER_THRESHOLD})"
                                )
                            else:
                                print(
                                    f"New VR reading: {value}  "
                                    f"(room: {room['occupied']} players, no readable VR numbers)"
                                )
                else:
                    pending_value = None
                    pending_count = 0

            except Exception as e:
                print(f"Frame error (continuing): {e}")

            time.sleep(POLL_INTERVAL_SEC)
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception:
        import traceback
        print("\n--- vr_tracker.py crashed ---")
        traceback.print_exc()
    finally:
        input("\nPress Enter to close this window...")
