#!/usr/bin/env python3
"""Write small synthetic test clips with known notes (standard library only).

    scripts/make-test-audio.py OUT_DIR

melody.wav  C4 E4 G4 C5 A3, 0.5 s each with 0.1 s gaps, harmonic tones (44.1 kHz stereo)
bass.wav    E2 A2 B2 E2, 0.6 s each (22.05 kHz mono)
drums.wav   kick on the beat, hi-hat on the off-beat, 120 BPM, 2 bars (44.1 kHz mono)
truth.json  the notes in each clip
"""

import json
import math
import random
import struct
import sys
import wave
from pathlib import Path


def tone(pitch, seconds, rate, harmonics=(1.0, 0.5, 0.3, 0.2), amp=0.25):
    f0 = 440.0 * 2 ** ((pitch - 69) / 12)
    n = int(seconds * rate)
    out = []
    for i in range(n):
        t = i / rate
        env = min(1.0, i / (0.01 * rate)) * min(1.0, (n - i) / (0.03 * rate))
        s = sum(a * math.sin(2 * math.pi * f0 * (k + 1) * t) for k, a in enumerate(harmonics) if f0 * (k + 1) < rate / 2)
        out.append(amp * env * s)
    return out


def write(path, samples, rate, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * 32767)
            frames += struct.pack("<h", v) * channels
        w.writeframes(bytes(frames))


def sequence(pitches, dur, gap, rate):
    samples, truth, t = [], [], 0.0
    for p in pitches:
        samples += tone(p, dur, rate)
        truth.append({"pitch": p, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
        samples += [0.0] * int(gap * rate)
        t += gap
    samples += [0.0] * int(0.3 * rate)
    return samples, truth


def drums(rate):
    rng = random.Random(1)
    beat = 0.5
    n = int(8 * beat * rate + 0.5 * rate)
    out = [0.0] * n
    truth = []
    for b in range(8):
        t = b * beat
        i0 = int(t * rate)
        for i in range(int(0.25 * rate)):  # kick: 55 Hz falling sine
            tt = i / rate
            f = 55 + 60 * math.exp(-tt * 30)
            out[i0 + i] += 0.8 * math.exp(-tt * 12) * math.sin(2 * math.pi * f * tt)
        truth.append({"pitch": 36, "start": t})
        th = t + beat / 2
        i0 = int(th * rate)
        for i in range(int(0.06 * rate)):  # closed hi-hat: high-passed noise burst
            tt = i / rate
            out[i0 + i] += 0.3 * math.exp(-tt * 60) * (rng.random() * 2 - 1) * (1 if i % 2 else -1)
        truth.append({"pitch": 42, "start": th})
    return out, truth


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    truth = {}
    mel, truth["melody"] = sequence([60, 64, 67, 72, 57], 0.5, 0.1, 44100)
    write(out / "melody.wav", mel, 44100, channels=2)
    bass, truth["bass"] = sequence([40, 45, 47, 40], 0.6, 0.1, 22050)
    write(out / "bass.wav", bass, 22050)
    dr, truth["drums"] = drums(44100)
    write(out / "drums.wav", dr, 44100)
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")


if __name__ == "__main__":
    main()
