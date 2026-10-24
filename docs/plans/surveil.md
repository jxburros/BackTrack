# Transcriber app plan: BackTrack Surveil

Part of the [overall plan](./overall.md). "Transcriber" is a working name. It lives in the new
audio-apps repository next to the [separator](./explode.md) and shares its `core/`,
`server/` and `ui/` (layout in the overall plan).

## What it is

An app that turns audio into MIDI. It works on its own, and inside Song Deck.

- **On its own:** `transcriber song.wav --stem bass -o bass.mid` on the command line, or open the
  page the binary serves, drop in a file, and download MIDI. Later, a double-click app.
- **In Song Deck:** a local bridge on `/transcribe`, and a WebAssembly build that runs inside the
  page with nothing to install.

What makes it different:

- **Song context:** key, tempo, chords and instrument shape which notes it picks.
- **One model per stem type,** built for separated stems.
- **Trained on separator output,** so bleed from other stems doesn't turn into notes.

## Interfaces

| Interface   | Details                                                                                                                                                                                                                                                                                                                                                                            |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CLI         | Input file, `--stem`, optional `--key`, `--tempo`, `--chords <file>`, `--program`; output MIDI and JSON; `--model`, `--device`                                                                                                                                                                                                                                                     |
| HTTP server | Song Deck's transcription contract: `POST /transcribe` with `source`, `model`, `pitch_bends` and the new optional `context`; returns notes with `confidence`, `bend` and `program`, plus `tempo` and `key`. Also `GET /info` (models installed and which stems each covers), `GET /health`, `POST /cancel`. Loopback only by default; optional key; CORS for Song Deck. Port 8826. |
| Web page    | Served by the binary: file in, stem type, optional key and tempo, a piano-roll preview, MIDI out                                                                                                                                                                                                                                                                                   |
| WebAssembly | The same core, built with Emscripten; a small JS wrapper for Song Deck's job worker (and for a hosted web version later)                                                                                                                                                                                                                                                           |

## How it works

1. **Decode and resample** (WAV, FLAC and MP3 through single-header decoders) to the model's rate,
   mono.
2. **Front end:** a harmonic constant-Q spectrogram (a log-frequency spectrum with harmonics
   stacked), computed in C++, matching the training code exactly.
3. **Model** (ggml, weights from GGUF): a small convolutional network in the spirit of Basic Pitch
   (tens of thousands to a few hundred thousand parameters). It outputs, per frame, the likelihood
   of a note starting, sounding and ending at each pitch, or at each drum class for drums.
4. **Note picking** (C++, own code): turns the likelihoods into notes. This is where context
   counts:
   - pitches outside the key or the current chord start with a lower prior;
   - note starts are pulled toward the beat grid;
   - pitches outside the instrument's range are removed;
   - monophonic parts (bass, vocals) go through a best-path search, so they never hold two notes;
   - pitch bends come from the pitch-contour output.
