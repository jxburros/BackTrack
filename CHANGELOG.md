# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [0.3.1] - 2026-10-19

### Added

- **TRAIN: Install buttons** for what's missing. **Install** next to *Python and PyTorch* sets up
  Python 3.12 (uv fetches it if there is none) with the PyTorch build for your GPU (CUDA with an
  NVIDIA card, the default on a Mac, else CPU) in the training folder's `venv`; next to *ACE-Step*
  it installs uv. Several steps run one after another, their output in the log.

### Fixed

- **TRAIN setup check** reported "no Python found" for any Python without PyTorch (it couldn't read
  the probe's answer), so it never told you to install PyTorch. It now finds Python itself: the `py`
  launcher (3.12 before a newer, PyTorch-less one), conda, `~/backtrack-training/venv` and
  ACE-Step's own `.venv`, and uses whichever one has PyTorch.
- **TRAIN** found `uv`, Python and Node only on the PATH the app started with. On Windows it now also
  reads the user and system PATH from the registry, `~/.local/bin` and python.org's install folders.
- **TRAIN** finds the Song Deck and ACE-Step folders by what's in them (also unzipped downloads such
  as `Song-Deck-main/Song-Deck-main`) in your home, Documents, Downloads and Desktop, instead of
  asking, and uses them for the check, runs and fixes.

## [0.3.0] - 2026-10-19

### Added

- **TRAIN in the BackTrack hub.** Trains Surveil's drum model without a terminal: choose the Song
  Deck and ACE-Step 1.5 folders, **Check setup** (BackTrack Surveil, Python with PyTorch and its
  GPU, Node.js, Song Deck, its sound bank, ACE-Step; it installs Song Deck's dependencies and sound
  bank itself), then **Start training**. Shows each step, the output and the report, with **Stop**
  and **Resume last run**. The hub carries the training code; ACE-Step is started for each run and
  stopped before training.

### Fixed

- The training pipeline runs without git installed, and on Windows no longer opens a console
  window for each program it starts when run from the hub.

## [0.2.0] - 2026-10-19

### Added

- **Desktop apps** (`desktop/`, on Tauri 2): *BackTrack Surveil* in its own window, with the drum
  model built in, and *BackTrack*, the hub from the design spec, which finds the installed apps
  and shows each in a tab; a track dropped on the hub opens in the app you pick. Installers for
  Windows (per-user NSIS), macOS (`.dmg`, ad-hoc signed) and Linux (`.deb`, `.AppImage`), built
  by `.github/workflows/desktop.yml` and attached to releases. Downloads from the pages open a
  save dialog. Not yet code-signed or notarized. `docs/desktop.md`.
- `backtrack-surveil serve --watch-stdin`: stop when standard input closes, so a server started
  by a desktop app never outlives it.

### Fixed

- **Double-clicking `backtrack-surveil.exe` on Windows** no longer flashes a window and exits: it
  starts the server and opens the page in the browser. The window stays open while the page is
  in use, and stays open on an error until Enter is pressed. From a terminal nothing changes.

## [0.1.0] - 2026-10-19

Milestone M3: the training pipeline, Surveil's first trained model (drums), release binaries and
Song Deck's preset.

### Added

- **Surveil drums 0.1.0** (`weights/surveil-drums-0.1.0.gguf`): seven drum classes, 89,567
  parameters, trained on 9.4 h of Song Deck renders. On 38 held-out songs' separated drums it
  scores F 0.813 against Song Deck's built-in drum transcription's 0.495, and beats it on every
  class (`weights/README.md`).
- **Training pipeline** (`training/`, PyTorch): the harmonic CQT front end matching the C++ one,
  the `surveil-cnn` graph, GGUF export and reading, readers for Song Deck's exported datasets,
  E-GMD and synthetic drums, a feature cache, drum training on CPU, CUDA or MPS, and evaluation
  through the binary. The parity check runs the same clips through PyTorch and the binary (CI
  runs it on every push).
- **Training on ACE-Step performances** (`--audio performed`): labels follow each take's offset,
  and only the windows whose drums agree with the song's notes count.
- **One command for training at home** (`python3 -m training.pipeline`): renders, has ACE-Step
  perform, trains, evaluates against the previous best and Song Deck's built-in, and reports;
  schedulable on Linux, macOS and Windows. Step by step in `docs/training-at-home.md`.
