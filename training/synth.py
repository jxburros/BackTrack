"""Synthetic drum songs, written as a Song Deck dataset (manifest format
"songdeck-dataset" version 1), so the whole pipeline runs with no downloads
and the reader is tested against the layout Song Deck exports.

    python3 -m training.synth --out DIR [--songs 24] [--seconds 20] [--seed 1] [--rate 44100]

Each song has its own kit (kick, snare, three toms, closed/pedal/open hi-hat,
crash, ride with bell, all synthesised with randomised pitch, decay and
colour), a pattern at a random tempo with fills, crashes and ride sections,
humanised timing and velocity, plus a bass line and a chord pad as the
other stems (so `--audio mix` has bleed to ignore). Toy data: it proves the
pipeline, it is not a substitute for Song Deck's renders.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .frontend import write_wav
from .midi import write_notes


def _band_noise(rng, n, rate, lo, hi, slope=2.0):
    """White noise shaped by a smooth band-pass (FFT domain)."""
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / rate) + 1e-3
    shape = 1 / (1 + (lo / f) ** (2 * slope)) / (1 + (f / hi) ** (2 * slope))
    y = np.fft.irfft(spec * np.sqrt(shape), n)
    return y / (np.abs(y).max() + 1e-9)


def _metal(rng, n, rate, base, count=6):
    """Inharmonic square-ish partials, the 808 cymbal trick."""
    t = np.arange(n) / rate
    ratios = np.array([1.0, 1.48, 1.62, 1.85, 2.13, 2.66])[:count] * rng.uniform(0.95, 1.05, count)
    y = sum(np.sign(np.sin(2 * math.pi * base * r * t + rng.uniform(0, 6.28))) for r in ratios)
    return y / count


class Kit:
    def __init__(self, rng: np.random.Generator, rate: int):
        self.rate, self.rng = rate, rng
        u = rng.uniform
        self.p = dict(
            kick_f0=u(90, 180), kick_f1=u(38, 60), kick_sweep=u(0.02, 0.06), kick_decay=u(0.15, 0.45), kick_click=u(0.1, 0.6),
            snare_f=u(150, 260), snare_tone=u(0.2, 0.7), snare_decay=u(0.08, 0.25), snare_lo=u(800, 2500), snare_hi=u(6000, 11000),
            tom_f=sorted(u(70, 260, 3)), tom_decay=u(0.25, 0.6),
            hat_lo=u(5000, 8500), hat_metal=u(250, 450), hat_mix=u(0.2, 0.7), hat_closed=u(0.025, 0.07), hat_open=u(0.25, 0.6),
            crash_decay=u(1.0, 2.2), crash_lo=u(2500, 5000), crash_metal=u(300, 500),
            ride_decay=u(0.8, 1.6), ride_f=u(380, 650), ride_noise=u(0.1, 0.4),
        )
        self.cache = {}

    def _env(self, n, decay, attack=0.001):
        t = np.arange(n) / self.rate
        return np.minimum(1, t / attack) * np.exp(-t / decay)

    def hit(self, note: int) -> np.ndarray:
        """One hit (unit level) for a GM drum note; a few variants each, picked at random."""
        variants = self.cache.setdefault(note, [])
        if len(variants) < 3:
            variants.append(self._make(note))
        return variants[self.rng.integers(len(variants))]

    def _make(self, note):
        p, r, rng = self.p, self.rate, self.rng
        n_of = lambda s: int(s * r)
        if note in (35, 36):
            n = n_of(p["kick_decay"] * 5)
            t = np.arange(n) / r
            f = p["kick_f1"] + (p["kick_f0"] - p["kick_f1"]) * np.exp(-t / p["kick_sweep"])
            y = np.sin(2 * math.pi * np.cumsum(f) / r) * self._env(n, p["kick_decay"])
            click = _band_noise(rng, n, r, 1500, 8000) * self._env(n, 0.004)
            return y + p["kick_click"] * click
        if note in (37, 38, 39, 40):
            n = n_of(p["snare_decay"] * 5)
            t = np.arange(n) / r
            tone = np.sin(2 * math.pi * p["snare_f"] * t) * self._env(n, 0.06)
            noise = _band_noise(rng, n, r, p["snare_lo"], p["snare_hi"]) * self._env(n, p["snare_decay"])
            y = p["snare_tone"] * tone + noise
            return y * (0.5 if note == 37 else 1.0)
        if note in (41, 43, 45, 47, 48, 50):
            f0 = p["tom_f"][0 if note in (41, 43) else 1 if note in (45, 47) else 2]
            n = n_of(p["tom_decay"] * 5)
            t = np.arange(n) / r
            f = f0 * (1 + 0.3 * np.exp(-t / 0.05))
            y = np.sin(2 * math.pi * np.cumsum(f) / r) * self._env(n, p["tom_decay"])
            return y + 0.2 * _band_noise(rng, n, r, 500, 5000) * self._env(n, 0.02)
        if note in (42, 44, 46):
            decay = p["hat_open"] if note == 46 else p["hat_closed"] * (0.7 if note == 44 else 1.0)
            n = n_of(decay * 5 + 0.01)
            y = p["hat_mix"] * _metal(rng, n, r, p["hat_metal"]) + _band_noise(rng, n, r, p["hat_lo"], 16000)
            y = _band_noise_filter(y, r, p["hat_lo"] * 0.8)
            return y * self._env(n, decay) * (0.6 if note == 44 else 1.0)
        if note in (49, 52, 55, 57):
            n = n_of(p["crash_decay"] * 4)
            y = 0.4 * _metal(rng, n, r, p["crash_metal"] * (1.1 if note == 57 else 1.0)) + _band_noise(rng, n, r, p["crash_lo"], 15000)
            y = _band_noise_filter(y, r, p["crash_lo"] * 0.7)
            return y * self._env(n, p["crash_decay"], 0.003)
        if note in (51, 53, 59):
            n = n_of(p["ride_decay"] * 4)
            t = np.arange(n) / r
            bell = sum(np.sin(2 * math.pi * p["ride_f"] * k * t) for k in (1.0, 2.76, 5.4, 8.9)) / 4
            y = (1.5 if note == 53 else 0.6) * bell + p["ride_noise"] * _band_noise(rng, n, r, 3000, 14000)
            return y * self._env(n, p["ride_decay"])
        raise ValueError(f"no synth for note {note}")


def _band_noise_filter(y, rate, lo):
    spec = np.fft.rfft(y)
    f = np.fft.rfftfreq(len(y), 1 / rate) + 1e-3
    return np.fft.irfft(spec / np.sqrt(1 + (lo / f) ** 4), len(y))


def pattern(rng: np.random.Generator, bpm: float, seconds: float) -> list[dict]:
    """Drum notes (GM numbers) for one song: bars of 16ths with variations."""
    sixteenth = 60 / bpm / 4
    bars = int(seconds / (16 * sixteenth)) + 1
    pats = [[0, 8], [0, 6, 8], [0, 8, 10], [0, 3, 8, 11], [0, 4, 8, 12], [0, 7, 10]]
    kick_pat = pats[rng.integers(len(pats))]
    hat_step = int(rng.choice([1, 2, 2, 4]))
    notes = []
    top = 42
    for bar in range(bars):
        t0 = bar * 16 * sixteenth
        if bar % 8 == 0 or (bar % 2 == 0 and rng.random() < 0.4):
            notes.append((t0, rng.choice([49, 57]), rng.uniform(90, 127)))
        if bar % 4 == 0:
            top = int(rng.choice([42, 42, 51, 46]))
        fill = bar % 4 == 3 and rng.random() < 0.6
        for s in range(16):
            t = t0 + s * sixteenth
            if fill and s >= 8:
                if rng.random() < 0.7:
                    notes.append((t, rng.choice([38, 50, 48, 47, 45, 43, 41]), rng.uniform(70, 120)))
                continue
            if s in kick_pat or (rng.random() < 0.05):
                notes.append((t, 36, rng.uniform(80, 127)))
            if s in (4, 12):
                notes.append((t, 38, rng.uniform(90, 127)))
            elif rng.random() < 0.06:
                notes.append((t, 38, rng.uniform(25, 55)))  # ghost note
            if s % hat_step == 0:
                if top == 51:
                    notes.append((t, 53 if s == 0 and rng.random() < 0.3 else 51, rng.uniform(60, 110)))
                else:
                    note = 46 if (top == 46 or rng.random() < 0.15) and s % 4 == 2 else 42
                    notes.append((t, note, rng.uniform(50, 110) * (1.0 if s % 4 == 0 else 0.8)))
            if top != 51 and s % 4 == 2 and rng.random() < 0.1:
                notes.append((t, 44, rng.uniform(40, 80)))
    out = []
    for t, note, vel in notes:
        t = t + 0.2 + rng.normal(0, 0.006)  # a short lead-in, then humanised timing
        if 0.05 <= t < seconds - 0.3:
            out.append({"pitch": int(note), "start": round(float(t), 5), "end": round(float(t) + 0.1, 5), "velocity": int(min(127, vel))})
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def render(notes, kit: Kit, n: int) -> np.ndarray:
    y = np.zeros(n)
    for nt in notes:
        h = kit.hit(nt["pitch"]) * (nt["velocity"] / 127) ** 1.6
        i = int(round(nt["start"] * kit.rate))
        m = min(len(h), n - i)
        if m > 0:
            y[i : i + m] += h[:m]
    return y


def bass_and_pad(rng, bpm, seconds, rate):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    beat = 60 / bpm
    root = rng.integers(28, 40)
    bass = np.zeros(n)
    pad = np.zeros(n)
    bass_notes, pad_notes = [], []
    for k in range(int(seconds / (2 * beat)) + 1):
        s, e = k * 2 * beat, min(seconds, (k + 1) * 2 * beat)
        p = int(root + rng.choice([0, 0, 5, 7, 3, 10]))
        i0, i1 = int(s * rate), int(e * rate)
        if i1 <= i0:
            continue
        f = 440 * 2 ** ((p - 69) / 12)
        tt = t[i0:i1] - s
        bass[i0:i1] += (np.sin(2 * math.pi * f * tt) + 0.3 * np.sin(4 * math.pi * f * tt)) * np.exp(-tt / 0.6)
        bass_notes.append({"pitch": p, "start": round(s, 5), "end": round(e, 5), "velocity": 90})
        for iv in (12, 16 if rng.random() < 0.5 else 15, 19):
            fp = 440 * 2 ** ((p + iv - 69) / 12)
            pad[i0:i1] += 0.3 * np.sin(2 * math.pi * fp * tt) * np.minimum(1, tt / 0.1)
            pad_notes.append({"pitch": p + iv, "start": round(s, 5), "end": round(e, 5), "velocity": 60})
    return bass, pad, bass_notes, pad_notes


def split_of(index: int) -> str:
    """Deterministic: songs 0, 10, 20... test, 1, 11, 21... valid, the rest train."""
    return "test" if index % 10 == 0 else "valid" if index % 10 == 1 else "train"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--songs", type=int, default=24)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rate", type=int, default=44100)
    a = ap.parse_args(argv)
    out = Path(a.out)
    songs = []
    for k in range(a.songs):
        seed = a.seed * 1000 + k
        rng = np.random.default_rng(seed)
        bpm = float(round(rng.uniform(70, 180)))
        sid = f"{k + 1:04d}-synth-s{seed}"
        d = out / "songs" / sid
        (d / "stems").mkdir(parents=True, exist_ok=True)
        kit = Kit(rng, a.rate)
        notes = pattern(rng, bpm, a.seconds)
        n = int(a.seconds * a.rate)
        gain = 10 ** (rng.uniform(-14, -3) / 20)
        drums = render(notes, kit, n)
        drums = drums / (np.abs(drums).max() + 1e-9) * gain
        bass, pad, bass_notes, pad_notes = bass_and_pad(rng, bpm, a.seconds, a.rate)
        bass *= gain * rng.uniform(0.2, 0.8) / (np.abs(bass).max() + 1e-9)
        pad *= gain * rng.uniform(0.1, 0.5) / (np.abs(pad).max() + 1e-9)
        noise = rng.standard_normal(n) * 10 ** (rng.uniform(-80, -55) / 20)
        drums_out = drums + noise
        mix = drums_out + bass + pad
        stems = []
        for track, name, stem, audio, tnotes, drum in (("drums-1", "Drums", "drums", drums_out, notes, True),
                                                       ("bass-1", "Bass", "bass", bass, bass_notes, False),
                                                       ("pad-1", "Pad", "other", pad, pad_notes, False)):
            write_wav(d / "stems" / f"{track}.wav", audio, a.rate)
            write_notes(d / "stems" / f"{track}.mid", tnotes, bpm, channel=9 if drum else 0)
            stems.append({"track": track, "name": name, "stem": stem, "drums": drum, "program": 0 if drum else 33 if stem == "bass" else 89,
                          "instrument": "BackTrack training.synth", "instrument_source": "synth",
                          "audio": f"songs/{sid}/stems/{track}.wav", "midi": f"songs/{sid}/stems/{track}.mid", "notes": tnotes})
        write_wav(d / "mix.wav", mix, a.rate)
        songs.append({"id": sid, "seed": seed, "genre": "synth", "split": split_of(k), "sample_rate": a.rate,
                      "duration": a.seconds, "tempo": bpm, "tempo_map": [{"time": 0, "bpm": bpm}], "time_signature": [4, 4],
                      "key": "", "chords": [], "mix": f"songs/{sid}/mix.wav", "stems": stems,
                      "variation": {"gain_db": round(20 * math.log10(gain), 2), "kit": {k2: (v if not isinstance(v, list) else list(v)) for k2, v in kit.p.items()}}})
    manifest = {"format": "songdeck-dataset", "version": 1, "songdeck_version": "synthetic (BackTrack training.synth)",
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "options": {"songs": a.songs, "seconds": a.seconds, "seed": a.seed, "rate": a.rate},
                "licence": "Synthesised by BackTrack's training.synth (no samples, no third-party data)", "songs": songs}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=float))
    print(f"wrote {a.songs} songs ({a.songs * a.seconds / 60:.1f} min) to {out}")


if __name__ == "__main__":
    main()
