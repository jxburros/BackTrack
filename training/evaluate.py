"""Drum evaluation: onset F-measure per class within +-50 ms, the convention of
Song Deck's `eval:datasets` (packages/audio/test/eval/metrics.ts: greedy
one-to-one matching, closest pairs first, same class).

    python3 -m training.evaluate --model M.gguf --data DIR [--split test] [--audio stem]
                                 [--binary build/backtrack-surveil] [--json report.json]

With --binary, each item goes through the shipped binary (`--stem drums
--json`) and its notes are scored, so what is measured is what ships;
without, the model runs in PyTorch and hits are picked by `pick_drums`, a
port of the C++ note picking (apps/surveil/notes.cpp, drums()).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

from . import datasets, export, features
from .frontend import read_wav, to_mono

TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# Picking (notes.cpp, drums()) and matching (metrics.ts, matchTimes)
# ---------------------------------------------------------------------------


def pick_drums(onset: np.ndarray, frame_rate: float, threshold: float = 0.5) -> list[tuple[float, int, float]]:
    """onset (frames, classes) likelihoods -> [(seconds, class, confidence)]."""
    T, N = onset.shape
    min_gap = max(1, int(math.floor(0.035 * frame_rate + 0.5)))
    prev = np.vstack([np.zeros((1, N), onset.dtype), onset[:-1]])
    nxt = np.vstack([onset[1:], np.zeros((1, N), onset.dtype)])
    peak = (onset >= threshold) & (onset >= prev) & (onset > nxt)
    out = []
    for i in range(N):
        mine = []
        last = -min_gap - 1
        for t in np.nonzero(peak[:, i])[0]:
            v = float(onset[t, i])
            if t - last < min_gap:
                if mine and v > mine[-1][2]:
                    mine.pop()  # keep the stronger of the two
                else:
                    continue
            mine.append((t / frame_rate, i, round(v * 1000) / 1000))
            last = t
        out += mine
    out.sort()
    return out


def match(est: list[float], ref: list[float], tol: float = TOLERANCE) -> list[tuple[int, int]]:
    """Greedy one-to-one matching within tol, closest pairs first (ties in
    estimate-then-reference order, as the stable sort in metrics.ts)."""
    pairs = []
    ref_sorted = np.array(ref)
    for i, a in enumerate(est):
        lo = np.searchsorted(ref_sorted, a - tol - 1e-9) if len(ref_sorted) else 0
        for j in range(lo, len(ref)):
            d = abs(a - ref[j])
            if ref[j] > a + tol + 1e-9:
                break
            if d <= tol:
                pairs.append((d, i, j))
    pairs.sort(key=lambda p: p[0])
    used_a, used_b, hits = set(), set(), []
    for _, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        hits.append((i, j))
    return hits


def f_from(hits: int, n_est: int, n_ref: int) -> dict:
    p = hits / n_est if n_est else (0.0 if n_ref else 1.0)
    r = hits / n_ref if n_ref else 1.0
    f = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return {"precision": p, "recall": r, "f": f, "hits": hits, "n_est": n_est, "n_ref": n_ref}


def score(results, classes: list[str], tol: float = TOLERANCE) -> dict:
    """results: [(estimated [(t, class)], reference [(t, class)])] per item."""
    per = {c: [0, 0, 0] for c in range(len(classes))}
    offsets = []
    for est, ref in results:
        for c in per:
            e = sorted(t for t, k in est if k == c)
            r = sorted(t for t, k in ref if k == c)
            m = match(e, r, tol)
            per[c][0] += len(m)
            per[c][1] += len(e)
            per[c][2] += len(r)
            offsets += [e[i] - r[j] for i, j in m]
    report = {"tolerance": tol, "items": len(results), "classes": {}}
    for c, (h, ne, nr) in per.items():
        report["classes"][classes[c]] = f_from(h, ne, nr)
    tot = [sum(v[k] for v in per.values()) for k in range(3)]
    report["overall"] = f_from(*tot)
    with_ref = [v["f"] for v in report["classes"].values() if v["n_ref"]]
    report["mean_class_f"] = float(np.mean(with_ref)) if with_ref else 0.0
    if offsets:
        o = np.array(offsets)
        report["timing_ms"] = {"mean": 1e3 * float(o.mean()), "mean_abs": 1e3 * float(np.abs(o).mean()),
                               "median_abs": 1e3 * float(np.median(np.abs(o)))}
    return report


def table(report: dict) -> str:
    lines = [f"{'class':14s} {'F':>6s} {'P':>6s} {'R':>6s} {'ref':>6s} {'est':>6s}"]
    for name, v in list(report["classes"].items()) + [("overall", report["overall"])]:
        lines.append(f"{name:14s} {v['f']:6.3f} {v['precision']:6.3f} {v['recall']:6.3f} {v['n_ref']:6d} {v['n_est']:6d}")
    lines.append(f"mean class F {report['mean_class_f']:.3f} over {report['items']} items")
    if "timing_ms" in report:
        t = report["timing_ms"]
        lines.append(f"timing of hits: mean {t['mean']:+.1f} ms, mean |error| {t['mean_abs']:.1f} ms, median |error| {t['median_abs']:.1f} ms")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def infer_long(model, feats: np.ndarray, chunk: int = 2048) -> np.ndarray:
    """Onset likelihoods (frames, classes) for a whole item, in chunks with
    the receptive radius as context (the binary's semantics)."""
    r = model.receptive_radius
    T = feats.shape[1]
    out = []
    with torch.no_grad():
        for s in range(0, T, chunk):
            n = min(chunk, T - s)
            x = torch.from_numpy(features.window(feats, s - r, n + 2 * r))[None]
            y = torch.sigmoid(model(x)["onset"][0, r : r + n])
            out.append(y.numpy())
    return np.concatenate(out) if out else np.zeros((0, model.n_outputs), np.float32)


def predict_torch(model, fe, items, cache_dir, threshold=0.5):
    paths = features.prepare(items, fe, cache_dir)
    return [[(t, c) for t, c, _ in pick_drums(infer_long(model, features.load(p, fe)), fe.frame_rate, threshold)] for p in paths]


def predict_binary(binary, model_path, items, class_notes, threshold=0.5, extra_args=()):
    note_class = {n: i for i, n in enumerate(class_notes)}
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, it in enumerate(items):
            audio = it.audio[0]
            if len(it.audio) > 1:  # stems summed into one file at their own rate (float WAV keeps them exact)
                xs = [read_wav(f) for f in it.audio]
                n = max(len(x) for x, _ in xs)
                mix = sum(np.pad(to_mono(x), (0, n - len(x))) for x, _ in xs)
                audio = Path(tmp) / f"item{k}.wav"
                _write_float_wav(audio, mix, xs[0][1])
            js = Path(tmp) / "out.json"
            cmd = [str(binary), str(audio), "--model", str(model_path), "--stem", "drums", "-o", "-", "--json", str(js),
                   "--onset-threshold", str(threshold), *extra_args]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            notes = json.loads(js.read_text())["notes"]
            out.append([(n["start"], note_class[n["pitch"]]) for n in notes if n["pitch"] in note_class])
    return out


def _write_float_wav(path, x, rate):
    import struct

    body = np.asarray(x, "<f4").tobytes()
    hdr = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
    Path(path).write_bytes(hdr + b"data" + struct.pack("<I", len(body)) + body)


def load_items(a) -> list:
    items = []
    for d in a.data or []:
        for audio in a.audio.split(","):
            items += datasets.read_songdeck(d, audio)
    if a.egmd:
        items += datasets.read_egmd(a.egmd, a.egmd_limit)
    return items


def add_data_args(ap):
    ap.add_argument("--data", action="append", help="Song Deck dataset directory (repeatable)")
    ap.add_argument("--audio", default="stem", help="stem | separated | mix, comma-separated for several (default stem)")
    ap.add_argument("--egmd", help="E-GMD root (holding e-gmd-v1.0.0.csv)")
    ap.add_argument("--egmd-limit", type=int, default=0, help="use about N E-GMD items (spread over the list)")
    ap.add_argument("--cache", default=None, help="feature cache directory (default: <first data dir>/.cache)")


def default_cache(a) -> Path:
    if a.cache:
        return Path(a.cache)
    first = (a.data or [a.egmd])[0]
    return Path(first) / ".cache"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="drum model GGUF")
    add_data_args(ap)
    ap.add_argument("--split", default="test", help="train | valid | test | all (default test)")
    ap.add_argument("--binary", help="score the backtrack-surveil binary's output instead of PyTorch")
    ap.add_argument("--threshold", type=float, default=0.5, help="onset threshold (default 0.5, as the binary)")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    if not a.data and not a.egmd:
        ap.error("give --data and/or --egmd")
    model, fe, meta = export.load(a.model)
    if model.kind != "drums":
        ap.error("evaluate scores drum models")
    items = [i for i in load_items(a) if a.split == "all" or i.split == a.split]
    if not items:
        sys.exit(f"no items in split {a.split}")
    if a.binary:
        est = predict_binary(a.binary, a.model, items, meta["audio.class_notes"], a.threshold)
    else:
        est = predict_torch(model, fe, items, default_cache(a), a.threshold)
    report = score(list(zip(est, [i.hits for i in items])), meta["audio.classes"])
    report.update({"model": str(a.model), "split": a.split, "backend": "binary" if a.binary else "torch"})
    print(f"{meta.get('general.name')} on {len(items)} {a.split} items ({report['backend']}):")
    print(table(report))
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
