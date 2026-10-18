"""Parity check: the same clips through PyTorch and through backtrack-surveil
must give the same front end and the same likelihoods. Fails loudly (exit 1).

    python3 -m training.parity --binary build/backtrack-surveil --placeholders [--model M.gguf ...] [--clip X.wav ...]

For every model and clip, the binary writes its HCQT (--hcqt) and raw
likelihoods (--activations); then

  hcqt   training.frontend on the WAV file (decode, resample, HCQT) vs the binary's HCQT;
  model  the PyTorch model (ggml_parity on) on the binary's HCQT vs its likelihoods;
  e2e    PyTorch end to end from the WAV file vs the binary's likelihoods;
  picks  drums: evaluate.pick_drums (the Python port of the note picking) on
         the binary's likelihoods vs the hits in its --json output (exact).

Likelihoods differ by float16 rounding: ggml_conv_2d rounds its operands to
float16, and a value that lands on the other side of a rounding step in one
of the two moves by a float16 step, scaled by the layers after it (the wired
placeholders' gain-20 kernels make that about 1e-3; trained models less).

--placeholders adds the binary's own placeholder models (pitched and drums,
wired and random, and the drums' temporal head) and a PyTorch-built model
exported here (extra trunk layer and temporal convs), so the reader, the
writer and every graph variant are covered. Without --clip it generates
clips at 44.1 kHz stereo 16-bit (7 s, so the binary runs two chunks),
48 kHz mono float and 22.05 kHz mono 24-bit.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

from . import export, gguf
from .frontend import FrontEnd, hcqt_of_file
from .model import DRUM_CLASSES, DRUM_NOTES, SurveilCNN
from .evaluate import pick_drums
from .synth import Kit, pattern, render

HEADS = ("onset", "note", "offset", "contour")


def write_wav_as(path, x: np.ndarray, rate: int, kind: str):
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    ch = x.shape[1]
    if kind == "f32":
        body, tag, bits = x.astype("<f4").tobytes(), 3, 32
    elif kind == "s24":
        v = np.rint(np.clip(x, -1, 1) * 8388607).astype(np.int32).reshape(-1)
        body, tag, bits = np.stack([v & 255, (v >> 8) & 255, (v >> 16) & 255], 1).astype(np.uint8).tobytes(), 1, 24
    else:
        body, tag, bits = np.rint(np.clip(x, -1, 1) * 32767).astype("<i2").tobytes(), 1, 16
    ba = bits // 8
    hdr = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, tag, ch, rate, rate * ch * ba, ch * ba, bits)
    Path(path).write_bytes(hdr + b"data" + struct.pack("<I", len(body)) + body)


def make_clips(d: Path) -> list[Path]:
    clips = []
    for name, rate, seconds, kind, stereo in (("a-44k1-stereo-s16.wav", 44100, 7.0, "s16", True),
                                              ("b-48k-mono-f32.wav", 48000, 4.0, "f32", False),
                                              ("c-22k05-mono-s24.wav", 22050, 3.0, "s24", False)):
        rng = np.random.default_rng(rate)
        n = int(rate * seconds)
        x = render(pattern(rng, 120.0, seconds), Kit(rng, rate), n)
        x = 0.5 * x / (np.abs(x).max() + 1e-9)
        t = np.arange(n) / rate
        for k, p in enumerate((45, 52, 57, 64, 69)):
            f = 440 * 2 ** ((p - 69) / 12)
            env = ((t > 0.5 + 0.8 * k) & (t < 1.2 + 0.8 * k)).astype(float)
            x += 0.15 * env * sum(a * np.sin(2 * np.pi * f * (h + 1) * t) for h, a in enumerate((1, 0.5, 0.3)))
        if stereo:
            x = np.stack([x, 0.8 * x + 0.01 * rng.standard_normal(n)], 1)
        write_wav_as(d / name, x, rate, kind)
        clips.append(d / name)
    return clips


def make_models(binary: str, d: Path) -> list[Path]:
    out = []
    for kind, init, head in (("pitched", "wired", "shared"), ("pitched", "random", "shared"), ("drums", "wired", "shared"),
                             ("drums", "random", "shared"), ("drums", "random", "temporal")):
        p = d / f"placeholder-{kind}-{init}-{head}.gguf"
        subprocess.run([binary, "make-placeholder", "--kind", kind, "--init", init, "--head", head, "--seed", "11", "-o", str(p)],
                       check=True, stderr=subprocess.DEVNULL)
        out.append(p)
    # Reader then writer: the C++ placeholder re-written by gguf.py.
    meta, tensors = gguf.read(out[1], typed=True)
    p = d / "rewritten-pitched-random.gguf"
    gguf.write(p, meta, {k: v.reshape(_ggml_shape(v, k)) for k, v in tensors.items()})
    out.append(p)
    # A PyTorch-built drum model with a third trunk layer, exported here.
    torch.manual_seed(5)
    m = SurveilCNN.drums(layout=dict(trunk=[(3, 3, 12), (3, 5, 12), (5, 3, 6)], band=24, tconv=[(7, 24)], onset_kh=3))
    p = d / "exported-drums-3trunk.gguf"
    export.save(p, m, FrontEnd.drums(), name="parity export", version="0.0.0", license="MIT",
                sources=["none: random weights (training.parity)"], classes=DRUM_CLASSES, class_notes=DRUM_NOTES)
    out.append(p)
    return out


def _ggml_shape(a: np.ndarray, name: str):
    """gguf.read pads to 4 dimensions; give back the natural one (biases 1-D, band 2-D)."""
    if name.endswith(".bias"):
        return (a.shape[-1],)
    if name == "band.weight":
        return a.shape[-2:]
    return a.shape


def run_binary(binary, clip, model_path, d: Path, stem: str):
    act, hq, js = d / "act.json", d / "hcqt.npy", d / "notes.json"
    subprocess.run([binary, str(clip), "--model", str(model_path), "--stem", stem, "-o", "-", "--activations", str(act), "--hcqt", str(hq),
                    "--json", str(js)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    a = json.loads(act.read_text())
    a["notes"] = json.loads(js.read_text())["notes"]
    return {k: np.array(a[k], np.float32) for k in HEADS if k in a}, np.load(hq), a


def picks_differ(info: dict) -> int:
    """Hits where the Python picking on the binary's likelihoods and the binary's own notes disagree."""
    onset = np.array(info["onset"], np.float32)
    mine = sorted((round(t, 4), info["class_notes"][c]) for t, c, _ in pick_drums(onset, info["frame_rate"]))
    theirs = sorted((round(n["start"], 4), n["pitch"]) for n in info["notes"])
    return len(set(mine) ^ set(theirs))


def max_diff(torch_out: dict, cpp: dict) -> float:
    return max(float(np.abs(torch_out[k][0].numpy() - cpp[k]).max()) for k in cpp)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--model", action="append", default=[], help="GGUF model (repeatable)")
    ap.add_argument("--placeholders", action="store_true", help="also check the placeholder and exported test models")
    ap.add_argument("--clip", action="append", default=[], help="WAV clip (repeatable; default: generated clips)")
    ap.add_argument("--tol-hcqt", type=float, default=1e-4, help="max abs difference of the front end (0..1 scale)")
    ap.add_argument("--tol-model", type=float, default=2e-3, help="max abs likelihood difference, same input")
    ap.add_argument("--tol-e2e", type=float, default=5e-3, help="max abs likelihood difference, end to end")
    ap.add_argument("--json", help="write the results here")
    a = ap.parse_args(argv)
    failures, rows = 0, []
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        clips = [Path(c) for c in a.clip] or make_clips(d)
        models = [Path(m) for m in a.model] + (make_models(a.binary, d) if a.placeholders else [])
        if not models:
            ap.error("give --model and/or --placeholders")
        hcqt_cache = {}
        print(f"{'model':40s} {'clip':24s} {'hcqt':>9s} {'model':>9s} {'e2e':>9s} {'picks':>5s}")
        for mp in models:
            model, fe, meta = export.load(mp)
            model.ggml_parity = True
            stem = "drums" if model.kind == "drums" else "piano"
            for clip in clips:
                cpp, cpp_h, info = run_binary(a.binary, clip, mp, d, stem)
                if info["receptive_radius"] != model.receptive_radius:
                    print(f"FAIL {mp.name}: receptive radius {model.receptive_radius} in PyTorch, {info['receptive_radius']} in C++")
                    failures += 1
                key = (str(clip), fe.key())
                if key not in hcqt_cache:
                    hcqt_cache[key] = hcqt_of_file(clip, fe)
                py_h = hcqt_cache[key]
                if py_h.shape != cpp_h.shape:
                    print(f"FAIL {mp.name} {clip.name}: HCQT shape {py_h.shape} vs {cpp_h.shape}")
                    failures += 1
                    continue
                dh = float(np.abs(py_h - cpp_h).max())
                with torch.no_grad():
                    dm = max_diff(model.infer(torch.from_numpy(cpp_h)[None]), cpp)
                    de = max_diff(model.infer(torch.from_numpy(py_h)[None]), cpp)
                dp = picks_differ(info) if model.kind == "drums" else 0
                ok = dh <= a.tol_hcqt and dm <= a.tol_model and de <= a.tol_e2e and dp == 0
                failures += not ok
                rows.append({"model": mp.name, "clip": clip.name, "hcqt": dh, "model_diff": dm, "e2e": de, "picks_differ": dp, "ok": ok})
                print(f"{mp.name[:40]:40s} {clip.name[:24]:24s} {dh:9.2e} {dm:9.2e} {de:9.2e} {dp if model.kind == 'drums' else '-':>5}"
                      f"{'' if ok else '  FAIL'}", flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))
    if failures:
        print(f"parity FAILED: {failures} check(s) over tolerance (hcqt {a.tol_hcqt:g}, model {a.tol_model:g}, e2e {a.tol_e2e:g})")
        sys.exit(1)
    print(f"parity ok: {len(rows)} model/clip pairs within tolerance (hcqt {a.tol_hcqt:g}, model {a.tol_model:g}, e2e {a.tol_e2e:g})")


if __name__ == "__main__":
    main()
