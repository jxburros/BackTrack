"""The twelve shots of "BackTrack: take it apart". Each function returns a ``Scene``.

Shots cut on the soundtrack's section lines (160 BPM, one bar = 1.5 s). The stems on screen are the
soundtrack's own stems as Song Deck rendered them, the piano roll in the Surveil shots is BackTrack
Surveil's real transcription of the drum stem (``promo/surveil/drums.json``), and the chord chart is the
one Luthier wrote (``promo/luthier/chords.txt``).
"""

from __future__ import annotations

import json
import math

from kit import (
    ACID, BAR, BEAT, BLAST, BLAST_MUTED, BLAST_PANEL, BLAST_ROW, BLAST_TRACK, BLOCK, BONE, CRT, CRT_MUTED, CRT_PANEL,
    CRT_ROLL, CRTF, FLASH, FRAK, GRID, GRID_STRONG, H, HAZARD, INK, M, MONO, MONOB, MUTED, NOTE_DIM, PHOSPHOR, PINK,
    PROMO, RIOT, RIOT_TEXT, SCRAWL, SHOUT, STENCIL, TAPE, TYPE, XEROX, W, Scene, blocks, bundle, group, grain, hits,
    line, peaks, poly, rect, rot, song, text,
)

# Shot order and lengths (ms). Cuts fall on the song's section lines.
CUTS = [
    ("01-one-file", 6000),
    ("02-title", 6000),
    ("03-explode", 12000),
    ("04-solo", 6000),
    ("05-blow-it-up", 12000),
    ("06-surveil", 12000),
    ("07-tap-the-line", 6000),
    ("08-song-deck", 12000),
    ("09-hub", 6000),
    ("10-no-cloud", 6000),
    ("11-who-made-this", 6000),
    ("12-out-now", 13500),
]
START = {}
_t = 0
for _name, _ms in CUTS:
    START[_name] = _t
    _t += _ms
TOTAL = _t

SONG = song()
LENGTH = 105.0  # seconds of rendered audio the stems cover
FILE = "take-it-apart.wav"
STEMS = [("VOCALS", None), ("DRUMS", "drums"), ("BASS", "bass"), ("GUITAR", "guitars")]  # spec order, ours present
SURVEIL = json.loads((PROMO / "surveil" / "drums.json").read_text())
LANES = [(49, "CRASH"), (51, "RIDE"), (46, "OPEN HAT"), (42, "HAT"), (45, "TOM"), (38, "SNARE"), (36, "KICK")]
RIOT_SCHEMES = [(INK, BONE, SHOUT), (RIOT, BONE, BLOCK), (BONE, INK, TYPE), (TAPE, INK, FRAK), (XEROX, INK, SHOUT)]
FLYER_SCHEMES = [(INK, ACID, SHOUT), (BONE, INK, TYPE), (ACID, INK, BLOCK), (INK, PINK, FRAK), (BONE, PINK, SHOUT)]


def at(shot):
    return START[shot] / 1000


def kicks(shot, dur):
    return hits(at(shot), at(shot) + dur / 1000, (36,))


def backbeat(shot, dur):
    return hits(at(shot), at(shot) + dur / 1000, (36, 38))


def stem_values(key, t0, t1, bins):
    if key is None:
        return [0.0] * bins
    return peaks(key, t0, t1, bins)


def press(sc, target, t, depth=4):
    """A flat button pressed: it moves down-right and comes back (buttons never tilt)."""
    sc.keys(target, "translate-x", [(0, 0), (t, depth), (t + 160, 0)], "step")
    sc.keys(target, "translate-y", [(0, 0), (t, depth), (t + 160, 0)], "step")


# ============================================================================================ 1
def s01_one_file():
    """Intro, the guitar alone: four ringing chords, one slam each."""
    sc = Scene("01-one-file", 6000, BONE, seed=101)
    sc.paper()
    # The finished file: a xeroxed scrap with the whole mix as one grey waveform.
    sx, sy, sw, sh = M + 40, 680, 1180, 290
    scrap = sc.torn(sx, sy, sw, sh, XEROX, name="file-scrap", toner=0.18)
    sc.add(text("file-name", FILE, sx + 44, sy + 34, 30, INK, MONOB),
           text("file-meta", "1:45  /  48k stereo  /  one file", sx + 520, sy + 38, 24, MUTED, MONO))
    sc.bars("file-wave", sx + 44, sy + 100, sw - 88, 150, peaks("music", 0, LENGTH, 220), INK)
    tape = sc.tape(sx - 40, sy - 18, 180, -16, name="file-tape")
    sc.add(group("file", [scrap, "file-name", "file-meta", "file-wave", tape]), rot("file", -1.5))
    sc.slam("file", 0, start=1.25, dur=120)

    sc.words("IT'S ALL", SHOUT, 280, M, 20, INK, -2, name="shout-1", toner=0.22)
    sc.words("ONE FILE.", SHOUT, 280, M + 40, 320, INK, -2, name="shout-2", toner=0.22)
    sc.slam("shout-1", BAR, deg=-2)
    sc.slam("shout-2", 2 * BAR, deg=-2)
    sc.jitter("shout-1", BAR + 300, amp=1.5)
    sc.jitter("shout-2", 2 * BAR + 300, amp=1.5)

    # Bar four: the marker crosses the file out and promises to take it apart.
    cross = [(sx + 30, sy + 40), (sx + sw - 40, sy + sh - 30)]
    sc.add(line("strike-1", cross, RIOT, 14), line("strike-2", [(cross[0][0], cross[1][1]), (cross[1][0], cross[0][1])],
                                                    RIOT, 14))
    for i, s in enumerate(["strike-1", "strike-2"]):
        t = 3 * BAR + i * 180
        sc.keys(s, "trim_end", [(0, 0), (t, 0), (t + 200, 100)], "ease-out-cubic")
    sc.scrawl("we'll take it apart.", 1180, 520, 84, RIOT, -7, name="scrawl")
    sc.slam("scrawl", 3 * BAR + 380, start=1.3, dur=110)
    sc.shake("file", [3 * BAR + 10, 3 * BAR + 380], amp=8)
    return sc


