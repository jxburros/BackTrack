"""Several training runs one after another (for example drums, then bass, then melody overnight):

    python3 -m training.queue --config ~/backtrack-training/pipeline.json \\
        --config ~/backtrack-training/pipeline-bass.json --config ~/backtrack-training/pipeline-melody.json \\
        [--start-at 23:30] [--stop-on-failure] [--resume] [--status FILE] [-- PIPELINE OPTIONS]

Each config is one `python3 -m training.pipeline --config FILE` run, started when the one before it
has ended. A run that fails does not stop the queue (each model has its own songs and runs), unless
--stop-on-failure; it can be resumed later with the pipeline's `--run latest`. Options after `--`
go to every run (e.g. `-- --steps train,evaluate,parity,report --force`).

--start-at HH:MM waits until that local time (the next one: 23:30 started at 23:45 means tomorrow)
before the first run, so a queue set up in the evening trains at night. Stopping the queue (Ctrl-C,
or STOP in the desktop app) stops the run in progress the way the pipeline is always stopped, and
marks it interrupted; --resume then picks the queue up: finished entries are skipped, and the
interrupted or failed entry continues with the pipeline's `--run latest`.

The queue's state is in --status (default: queue-status.json next to the first config): which
entry is running, how each one ended and its run folder. The desktop app reads it to follow the
run in progress. Exit code: 0 when every run succeeded, 1 when one failed, 130 when stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .pipeline import REPO, WINDOWS, load_config, now_iso, resolve


def parse_time(text: str) -> tuple[int, int]:
    try:
        hh, mm = (int(x) for x in text.split(":"))
    except ValueError:
        raise SystemExit(f"--start-at {text}: use HH:MM (24-hour, local time)") from None
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise SystemExit(f"--start-at {text}: use HH:MM (24-hour, local time)")
    return hh, mm


def next_time(hh: int, mm: int, now: dt.datetime | None = None) -> dt.datetime:
    """The next local time at HH:MM (today if it is still ahead, else tomorrow)."""
    now = now or dt.datetime.now()
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at if at > now else at + dt.timedelta(days=1)


def entry_for(cfg_path: Path) -> dict:
    cfg = load_config(str(cfg_path))
    return {"config": str(cfg_path), "kind": cfg["model"]["kind"], "runs_dir": str(resolve(cfg["runs_dir"])),
            "status": "waiting"}


def latest_run(runs_dir: str) -> str | None:
    runs = Path(runs_dir)
    if not runs.is_dir():
        return None
    cands = sorted(d.name for d in runs.glob("[0-9]*") if d.is_dir())
    return cands[-1] if cands else None


class Queue:
    def __init__(self, status_path: Path, entries: list[dict]):
        self.path = status_path
        self.state = {"started": now_iso(), "pid": os.getpid(), "current": None, "entries": entries}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1) + "\n")
        tmp.replace(self.path)


def wait_until(at: dt.datetime) -> None:
    print(f"[queue] waiting until {at:%Y-%m-%d %H:%M} to start", flush=True)
    last = None
    while True:
        left = (at - dt.datetime.now()).total_seconds()
        if left <= 0:
            return
        hours = int(left // 3600)
        if hours != last and left > 60:
            print(f"[queue] {hours} h {int(left % 3600 // 60)} min to go", flush=True)
            last = hours
        time.sleep(min(30.0, left))


def run_entry(e: dict, resume: bool, extra: list[str]) -> int:
    cmd = [sys.executable, "-u", "-m", "training.pipeline", "--config", e["config"], *extra]
    if resume:
        cmd += ["--run", "latest"]
    print(f"[queue] {e['kind']}: {' '.join(cmd[3:])}", flush=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # The run is in this process group: Ctrl-C and the desktop app's STOP reach it as they reach
    # the queue, and the queue waits for it to record where it stopped.
    proc = subprocess.Popen(cmd, cwd=REPO, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        try:
            if WINDOWS:
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            return proc.wait(timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            return 130


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", action="append", default=[], help="a pipeline config (repeatable, run in this order)")
    ap.add_argument("--start-at", help="wait until this local time (HH:MM) before the first run")
    ap.add_argument("--stop-on-failure", action="store_true", help="stop the queue when a run fails")
    ap.add_argument("--resume", action="store_true",
                    help="continue the queue in --status: skip finished entries, resume the interrupted or failed one")
    ap.add_argument("--status", help="the queue's state file (default: queue-status.json next to the first config)")
    a = ap.parse_args(argv)

    configs = [Path(c).expanduser().resolve() for c in a.config]
    status = Path(a.status).expanduser().resolve() if a.status else None
    if a.resume:
        status = status or (configs[0].parent / "queue-status.json" if configs else None)
        if not status or not status.is_file():
            raise SystemExit("[queue] --resume: no queue status to continue (give --status FILE, or the first --config)")
        old = json.loads(status.read_text())
        entries = old["entries"]
        if configs and [Path(e["config"]) for e in entries] != configs:
            raise SystemExit(f"[queue] --resume: {status} holds a queue of other configs; leave out --config or start a new queue")
    else:
        if not configs:
            ap.error("give at least one --config")
        for c in configs:
            if not c.is_file():
                raise SystemExit(f"[queue] no config at {c}")
        entries = [entry_for(c) for c in configs]
        status = status or configs[0].parent / "queue-status.json"
    q = Queue(status, entries)
    if not WINDOWS:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    todo = [e for e in entries if e["status"] != "done"]
    print(f"[queue] {len(entries)} runs: " + ", ".join(f"{e['kind']} ({e['status']})" for e in entries)
          + f"; status in {status}", flush=True)
    failed = 0
    try:
        q.save()
        if a.start_at:
            wait_until(next_time(*parse_time(a.start_at)))
        for e in todo:
            resume = a.resume and e["status"] in ("interrupted", "failed", "running")
            i = entries.index(e)
            q.state["current"] = i
            e.update(status="running", started=now_iso())
            e.pop("exit", None)
            q.save()
            t0 = time.time()
            code = run_entry(e, resume, extra)
            e.update(exit=code, finished=now_iso(), minutes=round((time.time() - t0) / 60, 1), run=latest_run(e["runs_dir"]))
            if code == 130:
                e["status"] = "interrupted"
                q.save()
                raise KeyboardInterrupt
            e["status"] = "done" if code == 0 else "failed"
            q.save()
            print(f"[queue] {e['kind']}: {'finished' if code == 0 else f'FAILED (exit {code})'} in {e['minutes']} min", flush=True)
            if code != 0:
                failed += 1
                if a.stop_on_failure:
                    print("[queue] --stop-on-failure: not starting the rest; continue with --resume", flush=True)
                    break
        q.state["current"] = None
        q.state["finished"] = now_iso()
        q.save()
        print("[queue] done: " + ", ".join(f"{e['kind']} {e['status']}" for e in entries), flush=True)
        return 1 if failed else 0
    except KeyboardInterrupt:
        for e in entries:
            if e["status"] == "running":
                e["status"] = "interrupted"
        q.save()
        print("\n[queue] stopped; continue with: python3 -m training.queue --resume --status " + str(status), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
