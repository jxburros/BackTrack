# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- **BackTrack Explode** (0.7.0, milestone S0), the stem separator, on the shared core:
  `backtrack-explode song.wav` writes vocals, drums, bass and other (or vocals and instrumental,
  `--stems vocals,instrumental`) as 44.1 kHz stereo WAV files that sum back to the song, and
  `backtrack-explode serve` answers Song Deck's `POST /separate` on port 8827, with `stems` and
  `on_request_stems` in `GET /info`, and serves its page in the blast-zone theme: drop a song,
  DETONATE, then play the stems together with mute and solo and save them. It works in 20 s
  chunks with crossfades, so long songs use little memory and report `chunk 3/12, pass 1/2`
  progress, and `--augment` averages a polarity-flipped, channel-swapped second pass. The model is
  a placeholder (soft masks from harmonic/percussive filtering, the low band and the stereo
  centre): rough stems, for trying the app and its wiring until S2's trained model. Not in
  releases yet (S3).
- `core/`: a short-time Fourier transform with an exact inverse (`backtrack::Stft`), both stereo
  channels in one complex FFT per frame.

### Changed

- Surveil's command-line helpers moved to `apps/common/cli.h`, shared with Explode. No change in
  behaviour.

### Fixed

- **Release publishing** (0.6.1): the release workflow and validation pinned release-infrastructure
  `181636b`, whose publisher looked up the draft it had just created by tag (`GET
  releases/tags/<tag>` never returns drafts), so the v0.5.1 run failed with a 404 after building
  everything and left an empty draft. Both pins now point at `d7f42f9`, which finds drafts in the
  release list (the same move Onyx Engine made); the reusable workflows' interfaces are unchanged.

### Added

- **Bass and melody models** (`weights/surveil-bass-0.1.0.gguf`, `weights/surveil-melody-0.1.0.gguf`),
  the first trained pitched models, trained at home from the TRAIN tab. On 60 unseen rendered
  songs per part, against Song Deck's built-in transcription of the separated stems: bass note F
  0.472 against 0.211; melody 0.917 against 0.757 on instrumental leads and 0.684 against 0.372 on
  Song Deck's guide singer ([weights/README.md](weights/README.md)). Releases attach them beside
  the drum model, and the desktop apps ship them.
- `scripts/make-test-audio.py` also writes `lead.wav`, the test melody with eight partials: the
  melody model hears the thinner `melody.wav` as no lead at all. The WebAssembly check and the
  release workflow run each shipped model on its own part's clip.
- **Worse recordings in the TRAIN tab:** tick the degradation kinds (room, noise, mic, compress,
  clip, wow, codec, or all of them), the copies of each song (1–4) and optionally a folder of your
  own noise recordings, and the hub writes them to the pipeline's `train.augment`, so models
  trained from the desktop app also work on phone, live and rehearsal recordings, old tapes and
  low-bitrate MP3s. Off by default: runs without it are unchanged. **Check setup** says when
  ffmpeg is missing for the MP3 and Opus copies.

- **Chord and key labels** for the theory models (milestone C0, `training/chords.py`): chord
  symbol parsing, the map from Song Deck's 28 chord qualities to the 14 the built-in detector
  knows, per-frame root, quality, bass and key targets, and the weighted chord symbol recall and
  key score as Song Deck defines them; `datasets.read_songdeck_chords` reads a dataset's chords
  and keys. Checked in CI by `training/check_chords.py`. The baseline to beat, Song Deck's
  built-in detector on 60 unseen rendered songs: root 0.591, sevenths 0.441, key 0.785.

- **Model version in the TRAIN tab:** each kind of model keeps its own version, written into the
  model and its file name. It starts at the next minor after the released model that comes with
  BackTrack (drums 0.3.0, bass and melody 0.2.0) and is saved, so it stays put between runs and
  across updates until you change it. The tab used to write 0.1.0 into every model, so a retrained
  drum model collided with the released drums 0.1.0. **Check setup** fails on a version that isn't
  three numbers and warns on one that reuses a released version.

### Changed

- **Drums 0.2.0** (`weights/surveil-drums-0.2.0.gguf`) replaces 0.1.0: the same network trained
  on more songs and on ACE-Step performances, F 0.848 against 0.837 for 0.1.0 and 0.527 for Song
  Deck's built-in transcription on 60 unseen rendered songs.
- **ggml `f1aa6a3`** (0.27.0, was `36da571`), the revision Onyx Engine's llama.cpp pin (b11820)
  synced. Every model still passes the PyTorch parity check; likelihoods move by up to 3e-3 and
  three of about 1,800 test notes change.

