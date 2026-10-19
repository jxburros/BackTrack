"""Train the Surveil drum model and export it as GGUF.

    python3 -m training.train_drums --data DIR [--data DIR2] [--egmd EGMD_ROOT] \\
        --out surveil-drums-0.1.0.gguf [--epochs 30] [--minutes 0] [--audio stem,separated,performed] \\
        [--device auto] [--patience 0] [--resume]

Data: Song Deck dataset directories (train / valid splits from the
manifest) and optionally E-GMD. Features are computed once and cached
(float16, memory-mapped, <data>/.cache); each step trains on random crops
with the receptive radius as context, exactly as the binary runs chunks.
Performed audio (ACE-Step takes) is trained only on its accepted windows:
crops are drawn around accepted frames and the loss is masked outside them.
The best epoch on the valid split (mean class onset F at +-50 ms, hits
picked as the binary picks them) is exported, with audio.sources listing
every dataset used. A .pt checkpoint next to --out (written after every
epoch) allows --resume; <out>.history.json and <out>.summary.json are the
machine-readable record. Exit code 0 on success, 1 on failure, 130 when
interrupted (rerun with --resume).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import datasets, evaluate, export, features
from .datasets import summary
from .frontend import FrontEnd
from .model import DRUM_CLASSES, DRUM_LAYOUT, DRUM_NOTES, SurveilCNN

CPU_BATCH, GPU_BATCH = 16, 64


def batches(feats, targets, masks, crop, radius, batch, rng, gain_db):
    """Endless numpy batches (ext, y, m, g): ext (N, crop + 2r, n_ext) float16
    (the cached transform, expanded on the device by features.Expander),
    y (N, crop, classes), m (N, crop) loss mask, g (N,) level shift or None.
    Items are drawn in proportion to their usable frames; an item with a mask
    (performed audio) gets crops around a random accepted frame."""
    usable = [np.flatnonzero(m) if m is not None else None for m in masks]
    lengths = np.array([f.shape[1] if u is None else len(u) for f, u in zip(feats, usable)], np.float64)
    p = lengths / lengths.sum()
    while True:
        xs, ys, ms = [], [], []
        for k in rng.choice(len(feats), size=batch, p=p):
            T = feats[k].shape[1]
            if usable[k] is None:
                s = int(rng.integers(0, max(1, T - crop + 1)))
            else:
                f = int(usable[k][rng.integers(0, len(usable[k]))])
                s = min(max(0, f - int(rng.integers(0, crop))), max(0, T - crop))
            xs.append(features.ext_window(feats[k], s - radius, crop + 2 * radius))
            n = min(crop, T - s)
            y = np.zeros((crop, targets[k].shape[1]), np.float32)
            y[:n] = targets[k][s : s + n]
            m = np.ones(crop, np.float32) if masks[k] is None else np.zeros(crop, np.float32)
            if masks[k] is not None:
                m[:n] = masks[k][s : s + n]
            ys.append(y)
            ms.append(m)
        # A level change is a shift on the log scale (1/80 per dB); the -80 dB floor stays put.
        g = rng.uniform(-gain_db, gain_db, size=batch).astype(np.float32) / 80 if gain_db else None
        yield np.stack(xs), np.stack(ys), np.stack(ms), g


def prefetch(gen, depth=4):
    """Runs a batch generator in a background thread (batch assembly overlaps
    the training step; numpy copies release the GIL)."""
    q: queue.Queue = queue.Queue(maxsize=depth)

    def work():
        try:
            for b in gen:
                q.put(b)
        except BaseException as e:  # surfaced in the training thread
            q.put(e)

    threading.Thread(target=work, daemon=True).start()
    while True:
        b = q.get()
        if isinstance(b, BaseException):
            raise b
        yield b


def to_device(b, expand, device):
    ext, y, m, g = b
    pin = device.type == "cuda"
    t = lambda a: (torch.from_numpy(a).pin_memory() if pin else torch.from_numpy(a)).to(device, non_blocking=pin)
    x = expand(t(ext))
    if g is not None:
        x = torch.where(x > 0, (x + t(g).view(-1, 1, 1, 1)).clamp(0, 1), torch.zeros((), device=device))
    return x, t(y), t(m)


def masked_loss(logits, y, m, pos_weight):
    """Weighted BCE averaged over the frames the mask keeps (the plain mean
    when the mask is all ones)."""
    w = (1 + (pos_weight - 1) * y) * m[..., None]  # onset frames weigh more; the sparse classes need it
    total = F.binary_cross_entropy_with_logits(logits, y, weight=w, reduction="sum")
    return total / (m.sum() * y.shape[-1]).clamp(min=1)


def validate(model, fe, items, paths, threshold=0.5):
    model.eval()
    est = [[(t, c) for t, c, _ in evaluate.pick_drums(evaluate.infer_long(model, features.load(p, fe)), fe.frame_rate, threshold)]
           for p in paths]
    model.train()
    return evaluate.score(evaluate.pairs(items, est), DRUM_CLASSES)


def _atomic(path: Path, write):
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def performed_report(stats, train, valid, a) -> dict | None:
    if not stats.get("takes"):
        return None
    mins = lambda its: round(sum(datasets.region_seconds(i) for i in its if i.regions is not None) / 60, 2)
    return {"takes": stats["takes"], "accepted_takes": stats["accepted_takes"],
            "rejected": {"weak_offset": stats["weak_offset"], "no_window": stats["no_window"],
                         "missing_audio": stats["missing_audio"]},
            "minutes": round(stats["seconds"] / 60, 2), "accepted_minutes": round(stats["accepted_seconds"] / 60, 2),
            "accepted_minutes_train": mins(train), "accepted_minutes_valid": mins(valid),
            "providers": stats["providers"], "min_agreement": a.min_agreement,
            "min_offset_strength": a.min_offset_strength}


def run(a) -> dict:
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    if a.threads:
        torch.set_num_threads(a.threads)
    device = evaluate.pick_device(a.device)
    batch = a.batch or (CPU_BATCH if device.type == "cpu" else GPU_BATCH)
    print(f"device {device}, batch {batch}", flush=True)

    fe = FrontEnd.drums()
    stats: dict = {}
    items = evaluate.load_items(a, stats)
    print(summary(items))
    train = [i for i in items if i.split == "train"]
    valid = [i for i in items if i.split == "valid"]
    perf = performed_report(stats, train, valid, a)
    if perf:
        print(f"performed: {perf['accepted_takes']} of {perf['takes']} takes used, {perf['accepted_minutes']:.1f} of "
              f"{perf['minutes']:.1f} min accepted (agreement F >= {a.min_agreement:g}, offset strength >= "
              f"{a.min_offset_strength:g}); train {perf['accepted_minutes_train']:.1f} min, valid "
              f"{perf['accepted_minutes_valid']:.1f} min; rejected takes: {perf['rejected']}", flush=True)
    elif "performed" in a.audio.split(","):
        print("performed: no performances in the manifest(s)", flush=True)
    if not train:
        raise SystemExit("no training items")
    cache = evaluate.default_cache(a)
    t0 = time.time()
    train_paths = features.prepare(train, fe, cache)
    valid_paths = features.prepare(valid, fe, cache)
    print(f"features ready in {time.time() - t0:.1f} s (cache {cache})", flush=True)

    layout = json.loads(a.layout) if a.layout else DRUM_LAYOUT
    model = SurveilCNN.drums(len(fe.harmonics), fe.n_bins, len(DRUM_CLASSES), layout).to(device)
    r = model.receptive_radius
    print(f"model: {model.n_params()} parameters, receptive radius {r} frames ({1e3 * r / fe.frame_rate:.0f} ms)")
    feats = [features.load(p, fe) for p in train_paths]
    targets = [features.onset_targets(i.hits, f.shape[1], len(DRUM_CLASSES), fe.frame_rate) for i, f in zip(train, feats)]
    masks = [features.region_mask(i.regions, f.shape[1], fe.frame_rate) for i, f in zip(train, feats)]
    frames = int(sum(f.shape[1] if m is None else m.sum() for f, m in zip(feats, masks)))
    counts = np.bincount([c for i in train for _, c in datasets.in_regions(i.hits, i.regions)],
                         minlength=len(DRUM_CLASSES)).astype(np.float64)
    balance = np.clip((np.median(counts) / np.maximum(counts, 1)) ** a.class_balance, 0.5, 4.0) if a.class_balance else np.ones(len(counts))
    print("onset weight per class: " + " ".join(f"{n}={a.pos_weight * b:.1f}" for n, b in zip(DRUM_CLASSES, balance)))
    steps = a.steps_per_epoch or max(1, frames // (a.crop * batch))
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    total = steps * a.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=total, pct_start=0.05)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ckpt = out.with_suffix(".pt")
    hist_path = out.with_suffix(".history.json")
    start_epoch, best, history = 0, (-float("inf"), None, None), []
    if a.resume and ckpt.exists():
        st = torch.load(ckpt, weights_only=False, map_location="cpu")
        model.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        sched.load_state_dict(st["sched"])
        start_epoch, best = st["epoch"], tuple(st["best"])
        history = st.get("history")
        if history is None and hist_path.exists():  # checkpoints written before the history moved into them
            history = json.loads(hist_path.read_text())
        history = [h for h in history or [] if h["epoch"] <= start_epoch]
        print(f"resumed at epoch {start_epoch} (best so far: epoch {best[1]}, {best[0]:.3f})", flush=True)
    elif a.resume:
        print(f"--resume: no checkpoint at {ckpt}, starting from scratch", flush=True)

    expand = features.Expander(fe, device)
    data = prefetch(batches(feats, targets, masks, a.crop, r, batch, rng, a.gain_db))
    pos_weight = torch.tensor(a.pos_weight * balance, dtype=torch.float32, device=device)
    began = time.time()
    epoch = start_epoch
    stopped = "completed"
    for epoch in range(start_epoch, a.epochs):
        te = time.time()
        model.train()
        losses = torch.zeros((), device=device)
        for _ in range(steps):
            x, y, m = to_device(next(data), expand, device)
            logits = model(x)["onset"][:, r : r + a.crop]
            loss = masked_loss(logits, y, m, pos_weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            losses += loss.detach()
        mean_loss = float(losses) / steps  # one device sync per epoch
        if not np.isfinite(mean_loss):
            raise RuntimeError(f"loss is {mean_loss} in epoch {epoch + 1} (try a lower --lr)")
        dt = time.time() - te
        rep = validate(model, fe, valid, valid_paths) if valid else None
        vf = rep["mean_class_f"] if rep else -mean_loss
        history.append({"epoch": epoch + 1, "loss": mean_loss, "valid_f": rep and rep["mean_class_f"],
                        "valid_overall_f": rep and rep["overall"]["f"], "seconds": dt})
        print(f"epoch {epoch + 1}/{a.epochs}: loss {mean_loss:.4f}, {steps} steps in {dt:.1f} s "
              f"({steps * batch * a.crop / dt:.0f} frames/s)"
              + (f", valid mean class F {rep['mean_class_f']:.3f} (overall {rep['overall']['f']:.3f})" if rep else ""), flush=True)
        if vf > best[0]:
            best = (vf, epoch + 1, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()})
        _atomic(ckpt, lambda p: torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                                           "epoch": epoch + 1, "best": list(best), "history": history}, p))
        _atomic(hist_path, lambda p: p.write_text(json.dumps(history, indent=1)))
        if a.patience and epoch + 1 - best[1] >= a.patience:
            print(f"early stop: no better valid score for {a.patience} epochs after epoch {best[1]}")
            stopped = "patience"
            break
        if a.minutes and time.time() - began > 60 * a.minutes:
            print(f"time limit reached after epoch {epoch + 1}")
            stopped = "time limit"
            break
    epochs_done = history[-1]["epoch"] if history else epoch
    if best[2] is None:
        raise RuntimeError("no epoch finished, nothing to export")

    model.load_state_dict(best[2])
    model.cpu()
    sources = sorted({i.source for i in train + valid}) + a.source
    training = {"epochs": epochs_done, "best_epoch": best[1], "valid_mean_class_f": best[0], "train_frames": frames,
                "train_items": len(train), "valid_items": len(valid), "crop": a.crop, "batch": batch, "lr": a.lr,
                "pos_weight": a.pos_weight, "class_balance": a.class_balance, "gain_db": a.gain_db, "seed": a.seed,
                "layout": layout, "inputs": [s.strip() for s in a.audio.split(",")] if a.data else [],
                "datasets": [Path(d).name for d in a.data or []] + (["e-gmd"] if a.egmd else []),
                "device": device.type, "stopped": stopped}
    if perf:
        training["performed"] = perf
    _atomic(out, lambda p: export.save(p, model, fe, name=a.name, version=a.version, license=a.license, sources=sources,
                                       stems=["drums"], classes=DRUM_CLASSES, class_notes=DRUM_NOTES,
                                       extra={"audio.training": json.dumps(training)}))
    _atomic(hist_path, lambda p: p.write_text(json.dumps(history, indent=1)))
    result = {"ok": True, "out": str(out), "best_epoch": best[1], "epochs": epochs_done, "max_epochs": a.epochs,
              "valid_mean_class_f": best[0], "stopped": stopped, "device": device.type,
              "minutes": round((time.time() - began) / 60, 2), "training": training, "sources": sources}
    _atomic(out.with_suffix(".summary.json"), lambda p: p.write_text(json.dumps(result, indent=1)))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    evaluate.add_data_args(ap)
    ap.add_argument("--out", required=True, help="GGUF to write, e.g. surveil-drums-0.1.0.gguf")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--minutes", type=float, default=0, help="stop after this much training time (0: no limit)")
    ap.add_argument("--patience", type=int, default=0,
                    help="stop when the valid mean class F has not improved for N epochs (0: off)")
    ap.add_argument("--steps-per-epoch", type=int, default=0, help="default: one pass over the training frames")
    ap.add_argument("--batch", type=int, default=0, help=f"crops per step (default {CPU_BATCH} on CPU, {GPU_BATCH} on a GPU)")
    ap.add_argument("--crop", type=int, default=256, help="frames per training crop (default 256, about 3 s)")
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--pos-weight", type=float, default=4.0, help="weight of onset frames in the loss")
    ap.add_argument("--class-balance", type=float, default=0.5,
                    help="scale each class's onset weight by (median hits / its hits) ** X, within 0.5..4 (0: off)")
    ap.add_argument("--gain-db", type=float, default=8.0, help="random level change, +-dB (0: off)")
    ap.add_argument("--layout", default=None, help="JSON model layout (default: training.model.DRUM_LAYOUT)")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"],
                    help="training device (default auto: CUDA, then MPS, then CPU)")
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
    try:
        res = run(a)
    except KeyboardInterrupt:
        print(f"train_drums: INTERRUPTED; the checkpoint {Path(a.out).with_suffix('.pt')} holds the last finished "
              "epoch, rerun with --resume", flush=True)
        sys.exit(130)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"train_drums: FAILED: {e.code}", flush=True)
            sys.exit(1)
        raise
    except Exception as e:
        traceback.print_exc()
        print(f"train_drums: FAILED: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
    print(f"train_drums: OK wrote {res['out']}: best epoch {res['best_epoch']}/{res['epochs']} "
          f"(of {res['max_epochs']}, {res['stopped']}), valid mean class F {res['valid_mean_class_f']:.3f}, "
          f"{res['minutes']:.1f} min on {res['device']}", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 1))
    main()
