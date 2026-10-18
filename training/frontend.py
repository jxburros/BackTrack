"""The transcriber's front end in NumPy: WAV decoding, the resampler and the
harmonic CQT, step for step as core/src/audio.cpp and core/src/hcqt.cpp
compute them (docs/front-end.md). parity.py checks the two agree.

Settings come from a model's GGUF metadata (FrontEnd.from_meta). Float keys
are stored as float32 there, and the C++ reads them back as the float32
value (0.9 is 0.899999976...), so the defaults here are rounded the same way.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np


def f32(x: float) -> float:
    """x as the C++ sees it after a round trip through a float32 GGUF value."""
    return float(np.float32(x))


@dataclass
class FrontEnd:
    sample_rate: int = 22050
    hop: int = 256
    fmin: float = f32(27.5)
    bins_per_semitone: int = 3
    n_bins: int = 264
    harmonics: list = field(default_factory=lambda: [f32(h) for h in (0.5, 1, 2, 3, 4, 5, 6, 7)])
    top_fraction: float = f32(0.9)
    filter_scale: float = f32(1.0)

    @staticmethod
    def drums() -> "FrontEnd":
        # Drums need time resolution more than pitch resolution (model.cpp, make_placeholder).
        return FrontEnd(filter_scale=f32(0.25))

    @staticmethod
    def from_meta(meta: dict) -> "FrontEnd":
        fe = FrontEnd()
        fe.sample_rate = int(meta.get("audio.sample_rate", fe.sample_rate))
        fe.hop = int(meta.get("audio.hop", fe.hop))
        fe.fmin = f32(meta.get("audio.fmin", fe.fmin))
        fe.bins_per_semitone = int(meta.get("audio.bins_per_semitone", fe.bins_per_semitone))
        fe.n_bins = int(meta.get("audio.n_bins", fe.n_bins))
        fe.top_fraction = f32(meta.get("audio.top_fraction", fe.top_fraction))
        fe.filter_scale = f32(meta.get("audio.filter_scale", fe.filter_scale))
        if "audio.harmonics" in meta:
            fe.harmonics = [f32(h) for h in meta["audio.harmonics"]]
        return fe

    def meta(self) -> dict:
        """The GGUF keys for these settings (with their GGUF types, see gguf.py)."""
        from .gguf import F32, U32

        return {
            "audio.sample_rate": U32(self.sample_rate),
            "audio.hop": U32(self.hop),
            "audio.fmin": F32(self.fmin),
            "audio.bins_per_semitone": U32(self.bins_per_semitone),
            "audio.n_bins": U32(self.n_bins),
            "audio.top_fraction": F32(self.top_fraction),
            "audio.filter_scale": F32(self.filter_scale),
            "audio.harmonics": [F32(h) for h in self.harmonics],
        }

    def key(self) -> str:
        """A short hash of the settings, for feature caches."""
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]

    @property
    def bins_per_octave(self) -> int:
        return 12 * self.bins_per_semitone

    @property
    def frame_rate(self) -> float:
        return self.sample_rate / self.hop

    def bin_frequency(self, k):
        centre = self.bins_per_semitone // 2  # integer division, as in hcqt.cpp
        return self.fmin * np.power(2.0, (np.asarray(k, dtype=np.float64) - centre) / self.bins_per_octave)


# ---------------------------------------------------------------------------
# WAV (as dr_wav decodes it to float)
# ---------------------------------------------------------------------------


def read_wav(path) -> tuple[np.ndarray, int]:
    """Returns (samples float32 [frames, channels], sample_rate). PCM 8/16/24/32-bit
    and float 32/64, plain or WAVE_FORMAT_EXTENSIBLE."""
    data = Path(path).read_bytes()
    if data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a WAV file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise ValueError(f"{path}: no fmt or data chunk")
    tag, ch, rate, bits = fmt
    width = bits // 8
    n = len(pcm) // (width * ch) * ch
    if tag == 3 and bits == 32:
        x = np.frombuffer(pcm, "<f4", n).astype(np.float32)
    elif tag == 3 and bits == 64:
        x = np.frombuffer(pcm, "<f8", n).astype(np.float32)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(pcm, "<i2", n).astype(np.float32) * np.float32(0.000030517578125)
    elif tag == 1 and bits == 24:
        b = np.frombuffer(pcm, np.uint8, n * 3).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] << 8 | b[:, 1] << 16 | b[:, 2] << 24) >> 8
        x = v.astype(np.float32) * np.float32(0.00000011920928955078125)
    elif tag == 1 and bits == 32:
        x = (np.frombuffer(pcm, "<i4", n).astype(np.float64) / 2147483648.0).astype(np.float32)
    elif tag == 1 and bits == 8:
        x = np.frombuffer(pcm, np.uint8, n).astype(np.float32) * np.float32(0.00784313725490196078) - np.float32(1)
    else:
        raise ValueError(f"{path}: unsupported WAV format {tag}/{bits}-bit")
    return x.reshape(-1, ch), rate


def write_wav(path, x: np.ndarray, rate: int):
    """16-bit PCM, x float [frames] or [frames, channels]."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    v = np.rint(np.clip(x, -1, 1) * 32767).astype("<i2")
    body = v.tobytes()
    ch = x.shape[1]
    hdr = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * 2, ch * 2, 16)
    Path(path).write_bytes(hdr + b"data" + struct.pack("<I", len(body)) + body)


