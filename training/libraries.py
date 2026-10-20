"""Training-data libraries: a catalogue of outside datasets (each with its licence and what it is
good for), downloading them, and importing them as Song Deck-format datasets that the training
scripts read like Song Deck's own renders (`--library DIR`):

    python3 -m training.libraries list [--use drums|bass|melody|vocals|separation] [--all]
    python3 -m training.libraries show slakh2100
    python3 -m training.libraries fetch slakh2100 --dir ~/backtrack-training/libraries [--yes]
    python3 -m training.libraries import slakh2100 --root DOWNLOADED --out ~/backtrack-training/libraries/slakh2100
    python3 -m training.libraries add slakh2100 --dir ~/backtrack-training/libraries [--yes] [--limit N]
    python3 -m training.libraries credits ~/backtrack-training/libraries/*

`add` is fetch then import: the imported dataset is <dir>/<id> (manifest.json and, for FLAC
sources, 22.05 kHz mono WAV copies), the download stays in <dir>/downloads/<id>. The pipeline's
`libraries` step runs `add` for each id in its config's `libraries.use`
(docs/training-data.md).

Only entries whose licence allows training and releasing the weights commercially can be
imported (verdict "train"); the others are listed (`--all`) for evaluation, with their licence,
so nobody has to look them up again. Licences were checked on 2026-10-20 against the source named
in each entry; check again before shipping weights trained on a library.

Python standard library only (and NumPy, which training needs anyway). FLAC and other non-WAV
audio is converted with ffmpeg (or the soundfile module, if installed).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import wave
import zipfile
from pathlib import Path

from .midi import read_notes

ZENODO_API = "https://zenodo.org/api/records/"
MAGENTA = "https://storage.googleapis.com/magentadata/datasets/"

# verdict: "train" (training and commercial release of the weights allowed, keep the attribution),
# "train-sa" (share-alike: whether it reaches the weights is unsettled, kept out by default),
# "eval" (non-commercial or research only: internal evaluation at most), "no" (not usable).
# source: {"zenodo": record, "files": [glob, ...]} or {"urls": [...]}; importer: how `import` reads it.
CATALOG: list[dict] = [
    # ---- drums ----
    {"id": "egmd", "name": "E-GMD (Expanded Groove MIDI Dataset)", "use": ["drums"], "hours": 444, "gb": 90,
     "what": "Human drumming on a Roland TD-17 re-recorded through 43 drum kits, with aligned MIDI (velocity)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The dataset is made available by Google LLC under a Creative Commons Attribution 4.0 International (CC BY 4.0) License.",
     "licence_url": "https://magenta.tensorflow.org/datasets/e-gmd",
     "attribution": "E-GMD v1.0.0 (Magenta; Callender, Hawthorne and Engel 2020), CC BY 4.0",
     "source": {"urls": [MAGENTA + "e-gmd/v1.0.0/e-gmd-v1.0.0.zip"]}, "importer": "magenta-drums"},
    {"id": "groove", "name": "Groove MIDI Dataset (GMD)", "use": ["drums"], "hours": 13.6, "gb": 4.8,
     "what": "10 drummers on a Roland TD-11, MIDI with audio from the kit",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The dataset is made available by Google LLC under a Creative Commons Attribution 4.0 International (CC BY 4.0) License.",
     "licence_url": "https://magenta.tensorflow.org/datasets/groove",
     "attribution": "Groove MIDI Dataset v1.0.0 (Magenta; Gillick, Roberts, Engel, Eck and Bamman 2019), CC BY 4.0",
     "source": {"urls": [MAGENTA + "groove/groove-v1.0.0.zip"]}, "importer": "magenta-drums"},
    {"id": "stemgmd", "name": "StemGMD", "use": ["drums", "separation"], "hours": 1224, "gb": 1130,
     "what": "GMD rendered into isolated per-piece drum stems on 10 kits (drum sub-separation)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The dataset is made available under a Creative Commons Attribution 4.0 International (CC BY 4.0) License.",
     "licence_url": "https://zenodo.org/records/7860223",
     "attribution": "StemGMD (Mezza, Giampiccolo, De Martini and Bernardini 2024), CC BY 4.0",
     "source": {"zenodo": "7860223"}, "importer": None},
    {"id": "waivops-pop-rok", "name": "WaivOps POP-ROK", "use": ["drums"], "hours": 24, "gb": 12.6,
     "what": "5,378 acoustic drum loops on about 30 kits with drum-note labels (no toms)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The POP-ROK dataset is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).",
     "licence_url": "https://zenodo.org/records/14038284",
     "attribution": "WaivOps POP-ROK (Patchbanks), CC BY 4.0",
     "source": {"zenodo": "14038284"}, "importer": None},
    {"id": "waivops-edm-tr9", "name": "WaivOps EDM-TR9", "use": ["drums"], "hours": 8, "gb": 4,
     "what": "3,780 TR-909-style loops with BPM labels",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The EDM-TR9 dataset is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).",
     "licence_url": "https://zenodo.org/records/10278066",
     "attribution": "WaivOps EDM-TR9 (Patchbanks), CC BY 4.0",
     "source": {"zenodo": "10278066"}, "importer": None},
    # ---- multi-instrument with MIDI ----
    {"id": "slakh2100", "name": "Slakh2100 (redux)", "use": ["drums", "bass", "melody", "separation"], "hours": 145, "gb": 105,
     "what": "2,100 Lakh MIDI songs rendered per instrument: stems, mix and aligned MIDI per stem, train/validation/test splits",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0",
     "licence_url": "https://zenodo.org/records/4599666",
     "attribution": "Slakh2100 (Manilow, Wichern, Seetharaman and Le Roux 2019), CC BY 4.0",
     "caveat": "rendered with Native Instruments Kontakt instruments, whose current EULA bars AI training on its sounds; "
               "the dataset's own licence is CC BY 4.0 (from its authors, 2019). The MIDI comes from the Lakh MIDI Dataset, scraped from the web.",
     "source": {"zenodo": "4599666"}, "importer": "slakh"},
    {"id": "babyslakh", "name": "BabySlakh", "use": ["drums", "bass", "melody"], "hours": 1, "gb": 0.9,
     "what": "The first 20 Slakh songs at 16 kHz: a quick check that the Slakh import and training work",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/4603870",
     "attribution": "BabySlakh (Manilow et al. 2019), CC BY 4.0",
     "caveat": "as Slakh2100; 16 kHz, so a test set rather than training material",
     "source": {"zenodo": "4603870"}, "importer": "slakh"},
    {"id": "aam", "name": "AAM: Artificial Audio Multitracks", "use": ["drums", "bass", "melody", "separation"], "hours": 158, "gb": 190,
     "what": "3,000 generated songs played with real instrument samples: stems, mixes, MIDI, onsets, beats, chords, melody labels",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/5794629",
     "attribution": "AAM: Artificial Audio Multitracks (Ostermann, Vatolkin and Ebeling 2023), CC BY 4.0",
     "source": {"zenodo": "5794629"}, "importer": None},
    {"id": "cocochorales", "name": "CocoChorales", "use": ["bass", "melody", "separation"], "hours": 1411, "gb": 2900,
     "what": "240,000 four-part chorales synthesized per instrument (MIDI-DDSP), stems and MIDI",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "is available under the Creative Commons Attribution 4.0 International (CC-BY 4.0).",
     "licence_url": "https://magenta.tensorflow.org/datasets/cocochorales",
     "attribution": "CocoChorales (Wu, Gardner, Manilow, Simon, Hawthorne and Engel 2022), CC BY 4.0",
     "source": {"urls": []}, "importer": None},
    {"id": "nsynth", "name": "NSynth", "use": ["melody"], "hours": 340, "gb": 30,
     "what": "305,979 single notes from about 1,000 instruments (augmentation, timbre variety)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "made available by Google Inc. under a Creative Commons Attribution 4.0 International (CC BY 4.0) license.",
     "licence_url": "https://magenta.tensorflow.org/datasets/nsynth",
     "attribution": "NSynth (Engel et al. 2017), CC BY 4.0",
     "source": {"urls": []}, "importer": None},
    {"id": "guitarset", "name": "GuitarSet", "use": ["melody"], "hours": 3, "gb": 8.2,
     "what": "Acoustic guitar, hexaphonic pickup, note and f0 annotations (JAMS)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/3371780",
     "attribution": "GuitarSet (Xi, Bittner, Pauwels, Ye and Bello 2018), CC BY 4.0",
     "source": {"zenodo": "3371780", "files": ["annotation.zip", "audio_mono-mic.zip"]}, "importer": None},
    {"id": "jazzsamba", "name": "JazzSAMBA", "use": ["drums", "bass", "melody", "separation"], "hours": 10, "gb": 22,
     "what": "76 jazz standards by 8 musicians: real recorded stems (drums, bass, piano, horns), mixes and MIDI",
     "licence": "MIT-style (bundled LICENSE)", "verdict": "train",
     "licence_quote": "Permission is hereby granted, free of charge... to use, copy, modify, merge, publish, and distribute, subject to the following conditions: The above copyright notice... shall be included",
     "licence_url": "https://zenodo.org/records/22943963",
     "attribution": "JazzSAMBA (2026), MIT-style licence; copyright notice in its LICENSE",
     "caveat": "the standards themselves may still be copyrighted compositions (the recordings are the dataset's own)",
     "source": {"zenodo": "22943963", "files": ["JazzSAMBA-sync.zip", "JazzSAMBA-sync-derived.zip", "JazzSAMBA-catalog.zip"]},
     "importer": None},
    {"id": "musicnet", "name": "MusicNet", "use": ["melody"], "hours": 34, "gb": 11,
     "what": "330 classical recordings with aligned note labels",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The audio recordings in MusicNet consist of Creative Commons licensed and Public Domain performances",
     "licence_url": "https://zenodo.org/records/5120004",
     "attribution": "MusicNet (Thickstun, Harchaoui and Kakade 2017), CC BY 4.0",
     "caveat": "per-recording licences of the source performances are not listed",
     "source": {"zenodo": "5120004"}, "importer": None},
    {"id": "lakh-midi", "name": "Lakh MIDI Dataset", "use": ["drums", "bass", "melody"], "hours": None, "gb": 1.7,
     "what": "176,581 MIDI files (no audio: render them through Song Deck's instruments)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "The Lakh MIDI Dataset is distributed with a CC-BY 4.0 license",
     "licence_url": "https://colinraffel.com/projects/lmd/",
     "attribution": "Lakh MIDI Dataset (Raffel 2016), CC BY 4.0",
     "caveat": "the files were scraped from the web; their transcriptions and compositions belong to unknown third parties",
     "source": {"urls": ["http://hog.ee.columbia.edu/craffel/lmd/lmd_full.tar.gz"]}, "importer": None},
    # ---- vocals ----
    {"id": "vocalset", "name": "VocalSet", "use": ["vocals", "separation"], "hours": 10.1, "gb": 2.1,
     "what": "20 professional singers: techniques, scales and excerpts, a cappella (no note labels)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/1193957",
     "attribution": "VocalSet (Wilkins, Seetharaman, Wahl and Pardo 2018), CC BY 4.0",
     "source": {"zenodo": "1193957"}, "importer": None},
    {"id": "choral-singing", "name": "Choral Singing Dataset", "use": ["vocals"], "hours": 0.5, "gb": 1.1,
     "what": "16 singers, close-miked one by one, with f0, notes and MIDI",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/2649950",
     "attribution": "Choral Singing Dataset (Cuesta, Gómez, Martorell and Gómez 2018), CC BY 4.0",
     "source": {"zenodo": "2649950", "files": ["ChoralSingingDataset.zip"]}, "importer": None},
    {"id": "esmuc-choir", "name": "ESMUC Choir Dataset", "use": ["vocals"], "hours": 0.5, "gb": 2.3,
     "what": "12 singers, close and room mics, corrected f0 and notes",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/5848990",
     "attribution": "ESMUC Choir Dataset (Rosenzweig, Cuesta et al. 2022), CC BY 4.0",
     "source": {"zenodo": "5848990"}, "importer": None},
    {"id": "cantoria", "name": "Cantoría Dataset", "use": ["vocals"], "hours": 0.3, "gb": 0.9,
     "what": "A vocal quartet: 11 songs, multitrack, f0",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/5878677",
     "attribution": "Cantoría Dataset (Cuesta, Gómez 2022), CC BY 4.0",
     "source": {"zenodo": "5878677"}, "importer": None},
    {"id": "multivox", "name": "MULTIVOX", "use": ["vocals", "separation"], "hours": 3, "gb": None,
     "what": "154 a cappella group performances, one near-field recording per singer (no note labels)",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/17058101",
     "attribution": "MULTIVOX (2025), CC BY 4.0",
     "source": {"zenodo": "17058101"}, "importer": None},
    {"id": "ursing", "name": "URSing", "use": ["vocals", "separation"], "hours": 3, "gb": None,
     "what": "65 songs: isolated solo vocal and the mix with accompaniment",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/6404999",
     "attribution": "URSing (2022), CC BY 4.0",
     "caveat": "the backing tracks the singers sang over may be third-party recordings",
     "source": {"zenodo": "6404999"}, "importer": None},
    {"id": "albumdb", "name": "AlbumDB", "use": ["separation", "drums", "bass", "vocals"], "hours": 0.7, "gb": 15,
     "what": "A ten-song indie album: raw and mixed stems (vocals, drums, bass, other), stereo and 7.1.4 masters",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0 (made for automatic mixing and source separation)",
     "licence_url": "https://zenodo.org/records/19683001",
     "attribution": "AlbumDB (2026), CC BY 4.0",
     "source": {"zenodo": "19683001", "files": ["stems_mixed.zip", "masters_stereo.zip"]}, "importer": None},
    {"id": "chorale-bricks", "name": "ChoraleBricks", "use": ["melody", "separation"], "hours": 2.2, "gb": None,
     "what": "Wind and brass SATB multitracks with f0 and notes; 52 h of ensemble permutations",
     "licence": "CC BY 4.0", "verdict": "train",
     "licence_quote": "Zenodo record licence: cc-by-4.0", "licence_url": "https://zenodo.org/records/20849469",
     "attribution": "ChoraleBricks (2026), CC BY 4.0",
     "source": {"zenodo": "20849469"}, "importer": None},
    # ---- share-alike, evaluation only, not usable ----
    {"id": "medleydb-sample", "name": "MedleyDB Sample", "use": ["separation"], "hours": 0.2, "gb": 0.5,
     "what": "A few MedleyDB multitracks", "licence": "CC BY-SA 4.0", "verdict": "train-sa",
     "licence_quote": "Zenodo record licence: cc-by-sa-4.0", "licence_url": "https://zenodo.org/records/1438309",
     "attribution": "MedleyDB Sample (Bittner et al.), CC BY-SA 4.0", "source": {"zenodo": "1438309"}, "importer": None},
    *[{"id": i, "name": n, "use": u, "hours": None, "gb": None, "what": w, "licence": lic, "verdict": "eval",
       "licence_quote": q, "licence_url": url, "attribution": f"{n}, {lic}", "source": {"urls": []}, "importer": None}
      for i, n, u, w, lic, q, url in [
          ("maestro", "MAESTRO", ["melody"], "About 199 h of competition piano with MIDI", "CC BY-NC-SA 4.0",
           "...under a Creative Commons Attribution Non-Commercial Share-Alike 4.0 (CC BY-NC-SA 4.0) license.",
           "https://magenta.tensorflow.org/datasets/maestro"),
          ("musdb18hq", "MUSDB18-HQ", ["separation"], "150 songs, 4 stems", "educational use only",
           "provided for educational purposes only and the material contained in them should not be used for any commercial purpose",
           "https://zenodo.org/records/3338373"),
          ("moisesdb", "MoisesDB", ["separation"], "240 songs with hierarchical stems", "CC BY-NC-SA 4.0",
           "MoisesDB is distributed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License",
           "https://github.com/moises-ai/moises-db"),
          ("medleydb", "MedleyDB 1 and 2", ["separation", "melody"], "196 multitracks with melody f0", "CC BY-NC-SA 4.0",
           "MedleyDB is offered free of charge for non-commercial research use only", "https://zenodo.org/records/1649325"),
          ("mdb-drums", "MDB Drums", ["drums"], "23 MedleyDB songs with drum annotations", "CC BY-NC-SA 4.0",
           "The audio and annotation files are published under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License",
           "https://github.com/CarlSouthall/MDBDrums"),
          ("enst-drums", "ENST-Drums", ["drums"], "3 drummers, 8-channel recordings", "research only (signed licence)",
           "solely for scientific purposes", "https://perso.telecom-paristech.fr/grichard/ENST-drums/"),
          ("maps", "MAPS", ["melody"], "Piano with MIDI", "CC BY-NC-SA 2.0 FR", "Creative Commons (by-nc-sa 2.0 fr)",
           "https://zenodo.org/records/18160555"),
          ("pijama", "PiJAMA", ["melody"], "More than 200 h of jazz piano", "CC BY-NC 4.0", "Zenodo record licence: cc-by-nc-4.0",
           "https://zenodo.org/records/8354955"),
          ("csd", "Children's Song Dataset", ["vocals"], "200 sung recordings with MIDI", "CC BY-NC-SA 4.0",
           "it is prohibited to be used for commercial purposes", "https://zenodo.org/records/4785016"),
          ("m4singer", "M4Singer", ["vocals"], "Mandarin multi-singer", "CC BY-NC-SA 4.0", "CC BY-NC-SA 4.0 (NonCommercial!)",
           "https://github.com/M4Singer/M4Singer"),
          ("gtsinger", "GTSinger", ["vocals"], "Multilingual singing techniques", "CC BY-NC-SA 4.0", "CC BY-NC-SA 4.0 License",
           "https://github.com/AaronZ345/GTSinger"),
          ("opensinger", "OpenSinger", ["vocals"], "Chinese singing", "CC BY-NC-SA", "must follow the CC BY-NC-SA LICENSE",
           "https://github.com/Multi-Singer/Multi-Singer.github.io"),
          ("dagstuhl-choirset", "Dagstuhl ChoirSet", ["vocals"], "Choir multitracks", "CC BY-NC 4.0",
           "Zenodo record licence: cc-by-nc-4.0", "https://zenodo.org/records/4618287"),
      ]],
    *[{"id": i, "name": n, "use": u, "hours": None, "gb": None, "what": w, "licence": lic, "verdict": "no",
       "licence_quote": q, "licence_url": url, "attribution": f"{n}, {lic}", "source": {"urls": []}, "importer": None}
      for i, n, u, w, lic, q, url in [
          ("adtof", "ADTOF", ["drums"], "359 h of rhythm-game drum annotations", "CC BY-NC-SA 4.0, no redistribution",
           "offered free of charge for internal non-commercial use", "https://zenodo.org/records/10084511"),
          ("idmt-smt-drums", "IDMT-SMT-Drums (and -Bass, -Guitar)", ["drums", "bass"], "Loops with onsets", "CC BY-NC-ND 4.0",
           "Zenodo record licence: cc-by-nc-nd-4.0", "https://zenodo.org/records/7544164"),
          ("opencpop", "Opencpop", ["vocals"], "5.2 h Mandarin singing", "CC BY-NC-ND 4.0 (commercial licence on request)",
           "available to download for non-commercial purposes under a CC BY-NC-ND 4.0 License", "https://wenet-e2e.github.io/opencpop/"),
          ("mtg-jamendo", "MTG-Jamendo", ["vocals"], "55,000 full tracks", "non-commercial research only",
           "made available solely for non-commercial research and academic use", "https://github.com/MTG/mtg-jamendo-dataset"),
          ("cambridge-mt", "Mixing Secrets / Cambridge-MT multitracks", ["separation"], "Hundreds of multitracks",
           "educational use; AI training only outside commercial ventures", "for educational purposes only",
           "https://cambridge-mt.com/ms3/mtk-faq/"),
          ("dsd100", "DSD100", ["separation"], "100 Mixing Secrets songs", "the original resource's terms",
           "Please refer to this original resource for any question regarding your rights", "https://sigsep.github.io/datasets/dsd100.html"),
          ("rwc", "RWC Music Database", ["melody"], "Research database", "research only",
           "the database may not be used for any purpose other than research.", "https://staff.aist.go.jp/m.goto/RWC-MDB/"),
          ("damp", "DAMP (Smule)", ["vocals"], "Karaoke vocals", "non-commercial research, no longer downloadable",
           "the data could only be used for non-commercial, research purposes", "https://ccrma.stanford.edu/damp/"),
      ]],
]

BY_ID = {e["id"]: e for e in CATALOG}
TRAINABLE = ("train",)


def entry(lib_id: str) -> dict:
    e = BY_ID.get(lib_id)
    if not e:
        raise SystemExit(f"unknown library {lib_id!r} (python3 -m training.libraries list --all)")
    return e


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


def zenodo_files(record: str, patterns=None) -> list[dict]:
    """The record's files as {"name", "url", "size", "md5"}, those matching `patterns` (globs)."""
    with urllib.request.urlopen(ZENODO_API + record, timeout=60) as r:
        data = json.load(r)
    out = []
    for f in data.get("files", []):
        if patterns and not any(fnmatch.fnmatch(f["key"], p) for p in patterns):
            continue
        md5 = f.get("checksum", "")
        out.append({"name": f["key"], "url": f["links"]["self"], "size": int(f.get("size", 0)),
                    "md5": md5.split(":", 1)[1] if md5.startswith("md5:") else None})
    return out


