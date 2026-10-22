"""HCQT features cached on disk, and onset targets. Computing the front end
once per item keeps training epochs to the model's own cost.

The cache holds the extended transform every harmonic channel reads
(frames x 401 bins at the defaults, float16, memory-mapped), not the 8 x 264
stack: the channels are shifted reads of it, so this is exact and 5.3 times
smaller (about 0.25 GB per hour of audio).

Degraded copies (training.augment, `--augment`) are made on the waveform here, before the
HCQT, and cached beside the clean features under keys of their own (prepare_copies)."""

from __future__ import annotations

import dataclasses
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


def cache_key(item: Item, fe: FrontEnd, aug=None, copy: int = 0) -> str:
    """The clean features' key (the front end and the files); with aug (a training.augment.Augment),
    the key of degraded copy number `copy`: the same plus the item, the spec, its seed and the copy,
    prefixed "aug-" so it never names a clean entry."""
    h = hashlib.sha1(fe.key().encode())
    for f in item.audio:
        st = os.stat(f)
        h.update(f"{Path(f).resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    if aug is None:
        return h.hexdigest()[:20]
    h.update(f"|augment|{item.id}|{aug.key(copy)}".encode())
    return "aug-" + h.hexdigest()[:20]


def _compute(args):
    item, fe, path, *copy = args  # copy: (augment spec, copy number) for a degraded copy
    if not path.exists():
        mono = item_mono(item, fe.sample_rate)
        if copy:
            mono = copy[0].degrade(mono, fe.sample_rate, item.id, copy[1])
        ext = hcqt_for(fe).extended(mono)
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, ext.astype(np.float16))
        tmp.replace(path)
    return path


def _run(jobs, workers: int, what: str) -> None:
    todo = [j for j in jobs if not j[2].exists()]
    if todo:
        workers = workers or min(len(todo), os.cpu_count() or 1)
        print(f"computing {what} for {len(todo)} of {len(jobs)} items ({workers} processes)", flush=True)
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                list(ex.map(_compute, todo, chunksize=1))
        else:
            for j in todo:
                _compute(j)


def prepare(items: list[Item], fe: FrontEnd, cache_dir, workers: int = 0) -> list[Path]:
    """Feature files for items, computing the missing ones in parallel."""
    cache = Path(cache_dir) / f"hcqt-ext-{fe.key()}"
    cache.mkdir(parents=True, exist_ok=True)
    jobs = [(it, fe, cache / f"{cache_key(it, fe)}.npy") for it in items]
    _run(jobs, workers, "features")
    return [j[2] for j in jobs]


def prepare_copies(items: list[Item], fe: FrontEnd, cache_dir, aug, workers: int = 0) -> tuple[list[Item], list[Path]]:
    """aug.copies degraded copies of each item (training.augment) and their feature files, computed
    once like the clean ones and kept beside them (aug-*.npy). A copy is the item with the same
    labels, accepted regions and split: no degradation moves the audio in time."""
    cache = Path(cache_dir) / f"hcqt-ext-{fe.key()}"
    cache.mkdir(parents=True, exist_ok=True)
    jobs, copies = [], []
    for it in items:
        for k in range(aug.copies):
            jobs.append((it, fe, cache / f"{cache_key(it, fe, aug, k)}.npy", aug, k))
            copies.append(dataclasses.replace(it, id=f"{it.id}:aug{k + 1}"))
    _run(jobs, workers, f"degraded copies ({aug.describe()})")
    return copies, [j[2] for j in jobs]


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



def ext_window(feats: Features, start: int, length: int) -> np.ndarray:
    """Frames start..start+length of the cached extended transform itself
    (length, n_ext) float16, zeros outside the item: a training crop before
    the harmonic expansion, which `Expander` does on the training device."""
    T, E = feats.ext.shape
    out = np.zeros((length, E), np.float16)
    a, b = max(0, start), min(T, start + length)
    if b > a:
        out[a - start : b - start] = feats.ext[a:b]
    return out


class Expander:
    """The harmonic expansion (Hcqt.expand) as a gather in PyTorch, so crops
    travel to the device as the 401-bin float16 transform (5.3 times less
    than the 8 x 264 float32 stack) and expand there. Same values exactly."""

    def __init__(self, fe: FrontEnd, device="cpu"):
        import torch

        h = hcqt_for(fe)
        idx = np.stack([np.arange(fe.n_bins) + off - h.lo for off in h.offsets])  # (H, B)
        self.idx = torch.from_numpy(idx.astype(np.int64)).to(device)

    def __call__(self, ext):
        """ext (N, T, n_ext) -> (N, H, T, B) float32."""
        x = ext.float()[:, :, self.idx]  # (N, T, H, B)
        return x.permute(0, 2, 1, 3)


def region_mask(regions, n_frames: int, frame_rate: float) -> np.ndarray | None:
    """(frames,) float32, 1 on frames whose time k / frame_rate lies in a
    region; None when regions is None (every frame counts)."""
    if regions is None:
        return None
    m = np.zeros(n_frames, np.float32)
    for a, b in regions:
        k0 = max(0, int(np.ceil(a * frame_rate - 1e-9)))
        k1 = min(n_frames, int(np.ceil(b * frame_rate - 1e-9)))
        if k1 > k0:
            m[k0:k1] = 1
    return m
