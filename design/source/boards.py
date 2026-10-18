"""Build the BackTrack design-language boards with Vixl."""
import sys
from pathlib import Path

from PIL import Image

from kit import *

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "boards"
SRC = HERE / "vixl"
OUT.mkdir(parents=True, exist_ok=True)
SRC.mkdir(parents=True, exist_ok=True)

RANSOM = [(C["ink"], C["bone"], SHOUT), (C["riot"], C["bone"], BLOCK), (C["bone"], C["ink"], TYPE),
          (C["tape"], C["ink"], FRAK), (C["xerox"], C["ink"], SHOUT)]


def footer(b, page, title, ink, dim):
    b.rect(M, H - 72, W - 2 * M, 3, ink)
    b.text(f"BACKTRACK / DESIGN LANGUAGE / {title}", MONOB, 15, M, H - 52, ink, tracking=2)
    b.text(f"{page:02d} / 07", MONOB, 15, W - M - 70, H - 52, dim, tracking=2)


def bg(b, color, amount=0.07):
    n = b.rect(0, 0, W, H, color, name="paper")
    b.grain(n, amount)


# ---------------------------------------------------------------- 01 cover
def cover():
    b = Board(C["bone"], seed=11)
    bg(b, C["bone"])
    b.tape(W - 260, 40, 210, rot=14)
    slab = b.torn(40, 190, 1180, 330, C["ink"], rot=-3, jag=12, step=26, name="slab")
    b.grain(slab, 0.25)
    t = b.text("BACKTRACK", SHOUT, 262, 96, 196, C["bone"], rot=-3, name="wordmark", tracking=-4)
    b.grain(t, 0.22)
    b.rect(1000, 520, 260, 12, C["riot"], rot=-3)
    b.ransom("DESIGN LANGUAGE", M + 8, 600, 70, RANSOM)
    b.text("rough on purpose.  v0.1", SCRAWL, 46, 1010, 710, C["riot"], rot=-5)

    # app stickers
    ex = b.rect(1180, 150, 330, 120, C["hazard"], rot=8, name="explode-sticker", stroke=C["ink"], stroke_width=6)
    b.text("EXPLODE", STENCIL, 80, 1206, 166, C["ink"], rot=8, name="explode-word")
    sv = b.rect(1220, 330, 320, 110, C["crt"], rot=-6, name="surveil-sticker", stroke=C["phosphor"], stroke_width=4)
    b.text("SURVEIL", CRT, 96, 1246, 338, C["phosphor"], rot=-6, name="surveil-word")
    b.text("REC ●", CRT, 34, 1418, 296, C["riot"], rot=-6)
    b.tape(1160, 125, 120, rot=-30)
    b.tape(1460, 410, 110, rot=24)

    b.text("STEM SEPARATOR  +  MIDI TRANSCRIBER", MONOB, 22, M, 812, C["ink"], tracking=3)
    b.text("No Python at run time. No mercy. Your weights, your noise.", MONO, 22, M, 848, "#4a4741")
    footer(b, 1, "COVER", C["ink"], "#6b675f")
    return b, "01-cover"