def source_files(e: dict) -> list[dict]:
    src = e["source"]
    if src.get("zenodo"):
        return zenodo_files(src["zenodo"], src.get("files"))
    return [{"name": u.rsplit("/", 1)[-1], "url": u, "size": 0, "md5": None} for u in src.get("urls", [])]


def download(url: str, dest: Path, size: int = 0, md5: str | None = None) -> Path:
    """Downloads url to dest, resuming a partial download; checks the MD5 when given."""
    if dest.exists() and (not size or dest.stat().st_size == size):
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        if have and r.status != 206:
            have = 0  # the server ignored the range: start over
        total = have + int(r.headers.get("Content-Length") or 0)
        done, last = have, 0.0
        with open(part, "ab" if have else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done / total - last >= 0.02:
                    last = done / total
                    print(f"  {dest.name}: {done / 1e9:.2f} of {total / 1e9:.2f} GB ({100 * last:.0f}%)", flush=True)
    if size and part.stat().st_size != size:
        raise SystemExit(f"{dest.name}: got {part.stat().st_size} bytes, expected {size}; run again to resume")
    if md5:
        h = hashlib.md5()
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        if h.hexdigest() != md5:
            part.unlink()
            raise SystemExit(f"{dest.name}: checksum mismatch (deleted); run again")
    part.replace(dest)
    return dest


def extract(archive: Path, into: Path) -> None:
    marker = into / f".extracted-{archive.name}"
    if marker.exists():
        return
    print(f"  extracting {archive.name}", flush=True)
    into.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    elif archive.name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive) as t:
            try:
                t.extractall(into, filter="data")  # no paths outside `into` (Python 3.12, and 3.8-3.11 updates)
            except TypeError:
                for m in t.getmembers():
                    if Path(m.name).is_absolute() or ".." in Path(m.name).parts or m.issym() or m.islnk():
                        raise SystemExit(f"{archive.name}: unsafe path in the archive: {m.name}")
                t.extractall(into)
    else:
        return
    marker.touch()


