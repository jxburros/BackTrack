# Training the drum model at home

This guide takes you from a bare computer to a Surveil drum model trained on your own machine:
Song Deck composes and renders the training songs, ACE-Step plays them again as a "cover" so the
model also hears realistic audio, BackTrack trains on both, scores the result, and the whole
thing runs again every night by itself. It is written for doing it the first time; every command
is given for Windows, macOS and Linux where they differ.

**The easy way: the TRAIN tab.** Open the **BackTrack** desktop app (the hub,
[docs/desktop.md](desktop.md)) and click **TRAIN** in the top bar. Choose your Song Deck and
ACE-Step folders, click **Check setup** (it says what's missing and can install Song Deck's
dependencies and sound bank for you), then **Start training**. It shows each step, the output and
the report, with **Stop** and **Resume last run**.

**What to train** picks the model: **Drums**, **Bass**, or **Melody** (lead lines and vocals). Each
keeps its own songs and runs in the training data folder (`sd-drums` and `runs`, `sd-bass` and
`runs-bass`, `sd-melody` and `runs-melody`), so switching doesn't disturb another model's
training. ACE-Step takes are judged for the part being trained (Song Deck's `dataset:perform
--parts`): takes made for drums are reused for bass and melody without generating them again. The
first bass or melody runs are judged by Song Deck's built-in transcription, which passes few windows
on separated stems; once a model beats it, that model judges. Bass and melody reports also compare
the model with Song Deck's own transcription of the same parts. Under **Use it in Song Deck**,
**Install in Song Deck** puts the best model so far into Song Deck's plugins folder
(`~/.songdeck/plugins`). In Song Deck, open Settings → Plugins, press **Rescan** and switch it on:
it then transcribes those parts in the page, and joins **Use specialist transcribers** and
**Combine transcribers**. The panel also says whether Song Deck's own transcription still does
better, in which case train more first. Bass and melody need a Song Deck with model plugins (0.18.0 or
later).

It needs BackTrack Surveil installed, Python
with PyTorch, Node.js, a Song Deck checkout and, for performances, ACE-Step 1.5 (sections 1, 2, 4
and 5 below); there is no BackTrack source checkout or build to do. Keep the app open while it
trains: closing it stops the run.

Everything below is the same pipeline from a terminal, which you need for scheduled runs. One
command does the work:

```bash
python3 -m training.pipeline --config ~/backtrack-training/pipeline.json
```

It runs six steps in order, and a seventh after a new best model:

| Step       | What happens                                                                                       | Where                       |
| ---------- | -------------------------------------------------------------------------------------------------- | --------------------------- |
| `render`   | Song Deck composes and renders songs (mix, drum stem, drums separated from the mix, drum notes)     | Song Deck, CPU              |
| `perform`  | ACE-Step plays each song as a cover; Song Deck separates its drums, lines it up and scores it      | ACE-Step on the GPU         |
| `train`    | BackTrack trains the drum model on the stems, the separated drums and the trusted parts of the covers | PyTorch, GPU if there is one |
| `evaluate` | Scores the new model, the previous best and Song Deck's built-in drum transcription on test songs  | the BackTrack binary        |
| `parity`   | Checks the binary computes exactly what PyTorch computes for the new model                          | the BackTrack binary        |
| `report`   | Writes `report.md`; copies the model to `runs/best/` if it is better than the previous best        |                             |
| `rejudge`  | After a new best model: re-scores the covers with it (no ACE-Step), so the next run trusts better  | Song Deck + the binary      |

The pipeline never touches `weights/` (the release models in the repository); putting a model
there is a deliberate step ([below](#making-it-the-release-model)).

Reference for every option: [training/README.md](../training/README.md#the-pipeline-one-command).
How the parts work on their own: Song Deck's `docs/TRAINING-DATA.md` (rendering and ACE-Step
performances) and [training/README.md](../training/README.md) (training and evaluation).

## Contents

1. [What you need](#what-you-need)
2. [Install the prerequisites](#1-install-the-prerequisites)
3. [Python and PyTorch with CUDA](#2-python-and-pytorch-with-cuda)
4. [Build BackTrack](#3-build-backtrack)
5. [Set up Song Deck and its sound bank](#4-set-up-song-deck-and-its-sound-bank)
6. [Install and start ACE-Step](#5-install-and-start-ace-step)
7. [Write your config](#6-write-your-config)
8. [A first small run](#7-a-first-small-run)
9. [Reading the report](#8-reading-the-report)
10. [The full run](#9-the-full-run)
11. [Automate it](#10-automate-it)
12. [Use the trained model](#11-use-the-trained-model)
13. [Troubleshooting](#troubleshooting)
14. [Licence](#licence)

## What you need

### Hardware

| Part     | Enough                                                    | Better                                  | Why                                                                                                |
| -------- | --------------------------------------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| GPU      | NVIDIA with 4 GB of VRAM (ACE-Step 1.5's minimum)          | NVIDIA with 8 GB or more (recommended)   | ACE-Step needs it; training uses it too. Without one, set `perform.enabled` to `false` and train on the CPU |
| RAM      | 16 GB                                                     | 32 GB                                   | ACE-Step 1.5 is listed at 16 GB; training memory-maps its features, so it needs little             |
| CPU      | 4 cores                                                   | 8 or more                               | rendering, separation and the evaluation run on the CPU                                            |
| Disk     | 50 GB free for the default run                            | an SSD                                  | see the next table                                                                                 |
| OS       | Windows 10/11, macOS 13+, or a recent Linux               |                                         |                                                                                                    |

The VRAM figures are Song Deck's model catalogue entries for ACE-Step 1.5 (4 GB minimum, 8 GB
recommended, 16 GB of RAM, about 8 GB of model files, not usable on the CPU); ACE-Step 1.5 itself
advertises running in under 4 GB. The ACE-Step v1 bridge needs about 4 GB of VRAM or more. Check
ACE-Step's own documentation for your card and model. The drum model is small (about 90,000
parameters), so any CUDA GPU trains it; on a Mac, PyTorch uses the GPU through MPS (whether
ACE-Step runs well on your Mac is ACE-Step's to say; if not, train without performances).

### Disk

Measured on Song Deck renders (16-bit stereo at 44.1 kHz is about 10.6 MB per minute per file):

| What                                                                   | Per 60-second song | 400 songs (the default) |
| ---------------------------------------------------------------------- | ------------------ | ----------------------- |
| The song: mix, drum stem and drums separated from the mix              | about 32 MB        | about 13 GB             |
| Its feature cache for training (stem + separated)                      | about 6.5 MB       | about 2.6 GB            |
| Each ACE-Step take: the performance and its separated drums            | about 21 MB        | about 8.5 GB per take   |
| The take's feature cache                                               | about 4 MB         | about 1.6 GB per take   |
| While a chunk of 50 songs renders, the separated stems not kept yet    |                    | about 1.6 GB, briefly   |
| ACE-Step 1.5's model files                                             |                    | about 8 GB              |
| PyTorch with CUDA, Node modules, Song Deck's sound bank                |                    | about 5–8 GB            |

So the default (400 songs of 60 s, one take each) needs about 26 GB for the data, plus the
programs: plan on 50 GB free. Whole songs (`render.seconds: null`, 2–4 minutes each) take about
three times as much per song. Before `render` and `perform` the pipeline estimates what they
will write and refuses to start if that plus `disk.reserve_gb` (5 GB) is not free.

### Time

What has been measured (4-core machine, no GPU):

| Stage                                                | Measured                                                                                       |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Rendering a 60-second drums-only song                | about 2–4.5 s of wall clock per song on 4 cores (400 songs: about 15–30 min)                    |
| Song Deck's own part of a take (separate, align, score) | about 8–10 s per 60-second song                                                             |
| ACE-Step's part of a take                            | **not measured here**: it depends on your GPU and model. The first small run measures it        |
| Features for training (once)                         | about 4.5 min for 9.4 hours of audio                                                           |
| One training epoch on 9.4 hours of audio             | about 5.7 min on 4 CPU cores (30 epochs: about 3 hours)                                        |

A GPU is typically much faster for the training epochs; the report prints the minutes per
epoch your machine actually took, and the seconds per ACE-Step take, so after the small run you
can work out the full run: `songs × takes × seconds per take` for `perform`, and `epochs ×
minutes per epoch` (scaled by the hours of audio) for `train`.

## 1. Install the prerequisites

You need Git, Node.js 20.19 or newer (with npm), Python 3.10–3.12, CMake 3.16+ with a C++17
compiler, and for ACE-Step 1.5, [uv](https://docs.astral.sh/uv/). Python 3.13 works for training
but other tools may lag behind; 3.11 or 3.12 is the safe choice. A newer Python (3.14 or later) has no
PyTorch release yet: in the hub's TRAIN view, **Install** next to *Python and PyTorch* sets up Python
3.12 with PyTorch beside it, and uv too, with no terminal.

### Windows

Open **PowerShell** and install with `winget`:

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Python.Python.3.12 -e
winget install --id Kitware.CMake -e
winget install --id astral-sh.uv -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Close and reopen PowerShell so the new programs are on `PATH`. For an NVIDIA GPU, install the
current driver from nvidia.com (or GeForce Experience / the NVIDIA app). Two settings save
trouble later:

- Long paths: run once in an **administrator** PowerShell:
  `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force`
- Keep the checkouts and the data in folders without spaces, e.g. `C:\work\BackTrack` and
  `C:\work\backtrack-training`.

In this guide, write `python` where it says `python3` (or `py -3.12`), and `curl.exe` where it says
`curl` (in Windows PowerShell 5, `curl` is another command).

### macOS

```bash
xcode-select --install                        # Apple's compiler and Git
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # if you have no Homebrew
brew install node python@3.12 cmake uv
```

### Linux (Ubuntu or Debian; other distributions have the same packages)

```bash
sudo apt update
sudo apt install -y git build-essential cmake python3 python3-venv python3-pip curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -   # the distribution's Node is often too old
sudo apt install -y nodejs
curl -LsSf https://astral.sh/uv/install.sh | sh
```

For an NVIDIA GPU, install the driver your distribution recommends (`sudo ubuntu-drivers
install` on Ubuntu) and reboot. PyTorch brings its own CUDA libraries: you do not need the CUDA
toolkit.

### Check

```bash
git --version
node --version          # v20.19 or newer
npm --version
python3 --version       # 3.10, 3.11 or 3.12
cmake --version         # 3.16 or newer
uv --version
nvidia-smi              # NVIDIA only: shows the card, the driver and the highest CUDA version it supports
```

## 2. Python and PyTorch with CUDA

Make one folder for everything the pipeline writes, and a Python environment for training in it.

```bash
mkdir -p ~/backtrack-training
python3 -m venv ~/backtrack-training/venv
source ~/backtrack-training/venv/bin/activate          # macOS and Linux
```

```powershell
mkdir $HOME\backtrack-training
python -m venv $HOME\backtrack-training\venv
$HOME\backtrack-training\venv\Scripts\Activate.ps1     # Windows (if refused: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)
```

Then install NumPy and PyTorch. Which PyTorch depends on the machine:

```bash
python -m pip install --upgrade pip
python -m pip install numpy

# NVIDIA GPU (Windows or Linux): a CUDA build. Take the exact command from
# https://pytorch.org/get-started/locally/ (pick Pip, your OS, CUDA). It looks like:
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
#   cu128 = CUDA 12.8; use a CUDA version no higher than the one nvidia-smi shows.

# Mac (Apple Silicon): the default build has MPS.
python -m pip install torch

# No GPU: the small CPU build.
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Check that PyTorch sees the GPU:

```bash
python -c "import torch; print(torch.__version__, 'CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
python -c "import torch; print('MPS:', torch.backends.mps.is_available())"      # Mac
```

On an NVIDIA machine the first line must say `CUDA: True` and name your card. A version ending in
`+cpu` means the CPU build got installed: `python -m pip uninstall torch` and install again with
the CUDA index. Keep this environment activated whenever you run the pipeline by hand (the
scheduled runs name its Python directly).

## 3. Build BackTrack

```bash
cd ~                       # or wherever you keep code, e.g. C:\work on Windows
git clone https://github.com/jxburros/BackTrack
cd BackTrack
npm ci                                         # fetches and checks the pinned ggml
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --config Release
ctest --test-dir build -C Release              # optional: the unit tests
```

The binary is `build/backtrack-surveil` (Windows: `build\Release\backtrack-surveil.exe`; the
pipeline finds either). Check it:

```bash
build/backtrack-surveil --version
build/backtrack-surveil devices
```

Keep the default (CPU) build for the pipeline: the parity step checks the binary against PyTorch
with the tolerances measured on it. A GPU build (`-DBACKTRACK_CUDA=ON`, see
[building.md](building.md#gpu-backends)) is for using the model, not needed for training.

## 4. Set up Song Deck and its sound bank

Put Song Deck next to BackTrack (the example config looks for `../Song-Deck`):

```bash
cd ~                       # the folder that holds BackTrack
git clone https://github.com/jxburros/Song-Deck
cd Song-Deck
npm install
npm run soundbank:build    # downloads about 250 MB once, builds soundbank/ (about 60 MB)
```

The sound bank holds the recorded instruments (for drums, the MuldjordKit acoustic kit); without
it every song uses synthesized instruments, which is a poorer teacher. Behind a proxy, set
`NODE_USE_ENV_PROXY=1` first.

Try a render of two short songs:

```bash
npm run dataset:render -- --out ~/backtrack-training/try --songs 2 --seconds 10 --only-stems drums --separate
```

Its first line says how many sound bank instruments it found; then each song's line. Delete
`~/backtrack-training/try` afterwards.

## 5. Install and start ACE-Step

Song Deck talks to ACE-Step through its own provider presets; the pipeline only needs the server
running (or a command that starts it).

### ACE-Step 1.5 (recommended): its own API server

It needs Python 3.11 or 3.12 and uses [uv](https://docs.astral.sh/uv/). From
[ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5)'s README (checked 2026-10-18):

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh                                     # macOS / Linux
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows

# 2. Clone and install
cd ~
git clone https://github.com/ACE-Step/ACE-Step-1.5.git
cd ACE-Step-1.5
uv sync

# 3. Start the API server
uv run acestep-api          # listens on http://127.0.0.1:8001
```

Windows and macOS also have portable packages with everything pre-installed (links in its README).

How much GPU memory it needs, from its README: the 2B turbo model (`acestep-v15-turbo`, what Song
Deck asks for) runs in under 4 GB; 6–8 GB lets it also load its small language model; the XL models
need 12 GB or more with offloading (20 GB without). Its README's table picks the model for your
card. It also runs on Apple Silicon (MPS), AMD (ROCm) and CPU, much more slowly. The training step
waits until ACE-Step is stopped (`perform.server.stop_after`), so both never need the GPU at once.

The first start downloads its models (several GB) and can take a while. Do not start it with
`--api-key` on your own machine: Song Deck's perform step does not send a key. The Gradio app
(`acestep`, port 7860) is a different server: it is `acestep-api` you need.

Check it is up, from another terminal:

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/v1/model_inventory     # the models it has; Song Deck asks for acestep-v15-turbo
```

and that Song Deck can reach it (this changes nothing):

```bash
cd ~/Song-Deck
npm run dataset:perform -- --data ~/backtrack-training/try --provider ace-step-api --dry-run
```

(render the `try` dataset of step 4 again first if you deleted it).

### ACE-Step v1: through Song Deck's bridge

```bash
git clone https://github.com/ace-step/ACE-Step && cd ACE-Step && pip install -e .     # in its own Python environment
python3 ~/Song-Deck/bridges/acestep_bridge.py --device cuda:0                           # http://127.0.0.1:8810
```

In the config, set `perform.provider` to `ace-step-local` and `perform.url` to
`http://127.0.0.1:8810`. The bridge's `--cpu-offload` and `--overlapped-decode` options use less
VRAM at the cost of speed.

### Let the pipeline start and stop it

For unattended runs, give the pipeline the command instead of keeping ACE-Step running all the
time. It starts ACE-Step when nothing answers, waits for it (up to
`perform.server.startup_timeout_minutes`), and stops it after the perform step, which frees the
GPU for training:

```json
"server": { "command": ["uv", "run", "acestep-api"], "cwd": "~/ACE-Step-1.5", "startup_timeout_minutes": 15, "stop_after": true }
```

Its output goes to `runs/<id>/perform-server.log`. An ACE-Step you started yourself is left
running.

## 6. Write your config

```bash
cd ~/BackTrack
cp training/pipeline.example.json ~/backtrack-training/pipeline.json
```

(Windows: `copy training\pipeline.example.json $HOME\backtrack-training\pipeline.json`.) Open it
in an editor. The defaults suit a desktop with an NVIDIA GPU; check these:

| Field                     | Set it to                                                                                      |
| ------------------------- | ---------------------------------------------------------------------------------------------- |
| `songdeck`                | your Song Deck folder (relative paths are relative to the BackTrack folder)                     |
| `python`                  | the venv's Python, e.g. `~/backtrack-training/venv/bin/python` (Windows: `C:/Users/you/backtrack-training/venv/Scripts/python.exe`) so scheduled runs use it |
| `dataset`, `runs_dir`     | keep `~/backtrack-training/...`, or a bigger disk                                               |
| `perform.server`          | the ACE-Step command (above), or leave `command` `null` and start ACE-Step yourself             |
| `perform.enabled`         | `false` if you have no GPU for ACE-Step (also remove `performed` from `train.audio`)            |
| `train.device`            | `auto` picks CUDA, then MPS, then CPU                                                           |

Every field is explained in [training/README.md](../training/README.md#the-config-field-by-field).
JSON has no comments; keys starting with `_` (such as `"_comment"`) are ignored. Use forward
slashes in Windows paths (`C:/work/Song-Deck`) or double backslashes.

Then let the pipeline check everything without doing anything:

```bash
python -m training.pipeline --config ~/backtrack-training/pipeline.json --dry-run
```

It prints every command it would run and a line starting `[pipeline] problem:` or `WARNING:` for
anything missing (Song Deck not installed, no binary, no PyTorch, ACE-Step not answering, too
little disk).

## 7. A first small run

Before the long run, do a small one in its own folders, so its model never competes with the
real ones. Save this as `~/backtrack-training/small.json` (fix the paths):

```json
{
  "_comment": "A small first run: 24 short songs, ACE-Step on 6 of them, 2 epochs",
  "songdeck": "../Song-Deck",
  "python": "~/backtrack-training/venv/bin/python",
  "dataset": "~/backtrack-training/small/data",
  "runs_dir": "~/backtrack-training/small/runs",
  "render": { "songs": 24, "seconds": 30, "chunk": 24 },
  "perform": { "takes": 1, "songs": 6, "server": { "command": ["uv", "run", "acestep-api"], "cwd": "~/ACE-Step-1.5" } },
  "train": { "epochs": 2, "patience": null }
}
```

Any field left out takes the example's default. 24 songs with the default seed give all three
splits (18 train, 4 valid, 2 test); fewer songs may have no test song, and evaluation needs one.

```bash
cd ~/BackTrack
python -m training.pipeline --config ~/backtrack-training/small.json --dry-run
python -m training.pipeline --config ~/backtrack-training/small.json
```

What you will see, step by step:

- `render`: Song Deck's lines, one per song, then `pruned separated stems other than drums`.
- `perform`: `waiting for ACE-Step ...`, `ACE-Step is up`, a line per take ending in its
  agreement and how many windows passed, and at the end `N takes in M min: S s per take`. **This
  is your ACE-Step time per take**: the full run's perform step takes about `400 × S` seconds per
  take of each song.
- `train`: the data summary (including `performed: ... min accepted`), one line per epoch with
  its time, and `train_drums: OK wrote ...`.
- `evaluate`, `parity`: the scores and `parity ok`.
- `report`: `PROMOTED to runs/best: ...` (the first model always is: nothing to beat) and the
  path of `report.md`.

Everything is in `~/backtrack-training/small/runs/<date-time>/`: one log per step, the model,
the evaluation JSONs, `report.md`. If a step fails, the run stops there with the log's path and
the command to resume it once fixed (`--run <id>` or `--run latest`).

Two epochs on 24 short songs make a poor model; this run proves the setup and gives the timings.
Delete `~/backtrack-training/small` when you are done.

## 8. Reading the report

`runs/<id>/report.md` (also `report.json`, for scripts) starts with the verdict:
**PROMOTED to runs/best** or **not promoted** and why. Then:

- **Data**: songs per split, ACE-Step takes, the mean agreement, and how many minutes of the
  covers training trusted.
- **Separated test split, through the binary**: the onset F-measure (0–1, higher is better; a hit
  counts when found within ±50 ms) per drum class for **this run**, the **previous best** (scored
  again on the same songs, so the comparison is fair even as the dataset grows) and **Song Deck's
  built-in** drum transcription. This is the input Song Deck sends (drums separated from a mix)
  and the table that decides promotion (`promote.metric`, by default the mean class F). A `–` is
  a class with no hits in the test songs.
- **Performed test windows**: the same on ACE-Step's covers of the test songs, only where they
  are trusted.
- **Song Deck's harness**: Song Deck's own evaluation (`npm run eval:datasets`) of its built-in
  transcriber and this run's model served by `backtrack-surveil serve`, side by side.
- **Parity**, the **training history** (loss and valid score per epoch), the **steps** with
  their times (seconds per song rendered, per take), failed takes, and the model's licence record.

What to look for: the mean class F of this run above the previous best and above Song Deck's
built-in; no class far below the others (open hi-hats and crashes are the hard ones); valid
scores still rising at the last epoch (more epochs would help) or falling long before it (fewer
would do, and `train.patience` stops early).

## 9. The full run

With the small run working:

```bash
python -m training.pipeline --config ~/backtrack-training/pipeline.json --dry-run
python -m training.pipeline --config ~/backtrack-training/pipeline.json
```

The defaults: 400 songs of 60 s, one ACE-Step take each, 30 epochs (stopping after 8 without
improvement). Leave it overnight. You can stop it with Ctrl+C at any time; resume with:

```bash
python -m training.pipeline --config ~/backtrack-training/pipeline.json --run latest
```

Finished steps are skipped, the render and perform steps carry on where they stopped, and
training continues from its last epoch (as long as the training settings are unchanged).

To grow the data later, raise `render.songs` (new songs are added, nothing is rendered twice) or
`perform.takes` (more covers of each song), and run again. A run where nothing changed since the
last one does not train again (`train.skip_if_unchanged`).

Already have a dataset rendered with the same options (the same seed, seconds, stems)? Point
`dataset` at it and the render step keeps it.

## 10. Automate it

`schedule --print` writes out what your system needs, with the paths of your machine filled in.
Nothing is installed by it: you copy what it prints. Use the venv's Python with `--python`.

```bash
python -m training.pipeline schedule --print linux   --config ~/backtrack-training/pipeline.json --time 02:30 --python ~/backtrack-training/venv/bin/python
python -m training.pipeline schedule --print macos   --config ~/backtrack-training/pipeline.json --time 02:30 --python ~/backtrack-training/venv/bin/python
python -m training.pipeline schedule --print windows --config $HOME\backtrack-training\pipeline.json --time 02:30 --python $HOME\backtrack-training\venv\Scripts\python.exe
```

A nightly run that suits automation: set `render.grow_per_run` (e.g. 50 new songs a night, up
to `render.max_songs`), and `perform.server.command` so ACE-Step is started and stopped. Each
night then adds songs and covers, trains, and keeps the model only if it is better. When nothing
is new, the run ends in a minute. A lock (`runs/pipeline.lock`) makes a run that starts while
another is still going stop at once.

### Linux: cron

`crontab -e`, paste the two lines printed (the `PATH` line and the schedule line), save. The
output goes to `runs/scheduled.log`. To stop: `crontab -e` and delete or comment out the line.

### Linux: a systemd user timer

Save the two printed units as `~/.config/systemd/user/backtrack-training.service` and
`backtrack-training.timer`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now backtrack-training.timer
loginctl enable-linger $USER                        # so it runs while you are logged out
systemctl --user list-timers                        # when it runs next
systemctl --user start backtrack-training           # run it now
journalctl --user -u backtrack-training -f          # watch it
systemctl --user disable --now backtrack-training.timer     # stop the schedule
```

`Persistent=true` runs a missed night after the computer was off.

### macOS: launchd

Save the printed plist as `~/Library/LaunchAgents/com.backtrack.training.plist`, create the log's
folder (`mkdir -p ~/backtrack-training/runs`), then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.backtrack.training.plist
launchctl kickstart gui/$(id -u)/com.backtrack.training      # run it now
launchctl bootout gui/$(id -u)/com.backtrack.training        # stop the schedule
```

A Mac that sleeps at the scheduled time runs it when it wakes. To wake it for the run:
`sudo pmset repeat wakeorpoweron MTWRFSU 02:25:00`.

### Windows: Task Scheduler

The printed commands run [`training/schedule/run-pipeline.ps1`](../training/schedule/run-pipeline.ps1),
which starts the pipeline from the BackTrack folder, appends its output to `runs\scheduled.log`
and returns its exit code (Task Scheduler's **Last Run Result**: 0 is success). Run either:

- the `schtasks /Create ...` line, in a normal PowerShell or Command Prompt; or
- the four PowerShell lines (`Register-ScheduledTask`), which also wake the computer
  (`-WakeToRun`) and run a missed night when it is next on (`-StartWhenAvailable`).

```powershell
schtasks /Run /TN "BackTrack training"                 # run it now
schtasks /Query /TN "BackTrack training" /V /FO LIST   # status and last result
schtasks /Change /TN "BackTrack training" /DISABLE     # pause (/ENABLE to resume)
schtasks /Delete /TN "BackTrack training" /F           # remove
```

The task runs only while you are logged in unless you tick **Run whether user is logged on or
not** in Task Scheduler (taskschd.msc → the task → Properties). Wake timers must be allowed in
the power plan (Control Panel → Power Options → Change plan settings → Advanced → Sleep → Allow
wake timers).

### Stopping a run in progress

Ctrl+C in a terminal; `systemctl --user stop backtrack-training`; `launchctl kill SIGTERM
gui/$(id -u)/com.backtrack.training`; or End in Task Scheduler. The run is marked interrupted
and resumes with `--run latest`. If the computer crashed and a lock is left behind, the next run
removes it when its process is gone; otherwise delete `runs/pipeline.lock` yourself.

## 11. Use the trained model

**The easy way:** in the TRAIN tab, **Install in Song Deck** (above). The rest of this section is
the same by hand, and serving the model to other programs.

Every run also writes the model as a Song Deck model plugin, in
`runs/<id>/songdeck-plugin/surveil-<kind>-trained/` (and in `runs/best/` for the best model): copy
that folder into `~/.songdeck/plugins/` and switch it on under Settings → Plugins.

The best model so far is in `~/backtrack-training/runs/best/` (with its report). Serve it:

```bash
~/BackTrack/build/backtrack-surveil serve --models-dir ~/backtrack-training/runs/best      # http://127.0.0.1:8826
```

Try it on a file: `build/backtrack-surveil song.wav --stem drums --models-dir
~/backtrack-training/runs/best -o drums.mid`, or open http://127.0.0.1:8826 for the page.

In Song Deck: Services → Connections → Connect a service → **BackTrack Surveil** (preset
`backtrack-surveil`, `http://127.0.0.1:8826`), then turn on **Use specialist transcribers** in the
separation settings (Quality). Song Deck sends drum stems to it. If Song Deck installed its own
copy (Settings → Models and hardware), stop that one or start yours on another port
(`--port 8827`) and set that address in the connection.

### Making it the release model

The pipeline never writes `weights/`. To make a model the one in the repository:

1. Give it the release version **before** the run that makes it: `model.version` in the config
   (the version is written inside the file, `general.version`).
2. Copy it: `cp ~/backtrack-training/runs/best/surveil-drums-<version>.gguf weights/surveil-drums-<version>.gguf`.
3. Check it: `python -m training.export --check weights/surveil-drums-<version>.gguf` (name,
   version, sources) and `python -m training.parity --binary build/backtrack-surveil --model
   weights/surveil-drums-<version>.gguf`.
4. Commit it (with its report's numbers in the commit message or changelog), following the
   repository's release steps.

## Troubleshooting

**`CUDA: False`, or training says `on cpu`.** The CPU build of PyTorch is installed (its version
ends in `+cpu`), the driver is missing or too old, or another Python is running the pipeline. Run
the check in [step 2](#2-python-and-pytorch-with-cuda) with the Python named in the config's
`python`. Reinstall with the CUDA index URL, with a CUDA version no higher than `nvidia-smi`
shows. `train.device: cuda` makes a missing GPU an error instead of a silent CPU run.

**Out of GPU memory (CUDA out of memory).** ACE-Step and training both want the GPU. If you
started ACE-Step yourself it keeps its memory during training: let the pipeline start and stop it
(`perform.server.command`), or stop it before the train step. Lower `train.batch` (16 is small
already; the model itself is tiny). For ACE-Step itself: the turbo model, shorter songs
(`render.seconds`), and for the v1 bridge `--cpu-offload` / `--overlapped-decode`.

**ACE-Step timeouts or a take that fails.** Each take may take up to `perform.timeout_minutes`
(20); a stuck take is given up and the next one goes on. Failed takes are listed in the report and
tried again on the next run (`--skip-existing`); with `perform.allow_failed_takes: false` they stop
the run. Look at `perform.log` and `perform-server.log` (or ACE-Step's own terminal). The first
take after a start is slow while the models load. A server on another computer: set `perform.url`.

**`nothing answers at http://127.0.0.1:8001`.** ACE-Step is not running, runs on another port,
or is the Gradio app (7860) instead of `acestep-api`. Start it, or set `perform.server.command`.
`perform.optional: true` carries on without new covers instead of stopping.

**Disk full, or `not enough free disk`.** Free space or move `dataset` and `runs_dir` to a larger
disk (copy the dataset folder as a whole). Safe to delete: old run folders (keep `runs/best`),
and `<dataset>/.cache` (the feature cache, rebuilt when needed; old entries pile up when files
change). A Windows tip: exclude the dataset folder from Defender's real-time scanning to speed up
rendering.

**Few performed minutes accepted (low agreement).** Each cover is trusted only in windows where
the judge's transcription of its drums agrees with the song's notes (F ≥ `train.min_agreement`,
0.6). Song Deck's built-in drum transcription is a weak judge on drums separated from a full
mix, so early on many windows fail even where ACE-Step played well. Once a trained model beats
the built-in transcription in a report, the pipeline judges with it (`perform.judge: auto`) and
re-scores the existing covers after each new best (`rejudge`). Also: a lower `perform.strength`
makes ACE-Step follow the song more closely; lowering `train.min_agreement` (e.g. 0.5) accepts
more but noisier labels. `no performed test take passed the agreement thresholds` in the evaluate
step is not an error: that part of the report is left out.

**`the dataset ... was rendered with other options`.** Song Deck refuses to grow a dataset with
different render options. Set them back (the manifest's `options` shows them) or use a new
`dataset` folder.

**`another run holds ... pipeline.lock`.** A run is going (look for it), or a crashed one left
the lock on another computer sharing the folder. Delete the file if no run is going.

**Windows: `npm` not found, or odd quoting errors.** Open a new terminal after installing Node.
The pipeline runs `npm.cmd`; to name it exactly, set `"npm": "C:/Program Files/nodejs/npm.cmd"`.
Keep paths free of spaces and of `&`, `%` and `^`. `python` not found in a scheduled task: give the
full path with `--python` when printing the schedule. `running scripts is disabled`: the printed
commands pass `-ExecutionPolicy Bypass`; for your own terminal, `Set-ExecutionPolicy -Scope
CurrentUser RemoteSigned`.

**`backtrack-surveil serve did not start` or `the server found drum models [...]`.** The binary is
not built (step 3), or a `models` folder next to the binary holds other drum models: move them.

**A step failed.** The message names the log: `runs/<id>/<step>.log`. Fix the cause and resume
with `--run <id>` (or `--run latest`); `--steps evaluate,report` with `--run` runs those steps
again.

## Licence

The owner checked ACE-Step's licence and decided (2026-10-18) that its output may be used as
training data, and that a model trained on it may ship. A model trained with covers records
ACE-Step (provider and model) in its `audio.sources`, and may become a release model if it wins
in the report. If you switch ACE-Step versions or models, check that version's licence again
before training on its output. Song Deck's renders come from its own compositions and
instruments (the sound bank's licences are in its `soundbank/CREDITS.txt`).
