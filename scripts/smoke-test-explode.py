#!/usr/bin/env python3
"""End-to-end smoke test for backtrack-explode (standard library only).

    scripts/smoke-test-explode.py [path/to/backtrack-explode]

Runs the CLI, then starts the server and calls it the way Song Deck's separation adapter does
(packages/ai/src/adapters/separation-http.ts): GET /info for the stems, then POST /separate with
base64 audio. Checks that the stems sum back to the mix. The shared server rules (auth, CORS,
Host checks) are covered by smoke-test.py with Surveil.
"""

import base64
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module

smoke = import_module("smoke-test")  # free_port, request, as_json
ROOT = Path(__file__).resolve().parent.parent
BIN = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "backtrack-explode").resolve()
if not BIN.exists() and BIN.with_name(BIN.name + ".exe").exists():
    BIN = BIN.with_name(BIN.name + ".exe")
passed = failed = 0


def ok(cond, what):
    global passed, failed
    if cond:
        passed += 1
        print("ok   -", what)
    else:
        failed += 1
        print("FAIL -", what)


def write_mix(path, seconds, rate=44100):
    """Stereo: a centred voice-like tone, a low bass tone and a click on every beat."""
    frames = []
    for i in range(int(seconds * rate)):
        t = i / rate
        beat = t % 0.5
        voice = 0.2 * math.sin(2 * math.pi * 440 * t + 2 * math.sin(2 * math.pi * 5 * t))
        bass = 0.25 * math.sin(2 * math.pi * 55 * t)
        click = 0.5 * math.exp(-beat * 60) * math.sin(2 * math.pi * 80 * beat)
        l, r = voice + bass + click, voice + bass + click + 0.05 * math.sin(2 * math.pi * 660 * t)
        frames.append(struct.pack("<hh", int(max(-1, min(1, l)) * 32767), int(max(-1, min(1, r)) * 32767)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))


def read_wav(data):
    """(rate, channels, samples as floats) for 16-bit PCM and 32-bit float WAV bytes."""
    pos, fmt, raw = 12, None, b""
    while pos + 8 <= len(data):
        cid, n = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif cid == b"data":
            raw = data[pos + 8:pos + 8 + n]
        pos += 8 + n + (n & 1)
    tag, ch, rate = fmt[0], fmt[1], fmt[2]
    if tag == 3:
        return rate, ch, list(struct.unpack(f"<{len(raw) // 4}f", raw))
    return rate, ch, [v / 32768 for v in struct.unpack(f"<{len(raw) // 2}h", raw)]


def sum_error(mix, stems):
    """Largest |sum of the stems - mix| over all samples."""
    return max(abs(sum(s[i] for s in stems) - mix[i]) for i in range(len(mix)))