- **Degradation augmentation** (`training/augment.py`): `--augment room,noise,mic,compress,clip,wow,codec`
  (or `all`) and `--augment-copies N` on `train_drums` and `train_pitched` train on degraded copies
  of every training item as well as the clean ones, so models also work on phone, live and
  rehearsal recordings, old tapes and low-bitrate MP3s: a synthetic room, noise 10–30 dB below the
  item (white, pink, hum, room tone, crowd, or your own recordings), a phone's mic, heavy
  compression and clipping, wow and flutter, and MP3 or Opus through ffmpeg (skipped with a warning
  without it). Copies are made once before the feature cache, each under its own key, and keep the
  labels' timing (checked by `training/check_augment.py` in CI). Off by default: runs without it
  are unchanged. The pipeline's `train.augment` turns it on, and `evaluate.bad_recordings` scores a
  set of real bad recordings beside the clean test split in the report.

### Release infrastructure migration — 2026-10-20

- Adopt the unified release manifest, version-advance validation, manual main-only planning, complete artifact staging, and verified draft publisher. Preserve local packaging, existing updater trust, and distribution identities.
- Validation: shared engine tests, workflow lint, and prepared manifest/version checks. Native/hosted consumer release dry runs and publication remain unverified until the migration is merged and credentials/settings are checked.

## [0.5.0] - 2026-10-20

### Added

- **Training libraries** (`training/libraries.py`): a catalogue of outside datasets with their
  checked licences (training allowed, share-alike, evaluation only, not usable), downloading
  (Zenodo and Magenta, resumable, checksummed) and importing as Song Deck-format datasets:
  Slakh2100 and BabySlakh (drums, bass, melody stems with MIDI), E-GMD and the Groove MIDI Dataset.
  The training scripts read them with `--library DIR` (`--library-audio stem,mix`), their licence
  lines go into the model's `audio.sources`, and the pipeline's new `libraries` step fetches and
  imports the ones in `libraries.use`. The desktop TRAIN tab has **Also train on** boxes for them.
- **Training queue** (`training/queue.py`): several pipeline runs one after another, optionally
  from a set time (`--start-at 23:30`), stoppable and resumable (`--resume`). The desktop TRAIN tab
  has a **One after another** panel that runs it and follows the model training now.
- `docs/training-data.md`: every dataset, sound bank and paid source researched for training, with
  licences, prices and how to use each.

### Changed

- The pipeline asks Song Deck for only the separated stems it keeps (`dataset:render
  --separate-stems`, Song Deck 0.19.0 or later), instead of deleting the others afterwards.

- `docs/plans/theory.md`: plan for theory models (milestone M7): a chords and key model in
  Surveil, a harmony model in a new app, how both are trained, and a shareable interface.

## [0.4.0] - 2026-10-19

### Added

- **Surveil in WebAssembly** (milestone T4, `wasm/`): the same pipeline built with Emscripten,
  with a JS wrapper (`createSurveil()`), for Song Deck's job worker. It gives the native binary's
  notes (checked in CI by `wasm/test.mjs`), transcribes a 4-minute song in about 22 s on one
  thread, and reports progress and cancels between chunks. Releases attach it as
  `backtrack-surveil-wasm-<tag>.zip`. `scripts/install-emsdk.sh` installs the pinned Emscripten.
- **ACE-Step takes for bass and melody.** `train_pitched --audio performed` trains on takes Song Deck
  judged for that part (`dataset:perform --parts`), inside their accepted windows only. The pipeline
  makes and judges them for the kind being trained, with the best model of that kind as the judge
  once it beats Song Deck's built-in transcription, and scores the performed test windows. The TRAIN
  tab offers ACE-Step for every kind.
- **TRAIN trains bass and melody too.** **What to train** picks drums, bass or melody (lead
  lines and vocals); each keeps its own songs and runs. The pipeline's `model.kind` renders those
  stems, trains with `train_pitched`, scores by note F and compares with Song Deck's own
  transcription (Song Deck runs the model itself). Every run writes the model as a Song Deck model
  plugin, and **Install in Song Deck** puts the best one into Song Deck's plugins folder, with no
  terminal involved.
- `GgufFile::open_memory`: models load from bytes as well as files.
- **Pitched training** (milestone T5, `training/train_pitched.py`): bass, melody and vocal models from
  Song Deck's rendered stems (`--stem bass`, `--stem melody,vocals`), with note, onset, offset and
  contour targets (bends included), the valid note F as the binary picks notes, and the drum
  trainer's cache, resume, patience and records. `evaluate.py` scores pitched models (note F,
  through PyTorch or the binary) and `parity.py` checks the pitched note picking's Python port
  exact against the binary. CI trains, checks and scores a toy bass model.

### Changed

- The pipeline keeps a configured Python's path as given instead of following its symlink, which
  had turned a venv's `bin/python` into the system Python, without PyTorch, on macOS and Linux.

- **Resampling is about 5× faster** for the usual rates (44.1 and 48 kHz to 22.05 kHz): each
  phase's kernel is computed once. Results are unchanged (within 3e-8).

## [0.3.3] - 2026-10-19

### Fixed

- **Training pipeline** crashed with `UnicodeEncodeError` on a Windows console (cp1252) as soon as a
  step's output contained a character like `→` (the render step hit it right away). Output the
  console can't show is now replaced instead of aborting the run; the run log keeps it as UTF-8.

## [0.3.2] - 2026-10-19

Version bump for the release; no code changes since 0.3.1.

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
