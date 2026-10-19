"""Train a pitched Surveil model (bass, melody, vocals) and export it as GGUF.

    python3 -m training.train_pitched --data DIR [--data DIR2] --stem bass \\
        --out surveil-bass-0.1.0.gguf [--epochs 30] [--minutes 0] [--audio stem,separated,performed] \\
        [--device auto] [--patience 0] [--resume]

Data: Song Deck dataset directories (`npm run dataset:render`, with the
stems of `--stem` written: `--only-stems bass --separate` for bass), train
and valid splits from the manifest. `--stem` takes the stem kinds to learn,
comma-separated (`--stem melody,vocals` for one melody model); the first
names the model's source for note picking. Features are cached as for the
drum model; each step trains on random crops with the receptive radius as
context. The targets are the four heads of the pitched graph
(docs/gguf-metadata.md): note (frames a note sounds), onset and offset (a
Gaussian around each start and end), and contour (the pitch curve, with
bends, at 3 bins per semitone). The best epoch on the valid split (performed audio: ACE-Step takes, trained and
scored only inside their accepted windows, as for drums) (note F:
onset within 50 ms at the same pitch, notes picked as the binary picks them)
is exported. A .pt checkpoint next to --out allows --resume;
<out>.history.json and <out>.summary.json are the record. Exit code 0 on
success, 1 on failure, 130 when interrupted (rerun with --resume).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import datasets, evaluate, export, features
from .frontend import FrontEnd
from .model import SurveilCNN
from .train_drums import CPU_BATCH, GPU_BATCH, _atomic, prefetch, to_device

# The default pitched layout: a 5x5 then a 3x3 trunk conv (16 and 8 channels), a 3x3 contour
# head, a 32-channel pool and 5-frame note, onset and offset heads. About 5k parameters,
# receptive radius 5 frames; about twice the drum model's cost per frame.
PITCHED_LAYOUT = dict(trunk=[(5, 5, 16), (3, 3, 8)], contour=(3, 3), pool=32, note=5, onset=5, offset=5)
PITCH_MIN = 21
SIGMA_FRAMES, SIGMA_BINS = 0.6, 1.0


def build(fe: FrontEnd, layout: dict) -> SurveilCNN:
    return SurveilCNN("pitched", len(fe.harmonics), fe.n_bins, fe.bins_per_semitone, fe.n_bins // fe.bins_per_semitone,
                      layout["trunk"], pitched={k: layout[k] for k in ("contour", "pool", "note", "onset", "offset")})


def _bend_at(bend, pos: float) -> float:
    if not bend:
        return 0.0
    ps = [p for p, _ in bend]
    cs = [c for _, c in bend]
    return float(np.interp(pos, ps, cs))


def note_targets(notes, start: int, length: int, frame_rate: float, n_out: int, bps: int, pitch_min: int = PITCH_MIN):
    """Targets for frames start..start+length: note, onset, offset (length, n_out)
    and contour (length, n_out * bps), all 0..1."""
    note = np.zeros((length, n_out), np.float32)
    onset = np.zeros_like(note)
    offset = np.zeros_like(note)
    contour = np.zeros((length, n_out * bps), np.float32)
    t0, t1 = start / frame_rate, (start + length) / frame_rate
    for a, b, pitch, bend in notes:
        if b < t0 - 0.1 or a > t1 + 0.1:
            continue
        i = pitch - pitch_min
        if not 0 <= i < n_out:
            continue
        fa, fb = a * frame_rate - start, b * frame_rate - start
        k0, k1 = max(0, int(round(fa))), min(length, max(int(round(fa)) + 1, int(round(fb))))
        if k1 > k0:
            note[k0:k1, i] = 1
        for y, f in ((onset, fa), (offset, fb)):
            kf = int(math.floor(f))
            for k in range(kf - 2, kf + 4):
                if 0 <= k < length:
                    v = math.exp(-0.5 * ((k - f) / SIGMA_FRAMES) ** 2)
                    if v >= 0.05:
                        y[k, i] = max(y[k, i], v)
        centre = i * bps + bps // 2
        for k in range(k0, k1):
            pos = min(1.0, max(0.0, ((k + start) / frame_rate - a) / max(1e-6, b - a)))
            c = centre + _bend_at(bend, pos) * bps / 100
            for j in range(int(math.floor(c)) - 3, int(math.floor(c)) + 5):
                if 0 <= j < contour.shape[1]:
                    v = math.exp(-0.5 * ((j - c) / SIGMA_BINS) ** 2)
                    if v >= 0.05:
                        contour[k, j] = max(contour[k, j], v)
    return note, onset, offset, contour


def batches(feats, notes, masks, crop, radius, batch, rng, gain_db, frame_rate, n_out, bps):
    """Endless numpy batches (ext, targets, mask, gain): ext (N, crop + 2r, n_ext)
    float16, targets (N, crop, 3 * n_out + n_out * bps) as note | onset |
    offset | contour, mask (N, crop) of the frames the loss counts, gain or None.
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
            ys.append(np.concatenate(note_targets(notes[k], s, crop, frame_rate, n_out, bps), axis=1))
            m = np.ones(crop, np.float32)
            if masks[k] is not None:
                m[:] = 0
                n = min(crop, T - s)
                m[:n] = masks[k][s : s + n]
            ms.append(m)
        g = rng.uniform(-gain_db, gain_db, size=batch).astype(np.float32) / 80 if gain_db else None
        yield np.stack(xs), np.stack(ys), np.stack(ms), g