def main():
    if not BIN.exists():
        sys.exit(f"binary not found: {BIN} (build first)")
    tmp = Path(tempfile.mkdtemp(prefix="backtrack-explode-smoke-"))
    write_mix(tmp / "mix.wav", 3.0)
    _, _, mix = read_wav((tmp / "mix.wav").read_bytes())

    print("== CLI")
    v = subprocess.run([str(BIN), "--version"], capture_output=True, text=True)
    ok(v.returncode == 0 and v.stdout.startswith("backtrack-explode "), "--version")
    r = subprocess.run([str(BIN), str(tmp / "mix.wav"), "--format", "float32", "-o", str(tmp / "out"), "--json", "-"],
                       capture_output=True, text=True)
    meta = smoke.as_json(r.stdout)
    ok(r.returncode == 0 and meta and meta["stems"] == ["vocals", "drums", "bass", "other"], "separate writes the four default stems")
    stems = [read_wav((tmp / "out" / f"{n}.wav").read_bytes())[2] for n in ["vocals", "drums", "bass", "other"]]
    ok(all(len(s) == len(mix) for s in stems) and sum_error(mix, stems) < 1e-5, "CLI stems (float32) sum to the mix")
    r = subprocess.run([str(BIN), str(tmp / "mix.wav"), "--stems", "vocals,instrumental", "--augment", "-o", str(tmp / "two"),
                        "--json", "-"], capture_output=True, text=True)
    meta = smoke.as_json(r.stdout)
    ok(r.returncode == 0 and meta["stems"] == ["vocals", "instrumental"] and meta["passes"] == 2, "vocals + instrumental, two passes")
    r = subprocess.run([str(BIN), str(tmp / "mix.wav"), "--stems", "kazoo"], capture_output=True, text=True)
    ok(r.returncode == 1 and "cannot make the stem 'kazoo'" in r.stderr, "an unknown stem is an error")
    r = subprocess.run([str(BIN), "models", "--json"], capture_output=True, text=True)
    models = smoke.as_json(r.stdout) or []
    ok(models and models[0]["id"] == "placeholder" and models[0]["on_request_stems"] == ["instrumental"], "models --json")

    print("== server")
    port = smoke.free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, BACKTRACK_API_KEY="")
    srv = subprocess.Popen([str(BIN), "serve", "--port", str(port), "--quiet"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if smoke.request(base + "/health", timeout=1)[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        st, _, raw = smoke.request(base + "/health")
        ok(st == 200 and smoke.as_json(raw)["app"] == "explode", "GET /health")
        st, _, raw = smoke.request(base + "/info")
        info = smoke.as_json(raw)
        ok(st == 200 and info["role"] == "separation" and info["stems"] == ["vocals", "drums", "bass", "other"]
           and info["on_request_stems"] == ["instrumental"], "GET /info: stems and on_request_stems, as Song Deck reads them")
        st, _, raw = smoke.request(base + "/")
        ok(st == 200 and b'data-app="explode" data-theme="blast-zone"' in raw, "the page opens in Explode's theme (blast-zone)")

        audio = base64.b64encode((tmp / "mix.wav").read_bytes()).decode()

        def separate(**extra):
            return smoke.request(base + "/separate", {"audio_base64": audio, **extra})

        st, hdr, raw = separate(stems=["drums", "bass", "vocals", "other"])
        res = smoke.as_json(raw)
        ok(st == 200 and sorted(res["stems"]) == ["bass", "drums", "other", "vocals"] and res["model"] == "placeholder"
           and res["passes"] == 1, "POST /separate with Song Deck's default stems")
        ok(bool(hdr.get("X-Job-Id")), "X-Job-Id header")
        decoded = {k: read_wav(base64.b64decode(v)) for k, v in res["stems"].items()}
        ok(all(d[0] == 44100 and d[1] == 2 and len(d[2]) == len(mix) for d in decoded.values()), "stems are 44.1 kHz stereo WAV")
        # 16-bit stems: each is rounded on its own, so allow a few steps of 1/32768.
        ok(sum_error(mix, [d[2] for d in decoded.values()]) < 4e-4, "stems sum to the mix (16-bit)")
        st, _, raw = separate(stems=["vocals", "instrumental"], augment=True, format="float32")
        res = smoke.as_json(raw)
        ok(st == 200 and sorted(res["stems"]) == ["instrumental", "vocals"] and res["passes"] == 2, "vocals/instrumental with augment")
        ok(sum_error(mix, [read_wav(base64.b64decode(v))[2] for v in res["stems"].values()]) < 1e-5, "float32 stems sum to the mix")
        st, _, raw = separate(stems=["vocals"])
        ok(st == 200 and sorted(smoke.as_json(raw)["stems"]) == ["other", "vocals"], "a lone stem comes with 'other' holding the rest")
        st, _, raw = separate(passes=3)
        ok(st == 200 and smoke.as_json(raw)["passes"] == 3, "passes 3")

        st, _, raw = separate(stems=["kazoo"])
        ok(st == 400 and "kazoo" in smoke.as_json(raw)["error"], "an unknown stem is 400")
        st, _, raw = separate(stems=["other", "instrumental"])
        ok(st == 400, "other and instrumental together are 400")
        st, _, raw = separate(stems="vocals")
        ok(st == 400, "stems that are not an array are 400")
        st, _, raw = separate(model="htdemucs")
        ok(st == 404, "an unknown model is 404")
        st, _, raw = separate(passes=9)
        ok(st == 400, "passes out of range is 400")
        st, _, raw = smoke.request(base + "/separate", {"stems": ["vocals"]})
        ok(st == 400 and "audio_base64" in smoke.as_json(raw)["error"], "missing audio is 400")
        st, _, raw = smoke.request(base + "/separate", {"audio_base64": base64.b64encode(b"not audio").decode()})
        ok(st == 400, "undecodable audio is 400")

        # A long song, cancelled mid-job: the job answers 409 and the server stays healthy.
        write_mix(tmp / "long.wav", 60.0, rate=22050)
        long_audio = base64.b64encode((tmp / "long.wav").read_bytes()).decode()
        holder = {}
        t = threading.Thread(target=lambda: holder.setdefault(
            "r", smoke.request(base + "/separate", {"audio_base64": long_audio, "augment": True}, timeout=300)))
        t.start()
        stage, deadline = "", time.time() + 120
        while not stage.startswith("chunk") and time.time() < deadline:
            jobs = smoke.as_json(smoke.request(base + "/health")[2])["active_jobs"]
            stage = next((j.get("stage", "") for j in jobs if j["state"] == "running"), "")
            time.sleep(0.02)
        smoke.request(base + "/cancel", {})
        t.join(300)
        st = holder.get("r", (0,))[0]
        ok(stage.startswith("chunk") and ", pass " in stage, f"/health reports the chunk and pass ({stage!r})")
        ok(st == 409, f"POST /cancel stops a running job ({st})")
        ok(smoke.as_json(smoke.request(base + "/health")[2])["jobs"]["running"] == 0, "server healthy after cancel")
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()

    print(f"\npassed: {passed}, failed: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
