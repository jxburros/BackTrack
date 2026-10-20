"""Drum evaluation: onset F-measure per class within +-50 ms, the convention of
Song Deck's `eval:datasets` (packages/audio/test/eval/metrics.ts: greedy
one-to-one matching, closest pairs first, same class). Pitched models
(bass, melody, vocals) are scored the same way with the MIDI pitch as the
class: a note counts when its start is within +-50 ms at the same pitch.

    python3 -m training.evaluate --model M.gguf --data DIR [--split test] [--audio stem] [--stem bass]
                                 [--binary build/backtrack-surveil] [--json report.json]
                                 [--device auto] [--min-agreement 0.6] [--min-offset-strength 0.2]

With --binary, each item goes through the shipped binary (`--stem drums
--json`) and its notes are scored, so what is measured is what ships;
without, the model runs in PyTorch and hits are picked by `pick_drums`, a
port of the C++ note picking (apps/surveil/notes.cpp, drums()).

`--audio performed` (ACE-Step performances, see datasets.py) is scored only
inside each take's accepted windows: reference hits and estimates outside
them are left out, and the report says so ("performed" in the JSON).
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
    min_gap = max(1, math.floor(0.035 * frame_rate + 0.5))
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


# Pitched picking (notes.cpp: priors from the source's pitch range, then the
# monophonic best path or the polyphonic per-pitch runs, split at onset peaks).
PITCH_RANGE = {"bass": (28, 67), "vocals": (36, 84), "melody": (40, 96), "guitar": (40, 88)}
MONOPHONIC = ("bass", "vocals", "melody")


def _peak(on, t, i, thr):
    T = on.shape[0]
    v = on[t, i]
    if v < thr:
        return False
    prev = on[t - 1, i] if t > 0 else 0.0
    nxt = on[t + 1, i] if t + 1 < T else 0.0
    return v >= prev and v > nxt


def _emit_split(out, frame, on, i, t0, t1, mn, thr, pitch_min, frame_rate):
    start = t0
    for t in range(t0 + mn, t1 - mn + 1):
        if t - start >= mn and _peak(on, t, i, thr):
            out.append((start / frame_rate, t / frame_rate, pitch_min + i, float(frame[start:t, i].mean())))
            start = t
    if t1 - start >= mn:
        out.append((start / frame_rate, t1 / frame_rate, pitch_min + i, float(frame[start:t1, i].mean())))


def pick_pitched(acts: dict, frame_rate: float, source: str, pitch_min: int = 21, onset_threshold: float = 0.5,
                 frame_threshold: float = 0.5, min_note_seconds: float = 0.1) -> list[tuple[float, float, int, float]]:
    """note and onset likelihoods (frames, pitches) -> [(start, end, pitch, confidence)],
    as the binary picks them with no song context."""
    note = np.asarray(acts["note"], np.float32)
    onset = np.asarray(acts["onset"], np.float32)
    T, N = note.shape
    lo, hi = PITCH_RANGE.get(source, (21, 108))
    w = ((np.arange(N) + pitch_min >= lo) & (np.arange(N) + pitch_min <= hi)).astype(np.float32)
    frame, on = note * w, onset * w
    mn = max(1, math.floor(min_note_seconds * frame_rate + 0.5))
    out = []
    if T == 0:
        return out
    if source in MONOPHONIC:
        S, eps, switch = N + 1, 1e-4, 3.0
        em = np.empty((T, S))
        em[:, 0] = np.log(np.maximum(eps, 1.0 - frame.max(axis=1).astype(np.float64)))
        em[:, 1:] = np.log(np.maximum(eps, frame.astype(np.float64)))
        score = em[0] - np.r_[0.0, np.full(N, switch)]
        back = np.zeros((T, S), np.int32)
        for t in range(1, T):
            best = int(np.argmax(score))
            jump = score[best] - switch
            stay = score >= jump
            stay[best] = True
            back[t] = np.where(stay, np.arange(S), best)
            score = np.where(stay, score, jump) + em[t]
        path = np.empty(T, np.int32)
        path[-1] = int(np.argmax(score))
        for t in range(T - 1, 0, -1):
            path[t - 1] = back[t, path[t]]
        t = 0
        while t < T:
            s_, t0 = path[t], t
            while t < T and path[t] == s_:
                t += 1
            if s_ == 0 or frame[t0:t, s_ - 1].mean() < frame_threshold * 0.8:
                continue
            _emit_split(out, frame, on, s_ - 1, t0, t, mn, onset_threshold, pitch_min, frame_rate)
    else:
        for i in range(N):
            active = frame[:, i] >= frame_threshold
            for t in range(1, T - 1):  # bridge gaps of up to two frames
                if active[t] or not active[t - 1]:
                    continue
                g = t
                while g < T and not active[g] and g - t < 3:
                    g += 1
                if g < T and active[g] and g - t <= 2 and not _peak(on, g, i, onset_threshold):
                    active[t:g] = True
            t = 0
            while t < T:
                if not active[t]:
                    t += 1
                    continue
                t0 = t
                while t < T and active[t]:
                    t += 1
                for k in range(max(0, t0 - 3), t0):
                    if _peak(on, k, i, onset_threshold):
                        t0 = k
                        break
                _emit_split(out, frame, on, i, t0, t, mn, onset_threshold, pitch_min, frame_rate)
    out.sort(key=lambda n: (n[0], n[2]))
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


def pick_device(name: str = "auto") -> torch.device:
    """auto: CUDA, then Apple's MPS, then CPU."""
    if name == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda: CUDA is not available to this PyTorch build")
    if name == "mps" and not (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
        raise SystemExit("--device mps: MPS is not available to this PyTorch build")
    return torch.device(name)


def infer_long(model, feats: np.ndarray, chunk: int = 2048, device=None) -> np.ndarray:
    """Onset likelihoods (frames, classes) for a whole item, in chunks with
    the receptive radius as context (the binary's semantics)."""
    r = model.receptive_radius
    T = feats.shape[1]
    device = device or next(model.parameters()).device
    out = []
    with torch.no_grad():
        for s in range(0, T, chunk):
            n = min(chunk, T - s)
            x = torch.from_numpy(features.window(feats, s - r, n + 2 * r))[None].to(device)
            y = torch.sigmoid(model(x)["onset"][0, r : r + n])
            out.append(y.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, model.n_outputs), np.float32)