def fetch(lib_id: str, root: Path, yes: bool = False) -> Path:
    """Downloads and unpacks a library into <root>/downloads/<id>; returns that folder."""
    e = entry(lib_id)
    if e["verdict"] not in TRAINABLE:
        raise SystemExit(f"{e['name']}: {e['licence']} ({e['verdict']}): not fetched for training")
    files = source_files(e)
    if not files:
        raise SystemExit(f"{e['name']}: no direct download; get it from {e['licence_url']} and use `import --root`")
    total = sum(f["size"] for f in files) / 1e9 or (e.get("gb") or 0)
    dest = root / "downloads" / lib_id
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free / 1e9
    print(f"[libraries] {e['name']}: {len(files)} file(s), about {total:.1f} GB (and as much again unpacked); "
          f"{free:.0f} GB free; licence {e['licence']}", flush=True)
    if total * 2 > free:
        raise SystemExit(f"[libraries] {e['name']} needs about {2 * total:.0f} GB (download and unpacked) and "
                         f"{root} has {free:.0f} GB free")
    if total > 5 and not yes:
        raise SystemExit(f"[libraries] {e['name']} is about {total:.0f} GB: add --yes to download it")
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        a = download(f["url"], dest / f["name"], f["size"], f["md5"])
        extract(a, dest)
    return dest


