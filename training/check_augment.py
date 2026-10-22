"""Checks degradation augmentation (training/augment.py, `--augment`): that every kind keeps the
labels' timing, that copies are deterministic and cached under their own keys, that noise sits
10-30 dB below the item's level, and that with augmentation off nothing changes.

    python3 -m training.check_augment --work DIR

1. Alignment: drum-like hits through each kind at several strengths: every hit's onset frame
   (spectral flux peak of the drum front end) moves by at most one frame and the length is kept;
   through all kinds at once, no shift overall and at least 97% within one frame (the others are
   buried under noise and compression, not moved). The codec's delay is trimmed to the sample
   (also a raw 1105-sample MP3 delay, as from a build without the gapless header), wow and
   flutter move the timing by at most WOW_MAX_SECONDS, and room responses start with the direct
   sound.
2. Determinism: the same item, spec and seed give the same audio and cache key; another seed,
   copy or spec gives another key, never a clean one.
3. Noise level: 10-30 dB below the item's measured level for every noise type, relative to the
   item (not absolute), and silence or a tiny item gives finite audio.
4. Off: the clean cache key is the old one, and train_drums without --augment (or with
   --augment-copies 0) writes the same model byte for byte and no degraded copies.
5. No ffmpeg: the codec kind is skipped with one warning, under a cache key of its own.
6. train_drums and train_pitched with --augment: copies cached once and reused, a removed copy
   is rebuilt identical, and audio.training records the augmentation.

Skips the codec parts (saying so) when ffmpeg is not on PATH. Fails loudly (AssertionError,
non-zero exit). About a minute and a half on one core.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

import numpy as np
import torch

from . import augment as A
from . import features, gguf, synth, train_drums, train_pitched
from .datasets import Item, read_songdeck
from .frontend import FrontEnd, hcqt_for, write_wav


def click_track(rate: int, seconds: float = 8.0, seed: int = 0):
    """Drum-like hits (broadband noise bursts decaying in 3-8 ms) at irregular times over a
    -70 dBFS floor: (audio, the hits' first samples). Much shorter clicks can fall between the
    drum front end's short high-frequency kernels, so their onset frame is not well defined."""
    rng = np.random.default_rng(seed)
    x = 10 ** (-70 / 20) * rng.standard_normal(int(seconds * rate))
    at, t = [], 0.3
    while t < seconds - 0.5:
        k, tau = int(t * rate), rng.uniform(0.003, 0.008) * rate
        n = int(5 * tau)
        x[k : k + n] += rng.uniform(0.3, 0.9) * rng.standard_normal(n) * np.exp(-np.arange(n) / tau)
        at.append(k)
        t += rng.uniform(0.3, 0.6)
    return x, at


def onset_flux(x, fe: FrontEnd) -> np.ndarray:
    """Spectral flux of the front end's transform, per frame."""
    e = hcqt_for(fe).extended(np.asarray(x, np.float32))
    return np.maximum(np.diff(e, axis=0, prepend=e[:1]), 0).sum(1)


def onset_frames(flux: np.ndarray, at, hop: int) -> np.ndarray:
    """Per hit, the frame of the largest flux within -2..+3 frames of it."""
    out = []
    for k in at:
        f = round(k / hop)
        lo = max(0, f - 2)
        out.append(lo + int(np.argmax(flux[lo : f + 4])))
    return np.array(out)


def global_lag(a: np.ndarray, b: np.ndarray, m: int = 6) -> int:
    """The shift (frames) that lines flux b up with flux a best."""
    n = min(len(a), len(b))
    score = {k: float(np.dot(a[m : n - m], b[m + k : n - m + k])) for k in range(-m, m + 1)}
    return max(score, key=score.get)


def check_alignment(fe: FrontEnd, aug: A.Augment) -> None:
    rate = fe.sample_rate
    clean = [(x, at, onset_frames(onset_flux(x, fe), at, fe.hop), onset_flux(x, fe))
             for x, at in (click_track(rate, seed=s) for s in range(2))]
    kinds = [k for k in A.ORDER if k != "codec" or aug.codecs]
    total, exact = 0, 0
    for kind in kinds:
        for s in range(4):
            p = {"kind": kind, **A.draw(np.random.default_rng(1000 + s))[kind]}
            if kind == "room":  # the extremes: short and long, far and near
                p["rt60"], p["drr_db"] = [(0.15, -3), (1.2, -3), (1.2, 9), (0.6, 0)][s]
            if kind == "noise" and s % 2 == 0:
                p["snr_db"] = A.SNR_DB[0]
            for x, at, frames, _ in clean:
                y = A.APPLY[kind](x, rate, p, aug)
                assert len(y) == len(x) and np.all(np.isfinite(y)), (kind, s, len(y))
                d = np.abs(onset_frames(onset_flux(y, fe), at, fe.hop) - frames)
                assert d.max() <= 1, (kind, s, int(d.max()), p)
                total, exact = total + len(d), exact + int((d == 0).sum())
    print(f"alignment: {total} hits through each of {', '.join(kinds)} at several strengths: every onset within "
          f"one frame ({100 * exact / total:.1f}% on the same frame)")
    n, near = 0, 0
    for s in range(6):
        drawn = A.draw(np.random.default_rng(1000 + s))
        for x, at, frames, flux in clean:
            y = x
            for k in kinds:
                y = A.APPLY[k](y, rate, {"kind": k, **drawn[k]}, aug)
            fy = onset_flux(y, fe)
            assert len(y) == len(x) and global_lag(flux, fy) == 0, s
            d = np.abs(onset_frames(fy, at, fe.hop) - frames)
            n, near = n + len(d), near + int((d <= 1).sum())
    assert near >= 0.97 * n, (near, n)
    print(f"all of them at once: no shift overall (lag 0 frames); {near} of {n} onsets within one frame "
          "(the others buried under noise and compression)")

    for _ in range(200):
        rng = np.random.default_rng(_)
        h = A.room_ir(rate, rng.uniform(0.15, 1.2), rng.uniform(-3, 9), rng)
        assert np.argmax(np.abs(h)) == 0 and not np.any(h[1 : int(0.002 * rate)]), "direct sound first, no pre-delay"
        d = A.wow_displacement(rate * 4, rate, A.draw(rng)["wow"])
        assert np.abs(d).max() <= A.WOW_MAX_SECONDS + 1e-12
    print(f"room responses start with the direct sound; wow and flutter move the timing by at most "
          f"{1e3 * A.WOW_MAX_SECONDS:.1f} ms ({A.WOW_MAX_SECONDS * fe.frame_rate:.2f} frame)")

    # An ffmpeg that ignores the MP3's gapless header returns LAME's 576 + 529 samples of delay and
    # some padding: measured once and trimmed, whatever the build.
    y0 = A._calibration(rate)[: rate] + 0.01 * np.random.default_rng(5).standard_normal(rate)
    real = A._roundtrip
    try:
        A._roundtrip = lambda ff, x, r, c, kbps: np.r_[np.zeros(1105), x, np.zeros(700)]
        fake = A.Augment(["codec"])
        fake.ffmpeg, fake.codecs = "an-ffmpeg-without-gapless-mp3", ["mp3"]
        assert A.codec_delay(fake.ffmpeg, "mp3", 64, rate) == 1105
        y = A.codec(y0, rate, {"codec": "mp3", "kbps": 64}, fake)
        assert len(y) == len(y0) and np.allclose(y, y0, atol=1e-6)
    finally:
        A._roundtrip = real
    print("codec: a raw delay of 1105 samples (no gapless header) is measured and trimmed to the sample")
    if aug.codecs:
        for c in aug.codecs:
            for kbps in (32, 128):
                d = A.codec_delay(aug.ffmpeg, c, kbps, rate)
                y = A.codec(y0, rate, {"codec": c, "kbps": kbps}, aug)
                k, r = A.lag(y, y0, 2000)
                assert len(y) == len(y0) and k == 0 and r > 0.5, (c, kbps, k, r)
                print(f"codec {c} {kbps} kbit/s: delay through ffmpeg {d} samples, trimmed: lag 0, correlation {r:.2f}")
    else:
        print("codec: SKIPPED (ffmpeg is not on PATH)")


def check_determinism(aug: A.Augment, fe: FrontEnd, item) -> None:
    x, _ = click_track(fe.sample_rate, 4.0, seed=3)
    for k in range(3):
        a, b = aug.degrade(x, fe.sample_rate, "song:stem", k), aug.degrade(x, fe.sample_rate, "song:stem", k)
        assert np.array_equal(a, b), k
    assert not np.array_equal(aug.degrade(x, fe.sample_rate, "song:stem", 0), aug.degrade(x, fe.sample_rate, "song:stem", 1))
    again = A.Augment(list(aug.kinds), aug.copies, aug.seed).resolve(aug.ffmpeg)
    assert np.array_equal(again.degrade(x, fe.sample_rate, "song:stem", 2), aug.degrade(x, fe.sample_rate, "song:stem", 2))
    clean = features.cache_key(item, fe)
    keys = {features.cache_key(item, fe, aug, k) for k in range(3)}
    other_seed = A.Augment(list(aug.kinds), seed=aug.seed + 1).resolve(aug.ffmpeg)
    other_spec = A.Augment(["noise"], seed=aug.seed).resolve(aug.ffmpeg)
    keys2 = {features.cache_key(item, fe, other_seed, 0), features.cache_key(item, fe, other_spec, 0)}
    assert features.cache_key(item, fe, again, 1) == features.cache_key(item, fe, aug, 1)
    assert len(keys) == 3 and not keys & keys2 and len(keys2) == 2 and clean not in keys | keys2
    assert all(k.startswith("aug-") for k in keys | keys2) and not clean.startswith("aug-")
    print("determinism: the same item, spec and seed give the same audio and key; other copies, seeds and specs "
          "other keys, never a clean one")


def check_noise(fe: FrontEnd, work: Path) -> None:
    rate = fe.sample_rate
    x, _ = click_track(rate, 6.0, seed=4)
    rec = work / "noise" / "room-tone.wav"
    rec.parent.mkdir(parents=True, exist_ok=True)
    write_wav(rec, 0.1 * np.random.default_rng(9).standard_normal(rate * 2), 44100)
    aug = A.Augment(["noise"], noise_dir=str(rec.parent)).resolve()
    lo, hi = 99.0, 0.0
    for s in range(20):
        p = A.draw(np.random.default_rng(s), n_files=1)["noise"]
        assert A.SNR_DB[0] <= p["snr_db"] <= A.SNR_DB[1]
        for kind in list(A.NOISE_TYPES) + ["file"]:
            q = dict(p, types=[kind], gains_db=[0.0])
            for scale in (1.0, 0.01):  # relative to the item: a quieter item gets quieter noise
                y = A.noise(x * scale, rate, q, aug)
                snr = 20 * np.log10(A.active_level(x * scale, rate) / np.sqrt(np.mean((y - x * scale) ** 2)))
                assert abs(snr - p["snr_db"]) < 0.01, (kind, s, snr, p["snr_db"])
                lo, hi = min(lo, snr), max(hi, snr)
    every = A.Augment(list(A.ORDER), copies=4).resolve()
    for sig in (np.zeros(3 * rate), np.zeros(100), np.random.default_rng(1).standard_normal(100) * 0.5, np.zeros(0)):
        for k in range(4):
            y = every.degrade(sig, rate, "silence", k)
            assert len(y) == len(sig) and np.all(np.isfinite(y)), (len(sig), k)
            if not sig.any() and len(y):
                assert np.abs(y).max() < 1e-3, np.abs(y).max()  # silence stays (nearly) silent
    print(f"noise: {', '.join(A.NOISE_TYPES)} and a recording, {lo:.1f} to {hi:.1f} dB below the item's level "
          "(relative, not absolute); silent, tiny and empty items give finite audio")


def check_no_ffmpeg() -> None:
    A._warned.clear()  # it may have been said already, on a machine without ffmpeg
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        a = A.Augment(["noise", "codec"]).resolve(ffmpeg=None)
        b = A.Augment(["noise", "codec"]).resolve(ffmpeg=None)
    assert a.kinds_run == ["noise"] and out.getvalue().count("WARNING: augment: ffmpeg is not on PATH") == 1, out.getvalue()
    assert a.record(1)["skipped"] == ["codec"] and a.key(0) == b.key(0)
    with contextlib.redirect_stdout(io.StringIO()):
        assert A.Augment(["codec"]).resolve(ffmpeg=None).kinds_run == []
    with_ff = A.Augment(["noise", "codec"]).resolve()
    if with_ff.codecs:
        assert with_ff.key(0) != a.key(0)
    print("no ffmpeg: the codec kind is skipped with one warning, under a key of its own")


def old_cache_key(item, fe) -> str:
    """features.cache_key before augmentation existed."""
    h = hashlib.sha1(fe.key().encode())
    for f in item.audio:
        st = os.stat(f)
        h.update(f"{Path(f).resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:20]


def quiet(fn, args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(args)
    assert " OK wrote " in out.getvalue(), out.getvalue()[-2000:]
    return out.getvalue()


def check_training(work: Path) -> None:
    root = work / "data"
    if root.exists():
        shutil.rmtree(root)
    with contextlib.redirect_stdout(io.StringIO()):
        synth.main(["--out", str(root), "--songs", "10", "--seconds", "8", "--seed", "5"])
    items = read_songdeck(root, "stem")
    fe = FrontEnd.drums()
    assert all(features.cache_key(i, fe) == old_cache_key(i, fe) for i in items)
    cache = root / ".cache" / f"hcqt-ext-{fe.key()}"
    common = ["--data", str(root), "--epochs", "1", "--steps-per-epoch", "3", "--batch", "4", "--threads", "1",
              "--device", "cpu", "--version", "0.0.0-check"]
    quiet(train_drums.main, common + ["--out", str(work / "off" / "a.gguf")])
    quiet(train_drums.main, common + ["--out", str(work / "off" / "b.gguf"), "--augment", "noise", "--augment-copies", "0"])
    assert (work / "off" / "a.gguf").read_bytes() == (work / "off" / "b.gguf").read_bytes()
    assert not list(cache.glob("aug-*")), "no degraded copies when augmentation is off"
    tr = json.loads(gguf.read(work / "off" / "a.gguf")[0]["audio.training"])
    assert "augment" not in tr
    print("off: the clean cache keys are the old ones; without --augment (or with --augment-copies 0) the same "
          "model byte for byte, and no degraded copies")

    n_train = sum(1 for i in items if i.split == "train")
    log = quiet(train_drums.main, common + ["--out", str(work / "on" / "d.gguf"), "--augment", "all"])
    made = sorted(cache.glob("aug-*.npy"))
    assert len(made) == n_train and "computing degraded copies" in log, (len(made), n_train)
    meta, _ = gguf.read(work / "on" / "d.gguf")
    tr = json.loads(meta["audio.training"])
    assert tr["augment"]["copies"] == 1 and tr["augment"]["items"] == n_train and tr["train_items"] == n_train, tr
    train = [i for i in items if i.split == "train"]
    with contextlib.redirect_stdout(io.StringIO()):
        copies, paths = features.prepare_copies(train, fe, root / ".cache", A.Augment(list(A.ORDER)).resolve())
    assert sorted(paths) == made and [c.hits for c in copies] == [i.hits for i in train]  # the CLI's keys, the labels kept
    for i, p in zip(train, paths):  # the same length, so the same frames as the labels
        assert features.load(p, fe).shape == features.load(features.prepare([i], fe, root / ".cache")[0], fe).shape
    digest = hashlib.sha1(made[0].read_bytes()).hexdigest()
    made[0].unlink()
    log = quiet(train_drums.main, common + ["--out", str(work / "on" / "d2.gguf"), "--augment", "all"])
    assert any("degraded copies" in line and " for 1 of " in line for line in log.splitlines()), log
    assert hashlib.sha1(made[0].read_bytes()).hexdigest() == digest, "a rebuilt copy is identical"
    log = quiet(train_drums.main, common + ["--out", str(work / "on" / "d3.gguf"), "--augment", "all"])
    assert "computing degraded copies" not in log
    print(f"train_drums --augment all: {len(made)} copies cached once and reused; a removed one is rebuilt identical")

    log = quiet(train_pitched.main, common + ["--stem", "bass", "--out", str(work / "on" / "b.gguf"),
                                              "--augment", "room,noise,mic,wow", "--augment-copies", "2"])
    tr = json.loads(gguf.read(work / "on" / "b.gguf")[0]["audio.training"])
    assert tr["augment"]["kinds"] == ["room", "noise", "mic", "wow"] and tr["augment"]["copies"] == 2, tr
    assert "the clean audio is drawn 1 time in 3" in log
    print("train_pitched --augment room,noise,mic,wow --augment-copies 2: trained, recorded in audio.training")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True, help="scratch directory (replaced)")
    a = ap.parse_args(argv)
    torch.set_num_threads(1)
    work = Path(a.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    fe = FrontEnd.drums()
    with contextlib.redirect_stdout(io.StringIO()) as warn:
        aug = A.Augment(list(A.ORDER), copies=3, seed=7).resolve()
    print(warn.getvalue(), end="")
    check_alignment(fe, aug)
    root = work / "one"
    root.mkdir()
    write_wav(root / "x.wav", click_track(44100, 2.0)[0], 44100)
    check_determinism(aug, fe, Item("song:stem", "train", [root / "x.wav"]))
    check_noise(fe, work)
    check_no_ffmpeg()
    check_training(work)
    print("check_augment: OK")


if __name__ == "__main__":
    main()
