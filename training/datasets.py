"""Dataset readers: Song Deck's training-data export (`npm run dataset:render`,
manifest format "songdeck-dataset" version 1) and E-GMD. Each gives Items:
audio to transcribe plus the drum hits in it, mapped to the 7 classes.

Song Deck manifest (abridged; the full contract is Song Deck's):

    <dir>/manifest.json   {"format": "songdeck-dataset", "version": 1, "licence": ..., "songs": [
        {"id", "split": train|valid|test, "sample_rate", "duration", "mix": "songs/<id>/mix.wav",
         "stems": [{"track", "stem": "drums", "drums": true, "audio": ..., "midi": ...,
                    "notes": [{"pitch", "start", "end", "velocity"}]}],
         "separated"?: {"separator", "stems": {"drums": "songs/<id>/separated/drums.wav"}},
         "performances"?: [{"id", "provider", "model", "separated": {"stems": {"drums": ...}},
                            "offset", "offset_strength", "agreement",
                            "windows": [{"start", "end", "shift", "f", ...}]}]}]}

`performances` are ACE-Step covers of the rendered mix (Song Deck's
`npm run dataset:perform`): audio choice "performed" reads each take's
separated drums, with the song's hits moved to performance time (note time +
offset + the window's shift) and only the windows whose agreement F passes
`min_agreement` kept as the item's `regions` (seconds, performance time).
"""

from __future__ import annotations

import csv
from bisect import bisect_right
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
    # Accepted stretches [(start, end)] in seconds of this item's audio, or None
    # for all of it. Training masks the loss outside them; evaluation scores
    # only inside them.
    regions: list | None = None
    # Pitched items (read_songdeck_notes): [(start, end, pitch, bend)], bend [(position 0..1, cents)] or None.
    notes: list = field(default_factory=list)


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


# The record in audio.sources for ACE-Step output (the owner checked ACE-Step's
# licence for this use on 2026-10-18).
ACESTEP_LICENCE = "licence checked by the owner 2026-10-18"

MIN_AGREEMENT = 0.6
MIN_OFFSET_STRENGTH = 0.2


def performed_take(perf: dict, hits: list, duration: float, min_agreement: float = MIN_AGREEMENT,
                   min_offset_strength: float = MIN_OFFSET_STRENGTH):
    """One performance -> (hits in performance time, accepted regions) or None
    when the take is rejected (weak offset, or no window passes). Windows are
    in song time; a hit takes the shift of the window holding it (the nearest
    window outside them all). A take with no windows counts as one window
    over the whole song with f = its agreement and no extra shift."""
    if float(perf.get("offset_strength", 0)) < min_offset_strength:
        return None
    offset = float(perf.get("offset", 0))
    wins = perf.get("windows")
    if not wins:
        wins = [{"start": 0.0, "end": max(duration, max((t for t, _ in hits), default=0) + 1), "shift": 0.0,
                 "f": float(perf.get("agreement", 0))}]
    wins = sorted(wins, key=lambda w: float(w["start"]))
    starts = [float(w["start"]) for w in wins]
    out = []
    for t, c in hits:
        k = max(0, bisect_right(starts, t) - 1)
        out.append((t + offset + float(wins[k].get("shift", 0)), c))
    out.sort()
    regions = []
    for w in wins:
        if float(w.get("f", 0)) >= min_agreement:
            d = offset + float(w.get("shift", 0))
            a, b = float(w["start"]) + d, float(w["end"]) + d
            if regions and a <= regions[-1][1]:
                regions[-1] = (regions[-1][0], max(regions[-1][1], b))
            else:
                regions.append((max(0.0, a), b))
    return (out, regions) if regions else None


def in_regions(times_classes: list, regions) -> list:
    """The (time, class) pairs inside regions (all of them when regions is None)."""
    if regions is None:
        return list(times_classes)
    return [(t, c) for t, c in times_classes if any(a <= t < b for a, b in regions)]


def region_seconds(item: Item) -> float:
    if item.regions is None:
        return item.duration
    end = item.duration or float("inf")
    return sum(max(0.0, min(b, end) - a) for a, b in item.regions)