# ---------------------------------------------------------------------------
# Importing: a Song Deck-format dataset (manifest.json) over the library's files
# ---------------------------------------------------------------------------


def wav_info(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnframes() / w.getframerate()


def to_wav(src: Path, dst: Path, rate: int) -> Path:
    """src as a mono WAV at `rate` (training's own rate, 22050), converted once."""
    if dst.exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    if shutil.which("ffmpeg"):
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(rate),
                            "-c:a", "pcm_s16le", str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg could not convert {src}: {r.stderr.strip()[-300:]}")
    else:
        try:
            import numpy as np
            import soundfile as sf
        except ImportError:
            raise SystemExit("converting FLAC needs ffmpeg on the PATH (or: pip install soundfile)") from None
        from .frontend import resample
        x, sr = sf.read(str(src), dtype="float32", always_2d=True)
        y = resample(x.mean(axis=1), sr, rate)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    tmp.replace(dst)
    return dst


def audio_for(src: Path, out: Path, rel: str, rate: int) -> str:
    """The path the manifest lists: WAV sources in place (absolute), others converted under out/."""
    if src.suffix.lower() == ".wav":
        return str(src.resolve())
    return str(to_wav(src, out / rel, rate).resolve())


def note_dicts(notes: list[dict], remap: dict | None = None) -> list[dict]:
    out = []
    for n in notes:
        p = int(n["pitch"])
        out.append({"pitch": remap.get(p, p) if remap else p, "start": round(float(n["start"]), 4),
                    "end": round(float(n["end"]), 4), "velocity": int(n.get("velocity", 100))})
    return out


def split_by_hash(key: str) -> str:
    """80/10/10, stable per song, for libraries without their own splits."""
    v = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) % 10
    return "test" if v == 0 else "valid" if v == 1 else "train"


