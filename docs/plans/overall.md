# Overall plan: separator, transcriber, JX Runtime and Song Deck

> **Names (decided).** The audio-apps repository is **BackTrack**
> ([jxburros/BackTrack](https://github.com/jxburros/BackTrack)); the separator is **BackTrack
> Explode** (`backtrack-explode`, port 8827) and the transcriber is **BackTrack Surveil**
> (`backtrack-surveil`, port 8826). This copy of the plan moved here from Song Deck's
> `docs/plans/`; "separator" and "transcriber" below mean Explode and Surveil.

Written 2026-10-18 and updated the same day for the round-trip lab (Song Deck 0.15.0). This is
the plan that ties the others together. Each project has its own
plan with the detail:

| Plan                                    | Project                                                                       |
| --------------------------------------- | ----------------------------------------------------------------------------- |
| [Song Deck](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/song-deck.md)             | This repository                                                               |
| [JX Runtime](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/jx-runtime.md)           | [jxburros/JX-Runtime](https://github.com/jxburros/JX-Runtime)                 |
| [Onyx Engine](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/onyx-engine.md)         | [JX-Holdings-LLC/Onyx-Engine](https://github.com/JX-Holdings-LLC/Onyx-Engine) |
| [Transcriber app](./surveil.md) | BackTrack (this repository): BackTrack Surveil                                |
| [Separator app](./explode.md)     | BackTrack (this repository): BackTrack Explode                                |
| [Theory models](./theory.md)      | BackTrack (this repository): chords and key in Surveil; harmony app (2026-10-20) |

The plans live here because Song Deck is the hub. Move each one into its own repository once
that repository exists.

What is done, in progress and next across these plans, with the open issues, is summed up in
the [roadmap](../../ROADMAP.md).

## The goal

1. **Two standalone apps**, a stem separator and a MIDI transcriber, built from one repository.
   Each works on its own (command line, local web page, later a double-click app) with no Song
   Deck involved.
2. **Both are options inside Song Deck.** Song Deck talks to them through the separation and
   transcription bridge contracts it already has. The transcriber can also run inside Song Deck
   with nothing to install.
3. **Song Deck supports JX Runtime** for local language models, and eventually uses it as its
   default local runtime. Later, JX Runtime installs and runs the two audio apps too.
4. **Everything shippable is commercially safe:** own code, own model weights, trained only on
   data whose licence allows commercial use.

## How the pieces fit

```
                         ┌──────────────────────────── Song Deck ────────────────────────────┐
                         │  LLM roles ──► JX Runtime preset (OpenAI-compatible, pairing)     │
                         │  Separation ─► separator bridge preset  (/separate)               │
                         │  Transcription ► transcriber bridge preset (/transcribe)          │
                         │               └► transcriber WebAssembly, in the page             │
                         └───────────┬─────────────────────────┬────────────────────────────┘
                                     │                         │
                     ┌───────────────▼─────────┐   ┌───────────▼───────────────────────────┐
                     │ JX Runtime              │   │ Audio-apps repository (new)           │
                     │  keys, pairing, TLS,    │   │  core/ C++ on ggml, GGUF weights      │
                     │  installs, residency    │──►│  apps/transcriber  apps/separator     │
                     │  backends: llama.cpp,   │   │  CLI · HTTP server · web page · WASM  │
                     │  onyxengine, mlx, vllm, │   │  training/ (Python → GGUF)            │
                     │  + audio engines (new)  │   └───────────────────────────────────────┘
                     └───────────┬─────────────┘                ▲
                                 │ spawns                       │ build and release
                     ┌───────────▼─────────────┐                │ pattern copied from
                     │ Onyx Engine             │────────────────┘
                     │  LLM server on llama.cpp│
                     └─────────────────────────┘
```

## Decisions already made

| Decision                  | Choice                                                                                                         |
| ------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Repositories for the apps | **One** repository holding both apps on a shared core                                                          |
| Inference engine          | Own C++ code on **ggml** (the tensor library under llama.cpp and Onyx Engine). No ONNX, no Python at run time. |
| Weight format             | **GGUF**, with metadata naming the architecture, stems or classes, sample rate and licence                     |
| Builds                    | **Native** (CPU, plus CUDA, Vulkan or Metal where available) and **WebAssembly** from the same code            |
| Song Deck integration     | The existing bridge contracts (`packages/ai/src/contracts.ts`). Song Deck owns them; the apps implement them.  |
| Models                    | Trained in-house: on audio Song Deck renders itself, plus datasets that allow commercial use                   |
| Training code             | A `training/` folder in the audio-apps repository (Python), exporting GGUF                                     |
| Commercial status         | Treat everything as a commercial product                                                                       |
| ACE-Step output           | Allowed as training data (licence checked by the owner, 2026-10-18); recorded in each model's `audio.sources`  |

## Layout of the audio-apps repository

```
core/            audio decode (WAV/FLAC/MP3), resampling, STFT/CQT, ggml helpers, GGUF loading,
                 model metadata, progress and cancellation
server/          shared HTTP layer (bridge contract, /info /health /cancel, loopback by default,
                 optional key, CORS for Song Deck)
ui/              shared web page the binaries serve
apps/transcriber model graphs, note picking, MIDI writer, CLI
apps/separator   model graphs, chunked inference, stem writer, CLI
wasm/            WebAssembly builds and their JS wrapper
training/        data generation, models, training, export to GGUF, evaluation
third_party/     pinned ggml, cpp-httplib, single-header audio decoders
```

## Milestones across the projects

| #   | Milestone                                                                                                                       | Projects                      | Depends on | Rough size |
| --- | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | ---------- | ---------- |
| M0  | Round-trip lab: measure separation and transcription on ACE-Step performances, record every run (done in 0.15.0)                | Song Deck                     | none       | done       |
| M1  | Song Deck uses JX Runtime for LLMs: preset, discovery, pairing (done 2026-10-18)                                                | Song Deck, JX Runtime         | none       | done       |
| M2  | Audio-apps repository set up: shared core, server, page; transcriber running a placeholder model end to end (done, BackTrack 0.0.1) | Audio apps (Onyx as template) | none       | done       |
| M3  | Training pipeline, including Song Deck's training-data export; first drum model; Transcriber 0.1 binaries; Song Deck preset (done 2026-10-19, released as BackTrack v0.1.0) | Audio apps, Song Deck | M2 | done |
| M4  | Transcriber in WebAssembly inside Song Deck (`"model"` plugin kind); bass and melody models — **built; bass, melody and drums 0.2.0 trained (2026-10-24) and bundled in Song Deck; release 0.6.0 left** | Song Deck, audio apps         | M3         | 3–5 weeks  |
| M5  | Separator 0.1: first model, binaries, Song Deck preset                                                                          | Audio apps, Song Deck         | M2         | 8–12 weeks |
| M6  | JX Runtime runs the audio apps; Song Deck desktop runs JX Runtime in managed mode; JX Runtime becomes the default local runtime | JX Runtime, Song Deck         | M1, M3     | 3–4 weeks  |
| M7  | Theory models: chords and key from audio (Surveil), then a harmony model and app; shareable npm package ([plan](./theory.md))  | Audio apps, Song Deck         | M3 (M4 for WebAssembly) | not sized |

M0–M2 are done: the round-trip lab (Song Deck 0.15.0); JX Runtime support in Song Deck (M1,
[Song Deck plan, part 1](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/song-deck.md#part-1-jx-runtime-support-milestones-m1-and-m6)); and BackTrack 0.0.1, with Surveil running a placeholder
model end to end (M2).

M3 is done (2026-10-19): BackTrack v0.1.0 is released and Song Deck installs it in one click.

- BackTrack: the training pipeline (`training/`, with the PyTorch–C++ parity check in CI), the
  first drum model (`weights/surveil-drums-0.1.0.gguf`: F 0.813 against Song Deck's built-in 0.495
  on 38 held-out rendered songs' separated drums), the release workflow, and one-command training
  at home with ACE-Step performances (`docs/training-at-home.md`).
- Song Deck: `npm run dataset:render` and `dataset:perform` (ACE-Step takes, judged by Surveil),
  the `backtrack-surveil` preset, the one-click binary install with SHA-256 pins, song context on
  transcription requests, and `eval:datasets` for E-GMD, its own datasets and several
  transcribers side by side.
- Released: BackTrack v0.1.0 (CPU builds for linux-x64, darwin-arm64, darwin-x64 and win32-x64,
  and the drum model), pinned in Song Deck's binary recipe (now at v0.4.0).
- Left open: T2's "done when" asks for real recordings (E-GMD and the real test set are not
  scored yet), and GPU builds (Vulkan, Metal) wait for a CPU-fallback scheduler.

M4 is built (2026-10-24): Surveil in WebAssembly inside Song Deck (v0.4.0), and the first bass and
melody models, trained at home from the TRAIN tab with a new drum model beside them
([weights/](../../weights/README.md)). On 60 unseen rendered songs per part, against Song Deck's
built-in transcription: bass note F 0.47 against 0.21, instrumental leads 0.92 against 0.76, sung
leads (Song Deck's guide singer) 0.68 against 0.37, drums 0.85 against 0.53. Song Deck bundles
all three as model plugins. Left: releasing them (0.6.0), after which Song Deck moves its pin.

M5 can start any time, but its training data (especially vocals) needs the longest lead time, so
start sourcing data early.

## Measuring progress: the round-trip lab (done, M0)

Song Deck 0.15.0 has a temporary Labs tool, the **round-trip lab**
([Song Deck plan, part 3](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/song-deck.md#part-3-the-round-trip-lab-done)). It takes a song Song
Deck composed, the version ACE-Step performed, and separates that performance with whatever
separators Song Deck is set to use. It then compares each stem with what went in: the produced
stems sample for sample (when ACE-Step produced track by track), the guide render by harmony and
rhythm, and the song's own notes against a transcription of each stem.

Every run is recorded with its measurements, the settings that made it, what changed since the
last run and your notes, kept on the device and exported as CSV or JSON. Each run is compared with
the previous run of the same audio version.

What it gives the plan:

- **A baseline now.** The separators and transcribers Song Deck already supports (built-in,
  Demucs, the RoFormer models, Basic Pitch, the specialists) can be measured on realistic
  performances before any of our own models exist.
- **Realistic test material with known answers,** including sung vocals, which the
  [separator plan](./explode.md#training-data) names as the hardest data to get.
- **The yardstick for every milestone.** Once the apps are connected as Song Deck providers they
  show up in the lab like any other separator or transcriber, so "beats the current best" in the
  app plans is measured here, run against run.

ACE-Step's output is for measurement, and also for **training**: the owner checked ACE-Step's
licence (2026-10-18) and it allows it. Models trained on it record ACE-Step in their
`audio.sources`.

## Contracts between the projects

| Contract                                                         | Owner                   | Used by                                             |
| ---------------------------------------------------------------- | ----------------------- | --------------------------------------------------- |
| Separation and transcription bridges                             | Song Deck               | Both apps, JX Runtime (M6)                          |
| Harmony bridge (planned, M7)                                     | Song Deck               | BackTrack Theory                                    |
| GGUF metadata for audio models                                   | Audio apps              | Apps, JX Runtime, Song Deck's `"model"` plugin kind |
| Engine release convention (asset names, checksums, version tags) | Onyx Engine, written up | Audio apps, JX Runtime installs                     |
| Discovery, pairing, managed mode                                 | JX Runtime              | Song Deck                                           |

Change a contract in its owner's repository first, keep new fields optional, and update the
other side in the same milestone.

## Open decisions

- ~~**App names**~~ — decided: BackTrack Explode and BackTrack Surveil (binaries
  `backtrack-explode` and `backtrack-surveil`).
- **Licence for the audio-apps repository:** open source (MIT, like Onyx Engine) or closed. This
  decides whether model weights are published alongside the code.
- **Code signing** for the Windows and macOS releases, of the apps and of JX Runtime. Without it,
  "easy for users" stays out of reach.
- **Vocal training data for the separator:** licensed, commissioned or synthetic. This is the
  largest cost and risk in the whole plan; see the [separator plan](./explode.md#training-data).
- **Platforms for 1.0:** Windows, macOS and Linux, and which GPU backends each gets.
- **The round-trip lab's future:** remove it once the apps have their own evaluation, or keep it
  as a permanent benchmark (it is in one folder either way).