# ============================================================================================ 2
def s02_title():
    """The band comes in: the wordmark and the two app stickers."""
    sc = Scene("02-title", 6000, BONE, seed=202)
    sc.paper()
    slab = sc.torn(50, 300, 1420, 410, INK, name="slab", jag=14, step=28, toner=0.25)
    word = sc.words("BACKTRACK", SHOUT, 330, 110, 300, BONE, name="wordmark", toner=0.22, tracking=-4)
    sc.add(group("logo", [slab, word]), rot("logo", -3))
    sc.flash(INK, (0,), 67)
    sc.slam("logo", 0, start=1.35, dur=130)
    sc.shake("logo", [t for t in kicks("02-title", 6000) if t > 300], amp=6, dur=120)

    sc.add(rect("ex-bg", 1390, 220, 430, 150, HAZARD, INK, 7))
    sc.words("EXPLODE", STENCIL, 112, 1424, 228, INK, name="ex-word")
    sc.add(group("explode", ["ex-bg", "ex-word"]), rot("explode", 8))
    t1 = sc.tape(1370, 196, 150, -30, name="ex-tape")
    sc.slam("explode", BAR, deg=8)
    sc.show(t1, BAR + 140)

    sc.add(rect("sv-bg", 1420, 520, 410, 140, CRT, PHOSPHOR, 5))
    sc.words("SURVEIL", CRTF, 124, 1450, 520, PHOSPHOR, name="sv-word")
    sc.words("REC ●", CRTF, 46, 1690, 468, RIOT, name="sv-rec")
    sc.add(group("surveil", ["sv-bg", "sv-word", "sv-rec"]), rot("surveil", -6))
    t2 = sc.tape(1720, 630, 140, 24, name="sv-tape")
    sc.slam("surveil", 2 * BAR, deg=-6)
    sc.show(t2, 2 * BAR + 140)
    sc.blink("sv-rec", 1000, 2 * BAR)

    sc.add(rect("underline", 1000, 760, 320, 14, RIOT), rot("underline", -3))
    sc.show("underline", 3 * BAR)
    sc.words("STEM SEPARATOR  +  MIDI TRANSCRIBER", MONOB, 34, M, 880, INK, name="kind", tracking=5)
    sc.words("runs on your machine. nothing leaves it.", MONO, 30, M, 940, MUTED, name="promise")
    sc.show("kind", 3 * BAR)
    sc.show("promise", 3 * BAR + 2 * BEAT)
    return sc


# ============================================================================================ 3
def explode_panel(sc, title_size=150):
    """The Blast Zone panel: hazard border, hazard tape across the top, EXPLODE in the stencil."""
    px, py, pw, ph = M, 60, W - 2 * M, 960
    sc.add(rect("panel", px, py, pw, ph, BLAST_PANEL, HAZARD, 5))
    sc.hazard("hazard-top", px + 3, py + 3, pw - 6, 50)
    sc.words("EXPLODE", STENCIL, title_size, px + 48, py + 74, HAZARD, name="title", toner=0.25)
    return px, py, pw, ph


def stem_row(sc, i, label, key, x, y, w, h, t0, t1, bins=180, name_size=64):
    colour = FLASH if label == "VOCALS" else HAZARD
    sc.add(rect(f"row{i}-bg", x, y, w, h, BLAST_ROW))
    sc.words(label, STENCIL, name_size, x + 26, y + (h - name_size * 1.2) / 2, BONE, name=f"row{i}-name")
    wx, ww = x + 300, w - 300 - 190
    sc.bars(f"row{i}-wave", wx, y + 14, ww, h - 28, stem_values(key, t0, t1, bins), colour)
    bx = x + w - 170
    sc.add(rect(f"row{i}-m", bx, y + (h - 64) / 2, 64, 64, "transparent", BONE, 3),
           text(f"row{i}-m-t", "M", bx + 20, y + (h - 64) / 2 + 8, 34, BONE, MONOB),
           rect(f"row{i}-s", bx + 84, y + (h - 64) / 2, 64, 64, "transparent", HAZARD, 3),
           text(f"row{i}-s-t", "S", bx + 104, y + (h - 64) / 2 + 8, 34, HAZARD, MONOB))
    return wx, ww


