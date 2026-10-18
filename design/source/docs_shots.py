"""Screenshot the Surveil page for the documentation graphics (docs_art.py reads the results).

    build/backtrack-surveil serve &                       # port 8826
    python design/source/docs_shots.py [URL] [CLIP.wav]

Needs Playwright with Chromium. Writes shots/*.png and shots/boxes.json (where each part of the
page sits, in CSS pixels of the 1280-wide shot).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "shots"
OUT.mkdir(exist_ok=True)
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8826/"
clip = sys.argv[2] if len(sys.argv) > 2 else None
if not clip:
    tmp = Path(tempfile.mkdtemp())
    subprocess.run([sys.executable, str(HERE.parents[1] / "scripts" / "make-test-audio.py"), str(tmp)], check=True)
    clip = str(tmp / "bass.wav")
chromium = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None

PARTS = {"drop": "#drop", "wave": "#wave-wrap", "settings": ".sv-settings", "go": "#go", "status": "#status",
         "case": "#case-title", "redact": ".bt-redact", "roll": "#roll-wrap", "export": "#dl-midi", "cam": ".sv-cam",
         "toggle": "#theme-toggle"}


def run(page, theme, width, name, boxes=False):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base + "?theme=" + theme)
    page.wait_for_timeout(600)
    page.set_input_files("#file", clip)
    page.wait_for_timeout(400)
    page.click("#go")
    page.wait_for_selector(".bt-stamp", timeout=60000)
    page.focus("#roll")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.mouse.move(0, 0)
    # Size the viewport to the whole page: the fixed grain layer then covers it in one piece.
    page.set_viewport_size({"width": width, "height": page.evaluate("document.documentElement.scrollHeight")})
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"{name}.png"))
    if boxes:
        out = {k: page.evaluate(f"""() => {{ const r = document.querySelector('{s}').getBoundingClientRect();
                 return [r.x + scrollX, r.y + scrollY, r.width, r.height]; }}""") for k, s in PARTS.items()}
        (OUT / "boxes.json").write_text(json.dumps(out, indent=1))


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chromium)
    page = b.new_page(device_scale_factor=1)
    run(page, "wiretap", 1280, "surveil-done", boxes=True)
    run(page, "xerox-riot", 390, "surveil-light-phone")
    b.close()
print("wrote", sorted(f.name for f in OUT.iterdir()))
