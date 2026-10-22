"""Degradation augmentation: copies of the training items that sound like worse recordings (phone,
rehearsal and live recordings, old tapes, low-bitrate MP3s), so the models work on those too
(docs/training-data.md, section 6).

    python3 -m training.train_drums ... --augment room,noise,mic,codec --augment-copies 2

Each training item (stem, separated stem, mix or ACE-Step take) gets N degraded copies
(`--augment-copies`), made once on the waveform at the front end's rate, before the HCQT, and
cached like the clean features (features.prepare_copies): the HCQT is not linear, so noise cannot
be added to cached features. Each copy has its own cache entry, keyed by the augmentation spec, its
seed and the copy's number, so it is reproducible and never collides with the clean entry. A copy
keeps its item's labels, so no degradation may move the audio in time. Kinds, in the order they
are applied:

  room      convolution with a synthetic impulse response: the direct sound at t = 0 (no
            pre-delay), early reflections 2-33 ms later, none half as loud as the direct sound, and
            an exponentially decaying noise tail whose high bands die faster; RT60 0.15-1.2 s,
            direct-to-reverberant ratio -3 to +9 dB
  noise     one or two of white, pink, rumble (room tone), mains hum (50 or 60 Hz and harmonics),
            crowd (synthesised babble) or a recording of your own (`--augment-noise DIR`), 10-30 dB
            below the item's measured level
  mic       a phone or a cheap mic: high-pass at 80-160 Hz, low-pass at 4-8 kHz and an uneven
            response (a presence peak and three bumps of up to +-6 dB), minimum phase (no pre-echo)
  compress  heavy compression: threshold 6-20 dB below the item's level, ratio 4-20, attack 3-20 ms
            (the first milliseconds of a hit get through, as through a recorder's compressor),
            release 50-300 ms
  clip      hard or soft clipping 3-12 dB below the peak
  wow       wow (0.3-2 Hz, 0.05-0.3 % speed) and flutter (6-16 Hz, 0.02-0.1 %); the timing moves
            by at most WOW_MAX_SECONDS (1.5 ms, 0.13 of a frame of 256 samples at 22.05 kHz)
  codec     MP3 (LAME at 44.1 kHz) or Opus (48 kHz) at 32-128 kbit/s through ffmpeg; the encoder and
            decoder delay is measured once per setting and trimmed, and the output has the input's
            length. Skipped, with one warning, when ffmpeg is not on PATH.

A copy applies each kind asked for with probability 1/2 (at least one), with its own random
strengths, and is brought back to the item's level (`--gain-db` still varies the level).
Silence stays silence plus a negligible amount of noise: nothing divides by zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Bump when a degradation changes: cached copies made by the old code are then not reused.
VERSION = 1
ORDER = ("room", "noise", "mic", "compress", "clip", "wow", "codec")
P_APPLY = 0.5
SNR_DB = (10.0, 30.0)  # noise this far below the item's level
LEVEL_FLOOR = 1e-4  # -80 dBFS: quieter items count as silent (noise is set relative to this floor)
NOISE_TYPES = ("white", "pink", "rumble", "hum", "crowd")
WOW_MAX_SECONDS = 0.0015
BITRATES = (32, 48, 64, 96, 128)
# codec -> (ffmpeg encoder, the rate it encodes at, file extension)
CODECS = {"mp3": ("libmp3lame", 44100, "mp3"), "opus": ("libopus", 48000, "ogg")}
LOOP = 1 << 18  # synthesised noise is a seamless loop of at most this many samples (12 s at 22.05 kHz)

_warned: set = set()


def warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        print(f"WARNING: {msg}", flush=True)


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def active_level(x, rate: int) -> float:
    """The level (RMS) where the item plays: the mean power of its 50 ms blocks within 40 dB of
    the loudest one, so the silence between hits does not lower it. 0 for silence."""
    x = np.asarray(x, np.float64)
    b = max(1, int(0.05 * rate))
    n = len(x) // b
    if n == 0:
        return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    p = np.mean(x[: n * b].reshape(n, b) ** 2, axis=1)
    top = float(p.max())
    if top <= 0:
        return 0.0
    return float(np.sqrt(p[p >= top * 1e-4].mean()))


def _unit(y: np.ndarray) -> np.ndarray:
    r = float(np.sqrt(np.mean(y * y))) if len(y) else 0.0
    return y / r if r > 0 else y


def _fftconv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """x convolved with h, its first len(x) samples: h[0] meets x[0], so nothing moves."""
    n = len(x) + len(h) - 1
    m = 1 << max(1, (n - 1).bit_length())
    return np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(h, m), m)[: len(x)]


def _tile(loop: np.ndarray, n: int, rng) -> np.ndarray:
    start = int(rng.integers(len(loop)))
    return loop[(start + np.arange(n)) % len(loop)]


def _shaped(rng, n: int, rate: int, shape) -> np.ndarray:
    """Unit-RMS noise of length n whose amplitude spectrum is shape(f): a loop made in the
    frequency domain (so it repeats seamlessly), tiled from a random point."""
    L = min(n, LOOP)
    f = np.maximum(np.fft.rfftfreq(L, 1 / rate), 1.0)
    spec = (rng.standard_normal(len(f)) + 1j * rng.standard_normal(len(f))) * shape(f)
    spec[0] = 0
    return _tile(_unit(np.fft.irfft(spec, L)), n, rng)


# ---------------------------------------------------------------------------
# The degradations: each takes the waveform (float64), the rate and its parameters (plan())
# ---------------------------------------------------------------------------


BAND_CENTRES = (125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0)
BAND_DECAY = (1.3, 1.15, 1.0, 1.0, 0.85, 0.65, 0.45)  # RT60 per band, relative: highs die faster


def room_ir(rate: int, rt60: float, drr_db: float, rng) -> np.ndarray:
    """A synthetic room impulse response: the direct sound at sample 0 (no pre-delay, so onsets
    stay put), 4-11 early reflections 2-33 ms later, weakening with their extra path as from a
    source 1-5 m away, and a tail of noise in octave bands, each decaying by 60 dB over its own
    RT60, building up from the first reflection. The reverberant part has the direct-to-reverberant
    energy ratio drr_db, at least half of it in the diffuse tail, and no reflection reaches half
    the direct sound (a strong late echo would read as a second onset); unit energy overall."""
    n = max(16, int(min(2.0, 1.2 * rt60) * rate))
    t = np.arange(n) / rate
    first = rng.uniform(0.002, 0.008)
    t0 = rng.uniform(0.003, 0.015)  # the direct sound's travel time: reflections weaken as t0 / (t0 + delay)
    early = np.zeros(n)
    for _ in range(int(rng.integers(4, 12))):
        tau = first + rng.uniform(0, 0.025)
        k = int(tau * rate)
        if k < n:
            early[k] += rng.choice((-1.0, 1.0)) * rng.uniform(0.4, 0.9) * t0 / (t0 + tau) * 10 ** (-3 * tau / rt60)
    f = np.maximum(np.fft.rfftfreq(n, 1 / rate), 1.0)
    spec = np.fft.rfft(rng.standard_normal(n))
    tail = np.zeros(n)
    for b, (c, rel) in enumerate(zip(BAND_CENTRES, BAND_DECAY)):
        pos = np.log2(f / c)
        if b == 0:
            pos = np.maximum(pos, 0)
        if b == len(BAND_CENTRES) - 1:
            pos = np.minimum(pos, 0)
        w = np.where(np.abs(pos) < 1, np.cos(0.5 * math.pi * pos) ** 2, 0.0)  # neighbours sum to 1
        rt = rt60 * rel * rng.uniform(0.85, 1.15)
        tail += np.fft.irfft(spec * w, n) * 10 ** (-3 * t / rt)
    tail *= np.clip((t - first) / 0.01, 0, 1)
    target = 10 ** (-drr_db / 10)  # reverberant energy, the direct sound's being 1
    e_early, e_tail = float(np.sum(early**2)), float(np.sum(tail**2))
    if e_early > 0.5 * target:
        early *= math.sqrt(0.5 * target / e_early)
        e_early = 0.5 * target
    if e_tail > 0:
        tail *= math.sqrt((target - e_early) / e_tail)
    h = early + tail
    peak = float(np.abs(h).max())
    if peak > 0.5:
        h *= 0.5 / peak
    h[0] += 1.0
    return h / math.sqrt(float(np.sum(h**2)))


def room(x, rate, p, aug=None):
    return _fftconv(x, room_ir(rate, p["rt60"], p["drr_db"], np.random.default_rng(p["seed"])))


def _hum(rng, n, rate):
    L = min(n, LOOP)
    f0 = float(rng.choice((50.0, 60.0))) * (1 + rng.uniform(-0.003, 0.003))
    cycles = max(1, round(f0 * L / rate))  # a whole number of cycles in the loop: it repeats seamlessly
    q, odd = rng.uniform(0.5, 1.5), rng.uniform(1, 3)
    ph = 2 * math.pi * cycles * np.arange(L) / L
    y = np.zeros(L)
    for k in range(1, 25):
        if k * cycles * rate / L >= 0.45 * rate:
            break
        y += k**-q * (odd if k % 2 else 1.0) * rng.uniform(0.5, 1) * np.sin(k * ph + rng.uniform(0, 2 * math.pi))
    return _tile(_unit(y), n, rng)


def _circular_envelope(rng, L, rate, syllables_hz):
    """A positive, loop-continuous envelope: smoothed noise at 100 Hz, half-wave rectified."""
    m = max(4, L * 100 // rate)
    c = rng.standard_normal(m)
    w = max(1, min(int(100 / syllables_hz / 2), m // 2))
    c = np.convolve(np.r_[c[-w:], c, c[:w]], np.ones(w) / w, "same")[w : w + m]
    env = np.maximum(c, 0) ** 1.5
    grid = np.arange(m + 1) * L / m
    return np.interp(np.arange(L), grid, np.r_[env, env[0]])


def _crowd(rng, n, rate):
    """Babble: 6-16 talkers, each noise through two formant peaks, switched on and off at a syllable rate."""
    L = min(n, LOOP)
    f = np.maximum(np.fft.rfftfreq(L, 1 / rate), 1.0)
    band = 1 / np.sqrt(1 + (150 / f) ** 4) / np.sqrt(1 + (f / 4000) ** 4)
    out = np.zeros(L)
    for _ in range(int(rng.integers(6, 17))):
        f1, f2 = rng.uniform(300, 900), rng.uniform(900, 2600)
        shape = band * (np.exp(-0.5 * (np.log2(f / f1) / 0.25) ** 2) + 0.7 * np.exp(-0.5 * (np.log2(f / f2) / 0.25) ** 2) + 0.05)
        voice = np.fft.irfft((rng.standard_normal(len(f)) + 1j * rng.standard_normal(len(f))) * shape, L)
        out += _unit(voice) * _circular_envelope(rng, L, rate, rng.uniform(3, 6)) * rng.uniform(0.3, 1)
    return _tile(_unit(out), n, rng)


def _rumble(rng, n, rate):
    """Room tone: brownish noise below a few hundred Hz, slowly swelling."""
    fc = rng.uniform(150, 800)
    y = _shaped(rng, n, rate, lambda f: 1 / f / np.sqrt(1 + (f / fc) ** 4) / np.sqrt(1 + (20 / f) ** 4))
    t = np.arange(n) / rate
    return y * (1 + rng.uniform(0.2, 0.5) * np.sin(2 * math.pi * rng.uniform(0.05, 0.5) * t + rng.uniform(0, 2 * math.pi)))


_file_cache: dict = {}


def _file(rng, n, rate, path):
    key = (str(path), rate)
    if key not in _file_cache:
        from .frontend import read_wav, resample, to_mono

        x, r = read_wav(path)
        _file_cache[key] = _unit(resample(to_mono(x), r, rate).astype(np.float64))
    loop = _file_cache[key]
    return _tile(loop, n, rng) if len(loop) else np.zeros(n)


def noise(x, rate, p, aug=None):
    """x plus noise p["snr_db"] dB below its active level (or below LEVEL_FLOOR when x is silent)."""
    rng = np.random.default_rng(p["seed"])
    n = len(x)
    total = np.zeros(n)
    for kind, gain_db in zip(p["types"], p["gains_db"]):
        if kind == "white":
            y = rng.standard_normal(n)
        elif kind == "pink":
            y = _shaped(rng, n, rate, lambda f: f**-0.5)
        elif kind == "rumble":
            y = _rumble(rng, n, rate)
        elif kind == "hum":
            y = _hum(rng, n, rate)
        elif kind == "crowd":
            y = _crowd(rng, n, rate)
        elif kind == "file":
            files = aug.noise_files if aug else []
            y = _file(rng, n, rate, files[p["file"] % len(files)][0])
        else:
            raise ValueError(f"unknown noise type {kind!r}")
        total += 10 ** (gain_db / 20) * _unit(y)
    level = max(active_level(x, rate), LEVEL_FLOOR)
    return x + _unit(total) * level * 10 ** (-p["snr_db"] / 20)


def _min_phase(mag: np.ndarray, nfft: int) -> np.ndarray:
    """The minimum-phase impulse response with this magnitude (rfft bins of nfft), by the cepstrum."""
    cep = np.fft.irfft(np.log(np.maximum(mag, 1e-5)), nfft)
    fold = np.zeros(nfft)
    fold[0], fold[nfft // 2] = 1, 1
    fold[1 : nfft // 2] = 2
    return np.fft.irfft(np.exp(np.fft.rfft(cep * fold)), nfft)


def mic_response(rate: int, p, nfft: int = 1 << 14) -> tuple[np.ndarray, np.ndarray]:
    """(frequencies, magnitude) of the mic: high-pass, low-pass, presence peak and bumps."""
    f = np.maximum(np.fft.rfftfreq(nfft, 1 / rate), 1e-3)
    mag = 1 / np.sqrt(1 + (p["hp"] / f) ** (2 * p["hp_order"])) / np.sqrt(1 + (f / p["lp"]) ** (2 * p["lp_order"]))
    db = sum(g * np.exp(-0.5 * (np.log2(f / c) / w) ** 2) for c, g, w in p["bumps"])
    return f, mag * 10 ** (db / 20)


def mic(x, rate, p, aug=None):
    nfft = 1 << 14
    _, mag = mic_response(rate, p, nfft)
    h = _min_phase(mag, nfft)[:4096]
    h[-1024:] *= np.cos(0.5 * math.pi * np.arange(1024) / 1024) ** 2
    return _fftconv(x, h)


def compress(x, rate, p, aug=None):
    """A feed-forward compressor on 2 ms blocks: the block level in dB follows rises with the attack
    and falls with the release; above the threshold the gain falls by (1 - 1/ratio) per dB. A
    block's gain takes effect at its end (no look-ahead: the start of a hit gets through)."""
    level = active_level(x, rate)
    if level <= LEVEL_FLOOR:
        return x
    b = max(1, int(0.002 * rate))
    nb = -(-len(x) // b)
    xp = np.pad(x, (0, nb * b - len(x)))
    db = 10 * np.log10(np.maximum(np.mean(xp.reshape(nb, b) ** 2, axis=1), 1e-12))
    att, rel = math.exp(-b / (rate * p["attack"])), math.exp(-b / (rate * p["release"]))
    env = np.empty(nb)
    e = db[0]
    for i, v in enumerate(db.tolist()):
        e = att * e + (1 - att) * v if v > e else rel * e + (1 - rel) * v
        env[i] = e
    thr = 20 * math.log10(level) + p["threshold_db"]
    gain_db = -np.maximum(env - thr, 0) * (1 - 1 / p["ratio"])
    ends = (np.arange(nb) + 1.0) * b
    return x * 10 ** (np.interp(np.arange(len(x)), ends, gain_db) / 20)


def clip(x, rate, p, aug=None):
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak <= 0:
        return x
    c = peak * 10 ** (-p["below_peak_db"] / 20)
    return c * np.tanh(x / c) if p["soft"] else np.clip(x, -c, c)


def wow_displacement(n: int, rate: int, p) -> np.ndarray:
    """How far (seconds) each output sample reads behind its own time: a wow and a flutter sine whose
    speed deviations are p["wow"] and p["flutter"] (fractions); at most WOW_MAX_SECONDS."""
    t = np.arange(n) / rate
    amps = [p["wow"] / (2 * math.pi * p["wow_hz"]), p["flutter"] / (2 * math.pi * p["flutter_hz"])]
    s = min(1.0, WOW_MAX_SECONDS / sum(amps))
    return s * (amps[0] * np.sin(2 * math.pi * p["wow_hz"] * t + p["phases"][0])
                + amps[1] * np.sin(2 * math.pi * p["flutter_hz"] * t + p["phases"][1]))


def _cubic(x: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """x read at fractional positions (Catmull-Rom), zero outside it."""
    n = len(x)
    xp = np.concatenate([np.zeros(2), x, np.zeros(3)])
    i = np.floor(pos)
    u = pos - i
    i = np.clip(i.astype(np.int64), -1, n) + 1
    p0, p1, p2, p3 = xp[i], xp[i + 1], xp[i + 2], xp[i + 3]
    return p1 + 0.5 * u * (p2 - p0 + u * (2 * p0 - 5 * p1 + 4 * p2 - p3 + u * (3 * (p1 - p2) + p3 - p0)))


def wow(x, rate, p, aug=None):
    return _cubic(x, np.arange(len(x)) - wow_displacement(len(x), rate, p) * rate)


# -- codec ------------------------------------------------------------------


def _roundtrip(ffmpeg: str, x: np.ndarray, rate: int, codec: str, kbps: int) -> np.ndarray:
    """x encoded to a file and decoded back to `rate`, as ffmpeg gives it (delay and padding kept).
    A file rather than a pipe, so the MP3 gets its gapless header."""
    lib, enc_rate, ext = CODECS[codec]
    with tempfile.TemporaryDirectory(prefix="backtrack-augment-") as d:
        f = Path(d) / f"a.{ext}"
        enc = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1",
               "-i", "pipe:0", "-c:a", lib, "-b:a", f"{kbps}k", "-ar", str(enc_rate), "-y", str(f)]
        r = subprocess.run(enc, input=np.asarray(x, "<f4").tobytes(), capture_output=True, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg could not encode {codec} at {kbps} kbit/s: {r.stderr.decode(errors='replace')[-300:]}")
        dec = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-i", str(f), "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"]
        r = subprocess.run(dec, capture_output=True, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg could not decode {codec}: {r.stderr.decode(errors='replace')[-300:]}")
        return np.frombuffer(r.stdout, "<f4").astype(np.float64)


def _calibration(rate: int) -> np.ndarray:
    """2 s of decaying noise bursts over a quiet noise floor: broadband, with an unambiguous lag."""
    rng = np.random.default_rng(17)
    x = 0.02 * rng.standard_normal(2 * rate)
    for k in range(10):
        p = int(rate * (0.1 + 0.18 * k))
        x[p : p + 200] += 0.3 * rng.standard_normal(200) * np.exp(-np.arange(200) / 40)
    return x


def lag(y: np.ndarray, x: np.ndarray, max_lag: int) -> tuple[int, float]:
    """(k, r): y[t + k] matches x[t] best at k in -max_lag..max_lag, r the normalised correlation there."""
    m = 1 << (len(x) + len(y) + max_lag).bit_length()
    c = np.fft.irfft(np.fft.rfft(y, m) * np.conj(np.fft.rfft(x, m)), m)
    ks = np.r_[np.arange(0, max_lag + 1), np.arange(-max_lag, 0)]
    vals = np.r_[c[: max_lag + 1], c[m - max_lag :]]
    i = int(np.argmax(vals))
    return int(ks[i]), float(vals[i] / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-30))


_delays: dict = {}


def codec_delay(ffmpeg: str, codec: str, kbps: int, rate: int) -> int:
    """Samples (at rate) the round trip delays the audio, measured once per setting on a test signal.
    LAME adds 576 + 529 samples at its rate (1105); ffmpeg removes them when it reads the MP3's
    gapless header (ffmpeg 6.1 measures 0), and Opus files carry their pre-skip: measuring covers
    every build."""
    key = (ffmpeg, codec, kbps, rate)
    if key not in _delays:
        x = _calibration(rate)
        k, r = lag(_roundtrip(ffmpeg, x, rate, codec, kbps), x, rate // 2)
        if r < 0.5:
            raise RuntimeError(f"could not measure the {codec} {kbps} kbit/s delay (correlation {r:.2f})")
        _delays[key] = k
    return _delays[key]


def codec(x, rate, p, aug=None):
    """x through a lossy codec, with the codec's delay trimmed and the input's length."""
    if aug is None or not aug.codecs:
        return x
    name = p["codec"] if p["codec"] in aug.codecs else aug.codecs[0]
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak <= 0:
        return x
    scale = 0.5 / peak  # a safe level for the encoder; undone below
    y = _roundtrip(aug.ffmpeg, x * scale, rate, name, p["kbps"])
    d = codec_delay(aug.ffmpeg, name, p["kbps"], rate)
    y = y[d:] if d >= 0 else np.concatenate([np.zeros(-d), y])
    n = len(x)
    y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
    return y / scale


APPLY = {"room": room, "noise": noise, "mic": mic, "compress": compress, "clip": clip, "wow": wow, "codec": codec}


# ---------------------------------------------------------------------------
# Plans: what one copy does
# ---------------------------------------------------------------------------


def draw(rng, n_files: int = 0) -> dict:
    """Every kind's parameters, drawn whether the kind is used or not (so one kind's strengths do
    not depend on which others are on)."""
    seed = lambda: int(rng.integers(2**62))
    types = list(NOISE_TYPES) + (["file"] if n_files else [])
    k = 1 + int(rng.random() < 0.4)
    chosen = [types[i] for i in rng.permutation(len(types))[:k]]
    gains = [0.0, float(rng.uniform(-10, 0))][:k]
    log_uniform = lambda lo, hi: float(lo * (hi / lo) ** rng.random())
    return {
        "room": {"rt60": float(rng.uniform(0.15, 1.2)), "drr_db": float(rng.uniform(-3, 9)), "seed": seed()},
        "noise": {"snr_db": float(rng.uniform(*SNR_DB)), "types": chosen, "gains_db": gains,
                  "file": int(rng.integers(max(1, n_files))), "seed": seed()},
        "mic": {"hp": float(rng.uniform(80, 160)), "hp_order": int(rng.integers(2, 5)), "lp": float(rng.uniform(4000, 8000)),
                "lp_order": int(rng.integers(4, 9)),
                "bumps": [(float(rng.uniform(2000, 5000)), float(rng.uniform(2, 8)), float(rng.uniform(0.3, 0.8)))]
                + [(log_uniform(150, 6000), float(rng.uniform(-6, 6)), float(rng.uniform(0.2, 1.0))) for _ in range(3)]},
        "compress": {"threshold_db": float(rng.uniform(-20, -6)), "ratio": float(rng.uniform(4, 20)),
                     "attack": float(rng.uniform(0.003, 0.020)), "release": float(rng.uniform(0.05, 0.3))},
        "clip": {"below_peak_db": float(rng.uniform(3, 12)), "soft": bool(rng.random() < 0.5)},
        "wow": {"wow_hz": float(rng.uniform(0.3, 2.0)), "wow": float(rng.uniform(0.0005, 0.003)),
                "flutter_hz": float(rng.uniform(6, 16)), "flutter": float(rng.uniform(0.0002, 0.001)),
                "phases": [float(rng.uniform(0, 2 * math.pi)), float(rng.uniform(0, 2 * math.pi))]},
        "codec": {"codec": ("mp3", "opus")[int(rng.integers(2))], "kbps": int(rng.choice(BITRATES))},
    }


def plan(kinds, rng, n_files: int = 0) -> list[dict]:
    """The steps of one copy: each kind in `kinds` with probability P_APPLY (at least one), in ORDER."""
    params = draw(rng, n_files)
    use = {k: bool(rng.random() < P_APPLY) for k in ORDER}
    kinds = [k for k in ORDER if k in kinds]
    chosen = [k for k in kinds if use[k]]
    if not chosen and kinds:
        chosen = [kinds[int(rng.integers(len(kinds)))]]
    return [{"kind": k, **params[k]} for k in chosen]


def parse_kinds(text) -> list[str]:
    """'all' or comma-separated kinds -> the kinds in ORDER; an unknown kind is an error."""
    words = [w.strip() for w in (text.split(",") if isinstance(text, str) else text or []) if w.strip()]
    if "all" in words:
        return list(ORDER)
    bad = [w for w in words if w not in ORDER]
    if bad:
        raise SystemExit(f"--augment: unknown kind {', '.join(bad)} (kinds: {', '.join(ORDER)}, or all)")
    return [k for k in ORDER if k in words]


@dataclass
class Augment:
    """The augmentation spec: the kinds asked for, the copies per item and the seed, plus what this
    machine can do (resolve()): ffmpeg and its codecs, and the noise recordings."""

    kinds: list
    copies: int = 1
    seed: int = 0
    noise_dir: str | None = None
    kinds_run: list = field(default_factory=list)  # the kinds that run here (codec dropped without ffmpeg)
    ffmpeg: str | None = None
    ffmpeg_version: str = ""
    codecs: list = field(default_factory=list)
    noise_files: list = field(default_factory=list)  # [(path, size, mtime)]

    def resolve(self, ffmpeg="auto") -> Augment:
        """Finds ffmpeg (ffmpeg="auto": on PATH; None: as if missing) and the noise recordings;
        warns once when the codec kind cannot run."""
        self.kinds = parse_kinds(self.kinds)
        self.kinds_run = list(self.kinds)
        if "codec" in self.kinds:
            self.ffmpeg = shutil.which("ffmpeg") if ffmpeg == "auto" else ffmpeg
            self.codecs = []
            if self.ffmpeg:
                out = subprocess.run([self.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                     check=False).stdout
                self.codecs = [c for c, (lib, _, _) in CODECS.items() if f" {lib} " in out]
                self.ffmpeg_version = (subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, check=False).stdout
                                       .splitlines() or [""])[0].strip()
            if not self.codecs:
                why = ("ffmpeg is not on PATH" if not self.ffmpeg else
                       f"{self.ffmpeg} has neither the libmp3lame nor the libopus encoder")
                warn_once(f"augment: {why}, so the codec degradation (MP3 / Opus) is skipped; the other kinds still "
                          "run. Install ffmpeg (https://ffmpeg.org/download.html) and put it on PATH to use it.")
                self.kinds_run.remove("codec")
                self.ffmpeg = None
        if self.noise_dir:
            d = Path(self.noise_dir).expanduser()
            files = sorted(p for p in d.rglob("*") if p.suffix.lower() == ".wav") if d.is_dir() else []
            if not files:
                raise SystemExit(f"--augment-noise: no .wav files in {d}")
            self.noise_files = [(str(p.resolve()), p.stat().st_size, int(p.stat().st_mtime)) for p in files]
        return self

    def key(self, copy: int) -> str:
        """Everything that decides the audio of copy number `copy` (besides the item)."""
        return json.dumps({"v": VERSION, "kinds": self.kinds_run, "seed": self.seed, "copy": copy,
                           "codec": [self.ffmpeg_version, self.codecs] if "codec" in self.kinds_run else None,
                           "noise": self.noise_files if "noise" in self.kinds_run and self.noise_files else None},
                          sort_keys=True)

    def rng(self, item_id: str, copy: int):
        h = hashlib.sha1(json.dumps([VERSION, self.seed, item_id, copy]).encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], "little"))

    def plan(self, item_id: str, copy: int) -> list[dict]:
        return plan(self.kinds_run, self.rng(item_id, copy), len(self.noise_files))

    def degrade(self, x, rate: int, item_id: str, copy: int) -> np.ndarray:
        """Copy number `copy` of an item's waveform (mono at rate): the same length and timing,
        brought back to the item's level."""
        x = np.asarray(x, np.float64)
        if not len(x):
            return x.astype(np.float32)
        y = x
        for step in self.plan(item_id, copy):
            y = APPLY[step["kind"]](y, rate, step, self)
        a, b = active_level(x, rate), active_level(y, rate)
        if a > LEVEL_FLOOR and b > 0:
            y = y * (a / b)
        if len(y) != len(x) or not np.all(np.isfinite(y)):
            raise RuntimeError(f"augment: copy {copy} of {item_id} came out wrong (length {len(y)} of {len(x)}, or not finite)")
        return y.astype(np.float32)

    def record(self, items: int) -> dict:
        """For audio.training."""
        out = {"kinds": self.kinds_run, "copies": self.copies, "seed": self.seed, "items": items, "version": VERSION}
        if self.kinds_run != self.kinds:
            out["skipped"] = [k for k in self.kinds if k not in self.kinds_run]
        if "codec" in self.kinds_run:
            out["codecs"] = self.codecs
        if self.noise_files:
            out["noise_files"] = len(self.noise_files)
        return out

    def describe(self) -> str:
        return ", ".join(self.kinds_run) + (f"; {len(self.noise_files)} noise recordings" if self.noise_files else "")


def sources(aug: Augment) -> list[str]:
    """audio.sources lines: recordings mixed into the training audio are training data too (the
    synthesised degradations need no line)."""
    if not aug.noise_files or "noise" not in aug.kinds_run:
        return []
    return [(f"Augmentation noise: {len(aug.noise_files)} recordings from {Path(aug.noise_dir).expanduser().name}, "
             "supplied by the person training (their licence is theirs to check)")]


def add_args(ap) -> None:
    ap.add_argument("--augment", default="",
                    help="also train on degraded copies of the training items (worse recordings): "
                         f"{','.join(ORDER)} or all, comma-separated (default: off)")
    ap.add_argument("--augment-copies", type=int, default=1,
                    help="degraded copies of each training item, made once and cached (default 1; 0: off)")
    ap.add_argument("--augment-seed", type=int, default=0, help="seed of the degradations (default 0)")
    ap.add_argument("--augment-noise", default=None,
                    help="a folder of WAV recordings (crowd, room tone, ...) the noise kind may mix in as well")


def from_args(a) -> Augment | None:
    """The spec from --augment and friends, resolved; None when augmentation is off."""
    if not getattr(a, "augment", "") or a.augment_copies <= 0:
        return None
    aug = Augment(parse_kinds(a.augment), a.augment_copies, a.augment_seed, a.augment_noise)
    if a.augment_noise and "noise" not in aug.kinds:
        raise SystemExit("--augment-noise needs the noise kind in --augment")
    aug.resolve()
    if not aug.kinds_run:
        warn_once("augment: nothing left to run without ffmpeg: training without degraded copies")
        return None
    return aug