def read_slakh_meta(path: Path) -> dict:
    """Slakh's metadata.yaml (two levels under `stems:`) without a YAML parser."""
    stems: dict = {}
    cur = None
    in_stems = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        value = value.strip().strip("'\"")
        if indent == 0:
            in_stems = key == "stems"
        elif in_stems and indent == 2:
            cur = stems.setdefault(key, {})
        elif in_stems and cur is not None and indent >= 4:
            cur[key] = value
    return stems


# Slakh's inst_class -> Song Deck stem kind (what the training scripts select: drums, bass,
# melody, piano, guitar, other). Lead-like classes count as melody.
SLAKH_KIND = {"Drums": "drums", "Bass": "bass", "Piano": "piano", "Guitar": "guitar", "Synth Lead": "melody",
              "Reed": "melody", "Pipe": "melody", "Brass": "melody"}


def import_slakh(e: dict, root: Path, out: Path, rate: int, limit: int) -> list[dict]:
    songs = []
    tracks = sorted(p.parent for p in root.rglob("metadata.yaml"))
    if limit:
        tracks = tracks[:limit]
    for i, t in enumerate(tracks):
        split = {"train": "train", "validation": "valid", "test": "test"}.get(t.parent.name)
        if t.parent.name == "omitted":
            continue  # Slakh's own "omitted" folder: duplicates of other songs
        split = split or split_by_hash(t.name)
        meta = read_slakh_meta(t / "metadata.yaml")
        mix = next((p for p in (t / "mix.flac", t / "mix.wav") if p.exists()), None)
        if mix is None:
            continue
        stems = []
        for sid, m in sorted(meta.items()):
            audio = next((p for p in (t / "stems" / f"{sid}.flac", t / "stems" / f"{sid}.wav") if p.exists()), None)
            midi = t / "MIDI" / f"{sid}.mid"
            if audio is None or not midi.exists():
                continue
            drums = m.get("is_drum") == "true"
            kind = "drums" if drums else SLAKH_KIND.get(m.get("inst_class", ""), "other")
            stems.append({"track": sid, "name": m.get("midi_program_name", sid), "stem": kind, "drums": drums,
                          "program": int(m.get("program_num") or 0), "instrument": m.get("plugin_name", ""),
                          "audio": audio_for(audio, out, f"audio/{t.name}/{sid}.wav", rate),
                          "midi": str(midi.resolve()), "notes": note_dicts(read_notes(midi))})
        if not stems:
            continue
        mix_path = audio_for(mix, out, f"audio/{t.name}/mix.wav", rate)
        sr, dur = wav_info(Path(mix_path))
        songs.append({"id": f"{e['id']}-{t.name}", "split": split, "sample_rate": sr, "duration": round(dur, 4),
                      "mix": mix_path, "stems": stems})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(tracks)} songs", flush=True)
    return songs


