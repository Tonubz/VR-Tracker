# Mario Kart World VR Tracker + OBS Overlay

Detects your VR from the VR standings screen, tracks a
running average, and writes a live overlay for OBS.

## 1. Install Tesseract OCR (the engine, not just the Python wrapper)

Windows installer (UB Mannheim build): https://github.com/UB-Mannheim/tesseract/wiki

During install, note the install path (usually
`C:\Program Files\Tesseract-OCR\tesseract.exe`). If `pytesseract` can't
find it automatically, open `vr_tracker.py` and uncomment/edit this line:

```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## 2. Install Python packages

```
py -m pip install -r requirements.txt
```

## 3. Calibrate (do this once, with the VR screen visible)

Get Mario Kart World showing the VR standings screen, then run:

```
calibrate.py
```
TIP: It's best to take a screenshot of the VR screen beforehand so that you don't have to set it up whilst mid-game.

First it asks which **capture source** to use:

- **Desktop screen** — IGNORE THIS OPTION
- **Camera / capture card feed** — USE THIS, It reads frames through the **OBS Virtual
  Camera**.

  Before choosing this, in OBS: add your capture card as a source in a
  scene, then go to **Controls > Start Virtual Camera**. The script will
  then cycle through camera indices with a preview window — press `y` when
  it shows your capture card feed, or any other key to try the next index.

After that, it grabs one frame and you click the top-left corner of the
whole name/VR grid (just outside the first pill), then the bottom-right
corner (just outside the last pill in the bottom-right column). Press `s`
to save. This writes `config.json`.

Re-run this any time your resolution, UI scale, capture source, or game
window changes.

## 4. Run the tracker

```
vr_tracker.py
```

Leave it running. Every time it detects the VR screen, it OCRs the number and updates:

- `history.json` — every VR reading ever recorded (persists across runs)
- `overlay.html` — the live overlay file

## 5. Add the overlay to OBS

1. In OBS, add a **Browser Source**.
2. Check **Local file**, and point it at the full path to `overlay.html`
   (e.g. `C:\Users\your_user\Downloads\VR_Tracker\overlay.html`).
3. Set width/height to something like 500x150 and position it wherever
   you want on your scene.

OBS automatically reloads local-file browser sources when the file
changes on disk, so it'll update live as new VR readings come in, no
manual refresh needed.