def to_mono(x: np.ndarray) -> np.ndarray:
    if x.ndim == 1 or x.shape[1] == 1:
        return np.ascontiguousarray(x.reshape(-1), dtype=np.float32)
    acc = np.zeros(x.shape[0], np.float32)
    for c in range(x.shape[1]):
        acc += x[:, c]
    return acc * np.float32(1.0 / x.shape[1])


# ---------------------------------------------------------------------------
# Resampling (audio.cpp): a Kaiser-windowed sinc (beta 9, 32 zero crossings at
# the lower rate), tabulated at 512 points per zero crossing (float32) and
# linearly interpolated; accumulation in double.
# ---------------------------------------------------------------------------

_ZC, _RES, _BETA = 32, 512, 9.0


def _bessel_i0(x: float, iters: int, tol: float) -> float:
    s, term, q = 1.0, 1.0, x * x / 4
    for k in range(1, iters):
        term *= q / (float(k) * k)
        s += term
        if term < tol * s:
            break
    return s


_sinc_table_cache = None


def _sinc_table() -> np.ndarray:
    global _sinc_table_cache
    if _sinc_table_cache is None:
        i0b = _bessel_i0(_BETA, 50, 1e-12)
        t = np.zeros(_ZC * _RES + 2, np.float32)
        for i in range(len(t)):
            x = i / _RES
            if x >= _ZC:
                continue
            sinc = 1.0 if x == 0 else math.sin(math.pi * x) / (math.pi * x)
            r = x / _ZC
            t[i] = sinc * _bessel_i0(_BETA * math.sqrt(max(0.0, 1 - r * r)), 50, 1e-12) / i0b
        _sinc_table_cache = t
    return _sinc_table_cache


def _weights(dist: np.ndarray, cutoff: float) -> np.ndarray:
    table = _sinc_table()
    pos = dist * cutoff * _RES
    k = pos.astype(np.int64)
    frac = pos - k
    d = (table[k + 1] - table[k]).astype(np.float64)  # a float subtraction in C++
    return table[k].astype(np.float64) + d * frac