# E-GMD's Roland TD-17 also uses 22 (closed hi-hat edge), 26 (open hi-hat edge) and 58 (floor tom
# rim): written as the General MIDI notes the drum classes read.
TD17_TO_GM = {22: 42, 26: 46, 58: 43}


def import_magenta_drums(e: dict, root: Path, out: Path, rate: int, limit: int) -> list[dict]:
    """E-GMD and the Groove MIDI Dataset: a CSV (split, midi_filename, audio_filename, duration)."""
    csv_path = next(iter(sorted(root.rglob("e-gmd-v*.csv"))), None) or next(iter(sorted(root.rglob("info.csv"))), None)
    if csv_path is None:
        raise SystemExit(f"{root}: no e-gmd-v*.csv or info.csv")
    base = csv_path.parent
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("audio_filename")]
    if limit and len(rows) > limit:
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    songs = []
    for r in rows:
        audio, midi = base / r["audio_filename"], base / r["midi_filename"]
        if not audio.exists() or not midi.exists():
            continue
        split = {"validation": "valid"}.get(r["split"], r["split"])
        path = audio_for(audio, out, f"audio/{audio.stem}.wav", rate)
        sr, dur = wav_info(Path(path))
        rid = r.get("id") or audio.stem
        songs.append({"id": f"{e['id']}-{rid}".replace("/", "_"), "split": split, "sample_rate": sr,
                      "duration": round(dur, 4), "mix": path, "style": r.get("style"), "bpm": r.get("bpm"),
                      "stems": [{"track": "drums", "name": r.get("kit_name") or "drums", "stem": "drums", "drums": True,
                                 "audio": path, "midi": str(midi.resolve()),
                                 "notes": note_dicts(read_notes(midi), TD17_TO_GM)}]})
    return songs


