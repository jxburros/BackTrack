"""Helpers for the promo scenes: BackTrack's design language (design/AGENT-SPEC.md) as Vixl operations.

A ``Scene`` collects Vixl operations and writes one editable 1920 x 1080 ``.vixl`` master with a keyframe
timeline. Times are milliseconds from the start of the scene. The pieces follow the design boards: bone
paper and toner grain, torn scraps, tape, stamps, one scrawl, one shout, hard bar waveforms, and one hot
colour per shot (riot red, hazard yellow, phosphor green, or gig pink for the closing flyer).
"""

from __future__ import annotations

import json
import math
import os
import random
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

os.environ.setdefault("VIXL_NO_UPDATE", "1")

from vixl import Project  # noqa: E402
from vixl.typefaces import install_font  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PROMO = ROOT / "promo"
W, H, FPS = 1920, 1080, 30
M = 96  # page margin at film size
BLEED = 60  # backgrounds overhang the frame so a shaken stage never shows an edge

# design/tokens.json
BONE, INK, XEROX, TAPE = "#f1efe9", "#0d0d0d", "#bdb8ad", "#e8dcb0"
RIOT, RIOT_TEXT, HAZARD, FLASH = "#e10600", "#c20500", "#ffd400", "#ff6a00"
PHOSPHOR, CRT, CRT_PANEL, CRT_ROLL = "#39ff6a", "#07110a", "#0a170e", "#06100a"
GRID, GRID_STRONG, NOTE_DIM, CRT_MUTED = "#123a1e", "#1d5a30", "#1f6b35", "#3f8f55"
BLAST, BLAST_PANEL, BLAST_ROW, BLAST_TRACK, BLAST_MUTED = "#111110", "#1a1a18", "#232320", "#2b2b27", "#8c8670"
PINK, ACID, MUTED = "#ff2e93", "#d6ff2e", "#5d5a53"

FONTS = [
    ("Anton", 400), ("Space Mono", 400), ("Space Mono", 700), ("Permanent Marker", 400),
    ("Big Shoulders Stencil Display", 900), ("VT323", 400), ("Special Elite", 400),
    ("Rubik Mono One", 400), ("UnifrakturMaguntia", 400),
]
SHOUT, MONO, MONOB, SCRAWL = "anton-400", "space-mono-400", "space-mono-700", "permanent-marker-400"
STENCIL, CRTF, TYPE, BLOCK, FRAK = (
    "big-shoulders-stencil-display-900", "vt323-400", "special-elite-400", "rubik-mono-one-400",
    "unifrakturmaguntia-400",
)

TEMPO = 160
BEAT = 60000 // TEMPO  # 375 ms
BAR = 4 * BEAT  # 1500 ms


def song() -> dict:
    """The soundtrack's composition (``score.ts`` writes it)."""
    return json.loads((PROMO / "song.json").read_text())


@lru_cache
def stem(name: str):
    """Mono samples of a rendered stem (or ``music`` for the master) and the sample rate."""
    path = PROMO / "audio" / ("music.wav" if name == "music" else f"stems/{name}.wav")
    with wave.open(str(path)) as fh:
        data = np.frombuffer(fh.readframes(fh.getnframes()), "<i2").reshape(-1, 2).astype(float) / 32768
        return data.mean(axis=1), fh.getframerate()


def peaks(name: str, t0: float, t1: float, bins: int, norm: float | None = None) -> list[float]:
    """Peak level of a stem in ``bins`` slices of [t0, t1) seconds, 0..1 (scaled by ``norm`` or the slice max)."""
    x, sr = stem(name)
    seg = np.abs(x[int(t0 * sr): int(t1 * sr)])
    if not len(seg):
        return [0.0] * bins
    edges = np.linspace(0, len(seg), bins + 1).astype(int)
    p = np.array([seg[a:b].max() if b > a else 0 for a, b in zip(edges[:-1], edges[1:])])
    return list(np.clip(p / (norm or max(p.max(), 1e-6)), 0, 1))


