"""Build the documentation graphics (docs/images/) with Vixl, in the BackTrack design language.

    python design/source/docs_art.py [OUT_DIR] [name ...]

The page tour reads the screenshots in shots/ (made by docs_shots.py). The layered documents go
to vixl/docs/*.vixl, so every graphic stays editable.
"""
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image

import kit
from kit import C, CRT, MONO, MONOB, SCRAWL, SHOUT, STENCIL, Board

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / "docs" / "images"
SRC = HERE / "vixl" / "docs"
SHOTS = HERE / "shots"
OUT.mkdir(parents=True, exist_ok=True)
SRC.mkdir(parents=True, exist_ok=True)

M = 64
MUTED = "#5d5a53"          # ink-muted on bone
CRT_MUTED = "#3f8f55"      # ink-muted on CRT
CRT_PANEL = "#0a170e"
GRID, GRID_STRONG = "#123a1e", "#1d5a30"
HAZ_BG = "#111110"
PANEL = "#f7f5ef"            # xerox-riot surface
DANGER = "#ff5a4a"           # error text on the dark themes


def board(w, h, bg, seed):
    kit.W, kit.H = w, h
    b = Board(bg, seed=seed)
    b.w, b.h = w, h
    paper = b.rect(0, 0, w, h, bg, name="paper")
    b.grain(paper, 0.07 if bg != HAZ_BG else 0.09)
    return b


def box(b, x, y, w, h, fill, stroke=None, sw=4, **kw):
    if stroke:
        kw.update(stroke=stroke, stroke_width=sw)
    return b.rect(x, y, w, h, fill, **kw)