IMPORTERS = {"slakh": import_slakh, "magenta-drums": import_magenta_drums}


def import_library(lib_id: str, root: Path, out: Path, rate: int = 22050, limit: int = 0) -> Path:
    e = entry(lib_id)
    if e["verdict"] not in TRAINABLE:
        raise SystemExit(f"{e['name']}: {e['licence']} ({e['verdict']}): not imported for training")
    imp = IMPORTERS.get(e.get("importer") or "")
    if imp is None:
        raise SystemExit(f"{e['name']}: no importer yet (its labels need their own reader); see docs/training-data.md")
    out.mkdir(parents=True, exist_ok=True)
    print(f"[libraries] importing {e['name']} from {root} into {out}", flush=True)
    songs = imp(e, root, out, rate, limit)
    if not songs:
        raise SystemExit(f"[libraries] found no songs of {e['name']} under {root}")
    licence = f"{e['attribution']}" + (f"; caveat: {e['caveat']}" if e.get("caveat") else "")
    manifest = {
        "format": "songdeck-dataset", "version": 1, "songdeck_version": "n/a",
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": f"{e['name']} ({e['licence']}): {e['attribution']}",
        "licence": licence,
        "library": {"id": e["id"], "name": e["name"], "licence": e["licence"], "licence_url": e["licence_url"],
                    "licence_quote": e["licence_quote"], "attribution": e["attribution"], "root": str(root.resolve()),
                    "rate": rate, "limit": limit or None},
        "songs": songs,
    }
    tmp = out / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1) + "\n")
    tmp.replace(out / "manifest.json")
    by = {s: sum(1 for x in songs if x["split"] == s) for s in ("train", "valid", "test")}
    hours = sum(x["duration"] for x in songs) / 3600
    print(f"[libraries] {e['name']}: {len(songs)} songs ({by['train']} train, {by['valid']} valid, {by['test']} test), "
          f"{hours:.1f} h -> {out / 'manifest.json'}", flush=True)
    return out