def hits(t0: float, t1: float, pitches=(36,), role="drums") -> list[int]:
    """Times (ms from t0) of the soundtrack's notes with these pitches between t0 and t1 seconds."""
    tr = next(t for t in song()["tracks"] if t["role"] == role)
    return sorted({round((n[0] - t0) * 1000) for n in tr["notes"] if t0 <= n[0] < t1 and n[2] in pitches})


def r2(v):
    return round(float(v), 2)


# -------------------------------------------------------------------- operation builders
def rect(name, x, y, w, h, fill, stroke=None, stroke_width=3, **kw):
    op = {"type": "shape", "shape": "rectangle", "name": name, "x": r2(x), "y": r2(y),
          "width": max(1, round(w)), "height": max(1, round(h)), "fill": fill, **kw}
    if stroke:
        op.update(stroke=stroke, stroke_width=stroke_width)
    return op


def poly(name, points, fill, stroke=None, width=0, closed=True, **kw):
    xs, ys = zip(*points)
    x, y = min(xs), min(ys)
    d = "M" + " L".join(f"{r2(px - x)} {r2(py - y)}" for px, py in points) + (" Z" if closed else "")
    op = {"type": "shape", "shape": "path", "name": name, "path": d, "x": r2(x), "y": r2(y),
          "width": r2(max(max(xs) - x, 1)), "height": r2(max(max(ys) - y, 1)), "fill": fill, **kw}
    if stroke:
        op.update(stroke=stroke, stroke_width=width)
    return op


def line(name, points, colour, width=3):
    return poly(name, points, "transparent", colour, width, closed=False)


def blocks(name, boxes, fill):
    """Many rectangles as one editable compound path: [(x, y, w, h), ...]. Waveform bars, notes, stripes."""
    boxes = [b for b in boxes if b[2] > 0 and b[3] > 0]
    if not boxes:
        return []
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    parts = []
    for x, y, w, h in boxes:
        a, b, c, d = r2(x - x0), r2(y - y0), r2(x - x0 + w), r2(y - y0 + h)
        parts.append(f"M{a} {b} L{c} {b} L{c} {d} L{a} {d} Z")
    return [{"type": "shape", "shape": "path", "name": name, "path": " ".join(parts), "x": r2(x0), "y": r2(y0),
             "width": r2(max(x1 - x0, 1)), "height": r2(max(y1 - y0, 1)), "fill": fill}]


def text(name, value, x, y, size, color=INK, font=MONO, **extra):
    return {"type": "text", "name": name, "text": value, "font": font, "size": size, "color": color,
            "x": r2(x), "y": r2(y), **extra}


def group(name, targets):
    return {"type": "group", "name": name, "targets": list(targets)}


def names(ops):
    return [op["name"] for op in ops if "name" in op and op["type"] not in ("layer-intent", "keyframe", "effect")]


def bundle(name, ops):
    """Ops plus a group over every layer they create, so the whole thing moves as one."""
    return ops + [group(name, names(ops))]


def rot(target, deg):
    return {"type": "rotate", "target": target, "value": deg}


def grain(target, amount, seed):
    return {"type": "effect", "target": target, "name": "grain", "amount": amount, "seed": seed}


def torn_points(rng, x, y, w, h, jag=8, step=24):
    """Outline of a paper scrap with ripped top and bottom edges and slightly ragged sides."""
    pts = []
    n = max(2, int(w / step))
    m = max(2, int(h / (step * 2)))
    for i in range(n + 1):
        pts.append((x + w * i / n, y + rng.uniform(0, jag)))
    for i in range(1, m):
        pts.append((x + w - rng.uniform(0, jag / 2), y + h * i / m))
    for i in range(n, -1, -1):
        pts.append((x + w * i / n, y + h - rng.uniform(0, jag)))
    for i in range(m - 1, 0, -1):
        pts.append((x + rng.uniform(0, jag / 2), y + h * i / m))
    return pts