def infer_long_pitched(model, feats, chunk: int = 2048, device=None) -> dict:
    """Note and onset likelihoods (frames, pitches) for a whole item, as infer_long."""
    r = model.receptive_radius
    T = feats.shape[1]
    device = device or next(model.parameters()).device
    out = {"note": [], "onset": []}
    with torch.no_grad():
        for s in range(0, T, chunk):
            n = min(chunk, T - s)
            x = torch.from_numpy(features.window(feats, s - r, n + 2 * r))[None].to(device)
            y = model(x)
            for k in out:
                out[k].append(torch.sigmoid(y[k][0, r : r + n]).float().cpu().numpy())
    return {k: np.concatenate(v) if v else np.zeros((0, model.n_outputs), np.float32) for k, v in out.items()}


def note_pairs(items, est) -> list:
    """(estimated, reference) [(start, pitch)] per item, for score() with pitches as classes, each
    restricted to the item's accepted regions (performed audio; others are scored whole)."""
    return [(datasets.in_regions([(n[0], n[2]) for n in e], i.regions),
             datasets.in_regions([(n[0], n[2]) for n in i.notes], i.regions)) for e, i in zip(est, items)]


PITCH_CLASSES = [str(p) for p in range(128)]


def pitched_report(results) -> dict:
    """score() over pitches, keeping the overall numbers (per-pitch F is noise)."""
    rep = score(results, PITCH_CLASSES)
    rep["classes"] = {k: v for k, v in rep["classes"].items() if v["n_ref"] or v["n_est"]}
    return rep


def predict_torch_pitched(model, fe, items, cache_dir, source, pitch_min=21):
    paths = features.prepare(items, fe, cache_dir)
    return [pick_pitched(infer_long_pitched(model, features.load(p, fe)), fe.frame_rate, source, pitch_min) for p in paths]


def pairs(items, est) -> list:
    """(estimated, reference) per item for score(), each restricted to the
    item's accepted regions (performed audio; others are scored whole)."""
    return [(datasets.in_regions(e, i.regions), datasets.in_regions(i.hits, i.regions)) for e, i in zip(est, items)]


