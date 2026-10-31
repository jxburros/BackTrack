# Separator app plan: BackTrack Explode

Part of the [overall plan](./overall.md). "Separator" is a working name. It lives in the new
audio-apps repository next to the [transcriber](./surveil.md) and shares its `core/`,
`server/` and `ui/` (layout in the overall plan).

## What it is

An app that splits a song into stems. It works on its own, and as a separator inside Song Deck.

- **On its own:** `separator song.wav -o stems/` on the command line, or open the page the binary
  serves, drop in a file and download stems. Later, a double-click app.
- **In Song Deck:** a local bridge on `/separate`. It can be the only separator, or one of several
  in Song Deck's combine, compare and stage features.

The goal is not to beat the best research separators everywhere at first. It's to have a
separator you own that is good enough to ship, and that improves over time. Song Deck can still
combine it with others where they're stronger.

## Interfaces

| Interface   | Details                                                                                                                                                                                                                                                                                   |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CLI         | Input file, `--stems`, `--model`, `--device`, `--passes` (averaging), output format (WAV 16/24/32-bit float)                                                                                                                                                                              |
| HTTP server | Song Deck's separation contract: `POST /separate` with `stems`, `model` and `augment`; returns `stems`, `model` and `passes`. Also `GET /info` (models, stems, `on_request_stems`), `GET /health`, `POST /cancel`. Loopback only by default; optional key; CORS for Song Deck. Port 8827. |
| Web page    | Served by the binary: file in, stem choice, progress, preview players, download                                                                                                                                                                                                           |
| WebAssembly | Only for a small model, if one turns out usable. Not a goal for 0.1                                                                                                                                                                                                                       |

## How it works

1. Decode and resample to 44.1 kHz stereo.
2. Process in chunks with overlap, crossfading the results. This keeps memory flat for long songs
   and gives progress and cancellation per chunk.
3. **Model** (ggml, weights from GGUF). Start with a band-split RoFormer-style design. It
   estimates a mask over the spectrogram per stem, using attention across time and frequency
   bands. That suits ggml well: it's built from the same pieces as an LLM (matrix multiplies,
   attention, rotary position embeddings), which ggml is optimised for on every backend. A simpler
   fallback is a U-Net mask estimator (Open-Unmix-like). It's easier to train and weaker.
4. Make the stems sum back to the mix (residual shared out), the way Song Deck's `reconcileParts`
   does. It also makes the separator a well-behaved member of Song Deck's ensemble.
5. **Averaging** (`augment`): polarity-flipped and channel-swapped passes, averaged. `passes` is
   reported back.

## Stems, in order