def s03_explode():
    """Verse 1: DETONATE, the hazard bar counts real chunks, four stems write themselves."""
    sc = Scene("03-explode", 12000, BLAST, seed=303)
    sc.paper(BLAST, 0.09)
    px, py, pw, ph = explode_panel(sc)
    sc.add(text("file-line", f"{FILE}  /  1:45  /  48k stereo", px + pw - 900, py + 104, 30, BONE, MONOB),
           text("file-sub", "4 stems. sums back to the mix.", px + pw - 900, py + 150, 26, BLAST_MUTED, MONO))
    sc.show("file-sub", 7500)

    rx, rw, rh = px + 48, pw - 96, 112
    rows = []
    for i, (label, key) in enumerate(STEMS):
        y = py + 270 + i * (rh + 18)
        wx, ww = stem_row(sc, i, label, key, rx, y, rw, rh, 0, LENGTH)
        # The waveform writes itself chunk by chunk: a cover in the row colour slides off to the right.
        sc.add(rect(f"row{i}-cover", wx - 4, y + 6, ww + 12, rh - 12, BLAST_ROW))
        rows.append((f"row{i}", y, wx, ww))
        sc.add(group(f"row{i}", [f"row{i}-{p}" for p in ("bg", "name", "wave", "m", "m-t", "s", "s-t", "cover")]))
        sc.show(f"row{i}", 1650)

    # Before: the one file, in a single row.
    sc.add(rect("mix-bg", rx, py + 270, rw, 4 * rh + 54, BLAST_ROW),
           text("mix-name", "MIX", rx + 26, py + 270 + 170, 90, BLAST_MUTED, STENCIL))
    sc.bars("mix-wave", rx + 300, py + 300, rw - 390, 4 * rh - 6, peaks("music", 0, LENGTH, 240), BLAST_MUTED)
    sc.add(group("mix", ["mix-bg", "mix-name", "mix-wave"]))
    sc.show("mix", 0, 1650)
    sc.shake("mix", [1500], amp=14, dur=150)

    # Progress: 22 chunks, each done before the bar moves (never ahead of the work).
    by = py + ph - 120
    bw = 1000
    sc.add(rect("track", rx, by, bw, 30, BLAST_TRACK), rect("fill", rx, by, 2, 30, HAZARD))
    sc.add(text("meta", "CHUNK 0/22  •  PASS 2/2 (flip + swap)", rx, by + 48, 24, BLAST_MUTED, MONOB, tracking=2))
    chunks, t0, t1 = 22, 1650, 7500
    widths, metas, covers = [(0, 2)], [(0, "CHUNK 0/22  •  PASS 2/2 (flip + swap)")], {r[0]: [(0, 0)] for r in rows}
    for k in range(1, chunks + 1):
        t = t0 + (t1 - t0) * k / chunks
        widths.append((t, round(bw * k / chunks)))
        metas.append((t, f"CHUNK {k}/22  •  PASS 2/2 (flip + swap)"))
        for name, _, _, ww in rows:
            covers[name].append((t, round((ww + 12) * k / chunks)))
    sc.keys("fill", "width", widths, "step")
    sc.keys("meta", "text", metas, "step")
    for name, _, _, _ in rows:
        sc.keys(f"{name}-cover", "translate-x", covers[name], "step")
        sc.keys(f"{name}-cover", "width", [(0, rows[0][3] + 12)] + [(t, rows[0][3] + 12 - v) for t, v in covers[name][1:]],
                "step")

    # DETONATE: flash orange, stencil, bottom right. Pressed on the bar line.
    bx, byy = px + pw - 48 - 400, py + ph - 128
    sc.add(rect("det-bg", bx, byy, 400, 88, FLASH), text("det-t", "DETONATE", bx + 64, byy + 4, 72, INK, STENCIL))
    sc.add(group("detonate", ["det-bg", "det-t"]))
    press(sc, "detonate", 1350)
    sc.flash(FLASH, (1500,), 34)

    sc.stamp("DONE", 660, py + 100, HAZARD, 70, -7, name="done")
    sc.slam("done", 7500, start=1.6, dur=110)
    sc.add(text("written", "4 stems written to stems/", rx, by + 48, 24, BONE, MONOB, tracking=2))
    sc.show("written", 7500)
    sc.keys("meta", "visible", [(0, True), (7500, False)], "step")
    sc.scrawl("boom.", 1160, py + ph - 128, 58, FLASH, -8, name="scrawl")
    sc.slam("scrawl", 9000, start=1.4, dur=100)
    sc.shake("title", [t for t in kicks("03-explode", 12000) if t > 7500], amp=4, dur=100)
    return sc


# ============================================================================================ 4
def s04_solo():
    """Pre-chorus 1: S on the drums (two bars), then the bass (one bar), then everyone. The mix follows."""
    sc = Scene("04-solo", 6000, BLAST, seed=404)
    sc.paper(BLAST, 0.09)
    t0 = at("04-solo")
    sc.words("SOLO THE DRUMS.", SHOUT, 150, M, 50, BONE, -2, name="shout", toner=0.2)
    sc.keys("shout", "text", [(0, "SOLO THE DRUMS."), (2 * BAR, "SOLO THE BASS."), (3 * BAR, "ALL BACK IN.")], "step")
    sc.shake("shout", [0, 2 * BAR, 3 * BAR], amp=8)

    rx, rw, rh = M, W - 2 * M, 170
    solo = {"DRUMS": (0, 2 * BAR), "BASS": (2 * BAR, 3 * BAR)}
    for i, (label, key) in enumerate(STEMS):
        y = 290 + i * (rh + 18)
        wx, ww = stem_row(sc, i, label, key, rx, y, rw, rh, t0, t0 + 6, 150, 76)
        # A muted stem's waveform drops to 25 %; S lights in hazard when that stem is soloed.
        frames = [(0, 1.0 if label == "DRUMS" else 0.25)]
        frames.append((2 * BAR, 1.0 if label == "BASS" else 0.25))
        frames.append((3 * BAR, 1.0))
        sc.keys(f"row{i}-wave", "opacity", frames, "step")
        if label in solo:
            a, b = solo[label]
            sc.add(rect(f"row{i}-s-on", rx + rw - 170 + 84, y + (rh - 64) / 2, 64, 64, HAZARD),
                   text(f"row{i}-s-on-t", "S", rx + rw - 170 + 104, y + (rh - 64) / 2 + 8, 34, INK, MONOB))
            sc.add(group(f"row{i}-solo", [f"row{i}-s-on", f"row{i}-s-on-t"]))
            sc.show(f"row{i}-solo", a, b)
        if i == 0:
            head = (wx, ww)
    # The playhead crosses the six seconds that are playing.
    sc.add(rect("playhead", head[0], 290 - 10, 4, 4 * (rh + 18) + 2, BONE))
    sc.keys("playhead", "translate-x", [(0, 0), (6000, head[1])], "linear")
    return sc