def read_songdeck(root, audio: str = "stem", min_agreement: float = MIN_AGREEMENT,
                  min_offset_strength: float = MIN_OFFSET_STRENGTH, stats: dict | None = None) -> list[Item]:
    """audio: "stem" (the drum stems summed), "separated" (the separated drum
    stem, songs without one are skipped), "mix", or "performed" (each
    performance's separated drums, see the module doc; `stats` gathers how
    many takes and minutes were accepted)."""
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
        split = song.get("split", "train")
        split = "valid" if split == "validation" else split
        if audio == "performed":
            items += _performed_items(root, man, song, hits, split, min_agreement, min_offset_strength, stats)
            continue
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
            raise ValueError(f"unknown audio choice {audio!r} (stem, separated, mix or performed)")
        items.append(Item(f"{song['id']}:{audio}", split, files, hits, source, float(song.get("duration", 0))))
    return items


# Which of the separator's four stems holds each pitched stem kind (Song Deck's built-in separator
# writes drums, bass, vocals and other; an instrument playing the melody lands in "other").
SEPARATED_STEM = {"bass": "bass", "vocals": "vocals", "melody": "other", "piano": "other", "guitar": "other",
                  "other": "other"}


def part_of(stems) -> str:
    """The part Song Deck's dataset:perform judges these stem kinds as (its PARTS)."""
    return "bass" if "bass" in stems else "melody"


def performed_notes(perf: dict, notes: list, duration: float, part: str, min_agreement: float = MIN_AGREEMENT,
                    min_offset_strength: float = MIN_OFFSET_STRENGTH):
    """A take's scores for one pitched part (perf["parts"][part]) -> (notes in performance time,
    accepted regions), or None when the part was not judged or the take is rejected. Each note
    moves by the take's offset plus the shift of the window holding its start (performed_take)."""
    scored = (perf.get("parts") or {}).get(part)
    if not scored or scored.get("agreement") is None:
        return None
    pseudo = {"offset": perf.get("offset", 0), "offset_strength": perf.get("offset_strength", 0),
              "agreement": scored.get("agreement"), "windows": scored.get("windows")}
    take = performed_take(pseudo, [(n[0], k) for k, n in enumerate(notes)], duration, min_agreement, min_offset_strength)
    if take is None:
        return None
    moved, regions = take
    out = []
    for t, k in moved:
        a, b, pitch, bend = notes[k]
        out.append((t, b + (t - a), pitch, bend))
    out.sort()
    return out, regions


def read_songdeck_notes(root, stems=("bass",), audio: str = "stem", min_agreement: float = MIN_AGREEMENT,
                        min_offset_strength: float = MIN_OFFSET_STRENGTH, stats: dict | None = None) -> list[Item]:
    """Pitched items from a Song Deck dataset: the stems of the given kinds
    (`stem` field: bass, vocals, melody, piano, guitar, other) with their
    notes and bends. audio: "stem" (those stems summed), "separated" (the
    separator's stem that holds them, SEPARATED_STEM), "mix", or "performed"
    (each ACE-Step take's separated stems for the part, with the notes moved
    to performance time and only the accepted windows as regions; `stats`
    gathers the counts as for drums)."""
    root = Path(root)
    man = json.loads((root / "manifest.json").read_text())
    if man.get("format") != "songdeck-dataset":
        raise ValueError(f"{root}/manifest.json: format is not songdeck-dataset")
    if int(man.get("version", 0)) != 1:
        raise ValueError(f"{root}/manifest.json: version {man.get('version')} is not supported (expected 1)")
    source = f"Song Deck dataset {root.name} (Song Deck {man.get('songdeck_version', '?')}, {man.get('created', '?')}): {man.get('licence', '')}"
    items = []
    for song in man["songs"]:
        mine = [s for s in song.get("stems", []) if s.get("stem") in stems and not s.get("drums")]
        if not mine:
            continue
        raw = []
        for s in mine:
            raw += s["notes"] if s.get("notes") is not None else read_notes(root / s["midi"]) if s.get("midi") else []
        notes = sorted((float(n["start"]), float(n["end"]), int(round(n["pitch"])),
                        [tuple(map(float, b)) for b in n["bend"]] if n.get("bend") else None)
                       for n in raw if float(n["end"]) > float(n["start"]))
        split = song.get("split", "train")
        split = "valid" if split == "validation" else split
        names = sorted({SEPARATED_STEM.get(s["stem"], "other") for s in mine})
        if audio == "performed":
            items += _performed_pitched(root, song, notes, split, names, part_of(stems), source, min_agreement,
                                        min_offset_strength, stats)
            continue
        if audio == "stem":
            files = [root / s["audio"] for s in mine]
        elif audio == "separated":
            seps = (song.get("separated") or {}).get("stems", {})
            if not all(seps.get(n) for n in names):
                continue
            files = [root / seps[n] for n in names]
        elif audio == "mix":
            files = [root / song["mix"]]
        else:
            raise ValueError(f"unknown audio choice {audio!r} for pitched stems (stem, separated, mix or performed)")
        items.append(Item(f"{song['id']}:{audio}", split, files, [], source, float(song.get("duration", 0)), None, notes))
    return items