def loss_fn(out, y, m, r, crop, n_out, pos_weight, offset_weight):
    """Weighted BCE per head (positives weigh pos_weight in note and onset;
    offsets count offset_weight), summed, over the frames the mask keeps."""
    tn, to, tf, tc = torch.split(y, [n_out, n_out, n_out, y.shape[-1] - 3 * n_out], dim=-1)
    head = lambda name: out[name][:, r : r + crop]
    keep = m[..., None]
    count = lambda t: (m.sum() * t.shape[-1]).clamp(min=1)

    def bce(logits, t, pw):
        w = (1 + (pw - 1) * t) * keep
        return F.binary_cross_entropy_with_logits(logits, t, weight=w, reduction="sum") / count(t)
    return (bce(head("note"), tn, pos_weight) + bce(head("onset"), to, pos_weight)
            + offset_weight * bce(head("offset"), tf, pos_weight) + bce(head("contour"), tc, pos_weight))


def validate(model, fe, items, paths, source):
    model.eval()
    est = [evaluate.pick_pitched(evaluate.infer_long_pitched(model, features.load(p, fe)), fe.frame_rate, source)
           for p in paths]
    model.train()
    return evaluate.pitched_report(evaluate.note_pairs(items, est))


def run(a) -> dict:
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    if a.threads:
        torch.set_num_threads(a.threads)
    device = evaluate.pick_device(a.device)
    batch = a.batch or (CPU_BATCH if device.type == "cpu" else GPU_BATCH)
    stems = [s.strip() for s in a.stem.split(",") if s.strip()]
    source = stems[0]
    print(f"device {device}, batch {batch}, stems {', '.join(stems)}", flush=True)

    fe = FrontEnd()
    stats: dict = {}
    items = evaluate.load_items(a, stats, stems=stems)
    print(datasets.summary_notes(items))
    if stats.get("takes"):
        print(f"performed: {stats['accepted_takes']} of {stats['takes']} takes used, "
              f"{stats['accepted_seconds'] / 60:.1f} of {stats['seconds'] / 60:.1f} min accepted (agreement F >= "
              f"{a.min_agreement:g}); not judged for this part: {stats['not_judged']}, weak offset "
              f"{stats['weak_offset']}, no window {stats['no_window']}, missing audio {stats['missing_audio']}", flush=True)
    elif "performed" in a.audio.split(","):
        print("performed: no performances in the manifest(s)", flush=True)
    train = [i for i in items if i.split == "train" and i.notes]
    valid = [i for i in items if i.split == "valid" and i.notes]
    if not train:
        raise SystemExit(f"no training items with {'/'.join(stems)} notes (render the dataset with those stems)")
    cache = evaluate.default_cache(a)
    t0 = time.time()
    train_paths = features.prepare(train, fe, cache)
    valid_paths = features.prepare(valid, fe, cache)
    print(f"features ready in {time.time() - t0:.1f} s (cache {cache})", flush=True)

    layout = json.loads(a.layout) if a.layout else PITCHED_LAYOUT
    model = build(fe, layout).to(device)
    r = model.receptive_radius
    n_out, bps = model.n_outputs, fe.bins_per_semitone
    print(f"model: {model.n_params()} parameters, receptive radius {r} frames ({1e3 * r / fe.frame_rate:.0f} ms)")
    feats = [features.load(p, fe) for p in train_paths]
    notes = [i.notes for i in train]
    masks = [features.region_mask(i.regions, f.shape[1], fe.frame_rate) for i, f in zip(train, feats)]
    frames = int(sum(f.shape[1] if m is None else m.sum() for f, m in zip(feats, masks)))
    steps = a.steps_per_epoch or max(1, frames // (a.crop * batch))
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    total = steps * a.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=max(total, 4),
                                                pct_start=max(0.05, 2 / max(total, 4)))
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
        start_epoch, best, history = st["epoch"], tuple(st["best"]), st.get("history") or []
        print(f"resumed at epoch {start_epoch} (best so far: epoch {best[1]}, {best[0]:.3f})", flush=True)
    elif a.resume:
        print(f"--resume: no checkpoint at {ckpt}, starting from scratch", flush=True)

    expand = features.Expander(fe, device)
    data = prefetch(batches(feats, notes, masks, a.crop, r, batch, rng, a.gain_db, fe.frame_rate, n_out, bps))
    began = time.time()
    epoch = start_epoch
    stopped = "completed"
    for epoch in range(start_epoch, a.epochs):
        te = time.time()
        model.train()
        losses = torch.zeros((), device=device)
        for _ in range(steps):
            x, y, m = to_device(next(data), expand, device)
            loss = loss_fn(model(x), y, m, r, a.crop, n_out, a.pos_weight, a.offset_weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            losses += loss.detach()
        mean_loss = float(losses) / steps
        if not np.isfinite(mean_loss):
            raise RuntimeError(f"loss is {mean_loss} in epoch {epoch + 1} (try a lower --lr)")
        dt = time.time() - te
        rep = validate(model, fe, valid, valid_paths, source) if valid else None
        vf = rep["overall"]["f"] if rep else -mean_loss
        history.append({"epoch": epoch + 1, "loss": mean_loss, "valid_f": rep and rep["overall"]["f"], "seconds": dt})
        print(f"epoch {epoch + 1}/{a.epochs}: loss {mean_loss:.4f}, {steps} steps in {dt:.1f} s"
              + (f", valid note F {rep['overall']['f']:.3f} (P {rep['overall']['precision']:.3f}, "
                 f"R {rep['overall']['recall']:.3f})" if rep else ""), flush=True)
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
    training = {"epochs": epochs_done, "best_epoch": best[1], "valid_note_f": best[0], "train_frames": frames,
                "train_items": len(train), "valid_items": len(valid), "stems": stems, "crop": a.crop, "batch": batch,
                "lr": a.lr, "pos_weight": a.pos_weight, "offset_weight": a.offset_weight, "gain_db": a.gain_db,
                "seed": a.seed, "layout": layout, "inputs": [s.strip() for s in a.audio.split(",")],
                "datasets": [Path(d).name for d in a.data], "device": device.type, "stopped": stopped}
    if stats.get("takes"):
        training["performed"] = {"takes": stats["takes"], "accepted_takes": stats["accepted_takes"],
                                 "minutes": round(stats["seconds"] / 60, 2),
                                 "accepted_minutes": round(stats["accepted_seconds"] / 60, 2),
                                 "min_agreement": a.min_agreement, "min_offset_strength": a.min_offset_strength,
                                 "rejected": {k: stats[k] for k in ("not_judged", "weak_offset", "no_window",
                                                                    "missing_audio")}}
    _atomic(out, lambda p: export.save(p, model, fe, name=a.name or f"Surveil {source}", version=a.version,
                                       license=a.license, sources=sources, stems=stems, pitch_min=PITCH_MIN,
                                       extra={"audio.training": json.dumps(training)}))
    result = {"ok": True, "out": str(out), "best_epoch": best[1], "epochs": epochs_done, "max_epochs": a.epochs,
              "valid_note_f": best[0], "stopped": stopped, "device": device.type,
              "minutes": round((time.time() - began) / 60, 2), "training": training, "sources": sources}
    _atomic(out.with_suffix(".summary.json"), lambda p: p.write_text(json.dumps(result, indent=1)))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", action="append", required=True, help="Song Deck dataset directory (repeatable)")
    ap.add_argument("--stem", default="bass", help="stem kinds to learn, comma-separated (default bass)")
    ap.add_argument("--audio", default="stem",
                    help="stem | separated | mix | performed, comma-separated for several (default stem)")
    ap.add_argument("--min-agreement", type=float, default=datasets.MIN_AGREEMENT,
                    help="performed audio: use only windows whose note F against the labels is at least this (default 0.6)")
    ap.add_argument("--min-offset-strength", type=float, default=datasets.MIN_OFFSET_STRENGTH,
                    help="performed audio: skip takes whose offset estimate is weaker than this (default 0.2)")
    ap.add_argument("--cache", default=None, help="feature cache directory (default: <first data dir>/.cache)")
    ap.add_argument("--out", required=True, help="GGUF to write, e.g. surveil-bass-0.1.0.gguf")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--minutes", type=float, default=0, help="stop after this much training time (0: no limit)")
    ap.add_argument("--patience", type=int, default=0, help="stop when the valid note F has not improved for N epochs")
    ap.add_argument("--steps-per-epoch", type=int, default=0, help="default: one pass over the training frames")
    ap.add_argument("--batch", type=int, default=0, help=f"crops per step (default {CPU_BATCH} on CPU, {GPU_BATCH} on a GPU)")
    ap.add_argument("--crop", type=int, default=256, help="frames per training crop (default 256, about 3 s)")
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--pos-weight", type=float, default=4.0, help="weight of positive targets in the loss")
    ap.add_argument("--offset-weight", type=float, default=0.5, help="weight of the offset head's loss")
    ap.add_argument("--gain-db", type=float, default=8.0, help="random level change, +-dB (0: off)")
    ap.add_argument("--layout", default=None, help="JSON model layout (default: PITCHED_LAYOUT)")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="continue from the .pt checkpoint next to --out")
    ap.add_argument("--name", default=None, help='model name (default "Surveil <first stem>")')
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--license", default="MIT")
    ap.add_argument("--source", action="append", default=[], help="extra audio.sources line (repeatable)")
    a = ap.parse_args(argv)
    try:
        res = run(a)
    except KeyboardInterrupt:
        print(f"train_pitched: INTERRUPTED; the checkpoint {Path(a.out).with_suffix('.pt')} holds the last finished "
              "epoch, rerun with --resume", flush=True)
        sys.exit(130)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"train_pitched: FAILED: {e.code}", flush=True)
            sys.exit(1)
        raise
    except Exception as e:
        traceback.print_exc()
        print(f"train_pitched: FAILED: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
    score = (f"valid note F {res['valid_note_f']:.3f}" if res["training"]["valid_items"]
             else "no valid split (best = lowest loss)")
    print(f"train_pitched: OK wrote {res['out']}: best epoch {res['best_epoch']}/{res['epochs']} "
          f"(of {res['max_epochs']}, {res['stopped']}), {score}, {res['minutes']:.1f} min on {res['device']}", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 1))
    main()
