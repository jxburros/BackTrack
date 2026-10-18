"""Shared helpers for the BackTrack design-language boards."""
import math
import random

from vixl import Project
from vixl.typefaces import install_font

W, H = 1600, 1000
M = 64

C = {
    "bone": "#f1efe9", "ink": "#0d0d0d", "xerox": "#bdb8ad", "tape": "#e8dcb0",
    "riot": "#e10600", "hazard": "#ffd400", "flash": "#ff6a00",
    "phosphor": "#39ff6a", "crt": "#07110a", "dimgreen": "#1f6b35",
    "pink": "#ff2e93", "acid": "#d6ff2e",
}

FONTS = [
    ("Anton", 400), ("Space Mono", 400), ("Space Mono", 700), ("Permanent Marker", 400),
    ("Big Shoulders Stencil Display", 900), ("VT323", 400), ("Special Elite", 400),
    ("Rubik Mono One", 400), ("UnifrakturMaguntia", 400),
]
SHOUT, MONO, MONOB, SCRAWL = "anton-400", "space-mono-400", "space-mono-700", "permanent-marker-400"
STENCIL, CRT, TYPE, BLOCK, FRAK = ("big-shoulders-stencil-display-900", "vt323-400", "special-elite-400",
                                   "rubik-mono-one-400", "unifrakturmaguntia-400")


class Board:
    def __init__(self, bg, seed=1):
        self.p = Project(W, H, bg)
        for fam, wt in FONTS:
            install_font(self.p, fam, weight=wt)
        self.scratch = Project(W, H, bg)
        for fam, wt in FONTS:
            install_font(self.scratch, fam, weight=wt)
        self.ops = []
        self.rng = random.Random(seed)
        self.n = 0
        self.bg = bg

    def uid(self, base):
        self.n += 1
        return f"{base}-{self.n}"

    def add(self, *ops):
        self.ops.extend(ops)

    def text(self, text, font, size, x, y, color, rot=0, name=None, **kw):
        name = name or self.uid("txt")
        box = kw.pop("within", None)
        if box:
            import textwrap
            cols = max(8, int(box[2] / (size * 0.61)))
            text = "\n".join(textwrap.fill(par, cols) if par else "" for par in text.split("\n"))
        self.add({"type": "text", "name": name, "text": text, "font": font, "size": size,
                  "x": x, "y": y, "color": color, **kw})
        if rot:
            self.add({"type": "rotate", "target": name, "value": rot})
        return name

    def rect(self, x, y, w, h, fill, rot=0, name=None, **kw):
        name = name or self.uid("rect")
        self.add({"type": "shape", "shape": "rectangle", "name": name, "width": w, "height": h,
                  "x": x, "y": y, "fill": fill, **kw})
        if rot:
            self.add({"type": "rotate", "target": name, "value": rot})
        return name

    def torn(self, x, y, w, h, fill, rot=0, jag=7, step=22, name=None, **kw):
        """A paper scrap with ripped top and bottom edges and slightly ragged sides."""
        r = self.rng
        pts = []
        n = max(2, int(w / step))
        for i in range(n + 1):
            pts.append((w * i / n, r.uniform(0, jag)))
        m = max(2, int(h / (step * 2)))
        for i in range(1, m):
            pts.append((w - r.uniform(0, jag / 2), h * i / m))
        for i in range(n, -1, -1):
            pts.append((w * i / n, h - r.uniform(0, jag)))
        for i in range(m - 1, 0, -1):
            pts.append((r.uniform(0, jag / 2), h * i / m))
        d = "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in pts) + " Z"
        kw = {k: v for k, v in kw.items() if v is not None}
        name = name or self.uid("scrap")
        self.add({"type": "shape", "shape": "path", "name": name, "path": d, "x": x, "y": y, "fill": fill, **kw})
        if rot:
            self.add({"type": "rotate", "target": name, "value": rot})
        return name

    def tape(self, x, y, w=150, rot=-12, color=None):
        return self.rect(x, y, w, 40, color or C["tape"], rot=rot, opacity=0.78, name=self.uid("tape"))

    def grain(self, target, amount=0.35, seed=None):
        self.add({"type": "effect", "target": target, "name": "grain", "amount": amount,
                  "seed": seed if seed is not None else self.rng.randint(0, 9999)})

    def wave(self, x, y, w, h, color, bars=110, seed=0, quiet=()):
        """Waveform drawn as bars; returns the group name."""
        r = random.Random(seed)
        names = []
        bw = w / bars
        for i in range(bars):
            env = 0.35 + 0.65 * abs(math.sin(i / bars * math.pi * r.uniform(2, 5)))
            if any(a <= i / bars <= b for a, b in quiet):
                env *= 0.08
            a = max(0.04, min(1, env * r.uniform(0.3, 1)))
            bh = max(2, h * a)
            names.append(self.rect(round(x + i * bw), round(y + (h - bh) / 2), max(2, round(bw * 0.62)),
                                   round(bh), color, name=self.uid("bar")))
        g = self.uid("wave")
        self.add({"type": "group", "name": g, "targets": names})
        return g

    def measure(self, text, font, size):
        """(dx, dy, w, h) of the drawn text relative to its x, y."""
        self.scratch.apply([{"type": "text", "name": "m", "text": text, "font": font, "size": size, "x": 100, "y": 100}])
        x, y, w, h = self.scratch.bounds("m")
        self.scratch.undo()
        return x - 100, y - 100, w, h

    def ransom(self, word, x, y, size, schemes, rot_range=5):
        """Ransom-note lettering: each letter on its own scrap, font and tilt."""
        cx = x
        for ch in word:
            if ch == " ":
                cx += size * 0.45
                continue
            bgc, fg, font = schemes[self.rng.randrange(len(schemes))]
            s = int(size * self.rng.uniform(0.85, 1.12))
            mx, my, mw, mh = self.measure(ch, font, s)
            pad = max(6, int(size * 0.12))
            bw, bh = int(mw + 2 * pad), int(size * 1.0 + 2 * pad)
            dy = self.rng.randint(-8, 8)
            rot = self.rng.uniform(-rot_range, rot_range)
            scrap = self.torn(cx, y + dy, bw, bh, bgc, jag=4, step=12)
            ty = y + dy + pad + (size - mh) / 2 - my
            t = self.text(ch, font, s, round(cx + pad - mx), round(ty), fg)
            g = self.uid("letter")
            self.add({"type": "group", "name": g, "targets": [scrap, t]},
                     {"type": "rotate", "target": g, "value": rot})
            cx += bw + 6
        return cx

    def build(self, path_png, path_vixl):
        self.p.apply(self.ops)
        self.p.save(path_vixl, overwrite=True)
        self.p.export(path_png, overwrite=True)
        return self.p
