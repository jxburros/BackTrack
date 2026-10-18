"""Train the Surveil drum model and export it as GGUF.

    python3 -m training.train_drums --data DIR [--data DIR2] [--egmd EGMD_ROOT] \\
        --out surveil-drums-0.1.0.gguf [--epochs 30] [--minutes 0] [--audio stem,separated]

Data: Song Deck dataset directories (train / valid splits from the
manifest) and optionally E-GMD. Features are computed once and cached
(float16, memory-mapped, <data>/.cache); each step trains on random crops
with the receptive radius as context, exactly as the binary runs chunks.
The best epoch on the valid split (mean class onset F at +-50 ms, hits
picked as the binary picks them) is exported, with audio.sources listing
every dataset used. A .pt checkpoint next to --out allows --resume.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import evaluate, export, features
from .datasets import summary
from .frontend import FrontEnd
from .model import DRUM_CLASSES, DRUM_LAYOUT, DRUM_NOTES, SurveilCNN


def batches(feats, targets, crop, radius, batch, rng, gain_db):
    """Endless (x, y) batches: x (N, H, crop + 2r, B), y (N, crop, classes)."""
    lengths = np.array([f.shape[1] for f in feats], np.float64)
    p = lengths / lengths.sum()
    while True:
        xs, ys = [], []
        for k in rng.choice(len(feats), size=batch, p=p):
            T = feats[k].shape[1]
            s = int(rng.integers(0, max(1, T - crop + 1)))
            x = features.window(feats[k], s - radius, crop + 2 * radius)
            if gain_db:
                # A level change is a shift on the log scale (1/80 per dB); the
                # -80 dB floor stays put.
                g = rng.uniform(-gain_db, gain_db) / 80
                x = np.where(x > 0, np.clip(x + g, 0, 1), 0)
            y = np.zeros((crop, targets[k].shape[1]), np.float32)
            n = min(crop, T - s)
            y[:n] = targets[k][s : s + n]
            xs.append(x)
            ys.append(y)
        yield torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys))


def validate(model, fe, items, paths, threshold=0.5):
    model.eval()
    est = [[(t, c) for t, c, _ in evaluate.pick_drums(evaluate.infer_long(model, features.load(p, fe)), fe.frame_rate, threshold)]
           for p in paths]
    model.train()
    return evaluate.score(list(zip(est, [i.hits for i in items])), DRUM_CLASSES)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    evaluate.add_data_args(ap)
    ap.add_argument("--out", required=True, help="GGUF to write, e.g. surveil-drums-0.1.0.gguf")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--minutes", type=float, default=0, help="stop after this much training time (0: no limit)")
    ap.add_argument("--steps-per-epoch", type=int, default=0, help="default: one pass over the training frames")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--crop", type=int, default=256, help="frames per training crop (default 256, about 3 s)")
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--pos-weight", type=float, default=4.0, help="weight of onset frames in the loss")
    ap.add_argument("--class-balance", type=float, default=0.5,
                    help="scale each class's onset weight by (median hits / its hits) ** X, within 0.5..4 (0: off)")
    ap.add_argument("--gain-db", type=float, default=8.0, help="random level change, +-dB (0: off)")
    ap.add_argument("--layout", default=None, help="JSON model layout (default: training.model.DRUM_LAYOUT)")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="continue from the .pt checkpoint next to --out")
    ap.add_argument("--name", default="Surveil drums")
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--license", default="MIT")
    ap.add_argument("--source", action="append", default=[], help="extra audio.sources line (repeatable)")
    a = ap.parse_args(argv)
    if not a.data and not a.egmd:
        ap.error("give --data and/or --egmd")
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    if a.threads:
        torch.set_num_threads(a.threads)

    fe = FrontEnd.drums()
    items = evaluate.load_items(a)
    print(summary(items))
    train = [i for i in items if i.split == "train"]
    valid = [i for i in items if i.split == "valid"]
    if not train:
        raise SystemExit("no training items")
    cache = evaluate.default_cache(a)
    t0 = time.time()
    train_paths = features.prepare(train, fe, cache)
    valid_paths = features.prepare(valid, fe, cache)
    print(f"features ready in {time.time() - t0:.1f} s (cache {cache})", flush=True)

    layout = json.loads(a.layout) if a.layout else DRUM_LAYOUT
    model = SurveilCNN.drums(len(fe.harmonics), fe.n_bins, len(DRUM_CLASSES), layout)
    r = model.receptive_radius
    print(f"model: {model.n_params()} parameters, receptive radius {r} frames ({1e3 * r / fe.frame_rate:.0f} ms)")
    feats = [features.load(p, fe) for p in train_paths]
    targets = [features.onset_targets(i.hits, f.shape[1], len(DRUM_CLASSES), fe.frame_rate) for i, f in zip(train, feats)]
    frames = sum(f.shape[1] for f in feats)
    counts = np.bincount([c for i in train for _, c in i.hits], minlength=len(DRUM_CLASSES)).astype(np.float64)
    balance = np.clip((np.median(counts) / np.maximum(counts, 1)) ** a.class_balance, 0.5, 4.0) if a.class_balance else np.ones(len(counts))
    print("onset weight per class: " + " ".join(f"{n}={a.pos_weight * b:.1f}" for n, b in zip(DRUM_CLASSES, balance)))
    steps = a.steps_per_epoch or max(1, frames // (a.crop * a.batch))
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    total = steps * a.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=total, pct_start=0.05)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    ckpt = Path(a.out).with_suffix(".pt")
    start_epoch, best = 0, (-1.0, None, None)
    if a.resume and ckpt.exists():
        st = torch.load(ckpt, weights_only=False)
        model.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        sched.load_state_dict(st["sched"])
        start_epoch, best = st["epoch"], tuple(st["best"])
        print(f"resumed at epoch {start_epoch}")

    data = batches(feats, targets, a.crop, r, a.batch, rng, a.gain_db)
    pos_weight = torch.tensor(a.pos_weight * balance, dtype=torch.float32)
    began = time.time()
    epoch = start_epoch
    history = []
    for epoch in range(start_epoch, a.epochs):
        te = time.time()
        model.train()
        losses = []
        for _ in range(steps):
            x, y = next(data)
            logits = model(x)["onset"][:, r : r + a.crop]
            w = 1 + (pos_weight - 1) * y  # onset frames weigh more; the sparse classes need it
            loss = F.binary_cross_entropy_with_logits(logits, y, weight=w)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            losses.append(loss.item())
        dt = time.time() - te
        rep = validate(model, fe, valid, valid_paths) if valid else None
        vf = rep["mean_class_f"] if rep else -float(np.mean(losses))
        history.append({"epoch": epoch + 1, "loss": float(np.mean(losses)), "valid_f": rep and rep["mean_class_f"],
                        "valid_overall_f": rep and rep["overall"]["f"], "seconds": dt})
        print(f"epoch {epoch + 1}/{a.epochs}: loss {np.mean(losses):.4f}, {steps} steps in {dt:.1f} s "
              f"({steps * a.batch * a.crop / dt:.0f} frames/s)"
              + (f", valid mean class F {rep['mean_class_f']:.3f} (overall {rep['overall']['f']:.3f})" if rep else ""), flush=True)
        if vf > best[0]:
            best = (vf, epoch + 1, {k: v.detach().clone() for k, v in model.state_dict().items()})
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                    "epoch": epoch + 1, "best": list(best)}, ckpt)
        if a.minutes and time.time() - began > 60 * a.minutes:
            print(f"time limit reached after epoch {epoch + 1}")
            break

    model.load_state_dict(best[2])
    sources = sorted({i.source for i in train + valid}) + a.source
    training = {"epochs": epoch + 1, "best_epoch": best[1], "valid_mean_class_f": best[0], "train_frames": frames,
                "train_items": len(train), "valid_items": len(valid), "crop": a.crop, "batch": a.batch, "lr": a.lr,
                "pos_weight": a.pos_weight, "class_balance": a.class_balance, "gain_db": a.gain_db, "seed": a.seed, "layout": layout}
    export.save(a.out, model, fe, name=a.name, version=a.version, license=a.license, sources=sources,
                stems=["drums"], classes=DRUM_CLASSES, class_notes=DRUM_NOTES,
                extra={"audio.training": json.dumps(training)})
    Path(a.out).with_suffix(".history.json").write_text(json.dumps(history, indent=1))
    print(f"wrote {a.out} (epoch {best[1]}, valid mean class F {best[0]:.3f})")


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 1))
    main()
