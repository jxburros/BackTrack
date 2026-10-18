#!/usr/bin/env python3
"""End-to-end smoke test for backtrack-surveil (standard library only).

    scripts/smoke-test.py [path/to/backtrack-surveil]

Generates test clips, runs the CLI, then starts the server and calls it the
way Song Deck's transcription adapter does (packages/ai/src/adapters/
transcription-http.ts): POST /transcribe with base64 audio, plus /info,
/health and /cancel, auth, CORS and the error statuses.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "backtrack-surveil").resolve()
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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(url, body=None, headers=None, method=None, timeout=120):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    if isinstance(body, (dict, list)):
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def as_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def matches(notes, truth, tol=0.06):
    """Every truth note has a detected note of the same pitch starting within tol seconds."""
    return all(any(n["pitch"] == t["pitch"] and abs(n["start"] - t["start"]) <= tol for n in notes) for t in truth)


def main():
    if not BIN.exists():
        sys.exit(f"binary not found: {BIN} (build first)")
    tmp = Path(tempfile.mkdtemp(prefix="backtrack-smoke-"))
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make-test-audio.py"), str(tmp)], check=True)
    truth = json.loads((tmp / "truth.json").read_text())

    print("== CLI")
    v = subprocess.run([str(BIN), "--version"], capture_output=True, text=True)
    ok(v.returncode == 0 and v.stdout.startswith("backtrack-surveil "), "--version")
    r = subprocess.run([str(BIN), str(tmp / "melody.wav"), "--stem", "piano", "-o", str(tmp / "m.mid"), "--json", str(tmp / "m.json")],
                       capture_output=True, text=True)
    ok(r.returncode == 0, "transcribe melody.wav")
    out = json.loads((tmp / "m.json").read_text())
    ok(matches(out["notes"], truth["melody"]) and len(out["notes"]) == len(truth["melody"]), "melody notes match")
    mid = (tmp / "m.mid").read_bytes()
    ok(mid[:4] == b"MThd" and b"MTrk" in mid, "MIDI written")
    r = subprocess.run([str(BIN), str(tmp / "bass.wav"), "--stem", "bass", "--key", "E minor", "--tempo", "100", "-o", "-", "--json", "-"],
                       capture_output=True, text=True)
    bass = as_json(r.stdout)
    ok(r.returncode == 0 and bass and [n["pitch"] for n in bass["notes"]] == [40, 45, 47, 40], "bass is monophonic and right")
    ok(bass and bass.get("key") == "E minor" and bass.get("tempo") == 100, "context echoed as key and tempo")
    r = subprocess.run([str(BIN), str(tmp / "melody.wav"), "--stem", "nope"], capture_output=True, text=True)
    ok(r.returncode == 1 and "unknown --stem" in r.stderr, "bad --stem is an error")
    placeholder = tmp / "random.gguf"
    r = subprocess.run([str(BIN), "make-placeholder", "--init", "random", "--seed", "3", "-o", str(placeholder)], capture_output=True, text=True)
    ok(r.returncode == 0 and placeholder.stat().st_size > 10000, "make-placeholder writes GGUF")
    r = subprocess.run([str(BIN), str(tmp / "melody.wav"), "--model", str(placeholder), "-o", "-", "--json", "-"], capture_output=True, text=True)
    ok(r.returncode == 0 and as_json(r.stdout)["model"] == "random", "runs a GGUF model from a file")
    r = subprocess.run([str(BIN), "models", "--json", "--models-dir", str(tmp)], capture_output=True, text=True)
    ids = [m["id"] for m in as_json(r.stdout) or []]
    ok(ids[:2] == ["placeholder", "placeholder-drums"] and "random" in ids, "models lists built-ins and the models dir")

    print("== server")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, BACKTRACK_API_KEY="")
    srv = subprocess.Popen([str(BIN), "serve", "--port", str(port), "--quiet", "--models-dir", str(tmp)], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if request(base + "/health", timeout=1)[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        st, _, raw = request(base + "/health")
        h = as_json(raw)
        ok(st == 200 and h["status"] == "ok" and h["app"] == "surveil" and h["auth_required"] is False, "GET /health")
        st, _, raw = request(base + "/info")
        info = as_json(raw)
        ok(st == 200 and info["role"] == "transcription" and any(m["id"] == "placeholder-drums" and m["stems"] == ["drums"] for m in info["models"]),
           "GET /info lists models and their stems")
        st, hdr, raw = request(base + "/")
        ok(st == 200 and b"<canvas" in raw and "text/html" in hdr.get("Content-Type", ""), "GET / serves the page")
        ok(b'data-app="surveil" data-theme="wiretap"' in raw, "the page opens in Surveil's theme (wiretap)")
        for path, kind in [("/backtrack.css", "text/css"), ("/app.js", "text/javascript"), ("/waveform.js", "text/javascript"),
                           ("/fonts/fonts.css", "text/css"), ("/fonts/vt323-400.woff2", "font/woff2")]:
            st, hdr, raw = request(base + path)
            ok(st == 200 and raw and hdr.get("Content-Type", "").startswith(kind), f"GET {path} serves the bundled file")

        def transcribe(name, **extra):
            body = {"audio_base64": base64.b64encode((tmp / name).read_bytes()).decode(), **extra}
            return request(base + "/transcribe", body)

        st, hdr, raw = transcribe("melody.wav", source="piano")
        res = as_json(raw)
        ok(st == 200 and matches(res["notes"], truth["melody"]), "POST /transcribe (piano)")
        ok(all(set(n) >= {"pitch", "start", "end", "velocity", "confidence"} and 0 <= n["confidence"] <= 1 and n["end"] > n["start"]
               for n in res["notes"]), "notes have the contract's fields")
        ok(bool(hdr.get("X-Job-Id")), "X-Job-Id header")
        st, _, raw = transcribe("bass.wav", source="bass", pitch_bends=True, context={"key": "E minor", "tempo": 100, "program": 34})
        res = as_json(raw)
        ok(st == 200 and [n["pitch"] for n in res["notes"]] == [40, 45, 47, 40] and res["notes"][0]["program"] == 34, "bass with context")
        ok(any("bend" in n for n in res["notes"]) and all(0 <= p[0] <= 1 for n in res["notes"] for p in n.get("bend", [])), "pitch bends")
        st, _, raw = transcribe("drums.wav", source="drums")
        res = as_json(raw)
        ok(st == 200 and res["model"] == "placeholder-drums" and {n["pitch"] for n in res["notes"]} >= {36, 42}, "drums go to the drum model")
        st, _, raw = transcribe("drums.wav", source="mix", model="drums")
        ok(st == 200 and as_json(raw)["model"] == "placeholder-drums", "model 'drums' picks the drum model")
        st, _, raw = transcribe("melody.wav", include_midi=True)
        ok(st == 200 and base64.b64decode(as_json(raw)["midi_base64"])[:4] == b"MThd", "include_midi returns MIDI")

        st, _, raw = request(base + "/transcribe", {"source": "mix"})
        ok(st == 400 and "audio_base64" in as_json(raw)["error"], "missing audio is 400")
        st, _, raw = request(base + "/transcribe", {"audio_base64": base64.b64encode(b"not audio at all").decode()})
        ok(st == 400 and "error" in as_json(raw), "undecodable audio is 400")
        st, _, raw = transcribe("melody.wav", model="no-such-model")
        ok(st == 404, "unknown model is 404")
        st, _, raw = transcribe("melody.wav", context={"key": "Z flat"})
        ok(st == 400 and "context.key" in as_json(raw)["error"], "bad context is 400")
        st, _, raw = request(base + "/transcribe", b"{not json", headers={"Content-Type": "application/json"})
        ok(st == 400, "malformed JSON is 400")
        st, _, raw = request(base + "/nope")
        ok(st == 404 and "error" in as_json(raw), "unknown endpoint is a JSON 404")
        st, _, _ = request(base + "/cancel", {})
        ok(st == 204, "POST /cancel")

        st, hdr, _ = request(base + "/info", headers={"Origin": "http://localhost:5173"})
        ok(st == 200 and hdr.get("Access-Control-Allow-Origin") == "http://localhost:5173", "CORS for the Song Deck studio")
        st, hdr, _ = request(base + "/transcribe", method="OPTIONS", headers={"Origin": "http://127.0.0.1:7788",
                                                                             "Access-Control-Request-Method": "POST"})
        ok(st == 204 and "POST" in hdr.get("Access-Control-Allow-Methods", ""), "CORS preflight")
        st, _, _ = request(base + "/info", headers={"Origin": "https://evil.example"})
        ok(st == 403, "other origins are refused")
        st, _, _ = request(base + "/info", headers={"Origin": f"http://127.0.0.1:{port}"})
        ok(st == 200, "the page's own origin is allowed")
        st, _, _ = request(base + "/info", headers={"Host": "attacker.example"})
        ok(st == 403, "DNS-rebinding Host is refused")

        # A long clip, cancelled mid-job: the job answers 409 and the server stays healthy.
        long_wav = tmp / "long.wav"
        import wave
        with wave.open(str(tmp / "melody.wav")) as w:
            params, frames = w.getparams(), w.readframes(w.getnframes())
        with wave.open(str(long_wav), "wb") as w:
            w.setparams(params)
            w.writeframes(frames * 60)
        holder = {}
        t = threading.Thread(target=lambda: holder.setdefault("r", transcribe("long.wav", source="piano")))
        t.start()
        running = False
        deadline = time.time() + 120  # the upload and JSON parse come first (slow under sanitizers)
        while not running and time.time() < deadline:
            jobs = as_json(request(base + "/health")[2])["active_jobs"]
            running = any(j["state"] == "running" for j in jobs)
            time.sleep(0.02)
        st, hdr, _ = request(base + "/cancel", {})
        t.join(300)  # sanitizer builds are slow; cancellation is checked per chunk
        st = holder.get("r", (0,))[0]
        ok(running and st == 409, f"POST /cancel stops a running job ({st})")
        h = as_json(request(base + "/health")[2])
        ok(h["status"] == "ok" and h["jobs"]["running"] == 0, "server healthy after cancel")
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()

    print("== server with an API key")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    srv = subprocess.Popen([str(BIN), "serve", "--port", str(port), "--quiet", "--api-key", "s3cret"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if request(base + "/health", timeout=1)[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        ok(as_json(request(base + "/health")[2])["auth_required"] is True, "/health needs no key and says one is required")
        ok(request(base + "/info")[0] == 401, "no key is 401")
        ok(request(base + "/info", headers={"Authorization": "Bearer wrong"})[0] == 401, "wrong key is 401")
        ok(request(base + "/info", headers={"Authorization": "Bearer s3cret"})[0] == 200, "right key works")
        ok(all(request(base + p)[0] == 200 for p in ["/", "/app.css", "/fonts/space-mono-400.woff2"]), "the page's files need no key")
    finally:
        srv.terminate()
        srv.wait(10)

    print(f"\npassed: {passed}, failed: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