def add(lib_id: str, root: Path, yes: bool = False, limit: int = 0, rate: int = 22050) -> Path:
    out = root / lib_id
    if (out / "manifest.json").exists():
        man = json.loads((out / "manifest.json").read_text())
        if (man.get("library") or {}).get("limit") == (limit or None):
            print(f"[libraries] {lib_id}: already imported ({len(man['songs'])} songs) at {out}", flush=True)
            return out
    downloaded = fetch(lib_id, root, yes)
    return import_library(lib_id, downloaded, out, rate, limit)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def show_list(use: str | None, everything: bool) -> None:
    rows = [e for e in CATALOG if (everything or e["verdict"] in TRAINABLE) and (not use or use in e["use"])]
    print(f"{'id':18} {'verdict':8} {'licence':28} {'hours':>6} {'GB':>6} {'import':6}  what")
    for e in rows:
        print(f"{e['id']:18} {e['verdict']:8} {e['licence'][:28]:28} {e['hours'] if e['hours'] is not None else '':>6} "
              f"{e['gb'] if e['gb'] is not None else '':>6} {'yes' if e.get('importer') else '':6}  {e['what']}")
    if not everything:
        print("\n(--all also lists share-alike, evaluation-only and unusable datasets, with their licences)")


def credits(dirs: list[str]) -> None:
    for d in dirs:
        p = Path(d).expanduser() / "manifest.json"
        if p.exists():
            man = json.loads(p.read_text())
            lib = man.get("library") or {}
            print(f"- {lib.get('attribution') or man.get('licence')} <{lib.get('licence_url', '')}>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m training.libraries", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="the catalogue")
    p.add_argument("--use", choices=["drums", "bass", "melody", "vocals", "separation"])
    p.add_argument("--all", action="store_true", help="also evaluation-only and unusable datasets")
    p = sub.add_parser("show", help="one entry in full")
    p.add_argument("id")
    p = sub.add_parser("fetch", help="download and unpack into <dir>/downloads/<id>")
    p.add_argument("id")
    p.add_argument("--dir", required=True)
    p.add_argument("--yes", action="store_true", help="download even when it is large")
    p = sub.add_parser("import", help="write a Song Deck-format dataset over a downloaded library")
    p.add_argument("id")
    p.add_argument("--root", required=True, help="the unpacked library")
    p.add_argument("--out", required=True)
    p.add_argument("--rate", type=int, default=22050, help="rate of converted (non-WAV) audio (default 22050)")
    p.add_argument("--limit", type=int, default=0, help="import about N songs (0: all)")
    p = sub.add_parser("add", help="fetch, then import into <dir>/<id>")
    p.add_argument("id")
    p.add_argument("--dir", required=True)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--rate", type=int, default=22050)
    p = sub.add_parser("credits", help="attribution lines for imported libraries")
    p.add_argument("dirs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        show_list(a.use, a.all)
    elif a.cmd == "show":
        print(json.dumps(entry(a.id), indent=1, ensure_ascii=False))
    elif a.cmd == "fetch":
        print(fetch(a.id, Path(a.dir).expanduser(), a.yes))
    elif a.cmd == "import":
        import_library(a.id, Path(a.root).expanduser(), Path(a.out).expanduser(), a.rate, a.limit)
    elif a.cmd == "add":
        add(a.id, Path(a.dir).expanduser(), a.yes, a.limit, a.rate)
    elif a.cmd == "credits":
        credits(a.dirs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