# ---------------------------------------------------------------- 02 rules
def rules():
    b = Board(C["bone"], seed=21)
    bg(b, C["bone"])
    b.text("THE RULES", SHOUT, 150, M, 48, C["ink"], rot=-2, name="title")
    b.text("break the look, never the tool.", SCRAWL, 38, 700, 128, C["riot"], rot=-4)
    items = [
        ("01", "LOUD BEFORE PRETTY", "One shout per screen. A huge condensed headline, then shut up."),
        ("02", "NOTHING SITS STRAIGHT", "Tilt scraps, tape and headlines 1-4°. Never tilt a button, input or number."),
        ("03", "PHOTOCOPY IT", "Grain and toner on surfaces and display type. Body text stays clean."),
        ("04", "ONE HOT COLOR", "Each app owns one: Explode = hazard, Surveil = phosphor, hub = riot red."),
        ("05", "TAPE, DON'T BOX", "Torn paper, tape and stamps replace cards, soft shadows and gradients."),
        ("06", "SAY IT PLAIN", "Verbs over vibes: RIP, DROP, TAP, BLOW IT UP. Never 'unlock your creativity'."),
    ]
    for i, (num, head, body) in enumerate(items):
        col, row = i % 2, i // 2
        x, y = M + col * 760, 250 + row * 220
        sc = b.torn(x, y, 120, 110, C["ink"] if i != 3 else C["riot"], rot=b.rng.uniform(-5, 5), jag=5, step=14)
        b.text(num, SHOUT, 78, x + 22, y + 8, C["bone"], rot=b.rng.uniform(-4, 4))
        b.text(head, SHOUT, 50, x + 150, y - 2, C["ink"], tracking=1)
        b.text(body, MONO, 20, x + 152, y + 74, "#2c2a26", line_height=1.45, within=[x + 152, y + 74, 560, 120])
    b.tape(1440, 60, 140, rot=18)
    footer(b, 2, "RULES", C["ink"], "#6b675f")
    return b, "02-rules"