def predict_torch(model, fe, items, cache_dir, threshold=0.5):
    paths = features.prepare(items, fe, cache_dir)
    return [[(t, c) for t, c, _ in pick_drums(infer_long(model, features.load(p, fe)), fe.frame_rate, threshold)] for p in paths]


def predict_binary(binary, model_path, items, class_notes, threshold=0.5, extra_args=(), stem="drums"):
    """Drums: [(start, class)] per item; pitched (class_notes None): [(start, end, pitch, confidence)]."""
    note_class = {n: i for i, n in enumerate(class_notes or [])}
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
            cmd = [str(binary), str(audio), "--model", str(model_path), "--stem", stem, "-o", "-", "--json", str(js),
                   "--onset-threshold", str(threshold), *extra_args]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            notes = json.loads(js.read_text())["notes"]
            if class_notes is None:
                out.append([(n["start"], n["end"], n["pitch"], n["confidence"]) for n in notes])
            else:
                out.append([(n["start"], note_class[n["pitch"]]) for n in notes if n["pitch"] in note_class])
    return out


def _write_float_wav(path, x, rate):
    import struct

    body = np.asarray(x, "<f4").tobytes()
    hdr = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
    Path(path).write_bytes(hdr + b"data" + struct.pack("<I", len(body)) + body)


def load_items(a, stats: dict | None = None, stems=None) -> list:
    """Items for --data/--audio, --library/--library-audio and --egmd; stats (if given) gathers the
    performed-audio counts (datasets._performed_items). With stems (pitched
    stem kinds such as ["bass"]), pitched items from the Song Deck data."""
    items = []
    st = {} if stats is None else stats
    # Libraries (training.libraries import) are Song Deck-format datasets without separated stems or
    # performances: read with their own audio choices.
    sets = [(d, a.audio) for d in a.data or []] + [(d, getattr(a, "library_audio", "stem")) for d in getattr(a, "library", None) or []]
    if stems:
        for d, choices in sets:
            for audio in choices.split(","):
                items += datasets.read_songdeck_notes(d, tuple(stems), audio.strip(),
                                                      getattr(a, "min_agreement", datasets.MIN_AGREEMENT),
                                                      getattr(a, "min_offset_strength", datasets.MIN_OFFSET_STRENGTH), st)
        return items
    for d, choices in sets:
        for audio in choices.split(","):
            items += datasets.read_songdeck(d, audio.strip(), getattr(a, "min_agreement", datasets.MIN_AGREEMENT),
                                            getattr(a, "min_offset_strength", datasets.MIN_OFFSET_STRENGTH), st)
    if a.egmd:
        items += datasets.read_egmd(a.egmd, a.egmd_limit)
    return items


def add_data_args(ap):
    ap.add_argument("--data", action="append", help="Song Deck dataset directory (repeatable)")
    ap.add_argument("--audio", default="stem",
                    help="stem | separated | mix | performed, comma-separated for several (default stem)")
    ap.add_argument("--min-agreement", type=float, default=datasets.MIN_AGREEMENT,
                    help="performed audio: use only windows whose drum F against the labels is at least this (default 0.6)")
    ap.add_argument("--min-offset-strength", type=float, default=datasets.MIN_OFFSET_STRENGTH,
                    help="performed audio: skip takes whose offset estimate is weaker than this (default 0.2)")
    ap.add_argument("--library", action="append",
                    help="a library imported by training.libraries (repeatable), read with --library-audio")
    ap.add_argument("--library-audio", default="stem,mix",
                    help="what libraries give: stem | mix, comma-separated (default stem,mix)")
    ap.add_argument("--egmd", help="E-GMD root (holding e-gmd-v1.0.0.csv)")
    ap.add_argument("--egmd-limit", type=int, default=0, help="use about N E-GMD items (spread over the list)")
    ap.add_argument("--cache", default=None, help="feature cache directory (default: <first data dir>/.cache)")