# -------------------------------------------------------------------- scenes
class Scene:
    def __init__(self, name: str, duration: int, bg: str = BONE, seed: int = 1):
        self.name, self.duration, self.bg = name, duration, bg
        self.ops: list[dict] = []
        self.rng = random.Random(seed)
        self.n = 0

    def uid(self, base):
        self.n += 1
        return f"{base}-{self.n}"

    def add(self, *ops):
        for op in ops:
            self.ops.extend(op) if isinstance(op, list) else self.ops.append(op)
        return self

    # ---------------------------------------------------------------- pieces
    def paper(self, color=None, amount=0.08, name="paper", seed=None):
        """Full-bleed background with toner grain (light specks on dark paper come from the same effect)."""
        self.add(rect(name, -BLEED, -BLEED, W + 2 * BLEED, H + 2 * BLEED, color or self.bg),
                 grain(name, amount, seed if seed is not None else self.rng.randint(0, 9999)),
                 {"type": "layer-intent", "targets": [name], "role": "decoration", "allow_crop": True})
        return name

    def torn(self, x, y, w, h, fill, deg=0, jag=8, step=24, name=None, toner=0.0, **kw):
        name = name or self.uid("scrap")
        kw = {k: v for k, v in kw.items() if v is not None}
        self.add(poly(name, torn_points(self.rng, x, y, w, h, jag, step), fill, **kw))
        if toner:
            self.add(grain(name, toner, self.rng.randint(0, 9999)))
        if deg:
            self.add(rot(name, deg))
        return name

    def tape(self, x, y, w=170, deg=-14, name=None):
        name = name or self.uid("tape")
        self.add(rect(name, x, y, w, 44, TAPE, opacity=0.78), rot(name, deg))
        return name

    def words(self, value, font, size, x, y, color=INK, deg=0, name=None, toner=0.0, **kw):
        name = name or self.uid("txt")
        self.add(text(name, value, x, y, size, color, font, **kw))
        if toner:
            self.add(grain(name, toner, self.rng.randint(0, 9999)))
        if deg:
            self.add(rot(name, deg))
        return name

    def scrawl(self, value, x, y, size=54, color=RIOT, deg=-5, name=None):
        return self.words(value, SCRAWL, size, x, y, color, deg, name)

    def stamp(self, word, x, y, color, size=64, deg=-7, name=None):
        """A rubber stamp: the word in a heavy frame, rotated, slightly see-through."""
        name = name or self.uid("stamp")
        w = len(word) * size * 0.56 + 56
        h = size * 1.42
        self.add(rect(f"{name}-frame", x, y, w, h, "transparent", color, 7, opacity=0.9),
                 text(f"{name}-word", word, x + 28, y + size * 0.08, size, color, SHOUT, opacity=0.9),
                 group(name, [f"{name}-frame", f"{name}-word"]), grain(f"{name}-word", 0.3, 7), rot(name, deg))
        return name

    def bars(self, name, x, y, w, h, values, color, bar=5, gap=3, floor=3):
        """A hard bar waveform (design/waveform.js at film scale): quiet bins drop to a thin line."""
        n = int(w // (bar + gap))
        vals = np.interp(np.linspace(0, len(values) - 1, n), np.arange(len(values)), values)
        boxes = []
        for i, v in enumerate(vals):
            bh = floor if v < 0.04 else max(floor, v * h)
            boxes.append((x + i * (bar + gap), y + (h - bh) / 2, bar, bh))
        self.add(blocks(name, boxes, color))
        return name

    def hazard(self, name, x, y, w, h, stripe=46):
        """Hazard tape: yellow band with black diagonal stripes, clipped to the band."""
        from shapely.geometry import Polygon, box

        band = box(x, y, x + w, y + h)
        self.add(rect(f"{name}-band", x, y, w, h, HAZARD))
        parts = []
        k = x - h
        while k < x + w + h:
            p = Polygon([(k, y + h), (k + stripe, y + h), (k + stripe + h, y), (k + h, y)]).intersection(band)
            if not p.is_empty:
                parts.append(list(p.exterior.coords)[:-1])
            k += 2 * stripe
        x0 = min(px for pts in parts for px, _ in pts)
        y0 = min(py for pts in parts for _, py in pts)
        x1 = max(px for pts in parts for px, _ in pts)
        d = " ".join("M" + " L".join(f"{r2(px - x0)} {r2(py - y0)}" for px, py in pts) + " Z" for pts in parts)
        self.add({"type": "shape", "shape": "path", "name": f"{name}-stripes", "path": d, "x": r2(x0), "y": r2(y0),
                  "width": r2(x1 - x0), "height": r2(h), "fill": INK},
                 group(name, [f"{name}-band", f"{name}-stripes"]))
        return name

    def measure(self, value, font, size):
        """(dx, dy, w, h) of drawn text relative to its x, y."""
        scratch = _scratch()
        scratch.apply([{"type": "text", "name": "m", "text": value, "font": font, "size": size, "x": 100, "y": 100}])
        x, y, w, h = scratch.bounds("m")
        scratch.undo()
        return x - 100, y - 100, w, h

    def ransom(self, word, x, y, size, schemes, tilt=5, name=None):
        """Ransom-note lettering: each letter on its own scrap, font and tilt. Returns the letter groups."""
        name = name or self.uid("ransom")
        cx, letters = x, []
        for ch in word:
            if ch == " ":
                cx += size * 0.45
                continue
            bgc, fg, font = schemes[self.rng.randrange(len(schemes))]
            s = int(size * self.rng.uniform(0.86, 1.1))
            mx, my, mw, mh = self.measure(ch, font, s)
            pad = max(8, int(size * 0.13))
            bw, bh = int(mw + 2 * pad), int(size + 2 * pad)
            dy = self.rng.randint(-10, 10)
            g = f"{name}-{len(letters)}"
            scrap = self.torn(cx, y + dy, bw, bh, bgc, jag=5, step=14, name=f"{g}-scrap")
            t = self.words(ch, font, s, round(cx + pad - mx), round(y + dy + pad + (size - mh) / 2 - my), fg,
                           name=f"{g}-ch")
            self.add(group(g, [scrap, t]), rot(g, self.rng.uniform(-tilt, tilt)))
            letters.append(g)
            cx += bw + 8
        self.add(group(name, letters))
        return name, letters, cx

    # ---------------------------------------------------------------- motion
    def key(self, target, prop, time, value, easing=None):
        op = {"type": "keyframe", "target": target, "property": prop, "time": int(round(time)), "value": value}
        if easing:
            op["easing"] = easing
        self.ops.append(op)

    def keys(self, target, prop, frames, easing="ease-out-cubic"):
        """``frames`` is [(time, value), ...]; every key after the first uses ``easing``."""
        for i, (t, v) in enumerate(frames):
            self.key(target, prop, t, v, easing if i else None)

    def show(self, target, t, t_out=None):
        """Hard cut in at t (and out at t_out): no fades in a punk film."""
        frames = [(0, False), (t, True)] if t > 0 else [(0, True)]
        if t_out is not None:
            frames.append((t_out, False))
        self.keys(target, "visible", frames, "step")

    def slam(self, target, t, start=1.7, dur=140, deg=None, t_out=None):
        """Slap something down on the beat: it drops in big and lands in ``dur`` ms with a tiny overshoot."""
        self.show(target, t, t_out)
        self.keys(target, "scale", [(0, start), (t, start), (t + dur, 0.97), (t + dur + 70, 1)], "ease-in-cubic")
        if deg is not None:
            self.keys(target, "rotation", [(0, deg * 3), (t, deg * 3), (t + dur, deg)], "ease-out-cubic")

    def fling(self, target, t, dx, dy, dur=200, spin=0, t_out=None):
        """Throw something in from (dx, dy) off its spot, landing at t + dur."""
        self.show(target, t, t_out)
        self.keys(target, "translate-x", [(0, dx), (t, dx), (t + dur, 0)], "ease-out-expo")
        self.keys(target, "translate-y", [(0, dy), (t, dy), (t + dur, 0)], "ease-out-expo")
        if spin:
            self.keys(target, "rotation", [(0, spin), (t, spin), (t + dur, 0)], "ease-out-expo")

    def shake(self, target, times, amp=10, dur=150):
        """A jolt on each time (kicks, slams): a few stepped offsets that settle back to 0."""
        xs, ys = [(0, 0)], [(0, 0)]
        rng = random.Random(len(times) * 7 + int(amp))
        last = -1
        for t in sorted(times):
            if t <= last:
                continue
            steps = [(t + k * dur / 4, amp * (1 - k / 4)) for k in range(4)]
            for tt, a in steps:
                xs.append((tt, round(rng.uniform(-a, a), 1)))
                ys.append((tt, round(rng.uniform(-a, a), 1)))
            xs.append((t + dur, 0))
            ys.append((t + dur, 0))
            last = t + dur
        self.keys(target, "translate-x", xs, "step")
        self.keys(target, "translate-y", ys, "step")

    def pulse(self, target, times, amount=0.04, dur=180):
        """A short scale kick on each time (the soundtrack's kick drum)."""
        frames = [(0, 1)]
        last = -1
        for t in sorted(times):
            if t <= last + 60:
                continue
            frames += [(t - 1, 1), (t, 1 + amount), (t + dur, 1)]
            last = t + dur
        self.keys(target, "scale", frames, "ease-out-cubic")

    def jitter(self, target, start=0, end=None, amp=2.5, every=125, deg=0.0):
        """Stop-motion boil: the layer shifts a pixel or two every few frames, like a photocopy on a wall."""
        end = end or self.duration
        rng = random.Random(hash(target) & 0xFFFF)
        xs, ys, rs = [], [], []
        for t in range(int(start), int(end) + 1, every):
            xs.append((t, round(rng.uniform(-amp, amp), 1)))
            ys.append((t, round(rng.uniform(-amp, amp), 1)))
            if deg:
                rs.append((t, round(rng.uniform(-deg, deg), 2)))
        self.keys(target, "translate-x", xs, "step")
        self.keys(target, "translate-y", ys, "step")
        if rs:
            self.keys(target, "rotation", rs, "step")

    def flash(self, color=INK, times=(0,), dur=67, name=None):
        """A one- or two-frame full-screen flash (a photocopier lamp pass) at each time."""
        name = name or self.uid("flash")
        self.add(rect(name, -BLEED, -BLEED, W + 2 * BLEED, H + 2 * BLEED, color))
        frames = [(0, False)]
        for t in times:
            frames += [(t, True), (t + dur, False)]
        if times and times[0] == 0:
            frames = frames[1:]
        self.keys(name, "visible", frames, "step")
        return name

    def blink(self, target, period=1000, start=0):
        """REC blinks at 1 Hz."""
        frames = []
        for t in range(start, self.duration + period, period // 2):
            frames.append((t, (t - start) // (period // 2) % 2 == 0))
        self.keys(target, "visible", frames, "step")

    # ---------------------------------------------------------------- build
    def project(self) -> Project:
        p = Project(W, H, self.bg)
        for family, weight in FONTS:
            install_font(p, family, weight=weight)
        batch = [{"type": "timeline-set", "duration": self.duration, "fps": FPS, "loop_mode": "off"}] + self.ops
        for i in range(0, len(batch), 9000):
            p.apply(batch[i: i + 9000], detail="brief")
        return p

    def build(self) -> Project:
        p = self.project()
        p.compact()
        out = PROMO / "scenes" / f"{self.name}.vixl"
        out.parent.mkdir(parents=True, exist_ok=True)
        p.save(str(out), overwrite=True)
        return p


@lru_cache
def _scratch():
    p = Project(W, H, BONE)
    for family, weight in FONTS:
        install_font(p, family, weight=weight)
    return p
