"""PyTorch model <-> GGUF, with the metadata of docs/gguf-metadata.md.

    python3 -m training.export --check FILE.gguf     load a GGUF into PyTorch and print its layout
"""

from __future__ import annotations

import argparse

from . import gguf
from .frontend import FrontEnd
from .gguf import I32, U32
from .model import SurveilCNN

ARCH = "surveil-cnn"


def save(path, model: SurveilCNN, fe: FrontEnd, *, name: str, version: str, license: str, sources: list,
         stems: list | None = None, classes: list | None = None, class_notes: list | None = None,
         pitch_min: int = 21, extra: dict | None = None):
    """Writes `model` as a surveil-cnn GGUF. `sources` is the licence record
    (audio.sources): every dataset or render set that went into training."""
    if not sources:
        raise ValueError("audio.sources (the training-data licence record) must not be empty")
    meta = {
        "general.architecture": ARCH,
        "general.name": name,
        "general.version": version,
        "general.license": license,
        "audio.task": "transcribe",
        "audio.arch": ARCH,
        "audio.kind": model.kind,
        "audio.stems": list(stems or (["drums"] if model.kind == "drums" else ["mix", "other"])),
        "audio.sources": list(sources),
        "audio.placeholder": False,
    }
    meta.update(fe.meta())
    if model.kind == "drums":
        if not classes or len(classes) != model.n_outputs or len(class_notes or []) != len(classes):
            raise ValueError("drum models need classes and class_notes, one per output")
        meta["audio.classes"] = list(classes)
        meta["audio.class_notes"] = [I32(n) for n in class_notes]
    else:
        meta["audio.pitch_min"] = U32(pitch_min)
    meta.update(extra or {})
    gguf.write(path, meta, model.tensors())


def load(path) -> tuple[SurveilCNN, FrontEnd, dict]:
    """Any surveil-cnn GGUF (trained, exported here, or `make-placeholder`)."""
    meta, tensors = gguf.read(path)
    if meta.get("general.architecture") != ARCH:
        raise ValueError(f"{path}: architecture {meta.get('general.architecture')!r} is not {ARCH}")
    model = SurveilCNN.from_tensors(meta, tensors)
    model.eval()
    return model, FrontEnd.from_meta(meta), meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", required=True, metavar="FILE.gguf")
    a = ap.parse_args()
    model, fe, meta = load(a.check)
    print(f"{meta.get('general.name')} {meta.get('general.version')} ({model.kind}), {model.n_params()} parameters, "
          f"receptive radius {model.receptive_radius} frames, filter_scale {fe.filter_scale:g}")
    for k, t in model.tensors().items():
        print(f"  {k:16s} {tuple(t.shape)}")
    print("  sources:", meta.get("audio.sources"))


if __name__ == "__main__":
    main()