def default_cache(a) -> Path:
    if a.cache:
        return Path(a.cache)
    first = (a.data or getattr(a, "library", None) or [a.egmd])[0]
    return Path(first) / ".cache"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="model GGUF (drums or pitched)")
    ap.add_argument("--stem", default=None,
                    help="pitched models: the stem kinds to score, comma-separated (default: the model's first audio.stems)")
    add_data_args(ap)
    ap.add_argument("--split", default="test", help="train | valid | test | all (default test)")
    ap.add_argument("--binary", help="score the backtrack-surveil binary's output instead of PyTorch")
    ap.add_argument("--threshold", type=float, default=0.5, help="onset threshold (default 0.5, as the binary)")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"],
                    help="PyTorch backend's device (default auto: CUDA, then MPS, then CPU)")
    a = ap.parse_args(argv)
    if not a.data and not a.egmd and not a.library:
        ap.error("give --data, --library and/or --egmd")
    model, fe, meta = export.load(a.model)
    if model.kind == "pitched":
        return main_pitched(a, model, fe, meta)
    items = [i for i in load_items(a) if a.split == "all" or i.split == a.split]
    if not items:
        extra = " (no performed take passed --min-agreement / --min-offset-strength)" if "performed" in a.audio else ""
        sys.exit(f"no items in split {a.split}{extra}")
    if a.binary:
        est = predict_binary(a.binary, a.model, items, meta["audio.class_notes"], a.threshold)
    else:
        model.to(pick_device(a.device))
        est = predict_torch(model, fe, items, default_cache(a), a.threshold)
    report = score(pairs(items, est), meta["audio.classes"])
    report.update({"model": str(a.model), "split": a.split, "backend": "binary" if a.binary else "torch",
                   "audio": a.audio.split(",")})
    perf = [i for i in items if i.regions is not None]
    note = ""
    if perf:
        acc = sum(datasets.region_seconds(i) for i in perf) / 60
        report["performed"] = {"accepted_windows_only": True, "items": len(perf), "accepted_minutes": round(acc, 2),
                               "minutes": round(sum(i.duration for i in perf) / 60, 2),
                               "min_agreement": a.min_agreement, "min_offset_strength": a.min_offset_strength}
        note = (f"; performed audio scored only in accepted windows (agreement F >= {a.min_agreement:g}, "
                f"offset strength >= {a.min_offset_strength:g}): {acc:.1f} min of {len(perf)} takes")
    print(f"{meta.get('general.name')} on {len(items)} {a.split} items ({report['backend']}){note}:")
    print(table(report))
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2))


def main_pitched(a, model, fe, meta):
    stems = (a.stem or meta.get("audio.stems", ["bass"])[0]).split(",")
    if not a.data:
        sys.exit("pitched models are scored on Song Deck datasets (--data)")
    items = [i for i in load_items(a, stems=stems) if a.split == "all" or i.split == a.split]
    if not items:
        extra = " (no performed take passed --min-agreement / --min-offset-strength)" if "performed" in a.audio else ""
        sys.exit(f"no items in split {a.split} with {'/'.join(stems)}{extra}")
    source = stems[0]
    pitch_min = int(meta.get("audio.pitch_min", 21))
    if a.binary:
        est = predict_binary(a.binary, a.model, items, None, a.threshold, stem=source)
    else:
        model.to(pick_device(a.device))
        est = predict_torch_pitched(model, fe, items, default_cache(a), source, pitch_min)
    pairs_ = note_pairs(items, est)
    if "performed" in a.audio and not sum(len(r) for _, r in pairs_):
        sys.exit(f"no items in split {a.split} with reference notes inside the accepted windows: nothing to score")
    report = pitched_report(pairs_)
    report.update({"model": str(a.model), "split": a.split, "backend": "binary" if a.binary else "torch",
                   "audio": a.audio.split(","), "stems": stems})
    perf = [i for i in items if i.regions is not None]
    if perf:
        report["performed"] = {"accepted_windows_only": True, "items": len(perf),
                               "accepted_minutes": round(sum(datasets.region_seconds(i) for i in perf) / 60, 2),
                               "minutes": round(sum(i.duration for i in perf) / 60, 2),
                               "min_agreement": a.min_agreement, "min_offset_strength": a.min_offset_strength}
    o = report["overall"]
    print(f"{meta.get('general.name')} on {len(items)} {a.split} items of {'/'.join(stems)} ({report['backend']}):")
    print(f"note F {o['f']:.3f} (precision {o['precision']:.3f}, recall {o['recall']:.3f}; "
          f"{o['n_ref']} reference notes, {o['n_est']} found; onset within 50 ms at the same pitch)")
    if "timing_ms" in report:
        print(f"timing of notes: median |error| {report['timing_ms']['median_abs']:.1f} ms")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