- **Release workflow** (`.github/workflows/release.yml`): `backtrack-surveil` for linux-x64,
  darwin-arm64, darwin-x64 and win32-x64 (CPU builds), with the models and `checksums.txt`, in
  Onyx Engine's asset convention, which Song Deck's one-click install pins. GPU builds (Vulkan,
  Metal) come later: they failed on the first release run and need a CPU fallback first.
- `--activations` and `--hcqt` outputs; an optional deeper trunk and a temporal drum head
  (per-class onset kernels); chunk context from the receptive field.
- Song context: `pitch_range` and `monophonic`, which Song Deck now sends.

## [0.0.1] - Unreleased

### Changed

- **The page follows the design language** (`design/AGENT-SPEC.md`). Surveil opens in the
  `wiretap` theme: CCTV screen with scanlines, REC and a running CAM timestamp while
  transcribing, a torn drop zone, a bar waveform of the dropped file with a play button, a
  progress bar with the stage, percentage and seconds, a case file with redacted
  unknowns, and a piano roll with 25 px lanes, beat and bar lines from the tempo, and confidence
  shown as brightness (arrow keys step through notes and read out their numbers). Plain DONE and
  FAILED states with the cause and the fix, toasts, and a light mode (`xerox-riot`, remembered as
  `bt-theme`). The fonts (Anton, Space Mono, Permanent Marker, Big Shoulders Stencil Display,
  VT323) are bundled, so the page loads nothing from the network.
- **Docs in the house style.** A hero for the README, a banner for each doc, and diagrams drawn
  from the real numbers: how the repository fits together, Surveil's stages and where they sit
  on the progress bar, one HTTP job from start to finish, and where one note lands in each
  harmonic CQT channel. New `docs/page.md` walks through the page with a marked-up screenshot.
  All drawn with Vixl (`design/source/docs_art.py`), editable in `design/source/vixl/docs/`.
- **The server serves the page's files.** `ServerOptions::page_html` is replaced by `page` (every
  file in `ui/`, embedded by `cmake/EmbedPage.cmake`) and `theme`; the page's files need no API key.

Milestone M2: the repository, shared core, server and page, with BackTrack Surveil running a
placeholder model end to end.

### Added

- **Build.** CMake on standalone ggml `36da571` (the revision Onyx Engine's llama.cpp pin last
  synced), vendored with Onyx Engine's scripts and a `packaging/vendor/ggml.conf` spec; GPU
  switches (`BACKTRACK_CUDA`, `_VULKAN`, `_METAL`, `_HIP`, `_BLAS`) as in Onyx Engine.
- **`core/`.** WAV, FLAC and MP3 decoding from memory (dr_libs), band-limited resampling, WAV
  writing, an FFT, the harmonic constant-Q front end (documented step by step in
  `docs/front-end.md`), key and chord parsing, Song Deck's tempo and key estimators, a Standard
  MIDI File writer with pitch bends, GGUF and device helpers, and progress and cancellation.
- **`server/`.** Song Deck's bridge rules in C++: loopback by default, DNS-rebinding
  protection, optional bearer key, CORS for the Song Deck origins, JSON errors, one job at a
  time with a queue, cancellation on disconnect and `POST /cancel`, progress in `/health`.
- **`ui/`.** The page the binaries serve: drop a file, choose the stem and context, watch
  progress, preview a piano roll in time with the audio, download MIDI or JSON.
- **BackTrack Surveil** (`backtrack-surveil`): CLI, `serve` (port 8826) with `POST /transcribe`,
  `GET /info`, `GET /health`, `POST /cancel`; the `surveil-cnn` graph on ggml with GGUF weights;
  built-in placeholder models (pitched and drums) and `make-placeholder` to write them;
  note picking with onsets, a best-path search for monophonic stems, pitch bends, drum hits
  and song-context priors (key, chords, instrument range, tempo grid).
- **Contract proposal.** An optional `context` field on `POST /transcribe` (key, tempo, chords,
  program), described in `docs/http-api.md` for Song Deck to adopt.
- Unit tests (`tests/core_tests.cpp`) and an end-to-end smoke test (`scripts/smoke-test.py`).
- Plans moved here from Song Deck as `docs/plans/`, with the app names decided.
