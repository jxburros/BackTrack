"""The surveil-cnn graph in PyTorch, with ggml's semantics (apps/surveil/model.cpp,
docs/gguf-metadata.md).

Input is the harmonic CQT as (batch, harmonics, frames, bins): PyTorch's
(N, C, H, W) with H = time and W = frequency, so a ggml kernel
ne = [KW, KH, C_in, C_out] is a Conv2d weight (C_out, C_in, KH, KW) as is.
Parameters carry the GGUF tensor names (`conv1.weight`, `band.weight`...).

What ggml does that plain PyTorch does not, mirrored here:

- every convolution pads to keep the frame count (KH // 2 in time, KW // 2
  in frequency; the stride-`bins_per_semitone` pool pads nothing);
- the binary runs chunks with zero input frames outside the clip, so the
  layers see computed (not zero-padded) activations there: `infer` pads the
  input with `receptive_radius` zero frames and crops, which is the same;
- ggml_conv_2d rounds both its input (im2col) and its kernel to float16 and
  accumulates in float32. `ggml_parity = True` rounds the same way and
  accumulates in double (convolutions and the band layer, a float32 product
  over 2000-odd inputs in ggml), which lands on the same side of the next
  float16 rounding more often than PyTorch's float32 sums do; training
  leaves it off, parity.py turns it on.
- band rows index the trunk as `channel x n_bins + bin`.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

DRUM_CLASSES = ["kick", "snare", "toms", "hihat_closed", "hihat_open", "crash", "ride"]
DRUM_NOTES = [36, 38, 45, 42, 46, 49, 51]

# The T2 drum layout (also `make-placeholder --kind drums --head temporal`):
# trunk 3x3 convs to 16 and 8 channels, band to 32, two 9-frame temporal
# convs, a 5-frame per-class onset kernel. About 90k parameters; receptive
# radius 12 frames (140 ms at 86 frames per second).
DRUM_LAYOUT = dict(trunk=[(3, 3, 16), (3, 3, 8)], band=32, tconv=[(9, 32), (9, 32)], onset_kh=5)


class Layer(nn.Module):
    def __init__(self, weight_shape, bias_size):
        super().__init__()
        self.weight = nn.Parameter(torch.empty(weight_shape))
        self.bias = nn.Parameter(torch.zeros(bias_size))


class SurveilCNN(nn.Module):
    def __init__(self, kind: str, n_harmonics: int, n_bins: int, bins_per_semitone: int, n_outputs: int,
                 trunk, pitched=None, drums=None):
        """trunk: [(kw, kh, c_out)...]; pitched: dict(contour=(kw, kh), pool=c3,
        note=kh, onset=kh, offset=kh); drums: dict(band=D or None for T0's
        shared head, tconv=[(kh, c_out)...], onset_kh=kh)."""
        super().__init__()
        assert kind in ("pitched", "drums")
        self.kind, self.n_bins, self.bps, self.n_outputs = kind, n_bins, bins_per_semitone, n_outputs
        self.ggml_parity = False
        L = self.layers = nn.ModuleDict()
        c = n_harmonics
        for i, (kw, kh, co) in enumerate(trunk, 1):
            L[f"conv{i}"] = Layer((co, c, kh, kw), co)
            c = co
        self.n_trunk = len(trunk)
        if kind == "pitched":
            p = pitched
            L["contour"] = Layer((1, c, p["contour"][1], p["contour"][0]), 1)
            L["pool"] = Layer((p["pool"], c, 1, bins_per_semitone), p["pool"])
            for head in ("note", "onset", "offset"):
                L[head] = Layer((1, p["pool"], p[head], 1), 1)
            self.n_tconv = 0
        else:
            d = drums
            D = d.get("band") or n_outputs
            L["band"] = Layer((D, n_bins * c), D)
            ch = D
            for i, (kh, co) in enumerate(d.get("tconv", []), 1):
                L[f"tconv{i}"] = Layer((co, ch, kh, 1), co)
                ch = co
            self.n_tconv = len(d.get("tconv", []))
            self.shared_onset = not d.get("band") and self.n_tconv == 0
            if self.shared_onset:
                L["onset"] = Layer((1, 1, d["onset_kh"], 1), n_outputs)
            else:
                L["onset"] = Layer((n_outputs, ch, d["onset_kh"], 1), n_outputs)
        for layer in L.values():
            nn.init.kaiming_uniform_(layer.weight, a=5**0.5)
            fan_in = layer.weight[0].numel()
            nn.init.uniform_(layer.bias, -(fan_in**-0.5), fan_in**-0.5)

    # -- construction --------------------------------------------------------

    @staticmethod
    def drums(n_harmonics=8, n_bins=264, n_classes=7, layout=None) -> "SurveilCNN":
        lay = dict(DRUM_LAYOUT if layout is None else layout)
        return SurveilCNN("drums", n_harmonics, n_bins, 3, n_classes, lay["trunk"],
                          drums=dict(band=lay["band"], tconv=lay["tconv"], onset_kh=lay["onset_kh"]))

    @staticmethod
    def from_tensors(meta: dict, tensors: dict) -> "SurveilCNN":
        """From gguf.read() output: infers the layout from the tensor shapes."""
        kind = meta.get("audio.kind", "pitched")
        H = len(meta.get("audio.harmonics", [0] * 8))
        n_bins = int(meta.get("audio.n_bins", 264))
        bps = int(meta.get("audio.bins_per_semitone", 3))
        w = lambda name: tensors[name + ".weight"]  # (C_out, C_in, KH, KW)
        trunk, i = [], 1
        while f"conv{i}.weight" in tensors:
            co, _, kh, kw = w(f"conv{i}").shape
            trunk.append((kw, kh, co))
            i += 1
        if kind == "pitched":
            cfg = dict(contour=(w("contour").shape[3], w("contour").shape[2]), pool=w("pool").shape[0],
                       note=w("note").shape[2], onset=w("onset").shape[2], offset=w("offset").shape[2])
            m = SurveilCNN(kind, H, n_bins, bps, n_bins // bps, trunk, pitched=cfg)
        else:
            classes = meta["audio.classes"]
            D = w("band").shape[2]
            tconv, i = [], 1
            while f"tconv{i}.weight" in tensors:
                tconv.append((w(f"tconv{i}").shape[2], w(f"tconv{i}").shape[0]))
                i += 1
            ow = w("onset")
            shared = not tconv and ow.shape[0] == 1 and ow.shape[1] == 1 and D == len(classes)
            cfg = dict(band=None if shared else D, tconv=tconv, onset_kh=ow.shape[2])
            m = SurveilCNN(kind, H, n_bins, bps, len(classes), trunk, drums=cfg)
        sd = {}
        for name, p in m.named_parameters():
            key = name[len("layers."):]
            if key not in tensors:
                raise ValueError(f"missing tensor {key}")
            sd[name] = torch.from_numpy(tensors[key].reshape(p.shape).copy())
        extra = set(tensors) - {n[len("layers."):] for n, _ in m.named_parameters()}
        if extra:
            raise ValueError(f"unexpected tensors {sorted(extra)}")
        m.load_state_dict(sd)
        return m

    def tensors(self) -> dict:
        """GGUF name -> float32 array (gguf.write layout)."""
        return {n[len("layers."):]: p.detach().cpu().float().numpy() for n, p in self.named_parameters()}

    # -- shape facts ---------------------------------------------------------

    @property
    def receptive_radius(self) -> int:
        L = self.layers
        r = sum(L[f"conv{i}"].weight.shape[2] // 2 for i in range(1, self.n_trunk + 1))
        if self.kind == "pitched":
            heads = max(L[h].weight.shape[2] // 2 for h in ("note", "onset", "offset"))
            return r + max(L["contour"].weight.shape[2] // 2, heads)
        r += sum(L[f"tconv{i}"].weight.shape[2] // 2 for i in range(1, self.n_tconv + 1))
        return r + L["onset"].weight.shape[2] // 2

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    # -- forward -------------------------------------------------------------

    def _q(self, x):
        return x.half().double() if self.ggml_parity else x

    def _conv(self, name, x, stride_freq=1):
        layer = self.layers[name]
        k = layer.weight
        pad = (k.shape[2] // 2, k.shape[3] // 2 if stride_freq == 1 else 0)
        y = F.conv2d(self._q(x), self._q(k), stride=(1, stride_freq), padding=pad).float()
        return y + layer.bias.view(1, -1, 1, 1)

    def forward(self, x):
        """x (N, harmonics, frames, bins) -> logits: {"onset": (N, frames, outputs)}
        and for pitched also "note", "offset" and "contour" (N, frames, bins).
        Each layer zero-pads in time: use `infer` for the binary's clip semantics."""
        h = x
        for i in range(1, self.n_trunk + 1):
            h = F.relu(self._conv(f"conv{i}", h))
        out = {}
        if self.kind == "pitched":
            out["contour"] = self._conv("contour", h)[:, 0]
            pooled = F.relu(self._conv("pool", h, self.bps))
            for head in ("note", "onset", "offset"):
                out[head] = self._conv(head, pooled)[:, 0]
            return out
        N, C, T, B = h.shape
        flat = h.permute(0, 2, 1, 3).reshape(N, T, C * B)  # row index channel * n_bins + bin
        bw, bb = self.layers["band"].weight, self.layers["band"].bias
        if self.ggml_parity:
            band = F.relu(F.linear(flat.double(), bw.double(), bb.double())).float()
        else:
            band = F.relu(F.linear(flat, bw, bb))  # (N, T, D)
        if self.shared_onset:
            on = self._conv_nobias("onset", band.unsqueeze(1))[:, 0] + self.layers["onset"].bias.view(1, 1, -1)
        else:
            z = band.permute(0, 2, 1).unsqueeze(-1)  # (N, D, T, 1)
            for i in range(1, self.n_tconv + 1):
                z = F.relu(self._conv(f"tconv{i}", z))
            on = self._conv("onset", z)[..., 0].permute(0, 2, 1)
        out["onset"] = on
        return out

    def _conv_nobias(self, name, x):
        k = self.layers[name].weight
        return F.conv2d(self._q(x), self._q(k), padding=(k.shape[2] // 2, 0)).float()

    def infer(self, x):
        """Likelihoods (sigmoid) as the binary computes them for a whole clip:
        zero frames beyond the clip edges, no layer padding inside them."""
        r = self.receptive_radius
        xp = F.pad(x, (0, 0, r, r))
        out = self.forward(xp)
        return {k: torch.sigmoid(v[:, r : v.shape[1] - r]) for k, v in out.items()}