def arrow(b, x1, y1, x2, y2, color, w=4, head=16):
    """A straight horizontal or vertical arrow with a hard triangular head."""
    if y1 == y2:
        d = 1 if x2 > x1 else -1
        b.rect(min(x1, x2 - d * head), y1 - w // 2, abs(x2 - x1) - head, w, color)
        tip = f"M0 0 L{-d * head} {-head * 0.6:.1f} L{-d * head} {head * 0.6:.1f} Z"
    else:
        d = 1 if y2 > y1 else -1
        b.rect(x1 - w // 2, min(y1, y2 - d * head), w, abs(y2 - y1) - head, color)
        tip = f"M0 0 L{-head * 0.6:.1f} {-d * head} L{head * 0.6:.1f} {-d * head} Z"
    name = b.uid("head")
    b.add({"type": "shape", "shape": "path", "name": name, "path": tip, "x": x2, "y": y2, "fill": color,
           "width": 1, "height": 1})
    return name


def scanlines(b, x, y, w, h):
    d = " ".join(f"M0 {i} H{w} V{i + 2} H0 Z" for i in range(0, h - 2, 6))
    b.add({"type": "shape", "shape": "path", "name": b.uid("scan"), "path": d, "x": x, "y": y,
           "width": w, "height": h, "fill": "#000000", "opacity": 0.22})


def image(b, path, x, y, w=None, name=None):
    """Place a picture, resized to width w first (Pillow), so Vixl draws it 1:1."""
    src = Path(path)
    if w:
        im = Image.open(src)
        h = round(im.height * w / im.width)
        tmp = SRC / f"_{src.stem}-{w}.png"
        im.resize((w, h), Image.LANCZOS).save(tmp)
        src = tmp
    name = name or b.uid("pic")
    b.add({"type": "add", "path": str(src), "name": name, "x": x, "y": y})
    return name


def centered(b, text, font, size, cx, y, color, **kw):
    dx, _, tw, _ = b.measure(text, font, size)
    return b.text(text, font, size, round(cx - tw / 2 - dx), y, color, **kw)


def number(b, n, x, y, fill=C["riot"], ink=C["bone"], size=46):
    """A square numbered marker (square corners, like everything else)."""
    box(b, x, y, size, size, fill, stroke=C["ink"], sw=3)
    centered(b, str(n), MONOB, int(size * 0.55), x + size / 2, y + size * 0.18, ink)


def ink_bars(b, x, y, w, h, color, seed, bars=None):
    return b.wave(x, y, w, h, color, bars=bars or int(w / 7), seed=seed)


# ---------------------------------------------------------------- README hero
def hero():
    b = board(1600, 640, C["bone"], 101)
    b.tape(M + 1000, 18, 190, rot=12)
    slab = b.torn(30, 70, 1080, 290, C["ink"], rot=-2, jag=12, step=26, name="slab")
    b.grain(slab, 0.25)
    t = b.text("BACKTRACK", SHOUT, 236, 74, 74, C["bone"], rot=-2, name="wordmark", tracking=-4)
    b.grain(t, 0.22)
    b.rect(640, 372, 420, 12, C["riot"], rot=-2)
    b.text("SONG IN. STEMS AND MIDI OUT.", MONOB, 34, M, 420, C["ink"], tracking=3)
    b.text("A stem separator and a MIDI transcriber on ggml.\nRuns on your machine. No Python at run time.",
           MONO, 24, M, 474, C["ink"], line_height=1.4)
    # app stickers
    box(b, 1170, 90, 360, 150, C["hazard"], stroke=C["ink"], sw=6, rot=7, name="explode-sticker")
    b.text("EXPLODE", STENCIL, 88, 1194, 104, C["ink"], rot=7)
    b.text("split into stems", MONOB, 20, 1206, 200, C["ink"], rot=7)
    box(b, 1190, 300, 350, 150, C["crt"], stroke=C["phosphor"], sw=4, rot=-5, name="surveil-sticker")
    b.text("SURVEIL", CRT, 104, 1214, 314, C["phosphor"], rot=-5)
    b.text("turn it into MIDI", MONOB, 20, 1222, 404, C["phosphor"], rot=-5)
    b.text("REC ●", CRT, 34, 1420, 252, C["riot"], rot=-5)
    b.tape(1120, 222, 120, rot=-24)
    b.tape(1470, 420, 110, rot=22)
    b.text("nothing leaves it.", SCRAWL, 40, 1000, 520, C["riot"], rot=-5)
    ink_bars(b, M, 580, 1600 - 2 * M, 40, C["ink"], seed=7)
    return b, "hero"


# ---------------------------------------------------------------- doc banners
def banner_xerox(slug, shout, line, scrawl, scrap_lines, seed):
    b = board(1600, 340, C["bone"], seed)
    size = 150
    while b.measure(shout, SHOUT, size)[2] > 880:
        size -= 4
    t = b.text(shout, SHOUT, size, M, 44 + (150 - size) // 2, C["ink"], rot=-2, tracking=-2)
    b.grain(t, 0.2)
    _, _, tw, _ = b.measure(shout, SHOUT, size)
    b.rect(M + 6, 222, min(int(tw), 760), 10, C["riot"], rot=-2)
    b.text(line, MONO, 22, M, 268, MUTED)
    sx, sy, sw = 1000, 56, 530
    sh = 70 + 40 * len(scrap_lines)
    b.torn(sx, sy, sw, sh, C["xerox"], rot=2, jag=6, step=18)
    for i, ln in enumerate(scrap_lines):
        b.text(ln, MONOB, 22, sx + 36, sy + 36 + 40 * i, C["ink"], rot=2)
    b.tape(sx - 40, sy - 18, 150, rot=-18)
    b.text(scrawl, SCRAWL, 34, sx + 120, sy + sh + 14, C["riot"], rot=-4)
    return b, slug


def banner_building():
    return banner_xerox("banner-building", "BUILD IT.", "CMake, a C++17 compiler and the pinned ggml. That's the lot.",
                        "three lines. done.", ["$ npm ci", "$ cmake -B build", "$ cmake --build build -j"], 201)


def banner_gguf():
    return banner_xerox("banner-gguf", "WHAT'S IN THE BOX.", "Every model file says what it is, how it hears and where it came from.",
                        "no label, no load.", ["general.architecture", "audio.stems", "audio.sample_rate",
                                               "audio.sources  (licence)"], 202)


def banner_crt(slug, title, line, readouts, seed):
    b = board(1600, 340, C["crt"], seed)
    sx, sy, sw, sh = M, 36, 1600 - 2 * M, 268
    box(b, sx, sy, sw, sh, CRT_PANEL, stroke=C["phosphor"], sw=3)
    b.text(title, CRT, 130, sx + 36, sy + 14, C["phosphor"])
    b.text(line, MONO, 22, sx + 40, sy + 156, CRT_MUTED)
    b.text("REC ●", CRT, 46, sx + sw - 170, sy + 24, C["riot"])
    for i, r in enumerate(readouts):
        b.text(r, CRT, 30, sx + sw - 520, sy + 92 + 36 * i, C["phosphor"] if i == 0 else CRT_MUTED)
    scanlines(b, sx + 3, sy + 3, sw - 6, sh - 6)
    return b, slug


def banner_http():
    return banner_crt("banner-http", "THE WIRE", "Song Deck's bridge contract, on 127.0.0.1:8826.",
                      ["POST /transcribe", "GET  /health   GET /info", "POST /cancel"], 301)


def banner_front_end():
    b, slug = banner_crt("banner-front-end", "HOW IT HEARS", "Harmonic constant-Q: 8 harmonics x 264 bins, every 256 samples.",
                         ["22050 HZ  MONO", "36 BINS / OCTAVE", "A0 = 27.5 HZ"], 302)
    # a strip of spectrogram bars along the bottom edge
    r = random.Random(5)
    for i in range(110):
        h = int(8 + 50 * abs(math.sin(i / 9)) * r.uniform(0.3, 1))
        b.rect(M + 40 + i * 6, 290 - h, 4, h, C["dimgreen"] if i % 7 else C["phosphor"])
    return b, slug


def banner_page():
    b = board(1600, 340, HAZ_BG, 401)
    b.rect(0, 0, 1600, 24, C["hazard"])
    t = b.text("THE PAGE.", SHOUT, 150, M, 54, C["bone"], rot=-2, tracking=-2)
    b.grain(t, 0.2)
    b.text("What serve puts on 127.0.0.1:8826, and its rules.", MONO, 22, M, 270, "#8c8670")
    # three theme chips
    for i, (fill, ink, word, font, size) in enumerate([
            (C["bone"], C["ink"], "XEROX RIOT", SHOUT, 44), (C["hazard"], C["ink"], "BLAST ZONE", STENCIL, 46),
            (C["crt"], C["phosphor"], "WIRETAP", CRT, 56)]):
        x, y = 1110 + (i % 2) * 150, 50 + i * 80
        box(b, x, y, 270, 66, fill, stroke=C["phosphor"] if fill == C["crt"] else C["ink"], sw=4, rot=(-3, 2, -2)[i])
        b.text(word, font, size, x + 18, y + 6, ink, rot=(-3, 2, -2)[i])
    b.text("one hot colour each.", SCRAWL, 32, 720, 150, C["flash"], rot=-4)
    return b, "banner-page"


# ---------------------------------------------------------------- architecture
def architecture():
    b = board(1600, 1000, C["bone"], 501)
    t = b.text("HOW IT FITS.", SHOUT, 110, M, 34, C["ink"], rot=-2)
    b.grain(t, 0.2)
    b.text("One repository, one shared core, two apps, three ways in.", MONO, 22, M, 180, MUTED)
    b.text("same core. two apps.", SCRAWL, 34, 1120, 60, C["riot"], rot=-4)

    y0 = 270
    # inputs
    b.torn(M, y0 + 40, 270, 150, C["tape"], rot=-2, jag=5, step=16)
    b.text("YOUR AUDIO", MONOB, 18, M + 24, y0 + 66, C["ink"], tracking=3, rot=-2)
    b.text("song.wav\n.flac / .mp3", MONO, 24, M + 24, y0 + 102, C["ink"], rot=-2, line_height=1.3)
    b.torn(M, y0 + 330, 270, 170, C["xerox"], rot=2, jag=5, step=16)
    b.text("WEIGHTS", MONOB, 18, M + 24, y0 + 356, C["ink"], tracking=3, rot=2)
    b.text("model.gguf\narch, stems,\nfront end, licence", MONO, 22, M + 24, y0 + 392, C["ink"], rot=2, line_height=1.3)
    b.tape(M + 170, y0 + 26, 110, rot=18)

    # core
    cx, cw = 420, 330
    box(b, cx, y0, cw, 560, PANEL, stroke=C["ink"])
    b.rect(cx, y0, cw, 64, C["ink"])
    b.text("core/", SHOUT, 44, cx + 22, y0 + 4, C["bone"])
    items = ["decode WAV, FLAC, MP3", "resample", "FFT", "harmonic CQT", "music: keys, chords,\ntempo",
             "MIDI writer", "ggml + GGUF helpers", "progress + cancel"]
    yy = y0 + 92
    for it in items:
        b.rect(cx + 24, yy + 11, 10, 10, C["riot"])
        b.text(it, MONO, 21, cx + 48, yy, C["ink"], line_height=1.3)
        yy += 78 if "\n" in it else 50
    arrow(b, M + 280, y0 + 115, cx - 8, y0 + 115, C["ink"])
    arrow(b, M + 280, y0 + 415, cx - 8, y0 + 415, C["ink"])

    # apps
    ax, aw = 830, 330
    box(b, ax, y0, aw, 300, C["crt"], stroke=C["ink"])
    b.text("SURVEIL", CRT, 76, ax + 22, y0 + 6, C["phosphor"])
    b.text("apps/surveil", MONOB, 18, ax + 24, y0 + 92, CRT_MUTED, tracking=1)
    b.text("model graph\nnote picking\nMIDI + JSON", MONO, 22, ax + 24, y0 + 136, C["phosphor"], line_height=1.45)
    b.text("port 8826", MONOB, 18, ax + 24, y0 + 254, CRT_MUTED, tracking=2)
    box(b, ax, y0 + 340, aw, 220, C["hazard"], stroke=C["ink"])
    b.text("EXPLODE", STENCIL, 70, ax + 22, y0 + 352, C["ink"])
    b.text("apps/explode", MONOB, 18, ax + 24, y0 + 436, C["ink"], tracking=1)
    b.text("stems. port 8827.", MONO, 22, ax + 24, y0 + 472, C["ink"])
    b.text("coming in S0", MONOB, 18, ax + 24, y0 + 508, C["ink"], tracking=2)
    for yy in (y0 + 150, y0 + 450):
        arrow(b, cx + cw + 8, yy, ax - 8, yy, C["ink"])

    # ways in
    ox, ow = 1240, 296
    ways = [("CLI", "backtrack-surveil\nsong.wav -o bass.mid"), ("THE PAGE", "serve, then open\n127.0.0.1:8826"),
            ("SONG DECK", "a transcription\nprovider, same port")]
    for i, (head, body) in enumerate(ways):
        y = y0 + i * 190
        box(b, ox, y, ow, 160, "#f7f5ef", stroke=C["ink"])
        b.text(head, SHOUT, 40, ox + 20, y + 10, C["riot"] if i == 1 else C["ink"])
        b.text(body, MONO, 20, ox + 20, y + 76, C["ink"], line_height=1.35)
    # one bus from Surveil to the three ways in
    bus = ax + aw + 40
    b.rect(ax + aw + 8, y0 + 148, bus - (ax + aw + 8), 4, C["ink"])
    b.rect(bus - 2, y0 + 80, 4, 2 * 190 + 4, C["ink"])
    for i in range(3):
        arrow(b, bus, y0 + 80 + i * 190, ox - 8, y0 + 80 + i * 190, C["ink"])
    b.text("server/ + ui/: the HTTP layer and the page, built into each binary.", MONO, 20, ax, y0 + 600, MUTED)
    b.rect(M, 1000 - 72, 1600 - 2 * M, 3, C["ink"])
    b.text("BACKTRACK / DOCS / ARCHITECTURE", MONOB, 15, M, 1000 - 52, C["ink"], tracking=2)
    return b, "architecture"


# ---------------------------------------------------------------- Surveil pipeline
STAGES = [("DECODE", "0%", "WAV, FLAC or MP3\nto samples"),
          ("RESAMPLE", "2%", "mono,\n22 050 Hz"),
          ("FRONT END", "5-45%", "harmonic CQT:\n8 x 264 bins"),
          ("MODEL", "45-90%", "CNN on ggml: onset,\nframe, offset, bend"),
          ("NOTES", "92%", "note picking with\nkey, tempo, chords"),
          ("OUT", "100%", "MIDI file\n+ JSON notes")]


def pipeline():
    b = board(1600, 1000, C["crt"], 601)
    G = C["phosphor"]
    sx, sy, sw, sh = 40, 30, 1520, 930
    box(b, sx, sy, sw, sh, CRT_PANEL, stroke=G, sw=3)
    b.text("SURVEIL: AUDIO TO NOTES", CRT, 96, sx + 40, sy + 20, G)
    b.text("Five stages, then the file. The names are the ones GET /health reports.", MONO, 22, sx + 44, sy + 130, CRT_MUTED)
    bw, bh, gap = 410, 190, 86
    for i, (name, pct, body) in enumerate(STAGES):
        row, col = divmod(i, 3)
        col = col if row == 0 else 2 - col              # snake: left to right, then right to left
        x, y = sx + 60 + col * (bw + gap), sy + 200 + row * (bh + 90)
        box(b, x, y, bw, bh, "#06100a", stroke=G if name != "OUT" else C["phosphor"], sw=3)
        if name == "OUT":
            b.rect(x, y, bw, bh, G)
        ink = C["crt"] if name == "OUT" else G
        b.text(f"{i + 1:02d}", CRT, 40, x + 20, y + 12, ink if name == "OUT" else CRT_MUTED)
        b.text(name, CRT, 62, x + 82, y + 2, ink)
        b.text(body, MONO, 22, x + 22, y + 82, ink, line_height=1.4)
        b.text(pct, CRT, 34, x + bw - 20 - b.measure(pct, CRT, 34)[2], y + bh - 52, ink if name == "OUT" else CRT_MUTED)
        if i in (0, 1):
            arrow(b, x + bw + 8, y + bh // 2, x + bw + gap - 8, y + bh // 2, G)
        if i in (3, 4):
            arrow(b, x - 8, y + bh // 2, x - gap + 8, y + bh // 2, G)
        if i == 2:
            arrow(b, x + bw // 2, y + bh + 8, x + bw // 2, y + bh + 90 - 8, G)
    # progress bar split at the real stage boundaries
    py = sy + sh - 120
    px, pw = sx + 60, sw - 120
    b.text("PROGRESS IN /health", CRT, 30, px, py - 44, CRT_MUTED)
    b.rect(px, py, pw, 26, GRID)
    marks = [(0.0, 0.02), (0.02, 0.05), (0.05, 0.45), (0.45, 0.90), (0.90, 1.0)]
    shades = [GRID_STRONG, C["dimgreen"], G, C["dimgreen"], GRID_STRONG]
    for (a, z), shade in zip(marks, shades):
        b.rect(px + int(a * pw), py, max(3, int((z - a) * pw) - 2), 26, shade)
    for a, z, lab in [(0.05, 0.45, "FRONT END"), (0.45, 0.90, "MODEL")]:
        centered(b, lab, CRT, 28, px + (a + z) / 2 * pw, py + 34, G)
    b.text("most of the wait.", SCRAWL, 30, px + int(0.62 * pw), py - 58, C["riot"], rot=-4)
    scanlines(b, sx + 3, sy + 3, sw - 6, sh - 6)
    return b, "surveil-pipeline"


# ---------------------------------------------------------------- HTTP lifecycle
def lifecycle():
    b = board(1600, 1080, C["crt"], 701)
    G = C["phosphor"]
    sx, sy, sw, sh = 40, 30, 1520, 1010
    box(b, sx, sy, sw, sh, CRT_PANEL, stroke=G, sw=3)
    b.text("ONE JOB, START TO FINISH", CRT, 88, sx + 40, sy + 20, G)
    b.text("What the page (or Song Deck) sends, and what comes back.", MONO, 22, sx + 44, sy + 122, CRT_MUTED)
    lx, rx = sx + 230, sx + sw - 520
    top = sy + 190
    for x, lab, sub in [(lx, "CLIENT", "the page / Song Deck"), (rx, "SERVER", "backtrack-surveil :8826")]:
        box(b, x - 160, top, 320, 104, "#06100a", stroke=G, sw=3)
        centered(b, lab, CRT, 50, x, top + 12, G)
        centered(b, sub, MONO, 18, x, top + 68, CRT_MUTED)
        b.rect(x - 1, top + 104, 3, 8 * 78 + 6, GRID_STRONG)
    y = top + 136
    steps = [
        ("→", "GET /info", "", "which models and stems"),
        ("←", "200  models, sources, device", "", ""),
        ("→", "POST /transcribe", "X-Job-Id: k3x9", "audio, stem, context"),
        ("→", "GET /health   (every 400 ms)", "", "while it waits"),
        ("←", "active_jobs: k3x9  MODEL  0.64", "", "drives the progress bar"),
        ("→", "POST /cancel  {job_id: k3x9}", "optional", "changed your mind?"),
        ("←", "409  job cancelled", "", "or, if it ran to the end:"),
        ("←", "200  notes, key, tempo, midi_base64", "", "export .mid"),
    ]
    for i, (d, msg, tag, note) in enumerate(steps):
        yy = y + i * 78
        if d == "→":
            arrow(b, lx + 4, yy + 44, rx - 6, yy + 44, G if i != 5 else C["dimgreen"], w=3, head=14)
        else:
            arrow(b, rx - 4, yy + 44, lx + 6, yy + 44, G if i != 6 else DANGER, w=3, head=14)
        color = DANGER if i == 6 else G
        b.text(msg, CRT, 32, lx + 30, yy + 4, color)
        if tag:
            b.text(tag, CRT, 26, lx + 30 + b.measure(msg, CRT, 32)[2] + 24, yy + 9, CRT_MUTED)
        if note:
            b.text(note, MONO, 18, rx + 30, yy + 30, CRT_MUTED)
    b.text("one job runs. four can wait. more get 409.", MONO, 20, sx + 44, sy + sh - 52, G)
    b.text("REC ●", CRT, 46, sx + sw - 170, sy + 26, C["riot"])
    scanlines(b, sx + 3, sy + 3, sw - 6, sh - 6)
    return b, "http-lifecycle"


# ---------------------------------------------------------------- harmonic CQT figure
def hcqt():
    """Where one note lands in each harmonic channel, computed from the front end's real layout."""
    b = board(1600, 900, C["crt"], 801)
    G = C["phosphor"]
    sx, sy, sw, sh = 40, 30, 1520, 840
    box(b, sx, sy, sw, sh, CRT_PANEL, stroke=G, sw=3)
    b.text("ONE NOTE, EIGHT CHANNELS", CRT, 80, sx + 40, sy + 18, G)
    b.text("A2 (110 Hz) with 8 partials. Channel h at bin k holds the energy at h x f(k),\n"
           "so every whole-number channel lights the note's own bin: the bright column.",
           MONO, 21, sx + 44, sy + 112, CRT_MUTED, line_height=1.4)
    harmonics = [0.5, 1, 2, 3, 4, 5, 6, 7]
    B, n_bins = 36, 264
    k0 = 3 * (45 - 21) + 1                     # A2's centre bin
    cell = 4
    gx, gy, rowh = sx + 170, sy + 230, 58
    for r, h in enumerate(harmonics):
        y = gy + r * rowh
        b.rect(gx, y, n_bins * cell, rowh - 14, "#06100a", stroke=GRID, stroke_width=1)
        b.text(f"h = {h:g}", CRT, 32, sx + 40, y + 4, G)
        for m in range(1, 9):                   # partial m of the note sits at m * f0
            k = k0 + round(B * math.log2(m / h))
            if 0 <= k < n_bins:
                own = abs(m / h - 1) < 1e-9
                extra = {} if own else {"stroke": G, "stroke_width": 1}
                b.rect(gx + k * cell - 2, y + 4, cell + 4, rowh - 22, G if own else C["dimgreen"], **extra)
    # pitch axis: A0 .. C8 octave marks under the grid
    ay = gy + len(harmonics) * rowh + 8
    for p in range(21, 109):
        if p % 12 == 9:                         # every A
            k = 3 * (p - 21) + 1
            b.rect(gx + k * cell, ay, 2, 14, CRT_MUTED)
            b.text(f"A{p // 12 - 1}", CRT, 24, gx + k * cell - 8, ay + 16, CRT_MUTED)
    b.rect(gx + k0 * cell + 1, gy - 26, 2, 20, C["riot"])
    b.text("A2, bin 73", CRT, 26, gx + k0 * cell + 10, gy - 34, C["riot"])
    b.text("264 bins = 88 keys x 3", CRT, 30, gx + n_bins * cell - 300, ay + 56, G)
    b.text("the stack is the trick.", SCRAWL, 30, sx + 1080, sy + 40, C["riot"], rot=-4)
    scanlines(b, sx + 3, sy + 3, sw - 6, sh - 6)
    return b, "hcqt-channels"


# ---------------------------------------------------------------- page tour
TOUR = [("drop", "DROP", "WAV, FLAC or MP3. Click or drag."),
        ("wave", "LISTEN", "Bar waveform. Click to jump."),
        ("settings", "TELL IT", "Stem, model, key, tempo, bends."),
        ("go", "TAP THE LINE", "Starts the job."),
        ("cam", "WATCH", "REC + timestamp while it works."),
        ("case", "CASE FILE", "Black bar = unknown value."),
        ("roll", "THE ROLL", "Bright = sure. Dim = guessing.\nArrow keys read each note."),
        ("export", "EXPORT .MID", "Or [ EXPORT .JSON ].")]


def tour():
    b = board(1600, 1240, C["bone"], 901)
    boxes = json.loads((SHOTS / "boxes.json").read_text())
    scale = 0.6
    shot_w = int(1280 * scale)
    px, py = M, 150
    shot_h = Image.open(SHOTS / "surveil-done.png").height
    t = b.text("THE PAGE, MARKED UP.", SHOUT, 84, M, 26, C["ink"], rot=-1)
    b.grain(t, 0.2)
    box(b, px - 8, py - 8, shot_w + 16, int(shot_h * scale) + 16, C["ink"])
    image(b, SHOTS / "surveil-done.png", px, py, w=shot_w, name="shot")
    b.tape(px - 40, py - 22, 150, rot=-20)
    b.tape(px + shot_w - 110, py + int(shot_h * scale) - 20, 150, rot=16)
    lx = px + shot_w + 96
    for i, (key, head, line) in enumerate(TOUR, 1):
        x, y, w, h = boxes[key]
        my = py + int(y * scale) + int(h * scale) // 2 - 18
        b.rect(px + int((x + w) * scale) + 4, my + 17, shot_w - int((x + w) * scale) + 14, 3, C["riot"])
        number(b, i, px + shot_w + 18, my, size=36)
        ty = 170 + (i - 1) * 104
        number(b, i, lx, ty)
        b.text(head, SHOUT, 40, lx + 66, ty - 6, C["ink"])
        b.text(line, MONO, 19, lx + 68, ty + 46, MUTED, line_height=1.35)
    # light mode on a phone, taped on
    ph = Image.open(SHOTS / "surveil-light-phone.png")
    crop = SRC / "_phone-crop.png"
    ph.crop((0, 0, 390, 640)).resize((210, 345), Image.LANCZOS).save(crop)
    box(b, 1360, 860, 230, 365, C["ink"], rot=4, name="phone-back")
    image(b, crop, 1370, 870, name="phone")
    b.add({"type": "rotate", "target": "phone", "value": 4})
    b.tape(1420, 842, 120, rot=-10)
    b.text("light mode, same tool.", SCRAWL, 30, 980, 1130, C["riot"], rot=-4)
    return b, "page-tour"


ALL = ["hero", "banner_building", "banner_gguf", "banner_http", "banner_front_end", "banner_page",
       "architecture", "pipeline", "lifecycle", "hcqt", "tour"]

if __name__ == "__main__":
    for fn in sys.argv[2:] or ALL:
        b, slug = globals()[fn]()
        png = OUT / f"{slug}.png"
        b.build(png, SRC / f"{slug}.vixl")
        # Grain does not compress as PNG; ship JPEG.
        Image.open(png).convert("RGB").save(OUT / f"{slug}.jpg", quality=86)
        png.unlink()
        print("built", slug)
    for tmp in SRC.glob("_*.png"):
        tmp.unlink()