# ============================================================================================ 5
def s05_blow_it_up():
    """Chorus 1: the one shout, then the stems land as torn scraps, one a bar."""
    sc = Scene("05-blow-it-up", 12000, HAZARD, seed=505)
    sc.paper(HAZARD, 0.08)
    for name, y in (("band-top", 30), ("band-bottom", H - 100)):
        sc.hazard(name, -1300, y, W + 2600, 70, 52)
        sc.keys(name, "translate-x", [(0, 0), (12000, -104 * 10 if name == "band-top" else 104 * 10)], "linear")
    _, _, sw, _ = sc.measure("BLOW IT UP.", STENCIL, 330)
    shout = sc.words("BLOW IT UP.", STENCIL, 330, (W - sw) / 2, 250, INK, -3, name="shout", toner=0.25)
    sc.flash(INK, (0,), 67)
    sc.slam(shout, 0, start=2.0, dur=150, deg=-3)
    k = kicks("05-blow-it-up", 12000)
    sc.pulse(shout, [t for t in k if t < 2 * BAR], 0.035)
    # From bar 3 the shout moves up and the stems land below it.
    sc.keys(shout, "translate-y", [(0, 0), (2 * BAR, 0), (2 * BAR + 220, -150)], "ease-out-expo")
    sc.keys(shout, "scale", [(2 * BAR, 1), (2 * BAR + 220, 0.62)], "ease-out-expo")
    t0 = at("05-blow-it-up")
    scraps = [("DRUMS", "drums", 2 * BAR, -4), ("BASS", "bass", 3 * BAR, 3), ("GUITAR", "guitars", 4 * BAR, -2),
              ("VOCALS", None, 5 * BAR, 4)]
    for i, (label, key, t, deg) in enumerate(scraps):
        x, y = 110 + i * 440, 470
        s = sc.torn(x, y, 410, 320, INK, name=f"scrap{i}-bg", toner=0.2)
        colour = FLASH if key is None else HAZARD
        sc.words(label, STENCIL, 80, x + 32, y + 30, colour, name=f"scrap{i}-name")
        sc.bars(f"scrap{i}-wave", x + 32, y + 150, 346, 130, stem_values(key, t0, t0 + 12, 70), colour, 5, 3)
        parts = [s, f"scrap{i}-name", f"scrap{i}-wave"]
        if key is None:
            sc.add(text(f"scrap{i}-note", "(instrumental)", x + 32, y + 240, 26, BONE, MONOB))
            parts.append(f"scrap{i}-note")
        tp = sc.tape(x + 130, y - 22, 160, deg * 3, name=f"scrap{i}-tape")
        sc.add(group(f"scrap{i}", parts + [tp]), rot(f"scrap{i}", deg))
        sc.fling(f"scrap{i}", t, (-1 if i % 2 else 1) * 900, -700, 180, spin=deg * 6)
        sc.shake(f"scrap{i}", [t + 180], amp=6, dur=100)
    sc.words("4 stems. sums back to the mix.", MONOB, 40, 110, 860, INK, name="line", tracking=2)
    sc.show("line", 6 * BAR)
    sc.stamp("DONE", 1500, 840, INK, 64, -7, name="count")
    sc.slam("count", 6 * BAR + 2 * BEAT, start=1.6, dur=110)
    return sc


# ============================================================================================ 6
def surveil_screen(sc, title_size=150):
    px, py, pw, ph = M, 60, W - 2 * M, 960
    sc.add(rect("screen", px, py, pw, ph, CRT_PANEL, PHOSPHOR, 4))
    sc.words("SURVEIL", CRTF, title_size, px + 44, py + 10, PHOSPHOR, name="title")
    sc.words("REC", CRTF, 64, px + pw - 250, py + 34, RIOT, name="rec-word")
    sc.add({"type": "shape", "shape": "ellipse", "name": "rec-dot", "x": px + pw - 140, "y": py + 40, "width": 50,
            "height": 50, "fill": RIOT})
    return px, py, pw, ph


def scanlines(sc):
    boxes = [(-60, y, W + 120, 2) for y in range(-60, H + 60, 6)]
    sc.add(blocks("scanlines", boxes, "#000000"), {"type": "layer-intent", "targets": ["scanlines"],
                                                   "role": "decoration", "allow_crop": True})
    sc.add({"type": "keyframe", "target": "scanlines", "property": "opacity", "time": 0, "value": 0.22})


def timecode(seconds):
    s = int(seconds)
    return f"00:{s // 60:02d}:{s % 60:02d}:{int((seconds - s) * 30):02d}"


