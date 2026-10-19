"""Checks training on performed audio (ACE-Step takes) end to end on a tiny
synthetic dataset: label shifting, accepted windows, loss masking, the
licence record, evaluation on accepted windows only, resume, exit codes.

    python3 -m training.check_performed --work DIR

DIR gets a `training.synth` dataset with a fake `performances` entry per
song: the drum stem delayed by 25 ms (offset 20 ms + window shift 5 ms),
its 4-8 s window replaced by noise and given a low agreement F, plus a
second take with a weak offset that must be skipped. Fails loudly
(AssertionError, non-zero exit). About half a minute on one core.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch

from . import datasets, evaluate, features, gguf, synth, train_drums
from .frontend import FrontEnd, read_wav, to_mono, write_wav
from .model import DRUM_CLASSES

DELAY = 0.025  # seconds: the performed copy plays this much later
OFFSET, SHIFT = 0.020, 0.005
BAD = (4.0, 8.0)  # song-time window replaced by noise


def make_dataset(root: Path, songs: int = 12, seconds: float = 8.0):
    if root.exists():
        shutil.rmtree(root)
    synth.main(["--out", str(root), "--songs", str(songs), "--seconds", str(seconds), "--seed", "3"])
    man = json.loads((root / "manifest.json").read_text())
    rng = np.random.default_rng(0)
    for song in man["songs"]:
        stem = next(s for s in song["stems"] if s["drums"])
        x, rate = read_wav(root / stem["audio"])
        x = to_mono(x)
        d = int(round(DELAY * rate))
        y = np.concatenate([np.zeros(d, np.float32), x])[: len(x)]
        a, b = int((BAD[0] + DELAY) * rate), int((BAD[1] + DELAY) * rate)
        y[a:b] = rng.standard_normal(len(y[a:b])) * np.abs(x).max() * 0.3  # the take went wrong here
        pdir = root / "songs" / song["id"] / "performed"
        pdir.mkdir(exist_ok=True)
        write_wav(pdir / "ace-0-drums.wav", y, rate)
        write_wav(pdir / "ace-1-drums.wav", y, rate)
        windows = [{"start": s, "end": s + 4.0, "shift": SHIFT, "f": 0.1 if s == BAD[0] else 0.9, "reference": 10, "detected": 10}
                   for s in np.arange(0.0, seconds, 4.0)]
        take = lambda k, strength: {
            "id": f"ace-{k}", "provider": "ace-step-api", "model": "acestep-v15-turbo", "seed": k, "strength": 0.35,
            "prompt": "synth", "audio": f"songs/{song['id']}/performed/ace-{k}.wav",
            "separated": {"separator": "builtin", "stems": {"drums": f"songs/{song['id']}/performed/ace-{k}-drums.wav"}},
            "sample_rate": rate, "duration": seconds, "offset": OFFSET, "offset_strength": strength, "agreement": 0.7,
            "windows": windows, "created": "2026-10-18T00:00:00Z", "songdeck_version": "test",
            "licence_note": "ACE-Step output: must be checked before shipping"}
        song["performances"] = [take(0, 0.7), take(1, 0.1)]
    (root / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


def onset_flux(f: features.Features) -> np.ndarray:
    e = np.asarray(f.ext, np.float32)
    return np.maximum(np.diff(e, axis=0, prepend=e[:1]), 0).sum(1)


def best_lag(a: np.ndarray, b: np.ndarray, max_lag: int = 6) -> int:
    """Lag k (frames) maximising sum a[t] * b[t + k]."""
    n = min(len(a), len(b))
    score = {k: float(np.dot(a[max_lag : n - max_lag], b[max_lag + k : n - max_lag + k])) for k in range(-max_lag, max_lag + 1)}
    return max(score, key=score.get)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True, help="scratch directory (replaced)")
    a = ap.parse_args(argv)
    torch.set_num_threads(1)
    work = Path(a.work)
    root = work / "data"
    man = make_dataset(root)
    fe = FrontEnd.drums()

    # 1. Labels: hits move by offset + shift; rejected takes and windows drop out.
    stats: dict = {}
    perf = datasets.read_songdeck(root, "performed", stats=stats)
    stem = {i.id.split(":")[0]: i for i in datasets.read_songdeck(root, "stem")}
    assert len(perf) == len(man["songs"]) and stats["accepted_takes"] == len(perf), stats
    assert stats["weak_offset"] == len(perf) and stats["takes"] == 2 * len(perf), stats
    for it in perf:
        ref = stem[it.id.split(":")[0]]
        assert np.allclose([t for t, _ in it.hits], [t + DELAY for t, _ in ref.hits]), it.id
        assert [c for _, c in it.hits] == [c for _, c in ref.hits]
        assert it.regions == [(0.0 + DELAY, 4.0 + DELAY)], it.regions
        assert "ACE-Step performances of Song Deck renders (ace-step-api, acestep-v15-turbo)" in it.source, it.source
        assert "checked by the owner" in it.source and "must be checked" not in it.source
    assert abs(stats["accepted_seconds"] - 4.0 * len(perf)) < 1e-6
    print(f"labels: {len(perf)} takes, hits shifted by {1e3 * DELAY:.0f} ms, 4-8 s windows and weak-offset takes rejected")

    # 2. The shifted labels sit where the delayed audio's onsets are.
    cache = root / ".cache"
    for it in perf[:4]:
        ref = stem[it.id.split(":")[0]]
        pf, sf = (features.load(p, fe) for p in features.prepare([it, ref], fe, cache, workers=1))
        audio_lag = best_lag(onset_flux(sf)[: int(4 * fe.frame_rate)], onset_flux(pf)[: int(4 * fe.frame_rate)])
        ty = lambda i, f: features.onset_targets(i.hits, f.shape[1], len(DRUM_CLASSES), fe.frame_rate).sum(1)
        label_lag = best_lag(ty(ref, sf), ty(it, pf))
        assert audio_lag == label_lag == round(DELAY * fe.frame_rate), (it.id, audio_lag, label_lag)
        # Every shifted onset frame is the stem's onset frame + the delay (to the nearest frame).
        pk = [int(np.floor(t * fe.frame_rate + 0.5)) for t, _ in it.hits]
        sk = [int(np.floor((t + DELAY) * fe.frame_rate + 0.5)) for t, _ in ref.hits]
        assert pk == sk
    print(f"alignment: audio and labels both lag the stem by {round(DELAY * fe.frame_rate)} frames")

    # 3. Rejected windows contribute nothing to the loss.
    paths = features.prepare(perf, fe, cache, workers=1)
    feats = [features.load(p, fe) for p in paths]
    masks = [features.region_mask(i.regions, f.shape[1], fe.frame_rate) for i, f in zip(perf, feats)]
    k0, k1 = int(np.ceil(DELAY * fe.frame_rate)), int(np.ceil((4 + DELAY) * fe.frame_rate))
    for m in masks:
        assert m[:k0].sum() == 0 and m[k0:k1].all() and m[k1:].sum() == 0
    targets = [features.onset_targets(i.hits, f.shape[1], len(DRUM_CLASSES), fe.frame_rate) for i, f in zip(perf, feats)]
    gen = train_drums.batches(feats, targets, masks, 64, 12, 8, np.random.default_rng(0), 8.0)
    pw = torch.full((len(DRUM_CLASSES),), 4.0)
    for _ in range(20):
        ext, y, m, g = next(gen)
        assert (m.sum(1) > 0).all(), "a crop with no accepted frame"
        y, m = torch.from_numpy(y), torch.from_numpy(m)
        logits = torch.randn(y.shape, requires_grad=True)
        loss = train_drums.masked_loss(logits, y, m, pw)
        loss.backward()
        assert (logits.grad[m == 0] == 0).all()
        junk = logits.detach() + 50 * torch.randn(y.shape) * (m == 0)[..., None]
        assert torch.allclose(train_drums.masked_loss(junk, y, m, pw), loss.detach())
    full = torch.ones(4, 32)
    lg, yy = torch.randn(4, 32, 7), torch.rand(4, 32, 7)
    plain = torch.nn.functional.binary_cross_entropy_with_logits(lg, yy, weight=1 + 3 * yy)
    assert torch.allclose(train_drums.masked_loss(lg, yy, full, pw), plain)  # unmasked = the old loss
    ex = features.Expander(fe)
    assert np.array_equal(ex(torch.from_numpy(features.ext_window(feats[0], -5, 40))[None])[0].numpy(),
                          features.window(feats[0], -5, 40))
    print("masking: no gradient and no loss change from rejected frames; unmasked loss and expansion unchanged")

    # 4. Training (interrupted by the time limit, then resumed), the record, evaluation.
    out = work / "model" / "perf.gguf"
    base = ["--data", str(root), "--audio", "stem,performed", "--out", str(out), "--epochs", "2", "--steps-per-epoch", "3",
            "--batch", "4", "--threads", "1", "--version", "0.0.0-check", "--device", "auto"]
    train_drums.main(base + ["--minutes", "0.00001"])
    assert len(json.loads(out.with_suffix(".history.json").read_text())) == 1
    train_drums.main(base + ["--resume"])
    hist = json.loads(out.with_suffix(".history.json").read_text())
    assert [h["epoch"] for h in hist] == [1, 2], hist
    meta, _ = gguf.read(out)
    src = meta["audio.sources"]
    assert any(s.startswith("ACE-Step performances of Song Deck renders (ace-step-api, acestep-v15-turbo)") for s in src), src
    tr = json.loads(meta["audio.training"])
    assert tr["inputs"] == ["stem", "performed"] and tr["performed"]["min_agreement"] == 0.6, tr
    assert tr["performed"]["accepted_takes"] == len(perf) and tr["performed"]["accepted_minutes"] == round(4 * len(perf) / 60, 2)
    summ = json.loads(out.with_suffix(".summary.json").read_text())
    assert summ["ok"] and summ["epochs"] == 2
    rep_path = work / "eval-performed.json"
    evaluate.main(["--model", str(out), "--data", str(root), "--audio", "performed", "--split", "all", "--json", str(rep_path)])
    rep = json.loads(rep_path.read_text())
    n_ref = sum(len(datasets.in_regions(i.hits, i.regions)) for i in perf)
    assert rep["performed"]["accepted_windows_only"] and rep["overall"]["n_ref"] == n_ref < sum(len(i.hits) for i in perf), rep
    print(f"train + resume + evaluate: sources {src}, {n_ref} accepted reference hits scored")

    # 5. Exit codes for unattended runs.
    r = subprocess.run([sys.executable, "-m", "training.train_drums", "--data", str(work / "missing"), "--out",
                        str(work / "x.gguf"), "--threads", "1"], capture_output=True, text=True)
    assert r.returncode == 1 and "train_drums: FAILED" in r.stdout, (r.returncode, r.stdout[-500:])
    print("exit codes: a failed run exits 1 with a FAILED line")
    print("check_performed: OK")


if __name__ == "__main__":
    main()