def _performed_pitched(root, song, notes, split, names, part, source, min_agreement, min_offset_strength, stats):
    st = stats if stats is not None else {}
    for k in ("takes", "accepted_takes", "missing_audio", "weak_offset", "no_window", "not_judged", "seconds",
              "accepted_seconds"):
        st.setdefault(k, 0)
    st.setdefault("providers", {})
    items = []
    duration = float(song.get("duration", 0))
    for perf in song.get("performances") or []:
        st["takes"] += 1
        dur = float(perf.get("duration") or duration)
        st["seconds"] += dur
        seps = (perf.get("separated") or {}).get("stems") or {}
        if not all(seps.get(n) and (root / seps[n]).exists() for n in names):
            st["missing_audio"] += 1
            continue
        if not (perf.get("parts") or {}).get(part):
            st["not_judged"] += 1
            continue
        take = performed_notes(perf, notes, duration, part, min_agreement, min_offset_strength)
        if take is None:
            st["weak_offset" if float(perf.get("offset_strength", 0)) < min_offset_strength else "no_window"] += 1
            continue
        pnotes, regions = take
        provider, model = perf.get("provider", "?"), perf.get("model", "?")
        src = (f"ACE-Step performances of Song Deck renders ({provider}, {model}), dataset {root.name}; "
               f"{ACESTEP_LICENCE}")
        it = Item(f"{song['id']}:performed:{perf.get('id', len(items))}", split, [root / seps[n] for n in names], [],
                  src, dur, regions, pnotes)
        st["accepted_takes"] += 1
        st["accepted_seconds"] += region_seconds(it)
        st["providers"][f"{provider} {model}"] = st["providers"].get(f"{provider} {model}", 0) + 1
        items.append(it)
    return items


def summary_notes(items: list[Item]) -> str:
    out = []
    for sp in SPLITS:
        it = [i for i in items if i.split == sp]
        if it:
            pitches = [n[2] for i in it for n in i.notes]
            bent = sum(1 for i in it for n in i.notes if n[3])
            out.append(f"{sp}: {len(it)} items, {sum(i.duration for i in it) / 60:.1f} min, {len(pitches)} notes"
                       + (f" (MIDI {min(pitches)}-{max(pitches)}, {bent} with bends)" if pitches else ""))
    return "\n".join(out)


def _performed_items(root, man, song, hits, split, min_agreement, min_offset_strength, stats):
    st = stats if stats is not None else {}
    for k in ("takes", "accepted_takes", "missing_audio", "weak_offset", "no_window", "seconds", "accepted_seconds"):
        st.setdefault(k, 0)
    st.setdefault("providers", {})
    items = []
    duration = float(song.get("duration", 0))
    for perf in song.get("performances") or []:
        st["takes"] += 1
        dur = float(perf.get("duration") or duration)
        st["seconds"] += dur
        drums = ((perf.get("separated") or {}).get("stems") or {}).get("drums")
        if not drums or not (root / drums).exists():
            st["missing_audio"] += 1
            continue
        take = performed_take(perf, hits, duration, min_agreement, min_offset_strength)
        if take is None:
            st["weak_offset" if float(perf.get("offset_strength", 0)) < min_offset_strength else "no_window"] += 1
            continue
        phits, regions = take
        provider, model = perf.get("provider", "?"), perf.get("model", "?")
        source = (f"ACE-Step performances of Song Deck renders ({provider}, {model}), dataset {root.name}; "
                  f"{ACESTEP_LICENCE}")
        it = Item(f"{song['id']}:performed:{perf.get('id', len(items))}", split, [root / drums], phits, source, dur, regions)
        st["accepted_takes"] += 1
        st["accepted_seconds"] += region_seconds(it)
        st["providers"][f"{provider} {model}"] = st["providers"].get(f"{provider} {model}", 0) + 1
        items.append(it)
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
        acc = sum(region_seconds(i) for i in it if i.regions is not None)
        out.append(f"{sp}: {len(it)} items, {sum(i.duration for i in it) / 60:.1f} min"
                   + (f" ({acc / 60:.1f} min of performed windows accepted)" if any(i.regions is not None for i in it) else "")
                   + ", hits "
                   + " ".join(f"{n}={k}" for n, k in zip(DRUM_CLASSES, counts)))
    return "\n".join(out)