| Release | Stems                                                                                                                                                                             |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0.1     | vocals / instrumental (two stems; the most requested split, and the easiest to make good)                                                                                         |
| 0.2     | drums, bass, vocals, other                                                                                                                                                        |
| 0.3     | + guitar, piano                                                                                                                                                                   |
| Later   | Finer splits on request: drums into kick, snare, toms, hi-hat, cymbals; vocals into lead and backing (Song Deck's `on_request_stems` and staged separation already ask for these) |

## Training data

This is the hardest part of the whole plan, and the part to start first.

- **Instrumental stems from Song Deck renders** ([Song Deck plan §2.5](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/song-deck.md#25-training-data-export-m3)):
  plenty of them, with exact stems and rights owned. Good for drums, bass, keys and synths. Vary
  instruments, effects, mixing and mastering heavily, including bus compression, limiting and
  reverb, so the model sees mastered mixes, not clean sums.
- **Slakh2100** (CC BY 4.0): more rendered multitrack material.
- **Vocals are the gap.** Rendered vocals (Song Deck's singers, DiffSinger) don't sound like real
  singers, and the well-known real multitrack sets (MUSDB18-HQ, MoisesDB, MedleyDB, Cambridge-MT
  multitracks) are research or education only. The options:
  - **Licensed multitrack libraries:** paid, but the cleanest route.
  - **Commissioned recordings:** session singers recording to your own instrumentals. Expensive per
    hour, but fully owned and exactly what you need.
  - **Isolated vocal sample packs** with licences that allow this use. Check each one; many
    sample-pack licences forbid training.
  - **Synthetic vocals** to fill in, never alone.
- **Augmentation:** random remixing of stems across songs, gain, pitch and tempo shifts, EQ,
  effects.
- **Evaluation:** Song Deck's `eval:datasets` scores SDR on MUSDB18-HQ and MoisesDB. Use those for
  measurement only, not training, and check that their licences cover evaluation in a commercial
  setting. Build a small owned real test set as well.
- **Round-trip lab** (Song Deck 0.15.0, [overall plan](./overall.md#measuring-progress-the-round-trip-lab-done-m0)):
  ACE-Step performs a Song Deck song **track by track**, so every stem, sung vocals included, is
  known and sums to the mix. Separating that sum gives exact SDR per stem on realistic audio, with
  every run recorded against the settings that made it. It's the best owned test set available
  today. Record baselines for Demucs, the RoFormer models and the built-in separator now; S2's
  "competitive with Demucs" is judged here.
- **ACE-Step output as training data** eases the vocal gap. The owner checked ACE-Step's licence
  (2026-10-18) and it allows training; re-check if a different ACE-Step version or model is used.

## Training code (`training/`, shared with the transcriber)

- PyTorch models, chunked training, mixed precision; export to GGUF with metadata (`audio.task =
separate`, `audio.stems`, `audio.sample_rate`, chunk and overlap settings, `general.license`).
- **Parity check:** the C++ binary and PyTorch give the same stems within a tolerance on fixed
  clips.
- **Compute:** a band-split model of a few million to a few tens of millions of parameters trains
  in days to a couple of weeks on one or two good GPUs. Budget rented GPU time for the larger runs.

## Milestones

| #   | What                                                                                        | Done when                                                              | Size                       |
| --- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | -------------------------- |
| S0  | App on the shared core: chunked processing, server, CLI, page, with a placeholder model     | Song Deck can use it as a separator and gets stems that sum to the mix | Done (0.7.0)               |
| S1  | Data pipeline: render export, Slakh, remixing, augmentation; first vocal data sourced       | A training set with vocals you have the rights to                      | 3–6 weeks, mostly sourcing |
| S2  | Vocal/instrumental model                                                                    | Competitive with Demucs two-stem on the owned real test set            | 3–4 weeks                  |
| S3  | Release 0.1: binaries (CPU and GPU builds), model download with checksums, Song Deck preset | Song Deck's one-click install works, and it runs as a combine member   | about 1 week               |
| S4  | Four-stem model                                                                             | Adds measurable value in Song Deck's **Combine their stems**           | 4–6 weeks                  |
| S5  | Six stems and finer splits on request                                                       | Song Deck's staged separation can use it for drum and vocal splits     | ongoing                    |

**S0 as built (0.7.0).** `backtrack-explode` separates on the command line, serves `POST /separate`,
`GET /info` (`stems`, `on_request_stems`), `GET /health` and `POST /cancel` on port 8827, and
serves the page in the blast-zone theme. Chunks of 20 s overlap by 1 s and crossfade; a
2048-point STFT goes to the model; the residual is shared out by power, so the stems sum to the
mix (checked in the unit tests and the smoke test, and through Song Deck's own adapter). Passes
are plain, flip + swap, flip and swap. The placeholder model uses masks from
harmonic/percussive median filtering, the low band and centred bins, for vocals, drums, bass and
other, with instrumental on request. Not in S0: GGUF separation models (S2), GPU devices,
releases (S3). See [apps/explode/README.md](../../apps/explode/README.md).

## Ideas for later

- **MIDI-informed separation:** accept the transcriber's notes as a hint and use them inside the
  model, a step beyond Song Deck's existing after-the-fact cleaning with MIDI.
- **Feedback loop with the transcriber:** separate, transcribe, clean, transcribe again.
- **Small model for WebAssembly,** for quick previews in the browser.

## Risks

- **Vocal training data:** cost and licences. Decide the sourcing route early; it sets the
  schedule.
- **Quality gap** with research separators trained on bigger datasets. Song Deck's ensemble softens
  this: the separator only has to win where it's strong.
- **GPU memory** for long songs: chunking keeps it bounded; set chunk sizes by device.
- **Licence records:** keep a per-model record of every source, and ship it in the GGUF metadata.