def s06_surveil():
    """Verse 2: Surveil writes the drum stem down as it plays, bright where it is sure."""
    sc = Scene("06-surveil", 12000, CRT, seed=606)
    sc.paper(CRT, 0.06)
    px, py, pw, ph = surveil_screen(sc)
    t0 = at("06-surveil")
    sc.add(text("cam", f"CAM 02   2026-10-19   {timecode(t0)}", px + pw - 620, py + 116, 36, CRT_MUTED, CRTF))
    sc.keys("cam", "text", [(int(ms), f"CAM 02   2026-10-19   {timecode(t0 + ms / 1000)}")
                            for ms in range(0, 12000, 100)], "step")
    sc.blink("rec-word")
    sc.blink("rec-dot")

    sc.add(text("case", "CASE FILE:  drums.mid", px + 48, py + 190, 54, PHOSPHOR, CRTF),
           text("subject", "SUBJECT  drums     KEY  E minor     TEMPO  160     SOURCE", px + 48, py + 256, 38,
                CRT_MUTED, CRTF),
           rect("redact", px + 1010, py + 262, 300, 32, PHOSPHOR))

    # The roll: seven drum lanes across the verse's eight bars, notes appearing as they are heard.
    rx, ry, rw = px + 210, py + 330, pw - 260
    lane = 60
    rh = lane * len(LANES)
    sc.add(rect("roll", rx, ry, rw, rh, CRT_ROLL, GRID_STRONG, 2))
    beats = [(rx + rw * b / 32, ry, 1, rh) for b in range(1, 32) if b % 4]
    barsl = [(rx + rw * b / 32, ry, 2, rh) for b in range(4, 32, 4)]
    lanes = [(rx, ry + lane * i, rw, 1) for i in range(1, len(LANES))]
    sc.add(blocks("grid", beats + lanes, GRID), blocks("grid-bars", barsl, GRID_STRONG))
    for i, (_, label) in enumerate(LANES):
        sc.add(text(f"lane{i}", label, px + 48, ry + lane * i + 12, 32, CRT_MUTED, CRTF))
    row = {p: i for i, (p, _) in enumerate(LANES)}
    notes = [n for n in SURVEIL["notes"] if t0 <= n["start"] < t0 + 12 and n["pitch"] in row]
    for j, n in enumerate(notes):
        x = rx + rw * (n["start"] - t0) / 12
        y = ry + lane * row[n["pitch"]] + 10
        w = max(14, rw * (n["end"] - n["start"]) / 12)
        sure = n["confidence"] >= 0.7
        sc.add(rect(f"n{j}", x, y, w, lane - 20, PHOSPHOR if sure else NOTE_DIM,
                    None if sure else PHOSPHOR, 2))
        sc.show(f"n{j}", int((n["start"] - t0) * 1000))
    sc.add(rect("head", rx, ry, 3, rh, PHOSPHOR))
    sc.keys("head", "translate-x", [(0, 0), (12000, rw)], "linear")

    by = ry + rh + 30
    counts = [(0, "NOTES 0     CONF -.--     BENDS OFF")]
    for k in range(1, len(notes) + 1):
        conf = sum(n["confidence"] for n in notes[:k]) / k
        counts.append((int((notes[k - 1]["start"] - t0) * 1000), f"NOTES {k}     CONF {conf:.2f}     BENDS OFF"))
    sc.add(text("summary", counts[0][1], px + 48, by, 44, PHOSPHOR, CRTF))
    sc.keys("summary", "text", counts, "step")
    sc.scrawl("bright = sure.  dim = guessing.", px + pw - 760, by - 4, 42, RIOT, -3, name="scrawl")
    sc.show("scrawl", 4 * BAR)
    ay = py + ph - 110
    sc.add(rect("tap-bg", px + 48, ay, 520, 76, "transparent", PHOSPHOR, 3),
           text("tap-t", "[ TAP THE LINE ]", px + 92, ay + 2, 60, PHOSPHOR, CRTF),
           rect("exp-bg", px + 600, ay, 340, 76, PHOSPHOR),
           text("exp-t", "EXPORT .MID", px + 640, ay + 2, 60, CRT, CRTF))
    scanlines(sc)
    return sc


# ============================================================================================ 7
def s07_tap_the_line():
    """Pre-chorus 2: TAP THE LINE, the real run time on the stage line, EXPORT .MID."""
    sc = Scene("07-tap-the-line", 6000, CRT, seed=707)
    sc.paper(CRT, 0.06)
    px, py, pw, ph = surveil_screen(sc, 120)
    sc.blink("rec-word")
    sc.blink("rec-dot")
    run = 3900  # backtrack-surveil took 3.9 s on the whole 105 s drum stem, on 4 CPU cores
    for n in ("rec-word", "rec-dot"):
        sc.keys(n, "opacity", [(0, 1), (run, 1), (run + 1, 0)], "step")

    bx, byy = px + 48, py + 250
    sc.add(rect("tap-bg", bx, byy, 760, 120, "transparent", PHOSPHOR, 4),
           text("tap-t", "[ TAP THE LINE ]", bx + 70, byy + 4, 96, PHOSPHOR, CRTF))
    sc.add(group("tap", ["tap-bg", "tap-t"]))
    press(sc, "tap", 0, 6)
    sc.add(rect("tap-lit", bx, byy, 760, 120, PHOSPHOR))
    sc.keys("tap-lit", "opacity", [(0, 0.9), (150, 0)], "step")

    gy = byy + 200
    sc.add(rect("track", bx, gy, pw - 96, 36, GRID), rect("fill", bx, gy, 2, 36, PHOSPHOR))
    stages = [(0, "DECODE"), (300, "HCQT"), (700, "MODEL"), (3400, "NOTES")]
    fills, lines = [(0, 2)], []
    for ms in range(0, run + 1, 100):
        stage = [s for t, s in stages if t <= ms][-1]
        pct = min(100, round(100 * ms / run))
        fills.append((ms, max(2, round((pw - 96) * pct / 100))))
        lines.append((ms, f"{stage} • {pct}% • {ms / 1000:.1f} S"))
    sc.keys("fill", "width", fills, "step")
    sc.add(text("stage", lines[0][1], bx, gy + 56, 72, PHOSPHOR, CRTF))
    sc.keys("stage", "text", lines, "step")

    sc.add(text("result", "", bx, gy + 170, 56, PHOSPHOR, CRTF))
    sc.keys("result", "text", [(0, ""), (run, f"NOTES {len(SURVEIL['notes'])}     CONF "
                                         f"{sum(n['confidence'] for n in SURVEIL['notes']) / len(SURVEIL['notes']):.2f}"
                                         "     MODEL surveil-drums-0.1.0")], "step")
    ex, ey = px + pw - 48 - 420, py + ph - 150
    sc.add(rect("exp-bg", ex, ey, 420, 100, PHOSPHOR), text("exp-t", "EXPORT .MID", ex + 52, ey + 6, 80, CRT, CRTF))
    sc.add(group("export", ["exp-bg", "exp-t"]))
    press(sc, "export", 3 * BAR, 6)
    sc.add(rect("toast-bg", px + 48, ey + 10, 700, 80, INK, PHOSPHOR, 2),
           text("toast-t", f"drums.mid saved. {len(SURVEIL['notes'])} notes.", px + 76, ey + 30, 30, PHOSPHOR, MONOB))
    sc.add(group("toast", ["toast-bg", "toast-t"]))
    sc.show("toast", 3 * BAR + 160)
    sc.stamp("DONE", 1300, py + 250, PHOSPHOR, 80, -7, name="done")
    sc.slam("done", run + 100, start=1.6, dur=110)
    scanlines(sc)
    return sc


