"""The drum model's training pipeline, one command from songs to a report:

    python3 -m training.pipeline --config training/pipeline.example.json \\
        [--steps render,perform,train,evaluate,parity,report] [--dry-run] [--run latest|ID] [--force]
    python3 -m training.pipeline schedule --print linux|macos|windows --config FILE [--time 02:30]

Steps (docs/training-at-home.md walks through all of it):

  render    Song Deck's `npm run dataset:render` into the dataset directory; only songs the
            manifest lacks are rendered (the same seed and options give the same songs), so
            a bigger `render.songs` adds songs and keeps everything already there.
  perform   Song Deck's `npm run dataset:perform` (ACE-Step plays each song; --skip-existing).
  train     python3 -m training.train_drums into runs/<id>/.
  evaluate  python3 -m training.evaluate through the binary on the separated test split (and
            the performed test windows), for this run's model and for runs/best; Song Deck's
            `npm run eval:datasets` with its built-in transcriber and this model served by
            `backtrack-surveil serve`, as the baseline.
  parity    python3 -m training.parity on this run's model.
  report    runs/<id>/report.md and report.json; promotes the model to runs/best/ when it beats
            the previous best on the separated test split. weights/ is never written.

Every step streams its output to the console and runs/<id>/<step>.log; the first step that fails
stops the run (non-zero exit), and `--run latest` resumes it from that step. A lock file in the
runs directory keeps two runs from overlapping. Python standard library only (plus NumPy, which
training needs anyway, to read the model's metadata for the report).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STEPS = ["render", "perform", "train", "evaluate", "parity", "report", "rejudge"]
WINDOWS = os.name == "nt"

# Sizes from the docs (Song Deck docs/TRAINING-DATA.md, training/README.md): 16-bit stereo
# 44.1 kHz is about 10.6 MB per minute per file; the feature cache about 0.25 GB per hour of audio
# per --audio input.
MB_PER_FILE_MINUTE = 10.6
CACHE_MB_PER_MINUTE = 250 / 60
RENDER_TAIL_SECONDS = 1.5

DEFAULTS: dict = {
    "songdeck": "../Song-Deck",
    "backtrack_binary": "build/backtrack-surveil",
    "python": None,
    "npm": None,
    "dataset": "~/backtrack-training/sd-drums",
    "runs_dir": "~/backtrack-training/runs",
    "low_priority": True,
    "model": {"name": "Surveil drums", "version": "0.1.0", "file_stem": "surveil-drums"},
    "render": {
        "songs": 400, "grow_per_run": 0, "max_songs": 0, "seconds": 60, "genres": None, "seed": 1000,
        "only_stems": "drums", "separate": True, "keep_separated": ["drums"], "sample_rate": 44100,
        "chunk": 50, "jobs": None, "soundbank": None, "extra_args": [],
    },
    "perform": {
        "enabled": True, "provider": "ace-step-api", "url": "http://127.0.0.1:8001", "strength": 0.35,
        "takes": 1, "split": "train,valid,test", "songs": None, "seed": None, "window": 4,
        "min_offset_strength": 0.2, "timeout_minutes": 20, "allow_failed_takes": True, "optional": False,
        "judge": "auto", "rejudge_after_promotion": True, "extra_args": [],
        "server": {"command": None, "cwd": None, "startup_timeout_minutes": 15, "stop_after": True},
    },
    "train": {
        "audio": "stem,separated,performed", "epochs": 30, "batch": 16, "device": "auto", "min_agreement": 0.6,
        "min_offset_strength": 0.2, "patience": None, "minutes": 0, "threads": None, "seed": 0,
        "steps_per_epoch": None, "resume": True, "skip_if_unchanged": True, "egmd": None,
        "egmd_limit": None, "extra_args": [],
    },
    "evaluate": {
        "split": "test", "performed": "auto", "previous_best": True, "songdeck_baseline": True,
        "serve_port": 0, "songdeck_extra_args": [],
    },
    "promote": {"metric": "mean_class_f", "min_improvement": 0.0, "require_parity": True},
    "disk": {"reserve_gb": 5},
}


class StepFailed(Exception):
    pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _merge(base: dict, over: dict, where: str) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k.startswith("_"):
            continue  # "_comment" keys
        if k not in base:
            raise SystemExit(f"config: unknown key {where}{k} (known: {', '.join(sorted(base))})")
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v, f"{where}{k}.")
        else:
            out[k] = v
    return out


def load_config(path: str) -> dict:
    p = Path(path).expanduser()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"config {p} not found")
    except json.JSONDecodeError as e:
        raise SystemExit(f"config {p}: not valid JSON ({e})")
    cfg = _merge(DEFAULTS, raw, "")
    cfg["_path"] = str(p.resolve())
    return cfg


def resolve(p) -> Path:
    """~ expanded; relative paths are relative to the BackTrack checkout."""
    q = Path(os.path.expandvars(str(p))).expanduser()
    return (q if q.is_absolute() else REPO / q).resolve()


def find_binary(cfg) -> Path:
    b = resolve(cfg["backtrack_binary"])
    cands = [b]
    if WINDOWS:
        cands = [b.with_suffix(".exe") if b.suffix != ".exe" else b, b.parent / "Release" / (b.stem + ".exe"), b]
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


def program(v) -> str:
    """A program from the config: a bare name stays a name (looked up on PATH), a path is resolved."""
    v = str(v)
    return str(resolve(v)) if ("/" in v or "\\" in v or v.startswith("~")) else v


def npm_cmd(cfg) -> str:
    if cfg["npm"]:
        return program(cfg["npm"])
    found = shutil.which("npm.cmd" if WINDOWS else "npm") or shutil.which("npm")
    return found or ("npm.cmd" if WINDOWS else "npm")


def python_cmd(cfg) -> str:
    return program(cfg["python"]) if cfg["python"] else sys.executable


def as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(s) for s in v]


def show(cmd) -> str:
    return subprocess.list2cmdline(cmd) if WINDOWS else shlex.join(str(c) for c in cmd)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self, run_id: str) -> None:
        info = {"pid": os.getpid(), "host": socket.gethostname(), "started": now_iso(), "run": run_id}
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                other = read_json(self.path, {}) or {}
                if other.get("host") == info["host"] and not pid_alive(int(other.get("pid", 0))):
                    print(f"[pipeline] removing a stale lock (process {other.get('pid')} is gone): {self.path}")
                    self.path.unlink(missing_ok=True)
                    continue
                raise SystemExit(
                    f"[pipeline] another run holds {self.path} (process {other.get('pid')} on {other.get('host')}, "
                    f"started {other.get('started')}, run {other.get('run')}). If no run is going, delete that file.")
            with os.fdopen(fd, "w") as f:
                json.dump(info, f)
            self.held = True
            return
        raise SystemExit(f"[pipeline] could not take the lock {self.path}")

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


# ---------------------------------------------------------------------------
# Running commands
# ---------------------------------------------------------------------------


class Runner:
    def __init__(self, cfg, run_dir: Path, dry_run: bool):
        self.cfg, self.run_dir, self.dry = cfg, run_dir, dry_run
        self.log = None  # the current step's log file
        self.child = None
        self.tail: list[str] = []  # the last lines the last command printed
        self.capture: str | None = None  # also keep every line containing this
        self.captured: list[str] = []

    def say(self, msg: str) -> None:
        line = f"[pipeline] {msg}"
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def env(self) -> dict:
        e = dict(os.environ)
        e.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "FORCE_COLOR": "0", "NO_COLOR": "1"})
        return e

    def popen_args(self, new_group=False) -> dict:
        kw = {}
        if WINDOWS:
            flags = 0
            if self.cfg["low_priority"]:
                flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
            if new_group:
                flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            kw["creationflags"] = flags
        else:
            if self.cfg["low_priority"]:
                kw["preexec_fn"] = lambda: os.nice(10)
            if new_group:
                kw["start_new_session"] = True
        return kw

    def run(self, cmd, cwd: Path, check=True) -> int:
        cmd = [str(c) for c in cmd]
        self.say(f"$ {show(cmd)}" + (f"    (in {cwd})" if cwd != REPO else ""))
        self.tail, self.captured = [], []
        if self.dry:
            return 0
        try:
            self.child = subprocess.Popen(cmd, cwd=cwd, env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, **self.popen_args())
        except FileNotFoundError:
            raise StepFailed(f"could not start {cmd[0]!r}: not found (is it installed and on PATH?)")
        assert self.child.stdout
        for raw in iter(self.child.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            self.tail = (self.tail + [line.rstrip()])[-40:]
            if self.capture is not None and self.capture in line:
                self.captured.append(line.rstrip())
            if self.log:
                self.log.write(line)
                self.log.flush()
        code = self.child.wait()
        self.child = None
        if check and code != 0:
            raise StepFailed(f"{Path(cmd[0]).name} {' '.join(cmd[1:4])} ... exited with code {code}")
        return code

    def background(self, cmd, cwd: Path, log_path: Path, env=None):
        """A server for the length of a step; its output goes to its own log."""
        cmd = [str(c) for c in cmd]
        self.say(f"$ {show(cmd)} &    (in {cwd}; output in {log_path.name})")
        if self.dry:
            return None
        cwd.mkdir(parents=True, exist_ok=True)
        f = open(log_path, "ab")
        try:
            return subprocess.Popen(cmd, cwd=cwd, env=env or self.env(), stdout=f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, **self.popen_args(new_group=True))
        except FileNotFoundError:
            raise StepFailed(f"could not start {cmd[0]!r}: not found")
        finally:
            f.close()

    def stop(self, proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            if not WINDOWS:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(10)

    def npm(self, script: str, args) -> int:
        return self.run([npm_cmd(self.cfg), "run", script, "--", *args], cwd=resolve(self.cfg["songdeck"]))

    def py(self, module: str, args, check=True) -> int:
        return self.run([python_cmd(self.cfg), "-m", module, *args], cwd=REPO, check=check)


def http_up(url: str, timeout=5.0) -> bool:
    """True if anything answers HTTP at url (any status); proxies are bypassed (local servers)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(url, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def http_json(url: str, timeout=10.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_gb(path: Path) -> float:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def module_flags(cfg, module: str) -> str:
    """The --help text of a training module (to check it knows the flags we pass)."""
    r = subprocess.run([python_cmd(cfg), "-m", module, "--help"], cwd=REPO, capture_output=True, text=True, check=False)
    return r.stdout


# ---------------------------------------------------------------------------
# The dataset (contract A: manifest.json written by npm run dataset:render)
# ---------------------------------------------------------------------------


def song_id(index: int, genre: str, seed: int) -> str:
    # Song Deck scripts/dataset/lib.ts: songIdFor / songSeedFor; render.ts: genre = genres[i % n].
    return f"{index + 1:04d}-{genre}-s{seed}"


def expected_songs(n: int, genres: list[str], seed: int) -> list[tuple[int, str, int, str]]:
    out = []
    for i in range(n):
        g, s = genres[i % len(genres)], (seed + i) & 0xFFFFFFFF
        out.append((i, g, s, song_id(i, g, s)))
    return out


def song_complete(ds: Path, song: dict) -> bool:
    files = [song.get("mix")] + [s.get("audio") for s in song.get("stems", [])]
    files += list(((song.get("separated") or {}).get("stems") or {}).values())
    return all(f and (ds / f).is_file() for f in files)


def manifest_path(ds: Path) -> Path:
    return ds / "manifest.json"


def perf_list(song: dict) -> list:
    return song.get("performances") or []


def render_minutes(rc) -> float:
    return ((rc["seconds"] or 180) + RENDER_TAIL_SECONDS) / 60


def render_mb_per_song(cfg) -> tuple[float, float]:
    """(MB a song keeps, with its feature cache; MB more it holds until its chunk is pruned)."""
    rc = cfg["render"]
    stems = as_list(rc["only_stems"])
    n_stems = 1.2 if stems == ["drums"] else (len(stems) * 1.2 if stems else 6)  # some songs have two kits
    sep = 4 if rc["separate"] else 0
    kept_sep = min(sep, len(as_list(rc["keep_separated"])) or sep)
    file_mb = MB_PER_FILE_MINUTE * (2 if "--float" in as_list(rc["extra_args"]) else 1) * render_minutes(rc)
    inputs = [a for a in as_list(cfg["train"]["audio"]) if a != "performed"]
    kept = file_mb * (1 + n_stems + kept_sep) + CACHE_MB_PER_MINUTE * render_minutes(rc) * len(inputs)
    return kept, file_mb * (sep - kept_sep)


def render_need_mb(cfg, songs: int) -> float:
    kept, transient = render_mb_per_song(cfg)
    return songs * kept + min(songs, int(cfg["render"]["chunk"])) * transient


def perform_mb_per_take(cfg) -> float:
    # The performance and its separated drums (ACE-Step may answer at 48 kHz or in float: x1.5),
    # plus their feature cache.
    m = render_minutes(cfg["render"])
    return MB_PER_FILE_MINUTE * 2 * 1.5 * m + CACHE_MB_PER_MINUTE * m


def check_disk(r: Runner, where: Path, need_mb: float, what: str) -> None:
    reserve = float(r.cfg["disk"]["reserve_gb"])
    have = free_gb(where)
    need = need_mb / 1000
    r.say(f"disk: {what} needs about {need:.1f} GB; {have:.1f} GB free at {where} (keeping {reserve:g} GB spare)")
    if have < need + reserve and not r.dry:
        raise StepFailed(f"not enough free disk for {what}: about {need:.1f} GB + {reserve:g} GB spare needed, "
                         f"{have:.1f} GB free at {where}. Free some space, move the dataset (config \"dataset\"), "
                         f"or lower render.songs / perform.takes.")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class Pipeline:
    def __init__(self, cfg, run_dir: Path, args):
        self.cfg, self.run_dir, self.args = cfg, run_dir, args
        self.dry = args.dry_run
        self.r = Runner(cfg, run_dir, self.dry)
        self.ds = resolve(cfg["dataset"])
        self.runs = resolve(cfg["runs_dir"])
        self.state_path = run_dir / "state.json"
        self.state = read_json(self.state_path, None) or {"run": run_dir.name, "created": now_iso(), "config": cfg["_path"],
                                                           "steps": {}, "outputs": {}}

    # -- bookkeeping --------------------------------------------------------

    def save(self) -> None:
        if not self.dry:
            write_json(self.state_path, self.state)

    @property
    def out(self) -> dict:
        return self.state["outputs"]

    def candidate(self) -> Path:
        return self.run_dir / f"{self.cfg['model']['file_stem']}-{self.cfg['model']['version']}.gguf"

    def manifest(self) -> dict | None:
        return read_json(manifest_path(self.ds), None)

    def best_dir(self) -> Path:
        return self.runs / "best"

    def best_model(self) -> Path | None:
        info = read_json(self.best_dir() / "best.json", None)
        if not info:
            return None
        p = self.best_dir() / info["model"]
        return p if p.is_file() else None

    # -- render -------------------------------------------------------------

    def render_args(self, out: Path, songs: int, seed: int, genres) -> list[str]:
        rc = self.cfg["render"]
        a = ["--out", str(out), "--songs", str(songs), "--seed", str(seed)]
        if genres:
            a += ["--genres", ",".join(genres)]
        if rc["seconds"]:
            a += ["--seconds", str(rc["seconds"])]
        if rc["sample_rate"]:
            a += ["--sample-rate", str(rc["sample_rate"])]
        if as_list(rc["only_stems"]):
            a += ["--only-stems", ",".join(as_list(rc["only_stems"]))]
        if rc["separate"]:
            a.append("--separate")
        if rc["jobs"]:
            a += ["--jobs", str(rc["jobs"])]
        if rc["soundbank"]:
            a += ["--soundbank", str(resolve(rc["soundbank"]))]
        return a + as_list(rc["extra_args"])

    def step_render(self) -> dict:
        """dataset:render --skip-existing in chunks: Song Deck keeps the songs already there (and
        their performances) and renders the rest; after each chunk the separated stems other than
        render.keep_separated are deleted, so at most one chunk of them is on disk at a time."""
        rc = self.cfg["render"]
        man = self.manifest()
        have = len(man["songs"]) if man else 0
        target = self.out.get("render_target")
        if target is None:
            target = int(rc["songs"])
            if rc["grow_per_run"] and have:
                target = max(target, have + int(rc["grow_per_run"]))
            if rc["max_songs"]:
                target = min(target, int(rc["max_songs"]))
            self.out["render_target"] = target
            self.save()
        chunk = max(1, int(rc["chunk"]))
        genres = as_list(rc["genres"]) or (man or {}).get("options", {}).get("genres")

        def complete_upto(k: int) -> bool:
            """Are songs 1..k in the manifest with their files? (unknown before the first render)"""
            m = self.manifest()
            if not m or not genres:
                return False
            by_id = {s["id"]: s for s in m["songs"]}
            return all(e[3] in by_id and song_complete(self.ds, by_id[e[3]]) for e in expected_songs(k, genres, int(rc["seed"])))

        if complete_upto(target):
            self.r.say(f"dataset complete: {target} songs at {self.ds}; nothing to render")
            return {"rendered": 0, "songs": have}
        todo = target - (sum(1 for s in man["songs"] if song_complete(self.ds, s)) if man else 0)
        check_disk(self.r, self.ds, render_need_mb(self.cfg, max(1, todo)), f"rendering about {max(1, todo)} songs")
        t0, before = time.time(), have
        for k in list(range(chunk, target, chunk)) + [target]:
            if not self.dry and complete_upto(k):
                continue
            self.r.say(f"songs 1..{k} of {target}")
            self.r.npm("dataset:render", self.render_args(self.ds, k, rc["seed"], as_list(rc["genres"])) + ["--skip-existing"])
            if self.dry:
                continue
            man = self.manifest()
            if not man:
                raise StepFailed("dataset:render wrote no manifest.json; see render.log")
            genres = genres or man.get("options", {}).get("genres")
            self.prune(man)
        man = self.manifest() or {"songs": []}
        added = len(man["songs"]) - before
        return {"rendered": added, "songs": len(man["songs"]), "seconds": time.time() - t0}

    def prune(self, man: dict) -> None:
        """Delete separated stems other than render.keep_separated (drums) to save disk."""
        keep = set(as_list(self.cfg["render"]["keep_separated"]))
        if not keep or not self.cfg["render"]["separate"]:
            return
        freed, changed = 0, False
        for s in man["songs"]:
            stems = (s.get("separated") or {}).get("stems")
            if not stems or not set(stems) - keep:
                continue
            changed = True
            for name in [n for n in stems if n not in keep]:
                f = self.ds / stems.pop(name)
                if f.exists():
                    freed += f.stat().st_size
                    f.unlink()
        if not changed:
            return
        man.setdefault("options", {})["pruned"] = f"separated stems other than {', '.join(sorted(keep))} removed to save disk"
        write_json(manifest_path(self.ds), man)
        if freed:
            self.r.say(f"pruned separated stems other than {', '.join(sorted(keep))}: {freed / 1e9:.2f} GB freed")

    # -- perform ------------------------------------------------------------

    def count_takes(self) -> int:
        man = self.manifest() or {"songs": []}
        return sum(len(perf_list(s)) for s in man["songs"])

    def pending_takes(self) -> int:
        pc = self.cfg["perform"]
        man = self.manifest() or {"songs": []}
        splits = set(as_list(pc["split"]))
        songs = [s for s in man["songs"] if s.get("split") in splits]
        if pc["songs"]:
            songs = songs[: int(pc["songs"])]
        return sum(max(0, int(pc["takes"]) - len(perf_list(s))) for s in songs) if songs else 0

    def judge_model(self) -> Path | None:
        """The model that judges agreement: runs/best's when it beat Song Deck's built-in drum
        transcription in its report (a weak early model would judge worse), else weights/'s."""
        best = self.best_model()
        if best:
            info = read_json(self.best_dir() / "best.json", {}) or {}
            if info.get("beats_internal"):
                return best
            self.r.say("runs/best's model did not beat Song Deck's built-in transcription in its report: not a judge")
        w = sorted((REPO / "weights").glob("surveil-drums-*.gguf"))
        return w[-1] if w else None

    def songdeck_knows(self, script: str, word: str) -> bool:
        try:
            return word in (resolve(self.cfg["songdeck"]) / script).read_text(encoding="utf-8")
        except OSError:
            return False

    def judge_args(self, tag: str):
        """(--judge args, the server to stop) per perform.judge: auto | internal | backtrack-surveil[=URL]."""
        j = str(self.cfg["perform"]["judge"] or "internal")
        if not self.songdeck_knows("scripts/dataset/perform.ts", "judge"):
            msg = "this Song Deck's dataset:perform has no --judge (update Song Deck)"
            if j in ("auto", "internal"):
                self.r.say(f"{msg}: agreement judged by the built-in transcription")
                return [], None
            raise StepFailed(msg + "; or set perform.judge to internal")
        if j == "internal":
            return ["--judge", "internal"], None  # explicit: Song Deck's own default may pick a running Surveil
        if j.startswith("backtrack-surveil="):
            return ["--judge", j], None
        model = self.judge_model()
        if model is None:
            if j == "auto":
                self.r.say("no model to judge with (runs/best, weights/): agreement judged by the built-in transcription")
                return ["--judge", "internal"], None
            raise StepFailed("perform.judge is backtrack-surveil but there is no model in runs/best or weights/")
        self.r.say(f"agreement judged by {model.name} (backtrack-surveil serve)")
        proc, url = self.serve_model(model, tag)
        return ["--judge", f"backtrack-surveil={url}"], proc

    def step_perform(self) -> dict:
        pc = self.cfg["perform"]
        if not pc["enabled"]:
            self.r.say("perform.enabled is false: no ACE-Step performances")
            return {"skipped": "disabled"}
        pending = self.pending_takes() if not self.dry or self.manifest() else int(pc["takes"]) * int(self.cfg["render"]["songs"])
        if pending == 0 and not self.dry:
            self.r.say("every selected song already has its takes; nothing to perform")
            return {"takes": 0, "total_takes": self.count_takes()}
        check_disk(self.r, self.ds, pending * perform_mb_per_take(self.cfg), f"about {pending} ACE-Step takes")
        server = judge = None
        url = pc["url"].rstrip("/")
        if not self.dry and not http_up(url):
            if not pc["server"]["command"] and pc["optional"]:
                self.r.say(f"WARNING: nothing answers at {url} and perform.optional is true: no new performances this run")
                return {"skipped": "ACE-Step unreachable", "total_takes": self.count_takes()}
            server = self.start_acestep(url)
        elif self.dry and pc["server"]["command"]:
            self.r.say(f"(if nothing answers at {url}) start ACE-Step:")
            self.start_acestep(url)
        args = ["--data", str(self.ds), "--provider", f"{pc['provider']}={url}", "--strength", str(pc["strength"]),
                "--takes", str(pc["takes"]), "--split", ",".join(as_list(pc["split"])),
                *(["--seed", str(pc["seed"])] if pc["seed"] is not None else []), "--window", str(pc["window"]),
                "--min-offset-strength", str(pc["min_offset_strength"]), "--timeout-minutes", str(pc["timeout_minutes"]),
                "--skip-existing"]
        if pc["songs"]:
            args += ["--songs", str(pc["songs"])]
        before, t0, failures = self.count_takes(), time.time(), []
        try:
            jargs, judge = self.judge_args("perform")
            args += jargs + as_list(pc["extra_args"])
            self.r.capture = "] "  # every line; failures picked out below
            code = self.r.run([npm_cmd(self.cfg), "run", "dataset:perform", "--", *args], cwd=resolve(self.cfg["songdeck"]),
                              check=False)
            failures = [ln.split("] ", 1)[1] for ln in self.r.captured if ln.startswith("[perform]") and " failed: " in ln]
            if code == 1 and pc["allow_failed_takes"]:
                self.r.say(f"WARNING: {len(failures) or 'some'} takes failed (listed in the report); "
                           "the next run retries them (--skip-existing)")
            elif code == 3:
                if pc["optional"]:
                    self.r.say(f"WARNING: ACE-Step is unreachable at {url}; perform.optional is true, carrying on without it")
                else:
                    raise StepFailed(f"ACE-Step is unreachable at {url} (dataset:perform exit 3); is its API server running?")
            elif code not in (0, 1):
                raise StepFailed(f"dataset:perform exited with code {code}")
            elif code == 1:
                raise StepFailed(f"{len(failures) or 'some'} takes failed (perform.allow_failed_takes is false)")
        finally:
            self.r.capture = None
            self.r.stop(judge)
            if server is not None and pc["server"]["stop_after"]:
                self.r.say("stopping the ACE-Step server this run started (frees the GPU for training)")
                self.r.stop(server)
        made = self.count_takes() - before
        secs = time.time() - t0
        info = {"takes": made, "total_takes": self.count_takes(), "seconds": secs, "exit": code, "failures": failures[:50],
                "judge": next((a for a in args if a.startswith("backtrack-surveil=")), "internal")}
        if made:
            info["seconds_per_take"] = secs / made
            self.r.say(f"{made} takes in {secs / 60:.1f} min: {secs / made:.0f} s per take")
        return info

    def start_acestep(self, url: str):
        sc = self.cfg["perform"]["server"]
        if not sc["command"]:
            if self.dry:
                self.r.say(f"WARNING: nothing answers at {url} yet; start ACE-Step's API server before the real run")
                return None
            raise StepFailed(f"nothing answers at {url}: start ACE-Step's API server (in its folder: uv run acestep-api), "
                             "or set perform.server.command so the pipeline starts it, or set perform.enabled false")
        cmd = sc["command"] if isinstance(sc["command"], list) else shlex.split(sc["command"], posix=not WINDOWS)
        cwd = resolve(sc["cwd"]) if sc["cwd"] else REPO
        proc = self.r.background(cmd, cwd, self.run_dir / "perform-server.log")
        if self.dry:
            return None
        limit = time.time() + 60 * float(sc["startup_timeout_minutes"])
        self.r.say(f"waiting for ACE-Step at {url} (up to {sc['startup_timeout_minutes']} min; the first start downloads its models)")
        while time.time() < limit:
            if proc.poll() is not None:
                raise StepFailed(f"the ACE-Step server exited with code {proc.returncode}; see {self.run_dir / 'perform-server.log'}")
            if http_up(url):
                self.r.say("ACE-Step is up")
                return proc
            time.sleep(5)
        self.r.stop(proc)
        raise StepFailed(f"ACE-Step did not answer at {url} within {sc['startup_timeout_minutes']} min; "
                         f"see {self.run_dir / 'perform-server.log'}")

    # -- train --------------------------------------------------------------

    def fingerprint(self) -> str:
        man = self.manifest() or {"songs": []}
        # Performances with their scores: a re-judged take changes which windows are trusted.
        data = [[s["id"], [[p.get("id"), p.get("agreement"), p.get("judge"), len(p.get("windows") or [])]
                           for p in perf_list(s)]] for s in man["songs"]]
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()
        except OSError:  # no git (the desktop hub's bundled copy): the training code's own files decide
            head = hashlib.sha1(b"".join(f.read_bytes() for f in sorted((REPO / "training").glob("*.py")))).hexdigest()
        blob = json.dumps({"data": data, "train": self.cfg["train"], "model": self.cfg["model"], "backtrack": head}, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()

    def previous_complete(self) -> dict | None:
        if not self.runs.is_dir():
            return None
        for d in sorted((p for p in self.runs.iterdir() if p.is_dir() and p.name[:1].isdigit()), reverse=True):
            if d == self.run_dir:
                continue
            rep = read_json(d / "report.json", None)
            if rep and rep.get("fingerprint"):
                return rep
        return None

    def train_audio(self) -> list[str]:
        audio = as_list(self.cfg["train"]["audio"])
        if "performed" in audio and not self.dry:
            man = self.manifest() or {"songs": []}
            if not any(perf_list(s) for s in man["songs"]):
                self.r.say("WARNING: train.audio has performed, but the dataset has no performances: training without")
                audio = [a for a in audio if a != "performed"]
        return audio

    def step_train(self) -> dict:
        tc, mc = self.cfg["train"], self.cfg["model"]
        if not self.dry:
            fp = self.fingerprint()
            self.out["fingerprint"] = fp
            prev = self.previous_complete()
            if tc["skip_if_unchanged"] and not self.args.force and prev and prev.get("fingerprint") == fp \
                    and not self.candidate().exists():
                self.out["unchanged"] = prev.get("unchanged_since") or prev.get("run")
                self.r.say(f"same data, settings and code as run {self.out['unchanged']}: not training again "
                           "(train.skip_if_unchanged; --force to train anyway). evaluate and parity are skipped too.")
                return {"skipped": "unchanged", "same_as": self.out["unchanged"]}
        audio = self.train_audio()
        out = self.candidate()
        args = ["--data", str(self.ds), "--audio", ",".join(audio), "--out", str(out), "--epochs", str(tc["epochs"]),
                "--name", mc["name"], "--version", mc["version"]]
        opt = {"--device": tc["device"], "--batch": tc["batch"], "--patience": tc["patience"], "--minutes": tc["minutes"] or None,
               "--threads": tc["threads"], "--seed": tc["seed"], "--steps-per-epoch": tc["steps_per_epoch"]}
        if "performed" in audio:
            opt["--min-agreement"] = tc["min_agreement"]
            opt["--min-offset-strength"] = tc["min_offset_strength"]
        if tc["egmd"]:
            opt["--egmd"] = str(resolve(tc["egmd"]))
            opt["--egmd-limit"] = tc["egmd_limit"]
        helptext = module_flags(self.cfg, "training.train_drums")
        for flag, v in opt.items():
            if v is None:
                continue
            if helptext and flag not in helptext:
                msg = f"training.train_drums has no {flag} (this BackTrack checkout is older than the config)"
                if self.dry:
                    self.r.say("WARNING: " + msg)
                else:
                    raise StepFailed(msg + f"; update BackTrack or remove train.{flag[2:].replace('-', '_')} from the config")
            args += [flag, str(v)]
        args += as_list(tc["extra_args"])
        # --resume continues the learning-rate schedule, which depends on the epochs, the batch and
        # the data: only resume a checkpoint made with exactly these arguments.
        if not self.dry and out.with_suffix(".pt").exists():
            same = self.out.get("train_args") == args
            if tc["resume"] and same:
                self.r.say(f"continuing from the checkpoint {out.with_suffix('.pt')}")
                args = args + ["--resume"]
            else:
                self.r.say("starting training over: " + ("train.resume is false" if not tc["resume"] else
                           "the data or training settings changed since the checkpoint was written"))
                out.with_suffix(".pt").unlink()
        self.out["train_args"] = [a for a in args if a != "--resume"]
        self.save()
        t0 = time.time()
        self.r.py("training.train_drums", args)
        self.out["candidate"] = str(out)
        summary = read_json(out.with_suffix(".summary.json"), {}) or {}
        return {"model": str(out), "seconds": time.time() - t0, "audio": audio,
                **{k: summary.get(k) for k in ("best_epoch", "epochs", "valid_mean_class_f", "stopped", "device", "minutes")
                   if k in summary}}

    # -- evaluate -----------------------------------------------------------

    def model_to_score(self) -> Path:
        if self.args.model:
            return resolve(self.args.model)
        return Path(self.out.get("candidate") or self.candidate())

    def has_test_performances(self) -> bool:
        man = self.manifest() or {"songs": []}
        return any(perf_list(s) for s in man["songs"] if s.get("split") == self.cfg["evaluate"]["split"])

    def evaluate_one(self, model: Path, audio: str, out: Path) -> bool:
        """False when nothing in the split could be scored (performed: no take passed the thresholds)."""
        args = ["--model", str(model), "--data", str(self.ds), "--audio", audio, "--split", self.cfg["evaluate"]["split"],
                "--binary", str(find_binary(self.cfg)), "--json", str(out)]
        if audio == "performed":
            args += ["--min-agreement", str(self.cfg["train"]["min_agreement"]),
                     "--min-offset-strength", str(self.cfg["train"]["min_offset_strength"])]
        code = self.r.py("training.evaluate", args, check=False)
        if code == 0:
            return True
        if audio == "performed" and any("no items in split" in t for t in self.r.tail):
            self.r.say(f"no performed {self.cfg['evaluate']['split']} take passed the agreement thresholds: "
                       "performed evaluation skipped")
            out.unlink(missing_ok=True)
            return False
        raise StepFailed(f"training.evaluate --audio {audio} on {model.name} exited with code {code}")

    def step_evaluate(self) -> dict:
        ec = self.cfg["evaluate"]
        if self.out.get("unchanged") and not self.args.model:
            return {"skipped": "unchanged"}
        model = self.model_to_score()
        if not self.dry and not model.is_file():
            raise StepFailed(f"no model to evaluate at {model} (run the train step, or pass --model FILE)")
        man = self.manifest()
        if not self.dry and man and not any(s.get("split") == "test" and s.get("stems") for s in man["songs"]):
            raise StepFailed("the dataset has no test songs with drums to score on yet: render more songs "
                             "(about a tenth of them are test songs; 50 or more is safe)")
        inputs = ["separated"]
        perf = ec["performed"]
        if perf is True or (perf == "auto" and (self.dry and self.cfg["perform"]["enabled"] or not self.dry and self.has_test_performances())):
            inputs.append("performed")
        done = {}
        for audio in list(inputs):
            p = self.run_dir / f"eval-{audio}.json"
            if self.evaluate_one(model, audio, p):
                done[audio] = p.name
            else:
                inputs.remove(audio)
        best = self.best_model()
        if ec["previous_best"] and best:
            for audio in inputs:
                p = self.run_dir / f"eval-best-{audio}.json"
                if self.evaluate_one(best, audio, p):
                    done[f"best-{audio}"] = p.name
        if ec["songdeck_baseline"]:
            done["songdeck"] = self.songdeck_baseline(model)
        return {"model": str(model), "reports": done}

    def serve_model(self, model: Path, tag: str, port: int = 0):
        """backtrack-surveil serve with only this model, on a free port: (process, url)."""
        port = port or (free_port() if not self.dry else 8826)
        url = f"http://127.0.0.1:{port}"
        models_dir = self.run_dir / f"{tag}-models"
        if not self.dry:
            models_dir.mkdir(exist_ok=True)
            for old in models_dir.glob("*.gguf"):
                old.unlink()
            try:
                os.link(model, models_dir / model.name)
            except OSError:
                shutil.copyfile(model, models_dir / model.name)
        env = self.r.env()
        env.pop("BACKTRACK_MODELS", None)
        # cwd: an empty directory, so the ./models of wherever the pipeline was started is not read.
        proc = self.r.background([find_binary(self.cfg), "serve", "--host", "127.0.0.1", "--port", str(port),
                                  "--models-dir", models_dir, "--quiet"], self.run_dir / f"{tag}-cwd",
                                 self.run_dir / f"{tag}-serve.log", env=env)
        if self.dry:
            return None, url
        try:
            limit = time.time() + 60
            while not http_up(url + "/health"):
                if proc.poll() is not None or time.time() > limit:
                    raise StepFailed(f"backtrack-surveil serve did not start on port {port}; see {tag}-serve.log")
                time.sleep(0.5)
            info = http_json(url + "/info")
            drums = [m.get("id") for m in info.get("models", []) if m.get("kind") == "drums" and not m.get("placeholder")]
            if drums != [model.stem]:
                raise StepFailed(f"the server found drum models {drums}, expected only {model.stem!r} "
                                 "(a models/ folder next to the binary?); move the others away")
        except BaseException:
            self.r.stop(proc)
            raise
        return proc, url

    def songdeck_baseline(self, model: Path) -> str:
        """Song Deck's own harness: its built-in transcriber and this model, side by side."""
        ec = self.cfg["evaluate"]
        out = self.run_dir / "eval-songdeck.json"
        proc, url = self.serve_model(model, "evaluate", int(ec["serve_port"]))
        try:
            self.r.npm("eval:datasets", ["--dataset", "songdeck", "--dir", str(self.ds), "--input", "separated",
                                         "--split", ec["split"], "--limit", "100000", "--seconds", "100000",
                                         "--tasks", "transcribe", "--transcriber", f"internal,backtrack-surveil={url}",
                                         "--out", str(out), *as_list(ec["songdeck_extra_args"])])
        finally:
            self.r.stop(proc)
        return out.name

    # -- parity -------------------------------------------------------------

    def step_parity(self) -> dict:
        if self.out.get("unchanged") and not self.args.model:
            return {"skipped": "unchanged"}
        model = self.model_to_score()
        self.r.py("training.parity", ["--binary", str(find_binary(self.cfg)), "--model", str(model),
                                      "--json", str(self.run_dir / "parity.json")])
        return {"ok": True}

    # -- rejudge ------------------------------------------------------------

    def step_rejudge(self) -> dict:
        """After a new best model: re-score the existing takes' windows with it (no ACE-Step), so the
        next run trains on labels judged by the better model."""
        pc = self.cfg["perform"]
        if not pc["enabled"] or not pc["rejudge_after_promotion"]:
            return {"skipped": "off (perform.rejudge_after_promotion)"}
        promoted = (self.state["steps"].get("report", {}).get("info") or {}).get("promoted")
        if not self.dry and not promoted and not (self.args.steps and "rejudge" in as_list(self.args.steps)):
            self.r.say("no new best model this run: nothing to re-judge")
            return {"skipped": "not promoted"}
        if not self.dry and not (read_json(self.best_dir() / "best.json", {}) or {}).get("beats_internal"):
            self.r.say("the new best model does not beat Song Deck's built-in transcription (report): "
                       "it would judge worse, so the takes keep their scores")
            return {"skipped": "best model does not beat the built-in transcription"}
        if not self.songdeck_knows("scripts/dataset/perform.ts", "rejudge"):
            self.r.say("this Song Deck's dataset:perform has no --rejudge (update Song Deck): skipped")
            return {"skipped": "dataset:perform has no --rejudge"}
        if not self.dry and not self.count_takes():
            return {"skipped": "no performances"}
        model = self.best_model() or (self.best_dir() / self.candidate().name)
        if self.dry:
            self.r.say("if this run's model is promoted, re-judge the takes with it:")
        proc, url = self.serve_model(model, "rejudge")
        try:
            self.r.npm("dataset:perform", ["--data", str(self.ds), "--rejudge", "--judge", f"backtrack-surveil={url}",
                                           "--split", ",".join(as_list(pc["split"])), "--window", str(pc["window"]),
                                           "--min-offset-strength", str(pc["min_offset_strength"])])
        finally:
            self.r.stop(proc)
        return {"judge": model.name, "takes": self.count_takes()}

    # -- report -------------------------------------------------------------

    def step_report(self) -> dict:
        if self.dry:
            self.r.say(f"would write {self.run_dir / 'report.md'} and report.json, and copy the model to "
                       f"{self.best_dir()} if it beats the previous best on the separated test split")
            return {}
        rep = build_report(self)
        write_json(self.run_dir / "report.json", rep)
        (self.run_dir / "report.md").write_text(report_markdown(rep), encoding="utf-8")
        if rep["promotion"]["promote"]:
            self.promote(rep)
        self.r.say(f"report: {self.run_dir / 'report.md'}")
        self.r.say(rep["promotion"]["summary"])
        return {"promoted": rep["promotion"]["promote"]}

    def promote(self, rep: dict) -> None:
        best, new, prev = self.best_dir(), self.runs / "best.new", self.runs / "best.prev"
        if new.exists():
            shutil.rmtree(new)
        new.mkdir(parents=True)
        model = Path(rep["model"]["path"])
        shutil.copy2(model, new / model.name)
        for extra in [model.with_suffix(".history.json")] + [self.run_dir / f for f in
                                                              ("report.md", "report.json", "eval-separated.json",
                                                               "eval-performed.json", "eval-songdeck.json", "parity.json")]:
            if extra.exists():
                shutil.copy2(extra, new / extra.name)
        write_json(new / "best.json", {"run": self.run_dir.name, "model": model.name, "promoted": now_iso(),
                                       "metric": rep["promotion"]["metric"], "value": rep["promotion"]["value"],
                                       "beats_internal": rep["promotion"]["beats_internal"]})
        if best.exists():
            if prev.exists():
                shutil.rmtree(prev)
            os.replace(best, prev)
        os.replace(new, best)
        self.r.say(f"promoted {model.name} to {best} (the one before is in {prev})")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _metric(rep: dict | None, name: str):
    if not rep:
        return None
    return rep.get("mean_class_f") if name == "mean_class_f" else (rep.get("overall") or {}).get("f")


def _songdeck_side(js: dict | None, who: str) -> dict | None:
    """eval:datasets' drums block for one transcriber, shaped like training.evaluate's report."""
    d = ((js or {}).get("drums") or {}).get(who)
    if not d:
        return None
    classes = {c: {"f": v.get("f", 0.0), "n_ref": v.get("reference", 0)} for c, v in (d.get("classes") or {}).items()}
    with_ref = [v["f"] for v in classes.values() if v["n_ref"]]
    return {"classes": classes, "overall": {"f": d.get("f"), "precision": d.get("precision"), "recall": d.get("recall")},
            "mean_class_f": sum(with_ref) / len(with_ref) if with_ref else None, "items": d.get("stems")}


def model_meta(path: Path) -> dict:
    try:
        from . import gguf

        meta, _ = gguf.read(path)
    except Exception as e:  # noqa: BLE001 - the report should still be written
        return {"error": str(e)}
    keep = {k: v for k, v in meta.items() if k.startswith("general.") or k in ("audio.sources", "audio.training", "audio.stems")}
    if isinstance(keep.get("audio.training"), str):
        try:
            keep["audio.training"] = json.loads(keep["audio.training"])
        except json.JSONDecodeError:
            pass
    return keep


def build_report(p: Pipeline) -> dict:
    rd, cfg = p.run_dir, p.cfg
    pc = cfg["promote"]
    steps = p.state["steps"]
    man = p.manifest() or {"songs": []}
    splits = {}
    for s in man["songs"]:
        splits[s.get("split")] = splits.get(s.get("split"), 0) + 1
    perfs = [q for s in man["songs"] for q in perf_list(s)]
    model = p.model_to_score()
    ev = {k: read_json(rd / f"eval-{k}.json") for k in ("separated", "performed", "best-separated", "best-performed")}
    sd = read_json(rd / "eval-songdeck.json")
    parity = read_json(rd / "parity.json")
    parity_ok = steps.get("parity", {}).get("status") == "done" and parity is not None and all(r.get("ok") for r in parity)
    history = read_json(model.with_suffix(".history.json"), [])
    rep = {
        "run": rd.name, "created": now_iso(), "config": cfg["_path"], "fingerprint": p.out.get("fingerprint"),
        "unchanged_since": p.out.get("unchanged"),
        "dataset": {"path": str(p.ds), "songs": len(man["songs"]), "splits": splits, "performances": len(perfs),
                    "songs_with_performances": sum(1 for s in man["songs"] if perf_list(s)),
                    "mean_agreement": (sum(float(q.get("agreement", 0)) for q in perfs) / len(perfs)) if perfs else None},
        "model": {"path": str(model), "exists": model.is_file(), "meta": model_meta(model) if model.is_file() else {}},
        "history": history,
        "evaluation": {"this": {"separated": ev["separated"], "performed": ev["performed"]},
                       "best": {"separated": ev["best-separated"], "performed": ev["best-performed"]},
                       "songdeck": {"internal": _songdeck_side(sd, "internal"), "backtrack": _songdeck_side(sd, "backtrack-surveil")}},
        "previous_best": read_json(p.best_dir() / "best.json"),
        "parity_ok": parity_ok,
        "steps": steps,
    }
    metric = pc["metric"]
    mine, theirs = _metric(ev["separated"], metric), _metric(ev["best-separated"], metric)
    promote, why = False, ""
    if (rep["previous_best"] or {}).get("run") == rd.name:
        why = "this run is the current best already (promoted earlier)"
    elif p.out.get("unchanged"):
        why = f"nothing new since run {p.out['unchanged']}: no model trained"
    elif mine is None:
        why = "no separated test evaluation in this run"
    elif pc["require_parity"] and not parity_ok:
        why = "parity did not pass (or was not run)"
    elif rep["previous_best"] and theirs is None:
        why = "the previous best was not re-evaluated on this test split (evaluate.previous_best)"
    elif theirs is not None and mine <= theirs + float(pc["min_improvement"]):
        why = f"{metric} {mine:.3f} does not beat the previous best's {theirs:.3f} (+{float(pc['min_improvement']):g} needed)"
    else:
        promote = True
        why = f"{metric} {mine:.3f}" + (f" beats the previous best's {theirs:.3f} ({mine - theirs:+.3f})" if theirs is not None
                                         else " (first model: nothing to beat)")
    sd_int, sd_bt = _metric(rep["evaluation"]["songdeck"]["internal"], metric), _metric(rep["evaluation"]["songdeck"]["backtrack"], metric)
    rep["promotion"] = {"promote": promote, "metric": metric, "value": mine, "previous": theirs,
                        "beats_internal": sd_int is not None and sd_bt is not None and sd_bt > sd_int,
                        "summary": ("PROMOTED to runs/best: " if promote else "not promoted: ") + why}
    return rep


def _f(v) -> str:
    return "–" if v is None else f"{v:.3f}"


def _class_table(cols: list[tuple[str, dict | None]]) -> list[str]:
    cols = [(n, r) for n, r in cols if r]
    if not cols:
        return ["(no results)"]
    classes = []
    for _, r in cols:
        for c in (r.get("classes") or {}):
            if c not in classes:
                classes.append(c)
    out = ["| class | " + " | ".join(n for n, _ in cols) + " |", "|---|" + "---:|" * len(cols)]
    def cell(r, c):
        v = (r.get("classes") or {}).get(c) or {}
        return "–" if not v or v.get("n_ref") == 0 else _f(v.get("f"))  # no reference hits: F says nothing
    for c in classes:
        out.append(f"| {c} | " + " | ".join(cell(r, c) for _, r in cols) + " |")
    out.append("| **overall F** | " + " | ".join(_f((r.get("overall") or {}).get("f")) for _, r in cols) + " |")
    out.append("| **mean class F** | " + " | ".join(_f(r.get("mean_class_f")) for _, r in cols) + " |")
    out.append("| items | " + " | ".join(str(r.get("items", "–")) for _, r in cols) + " |")
    return out


def report_markdown(rep: dict) -> str:
    e, d = rep["evaluation"], rep["dataset"]
    L = [f"# Training run {rep['run']}", "", f"**{rep['promotion']['summary']}**", ""]
    meta = rep["model"].get("meta", {})
    L += [f"Model: `{rep['model']['path']}` ({meta.get('general.name', '?')} {meta.get('general.version', '')}).",
          f"Previous best: {rep['previous_best']['run'] + ' (' + rep['previous_best']['model'] + ')' if rep['previous_best'] else 'none'}.", ""]
    L += ["## Data", "",
          f"- Dataset `{d['path']}`: {d['songs']} songs ({', '.join(f'{k} {v}' for k, v in sorted(d['splits'].items(), key=lambda kv: str(kv[0])))})",
          f"- ACE-Step performances: {d['performances']} takes on {d['songs_with_performances']} songs"
          + (f", mean agreement {d['mean_agreement']:.2f}" if d["mean_agreement"] is not None else "")]
    training = meta.get("audio.training")
    if isinstance(training, dict):
        perf = training.get("performed")
        if isinstance(perf, dict) and perf.get("takes"):
            L.append(f"- Performed audio in training: {perf.get('accepted_takes')} of {perf.get('takes')} takes, "
                     f"{perf.get('accepted_minutes', 0):.1f} of {perf.get('minutes', 0):.1f} min accepted "
                     f"(train {perf.get('accepted_minutes_train', 0):.1f}, valid {perf.get('accepted_minutes_valid', 0):.1f}; "
                     f"agreement F ≥ {perf.get('min_agreement')}, offset strength ≥ {perf.get('min_offset_strength')})"
                     + ("; rejected takes: " + ", ".join(f"{k} {v}" for k, v in (perf.get("rejected") or {}).items() if v)
                        if any((perf.get("rejected") or {}).values()) else ""))
        vf = training.get("valid_mean_class_f")
        secs = [h.get("seconds", 0) for h in rep["history"] if h.get("seconds")]
        L.append(f"- Training: {training.get('epochs')} epochs, best epoch {training.get('best_epoch')}, "
                 + (f"valid mean class F {_f(vf)}" if isinstance(vf, (int, float)) and vf >= 0 else "no valid split (best = lowest loss)")
                 + (f", device {training['device']}" if training.get("device") else "")
                 + (f", {sum(secs) / len(secs) / 60:.1f} min per epoch" if secs else ""))
    L += ["", "## Separated test split, through the binary (training.evaluate)", "",
          "Onset F per class within ±50 ms on the drums separated from the mix (what Song Deck sends).", ""]
    L += _class_table([("this run", e["this"]["separated"]), ("previous best", e["best"]["separated"]),
                       ("Song Deck built-in", e["songdeck"]["internal"])])
    L += ["", "The Song Deck column comes from Song Deck's own harness (next section), on the same songs and tolerance.", ""]
    if e["this"]["performed"] or e["best"]["performed"]:
        L += ["## Performed test windows (ACE-Step takes)", "",
              "Scored only inside the windows whose agreement passed `--min-agreement` (ACE-Step does not play every "
              "note as written; outside those windows the labels are not trusted).", ""]
        L += _class_table([("this run", e["this"]["performed"]), ("previous best", e["best"]["performed"])])
        pinfo = (e["this"]["performed"] or {}).get("performed")
        if isinstance(pinfo, dict):
            L += ["", "Scored: " + ", ".join(f"{k} {v:.1f}" if isinstance(v, float) else f"{k} {v}"
                                            for k, v in pinfo.items() if not isinstance(v, (list, dict)))]
        L.append("")
    if e["songdeck"]["internal"] or e["songdeck"]["backtrack"]:
        L += ["## Song Deck's harness (npm run eval:datasets, --input separated)", "",
              "Whole songs; this run's model served by `backtrack-surveil serve`. Its numbers can differ a little from "
              "training.evaluate's (Song Deck maps and clips notes its own way); compare within a column pair.", ""]
        L += _class_table([("Song Deck built-in", e["songdeck"]["internal"]), ("BackTrack (this run)", e["songdeck"]["backtrack"])])
        L.append("")
    L += ["## Parity", "", "PyTorch and the binary agree." if rep["parity_ok"] else "Not passed (or not run): see parity.log.", ""]
    if rep["history"]:
        L += ["## Training history", "", "| epoch | loss | valid mean class F | seconds |", "|---:|---:|---:|---:|"]
        for h in rep["history"]:
            L.append(f"| {h.get('epoch')} | {h.get('loss', 0):.4f} | {_f(h.get('valid_f'))} | {h.get('seconds', 0):.0f} |")
        L.append("")
    L += ["## Steps", "", "| step | status | minutes | notes |", "|---|---|---:|---|"]
    for name in STEPS:
        s = rep["steps"].get(name)
        if not s:
            continue
        info = s.get("info") or {}
        notes = []
        if name == "render" and info.get("rendered"):
            notes.append(f"{info['rendered']} songs rendered, {info.get('seconds', 0) / max(1, info['rendered']):.1f} s per song")
        if name == "perform" and info.get("seconds_per_take"):
            notes.append(f"{info['takes']} takes, {info['seconds_per_take']:.0f} s per take")
        if name == "perform" and info.get("judge") and not info.get("skipped"):
            notes.append(f"judged by {info['judge']}")
        if name == "perform" and info.get("failures"):
            notes.append(f"{len(info['failures'])} takes failed")
        if info.get("skipped"):
            notes.append(f"skipped: {info['skipped']}")
        status = "done" if name == "report" else s.get("status")
        L.append(f"| {name} | {status} | {s.get('seconds', 0) / 60:.1f} | {'; '.join(notes)} [log]({name}.log) |")
    fails = ((rep["steps"].get("perform") or {}).get("info") or {}).get("failures")
    if fails:
        L += ["", "## Failed takes (retried on the next run)", ""] + [f"- {f}" for f in fails]
    src = meta.get("audio.sources")
    if src:
        L += ["", "## Licence record (audio.sources)", ""] + [f"- {x}" for x in (src if isinstance(src, list) else [src])]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Scheduling (printed, never installed)
# ---------------------------------------------------------------------------


def schedule_text(target: str, cfg_path: Path, at: str, python: str) -> str:
    hh, mm = (int(x) for x in at.split(":"))
    repo, cfgp = str(REPO), str(cfg_path)
    cfg = load_config(str(cfg_path))
    runs = resolve(cfg["runs_dir"])
    tools = [shutil.which(t) for t in ("node", "npm", "git", "uv")]
    path_dirs = []
    for t in tools:
        if t and str(Path(t).parent) not in path_dirs:
            path_dirs.append(str(Path(t).parent))
    if target == "linux":
        path = ":".join(dict.fromkeys(path_dirs + ["/usr/local/bin", "/usr/bin", "/bin"]))
        return f"""# ---- Option 1: cron (crontab -e, then paste the two lines) ----
PATH={path}
{mm} {hh} * * * mkdir -p {shlex.quote(str(runs))} && cd {shlex.quote(repo)} && {shlex.quote(python)} -m training.pipeline --config {shlex.quote(cfgp)} >> {shlex.quote(str(runs / 'scheduled.log'))} 2>&1

# Stop: crontab -e and delete (or comment out with #) the line.

# ---- Option 2: a systemd user timer ----
# Save as ~/.config/systemd/user/backtrack-training.service
[Unit]
Description=BackTrack drum model training pipeline

[Service]
Type=oneshot
WorkingDirectory={repo}
Environment=PATH={path}
ExecStart={python} -m training.pipeline --config {cfgp}
Nice=10

# Save as ~/.config/systemd/user/backtrack-training.timer
[Unit]
Description=Run the BackTrack training pipeline nightly

[Timer]
OnCalendar=*-*-* {hh:02d}:{mm:02d}:00
Persistent=true

[Install]
WantedBy=timers.target

# Then:
#   systemctl --user daemon-reload
#   systemctl --user enable --now backtrack-training.timer
#   loginctl enable-linger $USER          # run while you are logged out
#   systemctl --user list-timers          # next run
#   journalctl --user -u backtrack-training -f    # its output
#   systemctl --user start backtrack-training     # run it now
# Stop: systemctl --user disable --now backtrack-training.timer
"""
    if target == "macos":
        path = ":".join(dict.fromkeys(path_dirs + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
        log = str(runs / "scheduled.log")
        x = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Save as ~/Library/LaunchAgents/com.backtrack.training.plist, then:
       launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.backtrack.training.plist
       launchctl kickstart gui/$(id -u)/com.backtrack.training      (run it now)
     Stop:  launchctl bootout gui/$(id -u)/com.backtrack.training
     A Mac that is asleep at {hh:02d}:{mm:02d} runs it when it wakes. -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.backtrack.training</string>
  <key>ProgramArguments</key>
  <array>
    <string>{x(python)}</string><string>-m</string><string>training.pipeline</string>
    <string>--config</string><string>{x(cfgp)}</string>
  </array>
  <!-- launchd does not create the log's folder: mkdir -p {x(str(runs))} once. -->
  <key>WorkingDirectory</key><string>{x(repo)}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{x(path)}</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hh}</integer><key>Minute</key><integer>{mm}</integer></dict>
  <key>StandardOutPath</key><string>{x(log)}</string>
  <key>StandardErrorPath</key><string>{x(log)}</string>
  <key>Nice</key><integer>10</integer>
</dict>
</plist>
"""
    if target == "windows":
        ps1 = str(REPO / "training" / "schedule" / "run-pipeline.ps1")
        tr = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{ps1}" -Config "{cfgp}" -Python "{python}"'
        return f"""# ---- Option 1: schtasks (Command Prompt or PowerShell) ----
schtasks /Create /TN "BackTrack training" /SC DAILY /ST {hh:02d}:{mm:02d} /F /TR "{tr.replace('"', chr(92) + '"')}"
# (/TR is limited to 261 characters; if schtasks refuses it, use option 2.)

# ---- Option 2: PowerShell (can also wake the computer) ----
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File "{ps1}" -Config "{cfgp}" -Python "{python}"'
$trigger = New-ScheduledTaskTrigger -Daily -At {hh:02d}:{mm:02d}
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 23)
Register-ScheduledTask -TaskName "BackTrack training" -Action $action -Trigger $trigger -Settings $settings

# Run now:    schtasks /Run /TN "BackTrack training"
# Status:     schtasks /Query /TN "BackTrack training" /V /FO LIST
# Pause:      schtasks /Change /TN "BackTrack training" /DISABLE    (/ENABLE to resume)
# Remove:     schtasks /Delete /TN "BackTrack training" /F
# Log of each scheduled start: {runs / 'scheduled.log'} (each run's own logs are in its run folder)
"""
    raise SystemExit("schedule --print takes linux, macos or windows")


def schedule_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m training.pipeline schedule",
                                 description="Print what runs the pipeline nightly on this kind of computer (nothing is installed).")
    ap.add_argument("--print", dest="target", required=True, choices=["linux", "macos", "windows"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--time", default="02:30", help="HH:MM, local time (default 02:30)")
    ap.add_argument("--python", default=None, help="the Python to run (default: the config's \"python\", else this one)")
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).expanduser().resolve()
    python = program(a.python) if a.python else python_cmd(load_config(str(cfg_path)))
    print(schedule_text(a.target, cfg_path, a.time, python))
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def preflight(p: Pipeline, steps: list[str]) -> None:
    cfg, problems = p.cfg, []
    sd = resolve(cfg["songdeck"])
    needs_sd = {"render", "perform"} & set(steps) or ("evaluate" in steps and cfg["evaluate"]["songdeck_baseline"])
    if needs_sd:
        pkg = read_json(sd / "package.json", None)
        if not pkg:
            problems.append(f"no Song Deck checkout at {sd} (config \"songdeck\")")
        else:
            scripts = pkg.get("scripts", {})
            want = [s for s, step in (("dataset:render", "render"), ("dataset:perform", "perform"), ("eval:datasets", "evaluate"))
                    if step in steps and not (step == "perform" and not cfg["perform"]["enabled"])]
            for s in want:
                if s not in scripts:
                    problems.append(f"Song Deck at {sd} has no `npm run {s}` (update it: git pull && npm install)")
            if "render" in steps and not p.songdeck_knows("scripts/dataset/render.ts", "skip-existing"):
                problems.append(f"Song Deck at {sd} is too old: its dataset:render has no --skip-existing (git pull && npm install)")
            if not (sd / "node_modules").is_dir():
                problems.append(f"Song Deck's dependencies are not installed: cd {sd} && npm install")
        if not shutil.which(npm_cmd(cfg)) and not Path(npm_cmd(cfg)).is_file():
            problems.append("npm not found: install Node.js 20.19+ (it brings npm)")
    if {"evaluate", "parity"} & set(steps) and not find_binary(cfg).is_file():
        problems.append(f"no backtrack-surveil binary at {find_binary(cfg)}: cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j")
    if {"train", "evaluate", "parity"} & set(steps):
        r = subprocess.run([python_cmd(cfg), "-c", "import numpy, torch"], capture_output=True, text=True, check=False)
        if r.returncode != 0:
            problems.append(f"{python_cmd(cfg)} cannot import numpy and torch (see docs/training-at-home.md, step 2)")
    if "perform" in steps and cfg["perform"]["enabled"] and not cfg["perform"]["server"]["command"] \
            and not http_up(cfg["perform"]["url"].rstrip("/")):
        msg = (f"nothing answers at {cfg['perform']['url']}: start ACE-Step's API server first (uv run acestep-api), "
               "set perform.server.command, or set perform.enabled false")
        if p.dry:
            p.r.say("WARNING: " + msg)
        else:
            problems.append(msg)
    if problems:
        for m in problems:
            print(f"[pipeline] problem: {m}", file=sys.stderr)
        if not p.dry:
            raise SystemExit(2)


def main(argv=None) -> int:
    # Child output (e.g. Song Deck's "->" arrows) is echoed to the console; a Windows cp1252 console can't encode
    # all of it, so never let a display character abort a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["schedule"]:
        return schedule_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="pipeline config (JSON), e.g. training/pipeline.example.json")
    ap.add_argument("--steps", default=None,
                    help=f"comma-separated (default {','.join(STEPS)}); with --run, steps named here run again even if done")
    ap.add_argument("--dry-run", action="store_true", help="print every command and check the setup; change nothing")
    ap.add_argument("--run", help="resume a run: its id (folder name in the runs directory) or 'latest'")
    ap.add_argument("--model", help="evaluate / parity / report on this GGUF instead of the run's own")
    ap.add_argument("--force", action="store_true", help="train even when nothing changed since the last run")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    steps = as_list(a.steps) or list(STEPS)
    for s in steps:
        if s not in STEPS:
            ap.error(f"unknown step {s} (steps: {', '.join(STEPS)})")
    steps = [s for s in STEPS if s in steps]
    runs = resolve(cfg["runs_dir"])

    if a.run:
        if a.run == "latest":
            cands = sorted((d for d in runs.glob("[0-9]*") if d.is_dir()), reverse=True) if runs.is_dir() else []
            if not cands:
                raise SystemExit(f"[pipeline] no runs in {runs} to resume")
            run_dir = cands[0]
        else:
            run_dir = runs / a.run
            if not run_dir.is_dir():
                raise SystemExit(f"[pipeline] no run {a.run} in {runs}")
    else:
        run_dir = runs / dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    p = Pipeline(cfg, run_dir, a)
    lock = Lock(runs / "pipeline.lock")
    if not p.dry:
        runs.mkdir(parents=True, exist_ok=True)
        lock.acquire(run_dir.name)
    if not WINDOWS:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if not p.dry:
            run_dir.mkdir(parents=True, exist_ok=True)
        print(f"[pipeline] {'DRY RUN: nothing is changed. ' if p.dry else ''}run {run_dir}"
              f"{' (resumed)' if a.run else ''}; steps {', '.join(steps)}; config {cfg['_path']}", flush=True)
        preflight(p, steps)
        p.save()
        for name in steps:
            st = p.state["steps"].get(name, {})
            if st.get("status") == "done" and not p.dry and not a.steps:
                print(f"[pipeline] {name}: done in an earlier attempt of this run, skipping", flush=True)
                continue
            log_path = run_dir / f"{name}.log"
            p.r.log = None if p.dry else open(log_path, "a", encoding="utf-8")
            t0 = time.time()
            p.r.say(f"==== {name} ({now_iso()}) ====")
            p.state["steps"][name] = {"status": "running", "started": now_iso()}
            p.save()
            try:
                info = getattr(p, f"step_{name}")()
            except StepFailed as e:
                p.state["steps"][name] = {"status": "failed", "started": p.state["steps"][name]["started"],
                                          "seconds": time.time() - t0, "error": str(e)}
                p.save()
                p.r.say(f"FAILED at step {name}: {e}")
                if not p.dry:
                    p.r.say(f"log: {log_path}")
                    p.r.say(f"fix it, then resume with: python3 -m training.pipeline --config {cfg['_path']} --run {run_dir.name}")
                return 1
            finally:
                if p.r.log:
                    p.r.log.close()
                    p.r.log = None
            p.state["steps"][name] = {"status": "done", "started": p.state["steps"][name]["started"],
                                      "finished": now_iso(), "seconds": time.time() - t0, "info": info}
            p.save()
            print(f"[pipeline] {name}: done in {(time.time() - t0) / 60:.1f} min", flush=True)
        p.state["finished"] = now_iso()
        p.save()
        print(f"[pipeline] {'dry run finished' if p.dry else 'run finished'}: {run_dir}", flush=True)
        return 0
    except KeyboardInterrupt:
        if p.r.child is not None:
            p.r.stop(p.r.child)
        for name, st in p.state["steps"].items():
            if st.get("status") == "running":
                st["status"] = "interrupted"
        p.save()
        print(f"\n[pipeline] interrupted; resume with --run {run_dir.name}", file=sys.stderr)
        return 130
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
