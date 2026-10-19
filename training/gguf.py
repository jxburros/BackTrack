"""A small GGUF (version 3) reader and writer: F32 tensors and the metadata
types BackTrack models use (docs/gguf-metadata.md). No dependency beyond NumPy.

Tensors are NumPy arrays in C order with the ggml shape reversed: ggml
ne = [KW, KH, C_in, C_out] is an array of shape (C_out, C_in, KH, KW), which
is also PyTorch's Conv2d weight layout over (time, frequency).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAGIC = b"GGUF"
ALIGN = 32
T_U8, T_I8, T_U16, T_I16, T_U32, T_I32, T_F32, T_BOOL, T_STR, T_ARR, T_U64, T_I64, T_F64 = range(13)
_FMT = {T_U8: "<B", T_I8: "<b", T_U16: "<H", T_I16: "<h", T_U32: "<I", T_I32: "<i", T_F32: "<f", T_BOOL: "<?",
        T_U64: "<Q", T_I64: "<q", T_F64: "<d"}
GGML_F32 = 0


class Typed:
    """A metadata value with an explicit GGUF type."""

    gguf_type = T_STR

    def __init__(self, value, gguf_type=None):
        self.value = value
        if gguf_type is not None:
            self.gguf_type = gguf_type

    def __repr__(self):
        return f"{type(self).__name__}({self.value!r})"


class U32(Typed):
    gguf_type = T_U32


class I32(Typed):
    gguf_type = T_I32


class F32(Typed):
    gguf_type = T_F32


def _type_of(v) -> int:
    if isinstance(v, Typed):
        return v.gguf_type
    if isinstance(v, bool):
        return T_BOOL
    if isinstance(v, str):
        return T_STR
    if isinstance(v, int):
        return T_U32 if v >= 0 else T_I32
    if isinstance(v, float):
        return T_F32
    if isinstance(v, (list, tuple)):
        return T_ARR
    raise TypeError(f"no GGUF type for {v!r}")


def _raw(v):
    return v.value if isinstance(v, Typed) else v


def _put_str(out: bytearray, s: str):
    b = s.encode("utf-8")
    out += struct.pack("<Q", len(b)) + b


def _put_val(out: bytearray, t: int, v):
    if t == T_STR:
        _put_str(out, _raw(v))
    elif t == T_ARR:
        if not v:
            raise ValueError("empty GGUF arrays have no element type")
        et = _type_of(v[0])
        out += struct.pack("<IQ", et, len(v))
        for e in v:
            _put_val(out, et, e)
    else:
        out += struct.pack(_FMT[t], _raw(v))


def write(path, meta: dict, tensors: dict):
    """meta: key -> value (str, bool, Typed, or a list of them); tensors:
    name -> float32 array (C order, ggml shape reversed). Trailing size-1
    ggml dimensions are dropped, as ggml_new_tensor does for the C++ placeholder."""
    out = bytearray(MAGIC)
    out += struct.pack("<IQQ", 3, len(tensors), len(meta))
    for k, v in meta.items():
        _put_str(out, k)
        t = _type_of(v)
        out += struct.pack("<I", t)
        _put_val(out, t, v)
    blobs, offset = [], 0
    for name, arr in tensors.items():
        a = np.ascontiguousarray(arr, dtype="<f4")
        ne = list(a.shape[::-1]) or [1]
        while len(ne) > 1 and ne[-1] == 1:
            ne.pop()
        _put_str(out, name)
        out += struct.pack("<I", len(ne)) + struct.pack(f"<{len(ne)}Q", *ne)
        out += struct.pack("<IQ", GGML_F32, offset)
        b = a.tobytes()
        blobs.append(b)
        offset += len(b) + (-len(b)) % ALIGN
    out += b"\0" * ((-len(out)) % ALIGN)
    for b in blobs:
        out += b + b"\0" * ((-len(b)) % ALIGN)
    Path(path).write_bytes(bytes(out))


class _Reader:
    def __init__(self, data: bytes):
        self.d, self.p = data, 0

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += struct.calcsize(fmt)
        return v[0] if len(v) == 1 else v

    def string(self):
        n = self.take("<Q")
        s = self.d[self.p : self.p + n].decode("utf-8")
        self.p += n
        return s

    def value(self, t, typed=False):
        if t == T_STR:
            return self.string()
        if t == T_ARR:
            et, n = self.take("<IQ")
            return [self.value(et, typed) for _ in range(n)]
        v = self.take(_FMT[t])
        return Typed(v, t) if typed and t != T_BOOL else v


def read(path, typed: bool = False) -> tuple[dict, dict]:
    """Returns (meta, tensors): plain Python values (with typed=True, numbers
    as Typed so write() keeps their GGUF types), and name -> float32 array
    with ggml's shape reversed and padded to 4 dimensions."""
    data = Path(path).read_bytes()
    if data[:4] != MAGIC:
        raise ValueError(f"{path}: not a GGUF file")
    r = _Reader(data)
    r.p = 4
    version, n_tensors, n_kv = r.take("<IQQ")
    if version not in (2, 3):
        raise ValueError(f"{path}: GGUF version {version} is not supported")
    meta = {}
    for _ in range(n_kv):
        k = r.string()
        meta[k] = r.value(r.take("<I"), typed)
    align = int(_raw(meta.get("general.alignment", ALIGN)))
    infos = []
    for _ in range(n_tensors):
        name = r.string()
        nd = r.take("<I")
        ne = [r.take("<Q") for _ in range(nd)]
        typ, off = r.take("<IQ")
        if typ != GGML_F32:
            raise ValueError(f"{path}: tensor {name} is not F32")
        infos.append((name, ne, off))
    base = r.p + (-r.p) % align
    tensors = {}
    for name, ne, off in infos:
        ne4 = (ne + [1, 1, 1, 1])[:4]
        n = int(np.prod(ne4))
        a = np.frombuffer(data, "<f4", n, base + off).reshape(ne4[::-1]).astype(np.float32)
        tensors[name] = a
    return meta, tensors
