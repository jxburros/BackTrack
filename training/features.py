"""HCQT features cached on disk, and onset targets. Computing the front end
once per item keeps training epochs to the model's own cost.

The cache holds the extended transform every harmonic channel reads
(frames x 401 bins at the defaults, float16, memory-mapped), not the 8 x 264
stack: the channels are shifted reads of it, so this is exact and 5.3 times
smaller (about 0.25 GB per hour of audio)."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .datasets import Item
from .frontend import FrontEnd, hcqt_for, read_wav, resample, to_mono


def item_mono(item: Item, rate: int) -> np.ndarray:
    """The item's audio as the binary would see one file holding it: the
    files summed at their own rate (sample-aligned stems), mono, resampled."""
    total, src_rate = None, None
    for f in item.audio:
        x, r = read_wav(f)
        m = to_mono(x)
        if total is None:
            total, src_rate = m.astype(np.float32), r
        else:
            if r != src_rate:
                raise ValueError(f"{item.id}: stems at different rates")
            n = max(len(total), len(m))
            total = np.pad(total, (0, n - len(total))) + np.pad(m, (0, n - len(m)))
    return resample(total, src_rate, rate)


def cache_key(item: Item, fe: FrontEnd) -> str:
    h = hashlib.sha1(fe.key().encode())
    for f in item.audio:
        st = os.stat(f)
        h.update(f"{Path(f).resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:20]


def _compute(args):
    item, fe, path = args
    if not path.exists():
        ext = hcqt_for(fe).extended(item_mono(item, fe.sample_rate))
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, ext.astype(np.float16))
        tmp.replace(path)
    return path


def prepare(items: list[Item], fe: FrontEnd, cache_dir, workers: int = 0) -> list[Path]:
    """Feature files for items, computing the missing ones in parallel."""
    cache = Path(cache_dir) / f"hcqt-ext-{fe.key()}"
    cache.mkdir(parents=True, exist_ok=True)
    jobs = [(it, fe, cache / f"{cache_key(it, fe)}.npy") for it in items]
    todo = [j for j in jobs if not j[2].exists()]
    if todo:
        workers = workers or min(len(todo), os.cpu_count() or 1)
        print(f"computing features for {len(todo)} of {len(jobs)} items ({workers} processes)", flush=True)
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                list(ex.map(_compute, todo, chunksize=1))
        else:
            for j in todo:
                _compute(j)
    return [j[2] for j in jobs]


class Features:
    """A cached item: shape (harmonics, frames, bins) like the HCQT, frames
    read on demand from the memory-mapped extended transform."""

    def __init__(self, path, fe: FrontEnd):
        self.ext = np.load(path, mmap_mode="r")
        self.h = hcqt_for(fe)
        self.shape = (len(self.h.offsets), self.ext.shape[0], fe.n_bins)

    def frames(self, a: int, b: int) -> np.ndarray:
        return self.h.expand(np.asarray(self.ext[a:b], np.float32))


def load(path, fe: FrontEnd) -> Features:
    return Features(path, fe)


def onset_targets(hits, n_frames: int, n_classes: int, frame_rate: float, sigma: float = 0.6) -> np.ndarray:
    """(frames, classes) in 0..1: a Gaussian of `sigma` frames around each hit's
    exact (fractional) frame, so the peak lands on the frame nearest the hit."""
    y = np.zeros((n_frames, n_classes), np.float32)
    for t, c in hits:
        f = t * frame_rate
        k0 = int(np.floor(f))
        for k in range(k0 - 2, k0 + 4):
            if 0 <= k < n_frames:
                v = float(np.exp(-0.5 * ((k - f) / sigma) ** 2))
                if v >= 0.05:
                    y[k, c] = max(y[k, c], v)
    return y


def window(feats, start: int, length: int) -> np.ndarray:
    """Frames start..start+length of a Features (or an [H, T, B] array) with
    zero frames outside the item."""
    H, T, B = feats.shape
    out = np.zeros((H, length, B), np.float32)
    a, b = max(0, start), min(T, start + length)
    if b > a:
        out[:, a - start : b - start] = feats.frames(a, b) if isinstance(feats, Features) else feats[:, a:b]
    return out

