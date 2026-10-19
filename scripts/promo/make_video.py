#!/usr/bin/env python3
"""Build "BackTrack: take it apart": the soundtrack, twelve editable scene masters, one MP4.

    python scripts/promo/make_video.py          # music, Surveil, scenes, film-spec.json and promo/backtrack-take-it-apart.mp4
    python scripts/promo/make_video.py --scenes # only rebuild the scene masters (needs song.json and audio/)

Luthier writes the guitar and bass parts and Song Deck arranges, drums, renders and masters the song
(``score.ts``, run with Song Deck's tsx); BackTrack Surveil transcribes the drum stem when
``build/backtrack-surveil`` exists (otherwise the committed ``promo/surveil/drums.json`` is used); Vixl
builds the scenes and assembles the film with its ``film-export`` workflow.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit import FPS, H, PROMO, ROOT, W  # noqa: E402

OUTPUT = "backtrack-take-it-apart.mp4"
SR = 48000
SONG_DECK = Path(os.environ.get("SONG_DECK", ROOT.parent / "Song-Deck")).resolve()
LUTHIER = Path(os.environ.get("LUTHIER", ROOT.parent / "Luthier")).resolve()


def read_wav(path):
    with wave.open(str(path)) as fh:
        assert fh.getframerate() == SR and fh.getnchannels() == 2
        return np.frombuffer(fh.readframes(fh.getnframes()), "<i2").reshape(-1, 2).astype(float) / 32768


def write_wav(path, x):
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(data.tobytes())


def finish_music(total_s):
    """The mastered song, cut to the film's length; the last chord rings into a short fade."""
    x = read_wav(PROMO / "audio" / "music.wav")[: int(total_s * SR)]
    if len(x) < total_s * SR:
        x = np.pad(x, ((0, int(total_s * SR) - len(x)), (0, 0)))
    k = int(1.2 * SR)
    x[-k:] *= np.linspace(1, 0, k)[:, None] ** 1.5
    return x


def click():
    t = np.arange(int(0.05 * SR)) / SR
    x = np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.005) * 0.5 + np.sin(2 * np.pi * 700 * t) * np.exp(-t / 0.01) * 0.3
    return np.stack([x, x], axis=1)


def effects(total_s):
    """Button clicks for the presses on screen (DETONATE, the S buttons, TAP THE LINE, EXPORT, RIP IT)."""
    import scenes as S

    fx = np.zeros((int(total_s * SR) + SR, 2))
    presses = {"03-explode": [1350], "04-solo": [0, 3000, 4500], "07-tap-the-line": [0, 4500], "09-hub": [4700]}
    for name, times in presses.items():
        for t in times:
            i = int((S.START[name] + t) / 1000 * SR)
            c = click()
            fx[i: i + len(c)] += c
    return fx[: int(total_s * SR)]


def surveil():
    """Transcribe the soundtrack's drum stem with BackTrack Surveil and its trained drums model."""
    exe = ROOT / "build" / ("backtrack-surveil.exe" if os.name == "nt" else "backtrack-surveil")
    if not exe.exists():
        print("build/backtrack-surveil not found; keeping promo/surveil/drums.json", flush=True)
        return
    out = PROMO / "surveil"
    out.mkdir(exist_ok=True)
    subprocess.run([str(exe), str(PROMO / "audio" / "stems" / "drums.wav"), "--stem", "drums", "--tempo", "160",
                    "--key", "E minor", "--model", str(ROOT / "weights" / "surveil-drums-0.1.0.gguf"),
                    "-o", str(out / "drums.mid"), "--json", str(out / "drums.json")], check=True)


def main():
    if "--scenes" not in sys.argv:
        tsx = SONG_DECK / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
        env = {**os.environ, "SONG_DECK": str(SONG_DECK), "LUTHIER": str(LUTHIER)}
        subprocess.run([str(tsx), "scripts/promo/score.ts"], cwd=ROOT, env=env, check=True)
        surveil()
    import scenes as S

    for make in S.SCENES:
        make().build()
        print("built", make.__name__, flush=True)
    if "--scenes" in sys.argv:
        return
    total = S.TOTAL / 1000
    write_wav(PROMO / "audio" / "soundtrack.wav", finish_music(total))
    write_wav(PROMO / "audio" / "effects.wav", effects(total))
    spec = {
        "version": 1,
        "width": W,
        "height": H,
        "fps": FPS,
        "shots": [{"source": f"scenes/{name}.vixl", "duration": ms} for name, ms in S.CUTS],
        "audio": [
            {"source": "audio/soundtrack.wav", "start": 0, "volume": 1.0},
            {"source": "audio/effects.wav", "start": 0, "volume": 0.5},
        ],
    }
    (PROMO / "film-spec.json").write_text(json.dumps({"output": OUTPUT, "spec": spec}, indent=2) + "\n")
    out = PROMO / OUTPUT
    out.unlink(missing_ok=True)
    subprocess.run(
        ["vixl", "workflow", "film-export", "--request", str(PROMO / "film-spec.json"), "--workspace", str(PROMO)],
        check=True,
    )


if __name__ == "__main__":
    main()