def resample(x: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """Mono float32 from rate_in to rate_out, as backtrack::resample."""
    x = np.asarray(x, dtype=np.float32)
    if rate_in == rate_out or len(x) == 0:
        return x.copy()
    ratio = rate_out / rate_in
    cutoff = min(1.0, ratio)
    span = _ZC / cutoff
    n_out = int(math.floor(len(x) * ratio))
    n_in = len(x)
    xd = x.astype(np.float64)
    y = np.zeros(n_out, np.float64)
    step = Fraction(rate_in, rate_out)
    if step.denominator <= 2000:
        # Polyphase: output j = P m + r sits at input Q m + r Q / P, so each
        # phase r has fixed taps (the same weights the C++ computes per sample).
        P, Q = step.denominator, step.numerator
        pad = int(span) + 2
        xp = np.concatenate([np.zeros(pad), xd, np.zeros(pad + Q + 2)])
        for r in range(min(P, n_out)):
            js = np.arange(r, n_out, P)
            m = (js - r) // P
            c0 = r / ratio  # the fractional centre for this phase (j / ratio in C++)
            lo = math.ceil(c0 - span)
            hi = math.floor(c0 + span)
            taps = np.arange(lo, hi + 1)
            w = _weights(np.abs(taps - c0), cutoff)
            acc = np.zeros(len(js))
            base = Q * m + pad
            for t, wt in zip(taps, w):
                acc += wt * xp[base + t]
            y[js] = acc
    else:
        chunk = 8192
        for j0 in range(0, n_out, chunk):
            js = np.arange(j0, min(n_out, j0 + chunk))
            centre = js / ratio
            lo = np.ceil(centre - span).astype(np.int64)
            width = int(2 * span) + 2
            idx = lo[:, None] + np.arange(width)[None, :]
            ok = (idx <= np.floor(centre + span)[:, None]) & (idx >= 0) & (idx < n_in)
            w = _weights(np.minimum(np.abs(idx - centre[:, None]), span), cutoff)
            y[js] = np.sum(np.where(ok, w * xd[np.clip(idx, 0, n_in - 1)], 0.0), axis=1)
    return (y * cutoff).astype(np.float32)


def load_mono(path, rate: int) -> np.ndarray:
    """A WAV file as the binary feeds it to the front end: mono, at `rate`."""
    x, r = read_wav(path)
    return resample(to_mono(x), r, rate)


# ---------------------------------------------------------------------------
# Harmonic CQT (hcqt.cpp)
# ---------------------------------------------------------------------------


_decim_cache = None


def _decimation_filter() -> np.ndarray:
    global _decim_cache
    if _decim_cache is None:
        taps, mid, beta = 101, 50, 8.0
        i0b = _bessel_i0(beta, 60, 1e-14)
        c = np.zeros(taps)
        for i in range(taps):
            m = i - mid
            sinc = 0.5 if m == 0 else math.sin(0.5 * math.pi * m) / (math.pi * m)
            r = m / mid
            c[i] = sinc * _bessel_i0(beta * math.sqrt(max(0.0, 1 - r * r)), 60, 1e-14) / i0b
        _decim_cache = c / c.sum()
    return _decim_cache


def decimate2(x: np.ndarray) -> np.ndarray:
    """y[i] = sum_j h[j] x[2i + 50 - j], zero outside the signal."""
    h = _decimation_filter()
    full = np.convolve(x.astype(np.float64), h)  # full[n] = sum_j h[j] x[n - j]
    n_out = (len(x) + 1) // 2
    return full[50 : 50 + 2 * n_out : 2][:n_out].astype(np.float32)


class Hcqt:
    """Precomputed sparse kernels for one FrontEnd; call it on mono audio."""

    def __init__(self, fe: FrontEnd):
        self.fe = fe
        B = fe.bins_per_octave
        self.offsets = [int(_lround(B * math.log2(h))) for h in fe.harmonics]
        self.lo = min(0, *self.offsets)
        hi = max(fe.n_bins - 1, *[fe.n_bins - 1 + o for o in self.offsets])
        self.n_ext = hi - self.lo + 1
        if not (fe.filter_scale > 0) or fe.filter_scale > 4:
            raise ValueError("invalid front-end filter scale")
        q = fe.filter_scale / (2.0 ** (1.0 / B) - 1.0)
        f_cap = fe.top_fraction * fe.sample_rate / 2.0
        self.L = L = _next_pow2(int(math.ceil(2.0 * q / fe.top_fraction)) + 2)
        groups: dict[int, list] = {}
        for e in range(self.n_ext):
            f = float(fe.bin_frequency(e + self.lo))
            if f > f_cap:
                continue
            o = int(math.floor(math.log2(f_cap / f)))
            rate = fe.sample_rate / 2.0**o
            n = int(math.ceil(q * rate / f)) | 1
            n = min(n, L - 1)
            half = n // 2
            m = np.arange(-half, half + 1)
            w = 0.5 * (1 + np.cos(2 * math.pi * m / (n + 1)))
            g = w / w.sum()
            a = 2 * math.pi * f * m / rate
            buf = np.zeros(L, np.complex128)
            buf[L // 2 + m] = (g * np.cos(a)).astype(np.float32) + 1j * (g * np.sin(a)).astype(np.float32)
            spec = np.fft.fft(buf).astype(np.complex64)
            mag = np.abs(spec)
            keep = mag >= np.float32(1e-3) * mag.max()
            coef = np.where(keep, np.conj(spec) / np.float32(L), 0).astype(np.complex64)
            groups.setdefault(o, []).append((e, coef))
        self.n_groups = max(groups) + 1 if groups else 0
        # Per group: the extended bins and an [L, K] coefficient matrix (zeros where dropped).
        self.groups = {}
        for o, ks in groups.items():
            self.groups[o] = (np.array([e for e, _ in ks]), np.stack([c for _, c in ks], axis=1).astype(np.complex128))

    def n_frames(self, n_samples: int) -> int:
        return n_samples // self.fe.hop + 1

    def __call__(self, mono: np.ndarray) -> np.ndarray:
        """mono float32 at fe.sample_rate -> float32 [harmonics, frames, bins] in 0..1."""
        return self.expand(self.extended(mono))

    def expand(self, ext: np.ndarray) -> np.ndarray:
        """The extended transform (frames, n_ext) to [harmonics, frames, bins]:
        channel h reads it at offset round(B log2 h)."""
        out = np.zeros((len(self.offsets), ext.shape[0], self.fe.n_bins), np.float32)
        for h, off in enumerate(self.offsets):
            out[h] = ext[:, off - self.lo : off - self.lo + self.fe.n_bins]
        return out

    def extended(self, mono: np.ndarray) -> np.ndarray:
        """The one transform every harmonic channel reads: float32 (frames, n_ext)."""
        fe, L = self.fe, self.L
        T = self.n_frames(len(mono))
        ext = np.zeros((T, self.n_ext), np.float32)
        signal = np.asarray(mono, dtype=np.float32)
        t = np.arange(T)
        for o in range(self.n_groups):
            if o > 0:
                signal = decimate2(signal)
            if o not in self.groups:
                continue
            bins, coef = self.groups[o]
            c = _lround_arr(t * fe.hop / 2.0**o)
            pad = L
            sp = np.concatenate([np.zeros(pad, np.float32), signal, np.zeros(pad + L, np.float32)])
            for t0 in range(0, T, 4096):
                tt = slice(t0, min(T, t0 + 4096))
                idx = (c[tt] - L // 2 + pad)[:, None] + np.arange(L)[None, :]
                idx = np.clip(idx, 0, len(sp) - 1)
                frames = sp[idx]
                # Samples outside the signal read as zero (the padding is zero).
                X = np.fft.fft(frames.astype(np.float64), axis=1)
                acc = (X @ coef).astype(np.complex64)
                ext[tt, bins] = normalise(np.abs(acc))
        return ext


def normalise(mag: np.ndarray) -> np.ndarray:
    db = np.float32(20.0) * np.log10(np.maximum(mag.astype(np.float32), np.float32(1e-5)))
    return np.clip(np.float32(1.0) + db / np.float32(80.0), 0, 1).astype(np.float32)


def _lround(x: float) -> int:
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def _lround_arr(x: np.ndarray) -> np.ndarray:
    return (np.sign(x) * np.floor(np.abs(x) + 0.5)).astype(np.int64)


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


_hcqt_cache: dict = {}


def hcqt_for(fe: FrontEnd) -> Hcqt:
    k = fe.key()
    if k not in _hcqt_cache:
        _hcqt_cache[k] = Hcqt(fe)
    return _hcqt_cache[k]


def hcqt(mono: np.ndarray, fe: FrontEnd) -> np.ndarray:
    return hcqt_for(fe)(mono)


def hcqt_of_file(path, fe: FrontEnd) -> np.ndarray:
    """The front end end to end from an audio file, as the binary computes it."""
    return hcqt(load_mono(path, fe.sample_rate), fe)