# ============================================================================================ 8
def s08_song_deck():
    """Chorus 2: Song Deck's own Services screen, xeroxed and taped down; the two apps are what it calls."""
    sc = Scene("08-song-deck", 12000, BONE, seed=808)
    sc.paper()
    sc.words("PLUGS RIGHT INTO", SHOUT, 150, M, 40, INK, -2, name="shout-1", toner=0.22)
    sc.words("SONG DECK.", SHOUT, 150, M + 20, 200, RIOT, -2, name="shout-2", toner=0.22)
    sc.slam("shout-1", 0, deg=-2)
    sc.slam("shout-2", 2 * BEAT, deg=-2)

    # The screenshot (Song Deck docs/images/services.png, 1600 x 720) as a photocopy.
    sx, sy, s = M, 390, 0.78
    sc.add({"type": "add", "path": str(PROMO / "art" / "song-deck-services.png"), "name": "shot", "x": sx, "y": sy},
           {"type": "resize", "target": "shot", "width": round(1600 * s), "height": round(720 * s)},
           {"type": "effect", "target": "shot", "name": "grayscale", "amount": 1},
           {"type": "effect", "target": "shot", "name": "invert", "amount": 1},
           {"type": "effect", "target": "shot", "name": "contrast", "amount": 0.3},
           grain("shot", 0.3, 81))
    tp1 = sc.tape(sx - 50, sy - 20, 190, -18, name="shot-tape-1")
    tp2 = sc.tape(sx + 1600 * s - 120, sy + 720 * s - 30, 190, 14, name="shot-tape-2")
    sc.add(group("screenshot", ["shot", tp1, tp2]), rot("screenshot", 1.5))
    sc.fling("screenshot", BAR, 0, 900, 220)

    # Marker rings around "Separate into stems" and "Make MIDI from audio", arrows out to the stickers.
    def ring(name, cx, cy, rw, rh, t):
        pts = [(cx + rw * math.cos(a) * (1 + 0.03 * math.sin(3 * a)), cy + rh * math.sin(a))
               for a in [2 * math.pi * k / 48 - 0.4 for k in range(54)]]
        sc.add(line(name, pts, RIOT, 9))
        sc.keys(name, "trim_end", [(0, 0), (t, 0), (t + 300, 100)], "ease-out-cubic")

    def arrow(name, a, b, t):
        sc.add(line(name, [a, b], RIOT, 9))
        sc.keys(name, "trim_end", [(0, 0), (t, 0), (t + 220, 100)], "ease-out-cubic")
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        head = [(b[0] - 40 * math.cos(ang - 0.45), b[1] - 40 * math.sin(ang - 0.45)), b,
                (b[0] - 40 * math.cos(ang + 0.45), b[1] - 40 * math.sin(ang + 0.45))]
        sc.add(line(f"{name}-head", head, RIOT, 9))
        sc.show(f"{name}-head", t + 220)

    row1 = (sx + 860 * s, sy + 252 * s)
    row2 = (sx + 860 * s, sy + 304 * s)
    ring("ring-1", *row1, 360 * s, 34 * s, 2 * BAR)
    arrow("arrow-1", (row1[0] + 380 * s, row1[1] - 10), (1430, 360), 2 * BAR + 330)
    ring("ring-2", *row2, 360 * s, 34 * s, 4 * BAR)
    arrow("arrow-2", (row2[0] + 380 * s, row2[1] + 10), (1430, 690), 4 * BAR + 330)

    sc.add(rect("ex-bg", 1440, 290, 400, 140, HAZARD, INK, 7))
    sc.words("EXPLODE", STENCIL, 104, 1470, 296, INK, name="ex-word")
    sc.add(group("explode", ["ex-bg", "ex-word"]), rot("explode", 6))
    sc.slam("explode", 2 * BAR + 2 * BEAT, deg=6)
    sc.add(rect("sv-bg", 1450, 620, 390, 130, CRT, PHOSPHOR, 5))
    sc.words("SURVEIL", CRTF, 116, 1478, 618, PHOSPHOR, name="sv-word")
    sc.add(group("surveil", ["sv-bg", "sv-word"]), rot("surveil", -5))
    sc.slam("surveil", 4 * BAR + 2 * BEAT, deg=-5)

    sc.words("POST /separate   → :8827", MONOB, 28, 1440, 820, INK, name="api-1")
    sc.words("POST /transcribe → :8826", MONOB, 28, 1440, 866, INK, name="api-2")
    sc.show("api-1", 6 * BAR)
    sc.show("api-2", 6 * BAR + 2 * BEAT)
    sc.words("or on their own.", SCRAWL, 54, 1440, 940, RIOT, -5, name="scrawl")
    sc.slam("scrawl", 7 * BAR, start=1.3, dur=100)
    return sc


