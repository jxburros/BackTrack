"""Chord and key labels for the theory models (milestone C0, docs/plans/theory.md).

Song Deck's training export writes each song's chords (seconds, with `root`, `quality`, `bass`
and `roman` since Song Deck 0.20.0; only `symbol` before) and its key and key changes. This
module turns them into what model A trains on and is scored with:

  parse_symbol      "F#m7b5/C" -> (root, quality, bass), for manifests without the fields
  model_quality     Song Deck's 28 qualities -> the 14 the built-in detector knows (QUALITIES)
  parse_key         "A minor" -> (tonic, mode)
  label_frames      chords and keys -> per-frame root, quality, bass and key indices
  chord_counts      weighted chord symbol recall at the root, major/minor and sevenths levels
  key_score         the weighted key score

The scores are defined as Song Deck's `eval:datasets --tasks chords,key`
(packages/audio/test/eval/metrics.ts), so a model's numbers compare with the built-in
detector's. `python3 -m training.check_chords` checks this module.
"""

from __future__ import annotations

import math
import re

# Pitch classes: C = 0.
_LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Model A's chord qualities: the 14 of Song Deck's built-in detector (packages/audio/src/analysis/
# chords.ts), named with Song Deck's quality ids. The order is the quality head's output order and
# is written into the GGUF.
QUALITIES = ("maj", "min", "aug", "dim", "sus2", "sus4", "6", "min6", "7", "maj7", "min7", "add9", "dim7", "m7b5")

# Song Deck's qualities (packages/core/src/ir/types.ts ChordQuality) -> QUALITIES. Extensions drop
# to the chord under them (min9 -> min7, 13 -> 7); a seventh the vocabulary has no slot for keeps
# its triad (minmaj7 -> min, aug7 -> aug). None: no third and no slot (the power chord), so the
# quality head ignores those frames while root and bass still train.
MODEL_QUALITY = {
    "maj": "maj", "min": "min", "dim": "dim", "aug": "aug", "sus2": "sus2", "sus4": "sus4",
    "5": None,
    "6": "6", "min6": "min6",
    "7": "7", "maj7": "maj7", "min7": "min7", "minmaj7": "min", "m7b5": "m7b5", "dim7": "dim7",
    "7sus4": "sus4", "add9": "add9", "minadd9": "min",
    "9": "7", "maj9": "maj7", "min9": "min7", "11": "sus4", "min11": "min7", "13": "7", "maj13": "maj7",
    "aug7": "aug", "7b9": "7", "7#9": "7",
}

# Chord-symbol suffixes -> Song Deck qualities (Song Deck's SUFFIX_ALIASES in theory/chords.ts).
SUFFIXES = {
    "": "maj", "M": "maj", "maj": "maj", "major": "maj",
    "m": "min", "mi": "min", "min": "min", "minor": "min", "-": "min",
    "dim": "dim", "°": "dim", "o": "dim",
    "aug": "aug", "+": "aug", "#5": "aug",
    "sus": "sus4", "sus4": "sus4", "sus2": "sus2", "2": "sus2", "5": "5",
    "6": "6", "maj6": "6", "m6": "min6", "min6": "min6", "-6": "min6",
    "7": "7", "dom7": "7",
    "maj7": "maj7", "M7": "maj7", "ma7": "maj7", "Δ": "maj7", "Δ7": "maj7",
    "m7": "min7", "min7": "min7", "mi7": "min7", "-7": "min7",
    "mmaj7": "minmaj7", "mM7": "minmaj7", "m(maj7)": "minmaj7", "m(M7)": "minmaj7", "minmaj7": "minmaj7",
    "m7b5": "m7b5", "m7(b5)": "m7b5", "-7b5": "m7b5", "ø": "m7b5", "ø7": "m7b5",
    "dim7": "dim7", "°7": "dim7", "o7": "dim7",
    "7sus4": "7sus4", "7sus": "7sus4",
    "add9": "add9", "add2": "add9", "madd9": "minadd9", "m(add9)": "minadd9", "minadd9": "minadd9",
    "9": "9", "maj9": "maj9", "M9": "maj9", "m9": "min9", "min9": "min9",
    "11": "11", "m11": "min11", "min11": "min11",
    "13": "13", "maj13": "maj13", "M13": "maj13",
    "aug7": "aug7", "+7": "aug7", "7#5": "aug7", "7+": "aug7",
    "7b9": "7b9", "7#9": "7#9",
}

# MIREX levels, as Song Deck scores them (metrics.ts): the third (major/minor/other) and the
# sevenths vocabulary (maj, min, 7, maj7, min7; None: outside it, left out of the total).
_MINOR_THIRD = {"min", "min6", "min7", "minmaj7", "minadd9", "min9", "min11", "dim", "dim7", "m7b5"}
_NO_THIRD = {"5", "sus2", "sus4", "7sus4", "11"}
SEVENTHS = {
    "maj": "maj", "6": "maj", "add9": "maj",
    "min": "min", "min6": "min", "minadd9": "min",
    "7": "7", "9": "7", "13": "7", "7b9": "7", "7#9": "7",
    "maj7": "maj7", "maj9": "maj7", "maj13": "maj7",
    "min7": "min7", "min9": "min7", "min11": "min7",
}

NO_CHORD = 12   # root and bass index for "no chord"
IGNORE = -1     # quality or key index the loss and scoring skip

_MODES = (
    (re.compile(r"^(maj|major|ionian|M)$"), "major"),
    (re.compile(r"^(m|min|minor|aeolian|-)$"), "minor"),
    (re.compile(r"^(dorian|phrygian|lydian|mixolydian|locrian)$"), None),
    (re.compile(r"^harmonic[\s-]?minor$"), "harmonic-minor"),
    (re.compile(r"^melodic[\s-]?minor$"), "melodic-minor"),
)


def pitch_class(name: str) -> int | None:
    """"C", "F#", "Bb", "e♭" -> 0..11, or None."""
    m = re.fullmatch(r"([A-Ga-g])(#|♯|b|♭)?", name.strip())
    if not m:
        return None
    pc = _LETTER[m.group(1).upper()]
    if m.group(2) in ("#", "♯"):
        pc += 1
    elif m.group(2) in ("b", "♭"):
        pc -= 1
    return pc % 12


def parse_symbol(symbol: str) -> tuple[int, str, int | None] | None:
    """A chord symbol -> (root, Song Deck quality, bass or None), or None when it can't be read
    (as Song Deck's parseChordSymbol)."""
    m = re.fullmatch(r"([A-Ga-g](?:#|♯|b|♭)?)(.*?)(?:/([A-Ga-g](?:#|♯|b|♭)?))?", symbol.strip())
    if not m:
        return None
    root = pitch_class(m.group(1))
    suffix = re.sub(r"\s+", "", m.group(2))
    suffix = re.sub(r"^\((.*)\)$", r"\1", suffix)
    quality = SUFFIXES.get(suffix)
    if quality is None and re.match(r"^(maj|min|dim|aug|sus|add|dom)", suffix.lower()):
        quality = SUFFIXES.get(suffix.lower())
    if root is None or quality is None:
        return None
    bass = pitch_class(m.group(3)) if m.group(3) else None
    if m.group(3) and bass is None:
        return None
    return root, quality, (bass if bass != root else None)


def model_quality(quality: str) -> str | None:
    """Song Deck's quality -> one of QUALITIES, or None (see MODEL_QUALITY)."""
    if quality not in MODEL_QUALITY:
        raise ValueError(f"unknown chord quality {quality!r}")
    return MODEL_QUALITY[quality]


def parse_key(text: str) -> tuple[int, str] | None:
    """"A minor", "F# major", "D dorian" -> (tonic, mode) with Song Deck's mode names; modal keys
    come back as their mode ("dorian"), or None when unreadable."""
    m = re.fullmatch(r"\s*([A-Ga-g](?:#|♯|b|♭)?)\s*(.*?)\s*", text)
    if not m:
        return None
    tonic = pitch_class(m.group(1))
    rest = m.group(2)
    if tonic is None:
        return None
    if rest == "":
        return tonic, "major"
    for pattern, mode in _MODES:
        if pattern.match(rest) or pattern.match(rest.lower()):
            return tonic, mode or rest.lower()
    return None


def key_index(key: tuple[int, str] | None) -> int:
    """The key head's class: 0..11 major on that tonic, 12..23 minor (harmonic and melodic minor
    count as minor); IGNORE for modal keys (modes come later) and unknown keys."""
    if key is None:
        return IGNORE
    tonic, mode = key
    if mode == "major":
        return tonic
    if mode in ("minor", "harmonic-minor", "melodic-minor"):
        return 12 + tonic
    return IGNORE


def song_chords(song: dict) -> list[tuple[float, float, int, str, int | None]]:
    """A manifest song's chords: [(start, end, root, Song Deck quality, bass or None)], from the
    structured fields when present and the symbol otherwise; unreadable chords are left out."""
    out = []
    for c in song.get("chords") or []:
        if c.get("root") is not None and c.get("quality"):
            spec = (int(c["root"]), c["quality"], c.get("bass"))
        else:
            spec = parse_symbol(c.get("symbol", ""))
        if spec is None:
            continue
        root, quality, bass = spec
        out.append((float(c["start"]), float(c["end"]), root, quality, None if bass in (None, root) else int(bass)))
    return out


def song_keys(song: dict) -> list[tuple[float, tuple[int, str] | None]]:
    """A manifest song's keys: [(time, (tonic, mode))], the key map or the one key."""
    keys = song.get("key_map") or ([{"time": 0, "key": song["key"]}] if song.get("key") else [])
    return [(float(k["time"]), parse_key(k["key"])) for k in keys]


def label_frames(chords, keys, n_frames: int, frame_rate: float) -> dict:
    """Per-frame targets for the heads: lists of length `n_frames` for `root` and `bass`
    (0..11, NO_CHORD between chords), `quality` (an index into QUALITIES, IGNORE between
    chords and for qualities with no slot) and `key` (key_index, IGNORE before the first key).
    A frame takes the label at its centre."""
    root = [NO_CHORD] * n_frames
    bass = [NO_CHORD] * n_frames
    quality = [IGNORE] * n_frames
    key = [IGNORE] * n_frames
    for start, end, r, q, b in chords:
        lo = max(0, math.ceil(start * frame_rate - 0.5))
        hi = min(n_frames, math.ceil(end * frame_rate - 0.5))
        mq = model_quality(q)
        for f in range(lo, hi):
            root[f] = r
            bass[f] = r if b is None else b
            quality[f] = IGNORE if mq is None else QUALITIES.index(mq)
    for i, (time, k) in enumerate(keys):
        lo = max(0, math.ceil(time * frame_rate - 0.5))
        hi = n_frames if i + 1 == len(keys) else min(n_frames, math.ceil(keys[i + 1][0] * frame_rate - 0.5))
        for f in range(lo, hi):
            key[f] = key_index(k)
    return {"root": root, "bass": bass, "quality": quality, "key": key}


def majmin(quality: str) -> str:
    """"maj", "min" or "other" by the chord's third."""
    if quality in _NO_THIRD:
        return "other"
    return "min" if quality in _MINOR_THIRD else "maj"


def _chord_at(chords, t):
    for c in chords:
        if c[0] <= t < c[1]:
            return c
    return None


def chord_counts(estimate, reference, step: float = 0.01) -> dict:
    """Reference time (in `step`s) where the estimate is right, per level; sum them over songs
    and divide with `chord_scores`. Chords are (start, end, root, quality, ...)."""
    n = {"total": 0, "root_quality": 0, "majmin": 0, "root": 0, "covered": 0, "sevenths_total": 0, "sevenths": 0}
    for ref in reference:
        start, end, root, quality = ref[:4]
        ref7 = SEVENTHS.get(quality)
        t = start + step / 2
        while t < end:
            n["total"] += 1
            n["sevenths_total"] += ref7 is not None
            est = _chord_at(estimate, t)
            if est is not None:
                n["covered"] += 1
                if est[2] == root:
                    n["root"] += 1
                    n["root_quality"] += est[3] == quality
                    n["majmin"] += majmin(est[3]) == majmin(quality)
                    n["sevenths"] += ref7 is not None and SEVENTHS.get(est[3]) == ref7
            t += step
    return n


def chord_scores(n: dict) -> dict:
    """Weighted chord symbol recall from (summed) counts: root, majmin, sevenths, root_quality
    and coverage, each 0..1 (sevenths over the reference time inside its vocabulary)."""
    def f(k, of="total"):
        return n[k] / n[of] if n[of] else 0.0
    return {"root": f("root"), "majmin": f("majmin"), "sevenths": f("sevenths", "sevenths_total"),
            "root_quality": f("root_quality"), "coverage": f("covered")}


def key_score(estimate: tuple[int, str] | None, reference: tuple[int, str]) -> float:
    """The MIREX weighted key score: 1 for the key, 0.5 a fifth away in the same mode, 0.3 for
    the relative key, 0.2 for the parallel key, 0 otherwise. Modal keys score only an exact match."""
    if estimate is None:
        return 0.0
    ei, ri = key_index(estimate), key_index(reference)
    if ei == IGNORE or ri == IGNORE:
        return 1.0 if estimate == reference else 0.0
    e_minor, r_minor = ei >= 12, ri >= 12
    d = (estimate[0] - reference[0]) % 12
    if e_minor == r_minor:
        return 1.0 if d == 0 else 0.5 if d in (5, 7) else 0.0
    if d == (3 if r_minor else 9):
        return 0.3
    return 0.2 if d == 0 else 0.0
