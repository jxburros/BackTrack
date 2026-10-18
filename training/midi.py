"""Standard MIDI File reading (notes in seconds, through the tempo map) and a
minimal writer for the synthetic dataset."""

from __future__ import annotations

import struct
from pathlib import Path


def _vlq(d: bytes, p: int) -> tuple[int, int]:
    v = 0
    while True:
        b = d[p]
        p += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, p


def read_notes(path) -> list[dict]:
    """Every note as {"pitch", "start", "end", "velocity", "channel"} (seconds)."""
    d = Path(path).read_bytes()
    if d[:4] != b"MThd":
        raise ValueError(f"{path}: not a MIDI file")
    hlen = struct.unpack(">I", d[4:8])[0]
    _, ntrk, div = struct.unpack(">HHH", d[8:14])
    p = 8 + hlen
    events = []  # (tick, order, kind, data)
    order = 0
    for _ in range(ntrk):
        if d[p : p + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", d[p + 4 : p + 8])[0]
        q, end = p + 8, p + 8 + tlen
        tick, status = 0, 0
        while q < end:
            delta, q = _vlq(d, q)
            tick += delta
            b = d[q]
            if b == 0xFF:
                typ = d[q + 1]
                n, q = _vlq(d, q + 2)
                if typ == 0x51 and n == 3:
                    events.append((tick, order, "tempo", int.from_bytes(d[q : q + 3], "big")))
                q += n
            elif b in (0xF0, 0xF7):
                n, q = _vlq(d, q + 1)
                q += n
            else:
                if b & 0x80:
                    status = b
                    q += 1
                kind = status & 0xF0
                nbytes = 1 if kind in (0xC0, 0xD0) else 2
                data = d[q : q + nbytes]
                q += nbytes
                if kind in (0x80, 0x90):
                    on = kind == 0x90 and data[1] > 0
                    events.append((tick, order, "on" if on else "off", (status & 0x0F, data[0], data[1])))
            order += 1
        p = end
    events.sort(key=lambda e: (e[0], e[1]))
    if div & 0x8000:
        fps = 256 - (div >> 8)
        sec_per_tick = lambda _tempo: 1.0 / (fps * (div & 0xFF))
    else:
        sec_per_tick = lambda tempo: tempo / 1e6 / div
    tempo, last_tick, now = 500000, 0, 0.0
    open_notes: dict = {}
    notes = []
    for tick, _, kind, data in events:
        now += (tick - last_tick) * sec_per_tick(tempo)
        last_tick = tick
        if kind == "tempo":
            tempo = data
        elif kind == "on":
            open_notes.setdefault((data[0], data[1]), []).append((now, data[2]))
        else:
            stack = open_notes.get((data[0], data[1]))
            if stack:
                start, vel = stack.pop(0)
                notes.append({"pitch": data[1], "start": start, "end": now, "velocity": vel, "channel": data[0]})
    for (ch, pitch), stack in open_notes.items():
        for start, vel in stack:
            notes.append({"pitch": pitch, "start": start, "end": start + 0.1, "velocity": vel, "channel": ch})
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def write_notes(path, notes: list[dict], tempo_bpm: float = 120.0, channel: int = 9, ppq: int = 480):
    """A type-0 file with one tempo; notes as {"pitch", "start", "end", "velocity"}."""
    tick = lambda s: int(round(s * ppq * tempo_bpm / 60.0))
    ev = []
    for n in notes:
        ev.append((tick(n["start"]), 1, bytes([0x90 | channel, n["pitch"], max(1, min(127, int(n["velocity"])))])))
        ev.append((tick(max(n["end"], n["start"] + 0.01)), 0, bytes([0x80 | channel, n["pitch"], 0])))
    ev.sort(key=lambda e: (e[0], e[1]))
    body = bytearray(b"\x00\xFF\x51\x03" + int(round(60e6 / tempo_bpm)).to_bytes(3, "big"))
    last = 0
    for t, _, msg in ev:
        body += _enc_vlq(t - last) + msg
        last = t
    body += b"\x00\xFF\x2F\x00"
    Path(path).write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + b"MTrk" + struct.pack(">I", len(body)) + bytes(body))


def _enc_vlq(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))