# ============================================================================================ 9
def s09_hub():
    """Breakdown, palm-muted: the hub. Drop a track; pick the app."""
    sc = Scene("09-hub", 6000, BONE, seed=909)
    sc.paper()
    sc.add(rect("appbar", -60, 0, W + 120, 84, INK),
           text("brand", "BACKTRACK", M, 12, 46, BONE, SHOUT),
           text("nav", "APPS      RUNS      SETTINGS", W - M - 640, 28, 24, BONE, MONOB, tracking=4),
           rect("nav-on", W - M - 640, 66, 86, 5, RIOT))
    sc.words("DROP A TRACK.", SHOUT, 190, M, 120, INK, -2, name="shout", toner=0.22)
    sc.slam("shout", 0, deg=-2)

    dx, dy, dw, dh = M, 420, 1000, 380
    zone = sc.torn(dx, dy, dw, dh, XEROX, name="zone", toner=0.15)
    dashes = []
    for x in range(int(dx + 30), int(dx + dw - 30), 34):
        dashes += [(x, dy + 30, 18, 4), (x, dy + dh - 34, 18, 4)]
    for y in range(int(dy + 30), int(dy + dh - 30), 34):
        dashes += [(dx + 30, y, 4, 18), (dx + dw - 34, y, 4, 18)]
    sc.add(blocks("zone-dash", dashes, INK))
    sc.add(text("zone-t", "WAV / FLAC / MP3 — or click to pick", dx + 120, dy + 168, 34, INK, MONOB))
    # A file lands, the zone goes accent (data-over), then the file is in.
    sc.add(rect("zone-over", dx + 30, dy + 30, dw - 60, dh - 60, RIOT))
    sc.show("zone-over", 2 * BEAT + 300, 4 * BEAT)
    sc.add(text("zone-over-t", f"DROP {FILE}", dx + 160, dy + 168, 38, BONE, MONOB))
    sc.show("zone-over-t", 2 * BEAT + 300, 4 * BEAT)
    sc.add(rect("file-bg", dx + 620, dy - 60, 360, 90, BONE, INK, 3), text("file-t", FILE, dx + 640, dy - 34, 26, INK, MONOB))
    sc.add(group("file", ["file-bg", "file-t"]))
    sc.fling("file", BEAT, 900, -500, 300, spin=20, t_out=4 * BEAT)
    sc.add(text("zone-in", f"{FILE}  •  1:45  •  48k stereo", dx + 120, dy + 168, 30, INK, MONOB))
    sc.show("zone-in", 4 * BEAT)
    sc.keys("zone-t", "visible", [(0, True), (2 * BEAT + 300, False)], "step")
    t1 = sc.tape(dx - 40, dy - 20, 170, -20, name="zone-tape")

    tx = 1200
    sc.add(rect("tile-ex", tx, 420, 620, 180, HAZARD, INK, 5),
           text("tile-ex-t", "EXPLODE", tx + 30, 434, 88, INK, STENCIL),
           text("tile-ex-s", "split into stems", tx + 34, 540, 26, INK, MONOB))
    sc.add(group("tile-explode", ["tile-ex", "tile-ex-t", "tile-ex-s"]))
    sc.add(rect("tile-sv", tx, 620, 620, 180, CRT, INK, 5),
           text("tile-sv-t", "SURVEIL", tx + 30, 624, 100, PHOSPHOR, CRTF),
           text("tile-sv-s", "turn it into MIDI", tx + 34, 740, 26, PHOSPHOR, MONOB))
    sc.add(group("tile-surveil", ["tile-sv", "tile-sv-t", "tile-sv-s"]))
    sc.slam("tile-explode", 2 * BAR, start=1.3)
    sc.slam("tile-surveil", 2 * BAR + 2 * BEAT, start=1.3)

    sc.add(rect("rip-shadow", M + 8, 868, 360, 84, RIOT), rect("rip-bg", M, 860, 360, 84, INK),
           text("rip-t", "RIP IT →", M + 70, 876, 40, BONE, MONOB, tracking=3))
    sc.add(group("rip", ["rip-bg", "rip-t"]))
    sc.show("rip-shadow", 3 * BAR, 3 * BAR + 200)
    press(sc, "rip", 3 * BAR + 200, 6)
    sc.add(text("caption", "runs on your machine. nothing leaves it.", M + 420, 892, 28, MUTED, MONO))
    return sc


# ============================================================================================ 10
def s10_no_cloud():
    """Bridge, building: one card every two beats, then four on the beat."""
    sc = Scene("10-no-cloud", 6000, BONE, seed=1010)
    cards = [
        ("NO CLOUD.", BONE, INK), ("NO PYTHON.", INK, BONE), ("WIN. MAC. LINUX.", RIOT, BONE),
        ("CPU. CUDA.\nVULKAN. METAL.", XEROX, INK), ("YOUR WEIGHTS.\nYOUR NOISE.", TAPE, INK), ("MIT LICENSED.", INK, BONE),
        ("TAKE", BONE, INK), ("IT", INK, BONE), ("ALL", RIOT, BONE), ("APART.", INK, HAZARD),
    ]
    t = 0
    for i, (word, bg, fg) in enumerate(cards):
        dur = 2 * BEAT if i < 6 else BEAT
        bgn = sc.paper(bg, 0.09, name=f"card{i}-paper", seed=40 + i)
        lines = word.split("\n")
        size = 300 if len(lines) == 1 and len(word) < 12 else 210
        if len(lines) == 1 and len(word) <= 6:
            size = 460
        deg = (-3, 2, -2, 3, -1.5, 2.5, -4, 3, -3, 2)[i]
        y0 = (H - size * 1.05 * len(lines)) / 2 - size * 0.12
        names_ = [bgn]
        for j, ln in enumerate(lines):
            names_.append(sc.words(ln, SHOUT, size, M + 40 + j * 40, y0 + j * size * 1.05, fg, deg,
                                   name=f"card{i}-l{j}", toner=0.25))
        sc.add(group(f"card{i}", names_))
        sc.show(f"card{i}", t, t + dur if i < len(cards) - 1 else None)
        sc.shake(f"card{i}", [t], amp=12, dur=120)
        t += dur
    return sc


