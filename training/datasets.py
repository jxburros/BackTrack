"""Dataset readers: Song Deck's training-data export (`npm run dataset:render`,
manifest format "songdeck-dataset" version 1) and E-GMD. Each gives Items:
audio to transcribe plus the drum hits in it, mapped to the 7 classes.

Song Deck manifest (abridged; the full contract is Song Deck's):

    <dir>/manifest.json   {"format": "songdeck-dataset", "version": 1, "licence": ..., "songs": [
        {"id", "split": train|valid|test, "sample_rate", "duration", "mix": "songs/<id>/mix.wav",
         "stems": [{"track", "stem": "drums", "drums": true, "audio": ..., "midi": ...,
                    "notes": [{"pitch", "start", "end", "velocity"}]}],
         "separated"?: {"separator", "stems": {"drums": "songs/<id>/separated/drums.wav"}}}]}
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .midi import read_notes
from .model import DRUM_CLASSES

# General MIDI drum notes -> class index (the M3 contract's mapping).
GM_DRUMS = {
    **{n: 0 for n in (35, 36)},
    **{n: 1 for n in (37, 38, 39, 40)},
    **{n: 2 for n in (41, 43, 45, 47, 48, 50)},
    **{n: 3 for n in (42, 44)},
    46: 4,
    **{n: 5 for n in (49, 52, 55, 57)},
    **{n: 6 for n in (51, 53, 59)},
}
# E-GMD records a Roland TD-17, which also uses 22 (closed hi-hat edge),
# 26 (open hi-hat edge) and 58 (floor tom rim).
EGMD_DRUMS = {**GM_DRUMS, 22: 3, 26: 4, 58: 2}

SPLITS = ("train", "valid", "test")


@dataclass
class Item:
    id: str
    split: str
    audio: list                       # files summed (sample-aligned, same rate) into one clip
    hits: list = field(default_factory=list)   # [(seconds, class)]
    source: str = ""                  # licence-record line for audio.sources
    duration: float = 0.0


def hits_from_notes(notes, mapping=GM_DRUMS) -> tuple[list, int]:
    hits, dropped = [], 0
    for n in notes:
        c = mapping.get(int(round(n["pitch"])))
        if c is None:
            dropped += 1
        else:
            hits.append((float(n["start"]), c))
    hits.sort()
    return hits, dropped


def read_songdeck(root, audio: str = "stem") -> list[Item]:
    """audio: "stem" (the drum stems summed), "separated" (the separated drum
    stem, songs without one are skipped) or "mix"."""
    root = Path(root)
    man = json.loads((root / "manifest.json").read_text())
    if man.get("format") != "songdeck-dataset":
        raise ValueError(f"{root}/manifest.json: format is not songdeck-dataset")
    if int(man.get("version", 0)) != 1:
        raise ValueError(f"{root}/manifest.json: version {man.get('version')} is not supported (expected 1)")
    source = f"Song Deck dataset {root.name} (Song Deck {man.get('songdeck_version', '?')}, {man.get('created', '?')}): {man.get('licence', '')}"
    items = []
    for song in man["songs"]:
        drum_stems = [s for s in song.get("stems", []) if s.get("drums") or s.get("stem") == "drums"]
        if not drum_stems:
            continue
        notes = []
        for s in drum_stems:
            if s.get("notes") is not None:
                notes += s["notes"]
            elif s.get("midi"):
                notes += read_notes(root / s["midi"])
        hits, _ = hits_from_notes(notes)
        if audio == "stem":
            files = [root / s["audio"] for s in drum_stems]
        elif audio == "separated":
            sep = (song.get("separated") or {}).get("stems", {}).get("drums")
            if not sep:
                continue
            files = [root / sep]
        elif audio == "mix":
            files = [root / song["mix"]]
        else:
            raise ValueError(f"unknown audio choice {audio!r} (stem, separated or mix)")
        split = song.get("split", "train")
        split = "valid" if split == "validation" else split
        items.append(Item(f"{song['id']}:{audio}", split, files, hits, source, float(song.get("duration", 0))))
    return items


EGMD_SOURCE = "E-GMD v1.0.0 (Magenta; Callender, Hawthorne and Engel 2020), CC BY 4.0"


def read_egmd(root, limit: int = 0, splits=SPLITS) -> list[Item]:
    """E-GMD: e-gmd-v1.0.0.csv (drummer, session, id, style, bpm, beat_type,
    time_signature, duration, split, midi_filename, audio_filename, kit_name).
    limit > 0 keeps every n-th row so that about `limit` items remain."""
    root = Path(root)
    csv_path = next(iter(sorted(root.glob("e-gmd-v*.csv"))), None)
    if csv_path is None:
        raise ValueError(f"{root}: no e-gmd-v*.csv")
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if limit and len(rows) > limit:
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    items = []
    for r in rows:
        split = {"validation": "valid"}.get(r["split"], r["split"])
        if split not in splits:
            continue
        hits, _ = hits_from_notes(read_notes(root / r["midi_filename"]), EGMD_DRUMS)
        items.append(Item(f"egmd:{r['id']}", split, [root / r["audio_filename"]], hits, EGMD_SOURCE, float(r["duration"] or 0)))
    return items


def summary(items: list[Item]) -> str:
    out = []
    for sp in SPLITS:
        it = [i for i in items if i.split == sp]
        if not it:
            continue
        counts = [0] * len(DRUM_CLASSES)
        for i in it:
            for _, c in i.hits:
                counts[c] += 1
        out.append(f"{sp}: {len(it)} items, {sum(i.duration for i in it) / 60:.1f} min, hits "
                   + " ".join(f"{n}={k}" for n, k in zip(DRUM_CLASSES, counts)))
    return "\n".join(out)