5. **Output:** notes with confidence (the model's own probability, not a constant), bends and a
   program; MIDI writing in the core.

Picking notes in code, not in the model, lets you change behaviour without retraining.

## Models, in order

| Order | Model             | Output                                                                                           | Why this order                                                                         |
| ----- | ----------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| 1     | Drums             | Hits per class: kick, snare, toms, closed/open hi-hat, crash, ride, mapped to General MIDI drums | Song Deck has no trained drum model today; drums render with exact labels; small model |
| 2     | Bass              | Monophonic notes with bends                                                                      | Easy to render convincingly; big effect on Song Deck's rebuilt songs                   |
| 3     | Melody and vocals | Monophonic notes with bends and vibrato                                                          | Vocals are the hardest to synthesise; needs real data mixed in                         |
| 4     | Keys and guitar   | Polyphonic notes                                                                                 | Hardest; most data hungry                                                              |

The drum classes should match the drum stems Song Deck's staged separation already produces
(kick, snare, toms, hi-hat, cymbals).

## Training data

- **Song Deck renders** (via Song Deck's training-data export, [Song Deck plan §2.5](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/song-deck.md#25-training-data-export-m3)):
  unlimited, exactly labelled, rights owned. Vary instruments, effects, mixing and tuning.
- **Separator output:** mix the rendered stems, separate the mix (the separator app or the
  separators Song Deck supports), and train on the separated stems with the original labels.
- **Datasets that allow commercial use**, after checking each licence yourself:
  - Slakh2100: rendered multi-instrument songs with MIDI (CC BY 4.0).
  - E-GMD: real drumming on an electronic kit (believed CC BY 4.0; check).
  - GuitarSet: guitar (CC BY 4.0).
- **Not for training:** MAESTRO, MUSDB18-HQ, MoisesDB, MedleyDB (non-commercial or research only).
  Pretrained weights with unclear training data are out too.
- **Real test set:** a few dozen real recordings with hand-corrected MIDI, for each stem type.
  Synthetic scores flatter the model; this set is the one to trust.
- **Round-trip lab** (Song Deck 0.15.0, [overall plan](./overall.md#measuring-progress-the-round-trip-lab-done-m0)):
  ACE-Step performances of Song Deck songs, separated, then transcribed and scored against the
  song's own notes. It's realistic audio with a known answer, recorded run against run. Use it to
  set the baseline for each stem type now (built-in, Basic Pitch, the specialists) and to judge
  every milestone below. Training on ACE-Step performances is allowed too (licence checked by the owner 2026-10-18).

## Training code (`training/`)

- Python with PyTorch: dataset readers, the front end (the same maths as the C++ version), models,
  training loops and evaluation.
- **Export:** PyTorch weights → GGUF, with metadata such as `audio.arch`, `audio.task =
transcribe`, `audio.stems`, `audio.sample_rate`, `audio.hop`, `audio.classes`,
  `general.license` and `general.version`.
- **Parity check:** the same clip through PyTorch and through the C++ binary must give the same
  likelihoods within a tolerance. This catches front-end drift, the most likely silent bug.
- **Evaluation:** note accuracy (onsets within ±50 ms, matching Song Deck's `eval:datasets`), per
  stem type, on synthetic, public and real sets.

## Milestones

| #   | What                                                                                                                   | Done when                                                                            | Size                   |
| --- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ---------------------- |
| T0  | Repository, shared core and server, CLI and page, with a placeholder model (random weights, real graph) — **done (0.0.1)** | Song Deck can call it as a bridge and gets notes back                                | done (M2)              |
| T1  | Training pipeline: data reader, front end, parity check, GGUF export — **done (M3)**                                                   | A trained toy model runs identically in PyTorch and C++                              | 1–2 weeks              |
| T2  | Drum model — **trained: 0.2.0 scores 0.848 vs 0.527 on unseen rendered songs; real recordings and E-GMD not scored yet**                                                                                                             | Beats Song Deck's onset-based drum transcription on the real test set and E-GMD      | 2–3 weeks              |
| T3  | Release 0.1: binaries for Windows, macOS and Linux (CPU, plus GPU builds), models as separate downloads with checksums — **released in v0.1.0 with CPU builds, which Song Deck's one-click install uses; GPU builds wait for a CPU-fallback scheduler** | Song Deck's one-click install works                                                  | about 1 week           |
| T4  | WebAssembly build inside Song Deck — **done: `wasm/`, released in v0.4.0; Song Deck runs it in the page for its model plugins; same notes as native, 4-minute song in about 22 s on one thread**   | Drums transcribe in the page with no install, in reasonable time for a 4-minute song | 1–2 weeks              |
| T5  | Bass, then melody and vocals — **trained (bass 0.1.0, melody 0.1.0): ahead of Song Deck on unseen renders; the real test set is not recorded yet** | Each beats the current best Song Deck path for that stem on the real test set        | 2–4 weeks each         |
| T6  | Song context in note picking — **built (key, chord, range and tempo priors since 0.0.1; Song Deck sends `context`); the gain with context on versus off is not measured yet** | Measurable gain with context on versus off                                           | about 1 week           |
| T7  | Keys and guitar (polyphonic)                                                                                           | Beats Basic Pitch on the real test set                                               | 4–6 weeks              |

## Risks

- **Synthetic vs real recordings:** models trained on renders do worse on real recordings. The
  mitigations are varied rendering, training on separator output, CC-BY real data and the real
  test set.
- **Front-end drift** between Python and C++. The parity check is not optional.
- **WebAssembly speed** for long songs: keep the models small and process in chunks with
  progress and cancellation.
- **Licences** of every dataset and sample library behind the training data. Keep a record per
  model of what went into it, and ship it in the GGUF metadata.