# ============================================================================================ 11
def s11_who_made_this():
    """Final chorus: the credits, as a pinboard. Everything here is real."""
    sc = Scene("11-who-made-this", 6000, BONE, seed=1111)
    sc.paper()
    sc.words("WHO MADE THIS NOISE?", SHOUT, 150, M, 30, INK, -2, name="shout", toner=0.22)
    sc.slam("shout", 0, deg=-2)
    chart = [s for s in SONG["sections"] if s["name"] == "Chorus 1"][0]
    scraps = [
        ("CHORDS + RIFFS", "LUTHIER", f"E minor  •  160 bpm\nchorus: {'  '.join(chart['chords'][:4])}\neighth pump, palm-muted breakdown",
         TAPE, -3),
        ("DRUMS, ARRANGEMENT, MIX", "SONG DECK", "punk, E minor, 68 bars\n\"double time on the drums\"\nrendered + mastered offline",
         XEROX, 2),
        ("DRUM MIDI", "SURVEIL", f"{len(SURVEIL['notes'])} notes from the drum stem\nmodel surveil-drums-0.1.0\n3.9 s on 4 CPU cores",
         BONE, -2),
        ("EVERY FRAME", "VIXL", "12 editable scene files\nkeyframes, no stock footage\nexported with film-export",
         TAPE, 1.5),
    ]
    for i, (label, name, body, bg, deg) in enumerate(scraps):
        x, y = M + (i % 2) * 880, 250 + (i // 2) * 420
        s = sc.torn(x, y, 820, 360, bg, name=f"c{i}-bg", toner=0.12, stroke=INK if bg == BONE else None,
                    stroke_width=3 if bg == BONE else None)
        sc.add(text(f"c{i}-label", label, x + 40, y + 34, 24, MUTED, MONOB, tracking=4),
               text(f"c{i}-name", name, x + 40, y + 70, 110, RIOT if i == 1 else INK, SHOUT),
               text(f"c{i}-body", body, x + 40, y + 216, 28, INK, TYPE, line_height=1.3))
        tp = sc.tape(x + 300, y - 22, 170, deg * 4, name=f"c{i}-tape")
        sc.add(group(f"c{i}", [s, f"c{i}-label", f"c{i}-name", f"c{i}-body", tp]), rot(f"c{i}", deg))
        sc.slam(f"c{i}", i * BAR + (0 if i else 2 * BEAT), start=1.35, dur=110, deg=deg)
    return sc


# ============================================================================================ 12
def s12_out_now():
    """Final chorus and outro: the gig flyer. The tear-off tabs go on the last hits."""
    sc = Scene("12-out-now", 13500, BONE, seed=1212)
    sc.paper()
    px, py, pw, ph = 560, 50, 800, 980
    poster = sc.torn(px, py, pw, ph, PINK, name="poster", jag=12, toner=0.3)
    sc.add(rot(poster, -2))
    sc.slam(poster, 0, start=1.25, dur=120)
    tps = [sc.tape(px + 280, py - 24, 220, -4, name="tape-top"), sc.tape(px + 40, py + ph - 40, 200, 24, name="tape-low")]
    for tp in tps:
        sc.show(tp, 120)

    sc.words("Backtrack", FRAK, 170, px + 70, py + 40, INK, -2, name="brand")
    sc.slam("brand", 2 * BEAT, start=1.4, deg=-2)
    sc.add(poly("slab", [(px - 20, py + 300), (px + pw + 30, py + 280), (px + pw + 20, py + 560), (px - 10, py + 575)],
                INK), text("slab-1", "EXPLODE", px + 30, py + 270, 170, ACID, SHOUT),
           text("slab-2", "+ SURVEIL", px + 40, py + 440, 110, ACID, SHOUT))
    sc.add(grain("slab-1", 0.25, 5), grain("slab-2", 0.25, 6), group("apps", ["slab", "slab-1", "slab-2"]), rot("apps", -3))
    sc.slam("apps", BAR, start=1.5, deg=-3)
    _, letters, _ = sc.ransom("OUT NOW", px + 70, py + 640, 92, FLYER_SCHEMES, name="ransom")
    for i, g in enumerate(letters):
        sc.slam(g, 2 * BAR + i * BEAT, start=1.8, dur=100)
    sc.words("TWO APPS.  ZERO CLOUD.", TYPE, 44, px + 70, py + 790, INK, -1, name="line-1")
    sc.words("win / mac / linux  •  free  •  MIT", TYPE, 32, px + 70, py + 850, INK, -1, name="line-2")
    sc.show("line-1", 4 * BAR)
    sc.show("line-2", 4 * BAR + 2 * BEAT)
    sc.scrawl("bring your own song.", px + 250, py + 900, 50, BONE, -5, name="scrawl")
    sc.slam("scrawl", 5 * BAR, start=1.3, dur=100)

    # Left: what it is, plainly. Right: the tear-off tabs with the address.
    sc.words("STEM SEPARATOR", MONOB, 30, M, 380, INK, name="kind-1", tracking=4)
    sc.words("+ MIDI TRANSCRIBER", MONOB, 30, M, 430, INK, name="kind-2", tracking=4)
    sc.words("github.com/\njxburros/BackTrack", MONOB, 30, M, 820, INK, name="url", line_height=1.3)
    sc.add(rect("url-line", M, 800, 380, 4, RIOT))
    for n in ("kind-1", "kind-2", "url", "url-line"):
        sc.show(n, BAR)
    tabs = []
    url = "github.com/jxburros/BackTrack"
    _, _, uw, uh = sc.measure(url, MONOB, 17)
    for i in range(6):
        x, y = 1430 + (i % 3) * 160, 110 + (i // 3) * 400
        nm = f"tab{i}"
        sc.add(rect(f"{nm}-bg", x, y, 140, 340, BONE, INK, 3),
               text(f"{nm}-t", url, x + 70 - uw / 2, y + 170 - uh / 2, 17, INK, MONOB), rot(f"{nm}-t", 90))
        sc.add(group(nm, [f"{nm}-bg", f"{nm}-t"]), rot(nm, (-2, 1.5, 1, -2.5, 2, -1)[i]))
        tabs.append(nm)
        sc.slam(nm, 4 * BAR + i * 120, start=1.3, dur=90)
    # The outro's last hits: a tab torn off on each, falling out of frame.
    for i, t in enumerate((6 * BAR, 7 * BAR, 8 * BAR, 8 * BAR + 2 * BEAT)):
        nm = tabs[i]
        sc.keys(nm, "translate-y", [(t, 0), (t + 900, 1200)], "ease-in-cubic")
        sc.keys(nm, "rotation", [(t, 0), (t + 900, (-40, 35, -25, 50)[i])], "ease-in-cubic")
    sc.flash(INK, (8 * BAR,), 67)
    sc.shake(poster, [0, 8 * BAR], amp=8)
    return sc


SCENES = [
    s01_one_file, s02_title, s03_explode, s04_solo, s05_blow_it_up, s06_surveil, s07_tap_the_line, s08_song_deck,
    s09_hub, s10_no_cloud, s11_who_made_this, s12_out_now,
]
