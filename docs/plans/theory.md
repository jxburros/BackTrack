# Theory models plan: chords, keys and harmony

Part of the [overall plan](./overall.md) (milestone M7). Written 2026-10-20. Song Deck's side of
the same work is in Song Deck's
[`docs/plans/theory-models.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/theory-models.md).
"BackTrack Theory" is a working name.

## What it is

Two small trained models about music theory, shipped the way Surveil ships. Both are local, run
on ggml with weights in GGUF and need no Python at run time. Each works on its own, inside Song
Deck, and inside other people's applications:

| Model                  | In                                   | Out                                                        | Lives in              |
| ---------------------- | ------------------------------------ | ---------------------------------------------------------- | --------------------- |
| **A: chords and key**  | Audio (a mix or a stem)              | Key and key changes; chords with start, end, bass and confidence | Surveil, as a model   |
| **B: harmony**         | Key, chords, melody, style (symbolic) | Suggested chords for masked slots; how likely each chord is | A new app, BackTrack Theory |

Song Deck's theory code (`packages/core/src/theory/`) stays the authority on facts: what notes a
chord has, its roman numeral, whether a cadence is there. Rules get facts right; the models add
what rules can't: what a recording sounds like it plays, and what is likely or idiomatic in a
style.

What makes it worth sharing:

- **Small and local.** Weights of a few megabytes, realtime in WebAssembly, nothing to install in a
  browser, no cloud.
- **Trained on data that can be shipped commercially** (a decision already made in the overall
  plan), so the weights can go inside other people's products.
- **A stable interface:** versioned, with the chord vocabulary stored in the weights, so a newer
  model doesn't break an app built on an older one.

## Model A: chords and key from audio

### Why it lives in Surveil

It is the same kind of job as transcription: audio in, musical labels out, from the same front
end. Song Deck's transcription contract already has `key` and `chords` in its response and
`context.chords` in its request (`packages/ai/src/contracts.ts`), so it ships with no contract
change: `POST /transcribe` with `model: "chords"` returns `key`, `chords` and an empty `notes`.

### Training data: already being written

Song Deck's training export (`npm run dataset:render`, Song Deck's `docs/TRAINING-DATA.md`) writes
each song's key, key changes and chords in seconds to `manifest.json`. The drum pipeline has
produced labelled chord data from the start; nothing reads it yet.

| Source                             | What it gives                                                   | Notes                                                                        |
| ---------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Song Deck renders                  | Clean audio with exact chords and key                           | Unlimited; varied instruments and mixes already                              |
| ACE-Step performances (covers)     | Realistic audio                                                 | A cover may change chords, so windows need a judge (below)                   |
| GuitarSet (CC BY 4.0)              | Real guitar with chord labels                                   | Small; first real-recording test set, possibly training                      |
| Owner's own recordings, annotated  | Real material in the styles that matter                         | Test set; few hours is enough                                                |
| Slakh2100 (chords derived from MIDI) | Rendered multitracks of real songs                            | "Licence yes, provenance risk" ([training-data research](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/training-data-research.md)); test only until counsel says otherwise |

**Judging covers.** The drum pipeline keeps only the windows of a cover where it agrees with the
score. Chords get the same treatment: a window is kept when Song Deck's chord detection, run
without the chart, agrees with the song's chords above a threshold. Once model A beats the built-in
detector, model A judges, and `rejudge` re-scores the covers with it, as for drums.

**Augmentation.**

- **Transposition:** shift the CQT by whole semitones and shift the labels with it, so every song
  appears in all 12 keys. Renders already pick random keys; this matters most for covers and real
  recordings.
- **Mix and sound:** stem balance, EQ, reverb, noise and level, as the drum training does.
- **Harmonic variety:** Song Deck's composer has a limited set of habits, and a model trained only
  on them learns "Song Deck-isms". Renders for chord training turn up borrowed chords, secondary
  dominants, extensions and modal keys (a Song Deck render option; see Song Deck's plan).

### The model

- **Front end:** Surveil's harmonic CQT, unchanged. It already matches between PyTorch and ggml,
  and is checked by `parity`.
- **Graph (`surveil-chords`):** a convolutional trunk like `surveil-cnn`, pooled to pitch classes,
  then a long temporal context, because chords last seconds and drums last milliseconds. Dilated
  temporal convolutions first (cheap in ggml and in WebAssembly); a small GRU if they aren't
  enough. About 0.2M to 1M parameters.
- **Heads, one per question,** instead of one list of every chord:

  | Head    | Classes                                                                                  |
  | ------- | ---------------------------------------------------------------------------------------- |
  | root    | 12 pitch classes plus "no chord"                                                         |
  | quality | Start with the 14 the built-in detector knows (maj, min, aug, dim, sus2, sus4, 6, m6, 7, maj7, m7, add9, dim7, m7b5) |
  | bass    | 12 pitch classes (inversions and slash chords)                                           |
  | key     | 24 major and minor keys; modes later                                                     |

  Rare chords still train well this way, because "Bb" and "dim7" are learned separately.
  Song Deck's richer chord qualities (min9, min11, minmaj7...) are mapped to this vocabulary for
  training (min9 to m7, and so on); the map is part of the training code and the vocabulary is
  written into the GGUF.
- **Decoding, in C++ (not in the model):** a Viterbi pass over the frame probabilities gives
  clean segments, optionally changing only on beats; segments below a confidence floor become "no
  chord". A known chord chart (`context.chords`) is a soft prior, as in Song Deck's informed mode.
  Keeping this in code means behaviour can change without retraining, as with Surveil's note
  picking.

### Measuring it

- **Chords:** weighted chord symbol recall at three levels (root, major/minor, sevenths), the
  standard measure in chord-recognition research, so results compare with published work.
- **Key:** the standard weighted key score (fifths and relative keys get partial credit).
- **Baseline:** Song Deck's built-in detector (`packages/audio/src/analysis/chords.ts` and
  `key.ts`) on the same held-out songs, just as the drum model was measured against Song Deck's
  built-in drum transcription. The report shows both, on renders and on the real test set.

## Model B: harmony

### What it does

One model trained to fill in blanks covers every task:

| Task                      | How                                                        |
| ------------------------- | ---------------------------------------------------------- |
| Suggest the next chord    | Mask the slot after the last chord                         |
| Reharmonize a passage     | Mask the passage                                           |
| Harmonize a melody        | Give the melody, mask every chord                          |
| Score a progression       | How likely the model finds each chord where it stands      |

### Representation

Everything is relative to the key, so C major and Eb major look the same to the model and every
song teaches every key:

- **Chord tokens:** scale degree of the root, quality, bass degree, length in beats (`V7`, 2 beats).
- **Conditioning:** mode, genre, section kind (verse, chorus...), meter, and the melody as scale
  degrees per beat (or half beat).

Song Deck's theory code (`theory/roman.ts`) does the conversion to and from this form, so the
tokenizer isn't written twice: Song Deck exports the training sequences, and the C++ side only
reads tokens.

### The model

- **Graph (`theory-harmony`):** a small transformer encoder trained by masked infilling, 4 to 6
  layers, about 3 to 5M parameters. ggml runs transformers well (llama.cpp is built on it).
- **Generation:** fill the masked slots, highest confidence first, sampled with a seed the caller
  passes, so the same seed gives the same chords.
- **In Song Deck it ranks, it doesn't invent.** Song Deck's rules generate valid candidates and the
  model orders them. The rules keep it from anything theoretically broken; the model adds taste.

### Training data: the hard part

The overall plan already decided that shipped models train only on data allowing commercial use.
For harmony that rules out most research chord datasets (many are research-only or scraped from
chord sites); assume each one is out unless its licence is checked and says otherwise.

| Source                                   | Role                                         | Notes                                                                                                   |
| ---------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Song Deck's composer                     | Bootstrap only                               | Proves the format and the pipeline; teaches Song Deck's own rules back, so not for taste                |
| Public-domain repertoire                 | Core corpus                                  | Bach chorales, hymns, folk and early popular songs. The music is public domain; check each digital edition's licence. Chords come from Song Deck's analysis of the notes |
| ACE-Step songs, analysed by model A      | Modern styles at scale                       | Original ACE-Step songs (not covers) across genres, chords taken by model A, kept above a confidence floor. ACE-Step output is allowed as training data. Inherits ACE-Step's habits and model A's mistakes; depends on C2 |
| Owner-written and annotated progressions | Fine-tuning and test                         | Small, high quality, in the styles that matter                                                          |
| Lakh MIDI / Slakh                        | Pending                                      | Same provenance question already open with counsel                                                      |
| Paid catalogues (Rightsify/GCX)          | Pending                                      | Claim chord labels; quote only                                                                          |

### Measuring it

- **Held out:** accuracy of the masked chord and its likelihood on held-out public-domain and
  owner-written songs.
- **By ear, in Song Deck:** progressions ranked by the model against the built-in rules alone,
  same seed, same song. This is the measure that decides whether it ships.

## The interfaces

Both models get the same four ways in, as Surveil has:

| Interface   | Model A (in Surveil)                                                   | Model B (BackTrack Theory)                                             |
| ----------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| CLI         | `backtrack-surveil song.wav --model chords -o chords.json` (also `.lab`) | `backtrack-theory suggest song.json`, `score`                          |
| HTTP        | `POST /transcribe` with `model: "chords"` (contract unchanged), port 8826 | `POST /harmony/suggest`, `POST /harmony/score`, `GET /info`, `/health`, `/cancel`; port 8828 |
| Web page    | Surveil's page shows the chord chart                                   | Its own page: type a progression, see suggestions                      |
| WebAssembly | The Surveil build, with the chords graph                               | Its own build                                                          |

The harmony contract is owned by Song Deck, like the other bridges, and written in
`packages/ai/src/contracts.ts` first (rule in the overall plan).

**For other people's applications,** on top of the bridges:

- **An npm package** with the WebAssembly builds and a small API:

  ```js
  analyze(audio)                      // { key, keyChanges, chords: [{ start, end, chord, root, quality, bass, confidence }] }
  suggest({ key, chords, melody, mask, style, seed })   // [{ chords, score }]
  score({ key, chords })              // likelihood per chord
  ```

- **Chord strings in two forms:** Song Deck's symbols (`G/B`, as the bridge uses now) and Harte
  notation (`G:maj/3`), the standard in music-research tools, plus the structured fields.
- **The vocabulary in the GGUF metadata** (`chords.qualities`, `harmony.tokens`), so callers decode
  labels from the file rather than hard-coding them.
- **A C API** over `core/` for native applications; Python bindings later if people ask.

New GGUF keys are written up in [`docs/gguf-metadata.md`](../gguf-metadata.md) and the endpoints in
[`docs/http-api.md`](../http-api.md) when each milestone lands.

## Code in this repository

| Where                       | What                                                                            |
| --------------------------- | ------------------------------------------------------------------------------- |
| `training/datasets.py`      | Read `chords`, `key` and `key_map` from the manifest                             |
| `training/chords.py`        | Chord symbol parsing, the vocabulary map, label frames, scoring                 |
| `training/train_chords.py`  | Model A training, next to `train_drums.py` and `train_pitched.py`              |
| `training/train_harmony.py` | Model B training on Song Deck's exported sequences                              |
| `training/pipeline.py`      | **What to train** gains Chords (render, perform, judge, train, evaluate, parity, report, rejudge) |
| `apps/surveil/`             | The `surveil-chords` graph and the Viterbi decoder                              |
| `apps/theory/`              | The new app: `theory-harmony` graph, sampling, CLI                              |
| `server/`, `ui/`, `wasm/`   | Shared as today; a page and a WebAssembly build for Theory                      |

## Milestones

| #      | What                                                                                                                                       | Repositories        | Done when                                                         |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------- | ----------------------------------------------------------------- |
| **C0** | Chord and key labels from the manifest; the vocabulary map; chord and key scoring; the built-in detector scored on held-out renders       | Both                | There is a baseline number to beat                                |
| **C1** | Model A trained on renders with transposition; `surveil-chords` graph in C++ with parity; `--model chords` on the CLI and `/transcribe`    | BackTrack           | Beats the built-in detector on held-out renders                   |
| **C2** | Covers with the chord judge; GuitarSet and the owner's test set; WebAssembly; the Song Deck model plugin (`task: "chords"`)                | Both                | Beats the built-in detector on real recordings                     |
| **C3** | Release; the npm package and its documentation                                                                                             | BackTrack           | Someone else can use it from a web page in a few lines            |
| **H0** | Harmony data: public-domain corpus, Song Deck's sequence export, ACE-Step harvest with model A; licence checks; harmony contract drafted   | Both                | A corpus whose every source is cleared for commercial training    |
| **H1** | Model B trained; `theory-harmony` graph with parity; the BackTrack Theory app (CLI, HTTP, page, WebAssembly)                               | BackTrack           | Suggests sensible chords on held-out songs                        |
| **H2** | Song Deck ranks its candidates with model B; suggestions in the chord view                                                                 | Song Deck           | The owner prefers ranked progressions by ear                      |

C0 is cheap and tells us how much room there is: if the built-in detector is already close on
renders, the real-recording test set decides whether model A is worth building.

**C0 is done (2026-10-24).** Song Deck's manifest carries structured chords and its
`eval:datasets --tasks chords,key` scores chords and keys; here, `training/chords.py` has the
vocabulary map, label frames and the same scores, and `datasets.read_songdeck_chords` reads
them. The baseline: on 60 unseen songs rendered by Song Deck 0.19.1 (seed 900000, 60 s, full mixes), Song Deck's chord stage alone gets root 0.591, major/minor 0.496, sevenths 0.441 and root and quality exact 0.300 (labelled 77% of the time), and its key detector a weighted 0.785 (68% exactly right). That is far from solved even on
Song Deck's own renders, so model A (C1) is worth building; a sevenths score near 0.44 and a key
right two times in three leave the most room.

## Open decisions

- **Name** of the harmony app (and so its binary, preset and release assets).
- **Chord vocabulary** beyond the first 14 qualities: ninths, elevenths and thirteenths, or keep
  extensions to the harmony model.
- **Modes in the key head:** major and minor only, or the church modes Song Deck composes in.
- **Repository licence** (open in the overall plan): decides whether weights are published, which
  is the point of a shareable tool.
- **Public-domain editions:** which digital editions of the repertoire have licences that allow
  training.