# ---------------------------------------------------------------- 03 parts
def parts():
    b = Board(C["bone"], seed=31)
    bg(b, C["bone"])
    b.text("THE PARTS", SHOUT, 120, M, 40, C["ink"], rot=-2)

    # palette
    b.text("INK", MONOB, 18, M, 210, C["ink"], tracking=4)
    sw = [("BONE", C["bone"], C["ink"]), ("INK", C["ink"], C["bone"]), ("XEROX", C["xerox"], C["ink"]),
          ("TAPE", C["tape"], C["ink"]), ("RIOT RED", C["riot"], C["bone"]), ("HAZARD", C["hazard"], C["ink"]),
          ("PHOSPHOR", C["phosphor"], C["ink"]), ("GIG PINK", C["pink"], C["ink"])]
    for i, (nm, col, fg) in enumerate(sw):
        x = M + (i % 4) * 172
        y = 244 + (i // 4) * 172
        s = b.torn(x, y, 156, 156, col, jag=6, step=16, stroke=C["ink"] if col == C["bone"] else None,
                   stroke_width=2 if col == C["bone"] else None)
        b.text(nm, MONOB, 15, x + 12, y + 104, fg, tracking=1)
        b.text(col.upper(), MONO, 14, x + 12, y + 126, fg)
    for i, (lbl, col, rr) in enumerate([("hub", C["riot"], -6), ("explode", "#8a7400", -4),
                                         ("surveil", C["dimgreen"], 3), ("releases", "#c4126b", -3)]):
        b.text(lbl, SCRAWL, 26, M + i * 172 + 18, 590, col, rot=rr)

    # type
    tx = 780
    b.text("TYPE", MONOB, 18, tx, 210, C["ink"], tracking=4)
    rows = [
        (SHOUT, 84, "SHOUT  Anton", "headlines, one per screen"),
        (MONOB, 34, "WORK  Space Mono", "UI, labels, numbers, body"),
        (SCRAWL, 40, "scrawl  Permanent Marker", "margin notes, never instructions"),
        (STENCIL, 64, "EXPLODE  Big Shoulders Stencil", "Explode display"),
        (CRT, 56, "SURVEIL  VT323", "Surveil display + readouts"),
    ]
    y = 240
    for font, size, sample, use in rows:
        b.text(sample, font, size, tx, y, C["ink"])
        y += int(size * 1.22)
        b.text(use, MONO, 15, tx + 4, y, "#6b675f")
        y += 30

    # components
    cy = 680
    b.text("BITS", MONOB, 18, M, cy, C["ink"], tracking=4)
    b.rect(M, cy + 40, 230, 72, C["ink"], name="btn")
    b.text("RIP IT  →", MONOB, 26, M + 34, cy + 59, C["bone"], tracking=2)
    b.rect(M + 8, cy + 120, 230, 6, C["riot"])
    b.text("primary: flat ink slab, never tilted", MONO, 14, M, cy + 138, "#6b675f")
    b.rect(M + 300, cy + 40, 200, 72, C["bone"], stroke=C["ink"], stroke_width=4)
    b.text("CANCEL", MONOB, 26, M + 344, cy + 59, C["ink"], tracking=2)
    st = b.rect(M + 560, cy + 34, 210, 84, "#00000000", stroke=C["riot"], stroke_width=5, rot=-7, name="stamp")
    b.text("DONE", SHOUT, 58, M + 610, cy + 38, C["riot"], rot=-7, name="stamp-word")
    b.grain("stamp-word", 0.5)
    b.text("stamps = status", MONO, 14, M + 572, cy + 138, "#6b675f")
    sc = b.torn(M + 830, cy + 36, 300, 90, C["tape"], rot=2, jag=6)
    b.text("VOCALS.WAV  24-bit", MONOB, 20, M + 852, cy + 66, C["ink"], rot=2)
    b.tape(M + 1060, cy + 18, 90, rot=24)
    b.text("scrap = file / item", MONO, 14, M + 840, cy + 138, "#6b675f")
    b.wave(M + 1180, cy + 40, 290, 80, C["ink"], bars=46, seed=4)
    b.text("waveform = bars, no smoothing", MONO, 14, M + 1180, cy + 138, "#6b675f")
    footer(b, 3, "PARTS", C["ink"], "#6b675f")
    return b, "03-parts"


def theme_card(b, x, y, name, sub, chips, mood, fg, dim, dos=()):
    b.text("THEME", MONOB, 16, x, y, dim, tracking=5)
    b.text(name, SHOUT, 92, x, y + 22, fg, rot=-2, line_height=0.98)
    b.text(sub, MONO, 18, x + 2, y + 232, dim, line_height=1.4)
    for i, col in enumerate(chips):
        b.rect(x + i * 62, y + 304, 50, 50, col, stroke=fg, stroke_width=2)
    b.text(mood, MONOB, 17, x + 2, y + 380, fg, line_height=1.6)
    if dos:
        b.rect(x, y + 500, 340, 2, dim)
        b.text("\n".join(dos), MONO, 16, x + 2, y + 520, dim, line_height=1.7)


# ---------------------------------------------------------------- 04 xerox riot (hub)
def xerox():
    b = Board(C["bone"], seed=41)
    bg(b, C["bone"])
    theme_card(b, M, 70, "XEROX\nRIOT", "BackTrack hub, light mode,\nthe default.",
               [C["bone"], C["ink"], C["xerox"], C["riot"], C["tape"]],
               "photocopied flyer\nblack toner, one red\ntape over everything", C["ink"], "#5d5a53",
               ["+ hub, settings, run history", "+ light mode for all apps", "- never grain body copy",
                "- one red per screen"])
    # screen
    sx, sy, sw, sh = 470, 70, 1066, 790
    b.rect(sx + 14, sy + 14, sw, sh, C["ink"])
    b.rect(sx, sy, sw, sh, "#f7f5ef", stroke=C["ink"], stroke_width=4, name="screen")
    b.rect(sx, sy, sw, 64, C["ink"])
    b.text("BACKTRACK", SHOUT, 36, sx + 28, sy + 9, C["bone"], tracking=1)
    b.text("EXPLODE    SURVEIL    RUNS    ?", MONOB, 16, sx + 600, sy + 22, C["bone"], tracking=2)
    t = b.text("DROP A TRACK.", SHOUT, 118, sx + 52, sy + 104, C["ink"], rot=-2)
    b.grain(t, 0.18)
    b.text("we'll take it apart.", SCRAWL, 44, sx + 640, sy + 196, C["riot"], rot=-5)
    dz = b.torn(sx + 52, sy + 290, sw - 104, 190, C["xerox"], jag=9)
    b.rect(sx + 74, sy + 312, sw - 148, 146, "#00000000", stroke=C["ink"], stroke_width=3, dash=[14, 10])
    b.text("WAV / FLAC / MP3   —   or click to pick", MONOB, 22, sx + 270, sy + 370, C["ink"], tracking=1)
    b.tape(sx + 30, sy + 272, 120, rot=-20)
    b.tape(sx + sw - 150, sy + 452, 120, rot=-16)
    # two app tiles
    for i, (nm, fnt, col, fg, line) in enumerate([("EXPLODE", STENCIL, C["hazard"], C["ink"], "split into stems"),
                                                   ("SURVEIL", CRT, C["crt"], C["phosphor"], "turn it into MIDI")]):
        x = sx + 52 + i * 490
        y = sy + 520
        b.rect(x, y, 470, 150, col, stroke=C["ink"], stroke_width=4)
        b.text(nm, fnt, 84 if i == 0 else 92, x + 24, y + 12, fg)
        b.text(line, MONO, 20, x + 28, y + 108, fg)
    b.rect(sx + 52, sy + 700, 260, 64, C["ink"])
    b.text("RIP IT  →", MONOB, 24, sx + 92, sy + 716, C["bone"], tracking=2)
    b.text("runs on your machine. nothing leaves it.", MONO, 18, sx + 340, sy + 722, "#5d5a53")
    footer(b, 4, "THEME 01  XEROX RIOT", C["ink"], "#6b675f")
    return b, "04-theme-xerox-riot"


# ---------------------------------------------------------------- 05 blast zone (explode)
def blast():
    b = Board(C["ink"], seed=51)
    bg(b, "#111110", 0.09)
    Y, D = C["hazard"], "#8c8670"
    theme_card(b, M, 70, "BLAST\nZONE", "Backtrack Explode,\nthe stem separator. Dark.",
               ["#111110", Y, C["flash"], C["bone"], "#3a3a36"],
               "demolition site\nhazard tape, stencil\nspray, nothing is safe", C["bone"], D,
               ["+ waveforms in hazard yellow", "+ vocals get flash orange", "+ progress = hazard tape",
                "- stencil only above 40px"])
    sx, sy, sw, sh = 470, 70, 1066, 790
    b.rect(sx, sy, sw, sh, "#1a1a18", stroke=Y, stroke_width=4)
    # hazard stripe header: one path of slanted bars, cut flush at both ends
    segs = []
    for i in range(-40, sw, 48):
        a, c = max(0, i), max(0, min(sw, i + 24))
        a2, c2 = max(0, i + 40), min(sw, i + 64)
        if c2 > a and a2 < sw:
            segs.append(f"M{a} 40 L{c} 40 L{c2} 0 L{a2} 0 Z")
    b.add({"type": "shape", "shape": "path", "name": "hazard-tape", "path": " ".join(segs),
           "x": sx, "y": sy, "fill": Y})
    t = b.text("EXPLODE", STENCIL, 150, sx + 40, sy + 58, Y, name="ex-title")
    b.grain(t, 0.2)
    b.text("song.wav  /  3:41  /  44.1k stereo", MONOB, 18, sx + 640, sy + 112, C["bone"], tracking=1)
    b.text("4 stems. sums back to the mix.", MONO, 18, sx + 640, sy + 142, D)
    stems = [("VOCALS", 1, ()), ("DRUMS", 2, ()), ("BASS", 3, ((0.0, 0.12),)), ("OTHER", 4, ((0.55, 0.7),))]
    for i, (nm, seed, quiet) in enumerate(stems):
        y = sy + 250 + i * 104
        b.rect(sx + 40, y, sw - 80, 88, "#232320")
        b.text(nm, STENCIL, 46, sx + 60, y + 14, C["bone"])
        b.wave(sx + 250, y + 12, 640, 64, Y if i != 0 else C["flash"], bars=120, seed=seed, quiet=quiet)
        b.rect(sx + 920, y + 22, 44, 44, "#00000000", stroke=C["bone"], stroke_width=2)
        b.text("M", MONOB, 20, sx + 934, y + 30, C["bone"])
        b.rect(sx + 974, y + 22, 44, 44, Y)
        b.text("S", MONOB, 20, sx + 989, y + 30, C["ink"])
    # progress as hazard bar
    py = sy + 680
    b.rect(sx + 40, py, sw - 80, 26, "#2b2b27")
    b.rect(sx + 40, py, int((sw - 80) * 0.64), 26, Y)
    b.text("CHUNK 14/22   •   PASS 2/2 (flip + swap)", MONOB, 16, sx + 40, py + 38, D, tracking=1)
    b.rect(sx + sw - 340, py + 34, 300, 60, C["flash"], name="detonate")
    b.text("DETONATE", STENCIL, 40, sx + sw - 318, py + 42, C["ink"])
    b.text("boom.", SCRAWL, 40, sx + sw - 480, py + 46, C["flash"], rot=-10)
    footer(b, 5, "THEME 02  BLAST ZONE  (EXPLODE)", C["bone"], D)
    return b, "05-theme-blast-zone"


# ---------------------------------------------------------------- 06 wiretap (surveil)
def wiretap():
    b = Board(C["crt"], seed=61)
    bg(b, C["crt"], 0.07)
    G, D = C["phosphor"], "#3f8f55"
    theme_card(b, M, 70, "WIRE\nTAP", "Backtrack Surveil,\nthe MIDI transcriber. Dark.",
               [C["crt"], G, C["dimgreen"], C["riot"], C["ink"]],
               "CCTV monitor, case file\nredacted, timestamped\nwe heard everything", G, D,
               ["+ notes glow by confidence", "+ readouts in VT323", "+ redaction = unknown value",
                "- red only for REC + errors"])
    sx, sy, sw, sh = 470, 70, 1066, 790
    b.rect(sx, sy, sw, sh, "#0a170e", stroke=G, stroke_width=3)
    b.text("SURVEIL", CRT, 130, sx + 36, sy + 18, G, name="sv-title")
    b.text("REC ●", CRT, 52, sx + sw - 170, sy + 30, C["riot"])
    b.text("CAM 02   2026-10-18   00:03:14:22", CRT, 30, sx + sw - 430, sy + 84, D)
    b.text("CASE FILE:  bass.mid", CRT, 34, sx + 40, sy + 150, G)
    sub = b.text("SUBJECT  bass (mono)   KEY  E minor   TEMPO  128   SOURCE", CRT, 28, sx + 40, sy + 192, D)
    _, _, tw, _ = b.measure("SUBJECT  bass (mono)   KEY  E minor   TEMPO  128   SOURCE", CRT, 28)
    b.rect(sx + 52 + int(tw), sy + 196, 210, 24, G, name="redacted")
    # piano roll
    rx, ry, rw, rh = sx + 40, sy + 250, sw - 80, 400
    b.rect(rx, ry, rw, rh, "#06100a", stroke=C["dimgreen"], stroke_width=2)
    lanes = 16
    for i in range(1, lanes):
        b.rect(rx, ry + i * rh // lanes, rw, 1, "#123a1e")
    for i in range(1, 16):
        b.rect(rx + i * rw // 16, ry, 2 if i % 4 == 0 else 1, rh, "#1d5a30" if i % 4 == 0 else "#123a1e")
    r = b.rng
    lane = 9
    x = rx + 4
    while x < rx + rw - 60:
        lane = max(1, min(lanes - 2, lane + r.choice([-2, -1, 0, 0, 1, 3, -3])))
        ln = r.choice([28, 56, 56, 112, 84])
        conf = r.uniform(0.45, 1)
        b.rect(int(x), ry + lane * rh // lanes + 3, ln - 6, rh // lanes - 6, G if conf > 0.7 else C["dimgreen"],
               stroke=G, stroke_width=1)
        x += ln + r.choice([0, 0, 28])
    b.text("bright = sure.  dim = guessing.", SCRAWL, 30, sx + 560, ry + rh + 16, C["riot"], rot=-3)
    b.text("NOTES 212   CONF 0.87   BENDS ON", CRT, 32, sx + 40, ry + rh + 20, G)
    b.rect(sx + 40, sy + 712, 320, 56, "#00000000", stroke=G, stroke_width=3)
    b.text("[ TAP THE LINE ]", CRT, 40, sx + 66, sy + 721, G)
    b.rect(sx + 380, sy + 712, 230, 56, G)
    b.text("EXPORT .MID", CRT, 40, sx + 412, sy + 721, C["crt"])
    # scanlines over the screen
    lines = [b.rect(sx + 4, sy + 4 + i * 6, sw - 8, 2, "#000000", opacity=0.22) for i in range(0, (sh - 8) // 6)]
    g = b.uid("scan")
    b.add({"type": "group", "name": g, "targets": lines})
    footer(b, 6, "THEME 03  WIRETAP  (SURVEIL)", G, D)
    return b, "06-theme-wiretap"


# ---------------------------------------------------------------- 07 gig flyer (release / marketing)
def gig():
    b = Board(C["bone"], seed=71)
    bg(b, "#f5f3ee", 0.07)
    P, A = C["pink"], C["acid"]
    theme_card(b, M, 70, "GIG\nFLYER", "Release pages, socials,\nchangelogs. Loudest mode.",
               ["#f5f3ee", C["ink"], P, A, C["xerox"]],
               "telephone-pole flyer\nfluoro ink, blackletter\nall ages, no refunds", C["ink"], "#5d5a53",
               ["+ release notes, socials", "+ one fluoro + ink only", "+ blackletter for the brand",
                "- never inside the apps"])
    fx, fy, fw, fh = 560, 50, 640, 830
    f = b.torn(fx, fy, fw, fh, P, rot=2, jag=10, name="flyer")
    b.grain(f, 0.25)
    b.tape(fx + 250, fy - 18, 160, rot=-4)
    b.tape(fx + 30, fy + fh - 30, 120, rot=30)
    b.text("Backtrack", FRAK, 120, fx + 50, fy + 50, C["ink"], rot=2)
    slab = b.torn(fx + 20, fy + 210, fw - 40, 230, C["ink"], rot=-4, jag=8)
    b.text("EXPLODE", SHOUT, 190, fx + 44, fy + 196, A, rot=-4, name="gig-headline")
    b.grain("gig-headline", 0.18)
    b.ransom("0.1 OUT NOW", fx + 50, fy + 480, 52, [(A, C["ink"], SHOUT), (C["bone"], C["ink"], TYPE),
                                                    (C["ink"], A, BLOCK), (C["ink"], P, SHOUT)])
    b.text("TWO STEMS.  ONE BINARY.  ZERO CLOUD.", MONOB, 22, fx + 60, fy + 640, C["ink"], rot=2, tracking=1)
    b.text("all ages  •  win / mac / linux  •  free", MONO, 20, fx + 60, fy + 690, C["ink"], rot=2)
    b.text("bring your own song.", SCRAWL, 40, fx + 180, fy + 740, C["bone"], rot=-6)
    # tear-off tabs
    for i in range(6):
        x = 1250 + (i % 2) * 150
        y = 120 + (i // 2) * 250
        tab = b.torn(x, y, 120, 210, C["bone"], rot=b.rng.uniform(-6, 6), jag=4, stroke=C["ink"], stroke_width=2)
        label = "BACKTRACK EXPLODE 0.1"
        _, _, tw, th = b.measure(label, MONOB, 14)
        b.text(label, MONOB, 14, round(x + 60 - tw / 2), round(y + 105 - th / 2), C["ink"],
               rot=90 + b.rng.uniform(-4, 4))
    footer(b, 7, "THEME 04  GIG FLYER  (RELEASES)", C["ink"], "#6b675f")
    return b, "07-theme-gig-flyer"


if __name__ == "__main__":
    which = sys.argv[2:] or ["cover", "rules", "parts", "xerox", "blast", "wiretap", "gig"]
    for fn in which:
        b, slug = globals()[fn]()
        png = OUT / f"{slug}.png"
        b.build(png, SRC / f"{slug}.vixl")
        # Grain does not compress as PNG; ship JPEG boards.
        Image.open(png).convert("RGB").save(OUT / f"{slug}.jpg", quality=88)
        png.unlink()
        print("built", slug)
    boards = sorted(OUT.glob("0*.jpg"))
    pages = [Image.open(p) for p in boards]
    pages[0].save(HERE.parent / "backtrack-design-language.pdf", save_all=True, append_images=pages[1:],
                  resolution=150)
