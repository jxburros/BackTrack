#!/usr/bin/env python3
"""Contact sheets of sampled frames for quick review: python preview.py OUT_DIR [shot numbers ...]

Builds the named shots (all by default) and writes OUT_DIR/<shot>.jpg, six frames across the shot.
"""

import subprocess
import sys
from pathlib import Path

from PIL import Image

import scenes as S
from kit import PROMO

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
pick = sys.argv[2:]
for (name, _), make in zip(S.CUTS, S.SCENES):
    if pick and name[:2] not in pick:
        continue
    sc = make()
    sc.build()
    frames = []
    for k in range(6):
        t = int(sc.duration * (k + 0.9) / 6.2)
        png = out / f"{sc.name}-{t}.png"
        png.unlink(missing_ok=True)
        subprocess.run(
            ["vixl", "-p", str(PROMO / "scenes" / f"{sc.name}.vixl"), "render", "--time", f"{t}ms", "--out", str(png)],
            check=True,
            capture_output=True,
        )
        frames.append(Image.open(png).convert("RGB").resize((960, 540)))
        png.unlink()
    sheet = Image.new("RGB", (1920, 1620))
    for k, f in enumerate(frames):
        sheet.paste(f, ((k % 2) * 960, (k // 2) * 540))
    sheet.save(out / f"{sc.name}.jpg", quality=82)
    print("previewed", sc.name, flush=True)
