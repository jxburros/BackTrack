"""Checks the chord and key labels and scores (training/chords.py, milestone C0): symbol parsing,
the vocabulary map, label frames, the scores against hand-worked cases (the same cases as Song
Deck's metrics test, so the two definitions stay equal), and the manifest reader.

    python3 -m training.check_chords

Needs NumPy and PyTorch (training.datasets imports the model); a fraction of a second. Fails loudly
(AssertionError, non-zero exit).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import chords as C
from .datasets import read_songdeck_chords


def check_parsing():
    assert C.parse_symbol("C") == (0, "maj", None)
    assert C.parse_symbol("F#m7b5") == (6, "m7b5", None)
    assert C.parse_symbol("Bbsus4") == (10, "sus4", None)
    assert C.parse_symbol("G/B") == (7, "maj", 11)
    assert C.parse_symbol("Am(add9)/C") == (9, "minadd9", 0)
    assert C.parse_symbol("Cmaj7/C") == (0, "maj7", None), "a bass on the root is no slash chord"
    assert C.parse_symbol("Dm(maj7)") == (2, "minmaj7", None)
    assert C.parse_symbol("Ebø7") == (3, "m7b5", None)
    assert C.parse_symbol("H7") is None and C.parse_symbol("Cblah") is None
    assert C.parse_key("A minor") == (9, "minor")
    assert C.parse_key("F# major") == (6, "major")
    assert C.parse_key("Bb") == (10, "major")
    assert C.parse_key("D dorian") == (2, "dorian")
    assert C.parse_key("E harmonic minor") == (4, "harmonic-minor")
    assert C.key_index((9, "minor")) == 21 and C.key_index((4, "harmonic-minor")) == 16
    assert C.key_index((2, "dorian")) == C.IGNORE


def check_vocabulary():
    assert len(C.QUALITIES) == 14
    every = set(C.SUFFIXES.values())
    assert every == set(C.MODEL_QUALITY), "every Song Deck quality has a mapping"
    for q, m in C.MODEL_QUALITY.items():
        assert m is None or m in C.QUALITIES, q
    assert C.model_quality("min9") == "min7" and C.model_quality("13") == "7"
    assert C.model_quality("minmaj7") == "min" and C.model_quality("5") is None
    # The map keeps the third: no major chord becomes minor or the other way round.
    for q, m in C.MODEL_QUALITY.items():
        if m is not None and C.majmin(q) != "other":
            assert C.majmin(m) == C.majmin(q), (q, m)


def check_frames():
    chords = [(0.0, 1.0, 0, "maj", None), (1.0, 2.0, 7, "maj", 11), (2.5, 3.0, 9, "5", None)]
    keys = [(0.0, (0, "major")), (2.0, (9, "minor"))]
    lab = C.label_frames(chords, keys, n_frames=30, frame_rate=10)
    assert lab["root"][:10] == [0] * 10 and lab["root"][10:20] == [7] * 10
    assert lab["bass"][10:20] == [11] * 10 and lab["bass"][0] == 0
    assert lab["root"][20:25] == [C.NO_CHORD] * 5 and lab["quality"][20:25] == [C.IGNORE] * 5
    assert lab["root"][25:30] == [9] * 5 and lab["quality"][25:30] == [C.IGNORE] * 5, "a power chord trains its root only"
    assert lab["quality"][0] == C.QUALITIES.index("maj")
    assert lab["key"][:20] == [0] * 20 and lab["key"][20:] == [21] * 10


def check_scores():
    # Song Deck's metrics test (packages/audio/test/eval/baseline.test.ts), case by case.
    ref = [(0, 2, 7, "maj"), (2, 4, 4, "min")]
    s = C.chord_scores(C.chord_counts(ref, ref))
    assert s == {"root": 1, "majmin": 1, "sevenths": 1, "root_quality": 1, "coverage": 1}, s
    off = C.chord_scores(C.chord_counts([(0, 4, 0, "maj")], ref))
    assert off["root"] == 0 and off["coverage"] == 1
    sev = C.chord_scores(C.chord_counts([(0, 2, 7, "7"), (2, 3, 4, "min7")], ref))
    assert sev["root_quality"] == 0 and abs(sev["majmin"] - 0.75) < 1e-2 and abs(sev["root"] - 0.75) < 1e-2
    assert sev["sevenths"] == 0
    jazz = C.chord_scores(C.chord_counts(
        [(0, 2, 2, "min7"), (2, 4, 7, "7"), (4, 6, 0, "maj")],
        [(0, 2, 2, "min9"), (2, 4, 7, "13"), (4, 5, 0, "maj7"), (5, 6, 11, "dim")]))
    assert abs(jazz["sevenths"] - 0.8) < 1e-2 and abs(jazz["root"] - 5 / 6) < 1e-2, jazz
    c = (0, "major")
    assert C.key_score(c, c) == 1
    assert C.key_score((7, "major"), c) == 0.5 and C.key_score((5, "major"), c) == 0.5
    assert C.key_score((9, "minor"), c) == 0.3 and C.key_score(c, (9, "minor")) == 0.3
    assert C.key_score((0, "minor"), c) == 0.2
    assert C.key_score((2, "major"), c) == 0 and C.key_score(None, c) == 0
    assert C.key_score((2, "dorian"), (2, "dorian")) == 1 and C.key_score((2, "minor"), (2, "dorian")) == 0


def check_reader():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        song = {"id": "0001-pop-s1", "split": "test", "duration": 4, "mix": "songs/1/mix.wav", "key": "C major",
                "stems": [{"stem": "drums", "drums": True, "audio": "songs/1/stems/d.wav"},
                          {"stem": "bass", "drums": False, "audio": "songs/1/stems/b.wav"}],
                "chords": [{"symbol": "C", "start": 0, "end": 2, "root": 0, "quality": "maj"},
                           {"symbol": "G/B", "start": 2, "end": 4}]}
        bare = {**song, "id": "0002-pop-s2", "chords": []}
        (root / "manifest.json").write_text(json.dumps({"format": "songdeck-dataset", "version": 1,
                                                        "songs": [song, bare]}))
        items = read_songdeck_chords(root)
        assert len(items) == 1, "songs without chords are left out"
        it = items[0]
        assert it.chords == [(0.0, 2.0, 0, "maj", None), (2.0, 4.0, 7, "maj", 11)], "structured, then from the symbol"
        assert it.keys == [(0.0, (0, "major"))] and it.audio == [root / "songs/1/mix.wav"]
        harmonic = read_songdeck_chords(root, audio="harmonic")[0]
        assert harmonic.audio == [root / "songs/1/stems/b.wav"], "the drums are left out"


def main():
    for check in (check_parsing, check_vocabulary, check_frames, check_scores, check_reader):
        check()
        print(f"ok   {check.__name__.removeprefix('check_')}")
    print("chords: all checks passed")


if __name__ == "__main__":
    main()
