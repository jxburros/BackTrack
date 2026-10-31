# BackTrack roadmap

What is done, what is in progress and what comes next, with every open issue placed on it. It
sums up the [plans](docs/plans/overall.md), the [changelog](CHANGELOG.md) and the issues as of
2026-10-31 (BackTrack 0.7.0). The plans keep the detail; update this page when a milestone moves
or an issue opens or closes.

## Where BackTrack stands

- **Milestones M4 done and M5 started** ([overall plan](docs/plans/overall.md)): Surveil runs
  inside Song Deck's page (WebAssembly), and v0.6.1 released the bass, melody and drums 0.2.0
  models. Explode S0 is built (0.7.0): the separator runs end to end with a placeholder model.
- **Trained** ([weights/](weights/README.md)), against Song Deck's built-in transcription on 60
  unseen rendered songs per part: drums 0.2.0 F 0.848 against 0.527, bass 0.1.0 note F 0.472
  against 0.211, melody 0.1.0 0.917 against 0.757 on instrumental leads and 0.684 against 0.372
  on Song Deck's guide singer. Song Deck bundles all three as model plugins. None is scored on
  E-GMD or real recordings yet. Piano, guitar and the full mix use the placeholder model.
- **Ships (latest release v0.6.1, 2026-10-24, the first through the unified release workflow):**
  `backtrack-surveil` CPU builds for linux-x64, darwin-arm64, darwin-x64 and win32-x64; the drums
  0.2.0, bass 0.1.0 and melody 0.1.0 models; the WebAssembly build; desktop installers for
  BackTrack Surveil and the BackTrack hub with its TRAIN tab (not code-signed or notarized).
  Song Deck's one-click install pins v0.6.1 and its page runs v0.6.1's WebAssembly build.
  The v0.5.1 draft on GitHub is empty, left by the failed run that 0.6.1 fixed.
- **Versioned, not released:** 0.7.0, Explode S0 ([apps/explode/](apps/explode/README.md)). The
  release workflow does not package Explode yet; that is S3, with the first trained model.
- **Not built:** Explode's trained models, the theory models, GPU builds.

## Milestones

Status is **done**, **in progress**, **next** (start soon) or **later**.

### Across the projects ([overall plan](docs/plans/overall.md))

| #  | Milestone                                                              | Status                       | Depends on | BackTrack's part                                       |
| -- | ---------------------------------------------------------------------- | ---------------------------- | ---------- | ------------------------------------------------------ |
| M0 | Round-trip lab in Song Deck (0.15.0)                                   | done                         | none       | None; it is the yardstick for every milestone          |
| M1 | Song Deck uses JX Runtime for LLMs                                     | done                         | none       | None                                                   |
| M2 | Repository, shared core, server, page; Surveil on a placeholder model  | done (0.0.1)                 | none       | T0                                                     |
| M3 | Training pipeline, drum model, Surveil 0.1 binaries, Song Deck preset  | done (v0.1.0)                | M2         | T1–T3; left open: T2's real-recording scores, GPU builds |
| M4 | Surveil in WebAssembly inside Song Deck; bass and melody models        | done (v0.6.1)                | M3         | T4 done; T5's models released (v0.6.1)                 |
| M5 | Explode 0.1: first model, binaries, Song Deck preset                   | in progress: S0 done (0.7.0) | M2         | S0–S3                                                  |
| M6 | JX Runtime runs the audio apps; Song Deck runs JX Runtime managed      | later                        | M1, M3     | Pinned releases and GGUF metadata, which exist; the work is in JX Runtime and Song Deck |
| M7 | Theory models: chords and key in Surveil, a harmony app, an npm package | C0 done; C1 next          | M3 (M4 for WebAssembly) | C0–C3, H0–H1                                |

### Surveil, the transcriber ([plan](docs/plans/surveil.md))

| #  | What                                                        | Status                                                                                      | Depends on                                         |
| -- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| T0 | Core, server, CLI, page, placeholder model                  | done (0.0.1)                                                                                | none                                               |
| T1 | Training pipeline: reader, front end, parity check, GGUF    | done (0.1.0)                                                                                | T0                                                 |
| T2 | Drum model                                                  | in progress: trained (0.2.0); to score on E-GMD (importable since 0.5.0) and a real test set | T1; a real drum test set                           |
| T3 | Release 0.1                                                 | done for CPU (v0.1.0); GPU builds later                                                     | T2; GPU builds need a CPU-fallback scheduler       |
| T4 | WebAssembly inside Song Deck                                | done (v0.4.0)                                                                               | T3; Song Deck's `model` plugin kind (built)        |
| T5 | Bass, then melody and vocals                                | in progress: bass and melody 0.1.0 trained, ahead of Song Deck on renders; to score on a real test set | T1; Song Deck's `dataset:render` and `dataset:perform`; a real test set |
| T6 | Song context in note picking                                | built (0.0.1); the gain with context on versus off is not measured                          | T0; Song Deck sends `context` (built)              |
| T7 | Keys and guitar (polyphonic)                                | later                                                                                       | T5; a GuitarSet importer; a real test set          |

### Explode, the separator ([plan](docs/plans/explode.md))

| #  | What                                                                      | Status | Issues | Depends on                                             |
| -- | ------------------------------------------------------------------------- | ------ | ------ | ------------------------------------------------------ |
| S0 | App on the shared core: chunking, STFT, server, CLI, page, placeholder    | done (0.7.0) |  | M2 (done)                                              |
| S1 | Data pipeline: renders, Slakh, remixing, augmentation; first vocal data   | next: start sourcing vocals early | #17 follow-up | Song Deck's renders; the vocal-data decision |
| S2 | Vocal/instrumental model, competitive with Demucs two-stem                 | later  | #3     | S0, S1                                                 |
| S3 | Release 0.1: binaries, model download, Song Deck preset                    | later  |        | S2; Song Deck's separator preset (waits for M5)        |
| S4 | Four-stem model                                                           | later  | #3     | S3                                                     |
| S5 | Six stems and finer splits on request                                     | later  | #3, #1 | S4                                                     |
| —  | Rare instruments as on-request splits of "other"                          | later  | #2     | S4 (the issue says so)                                 |

### Theory models, M7 ([plan](docs/plans/theory.md))

| #  | What                                                                                  | Status | Depends on                                                                 |
| -- | ------------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------- |
| C0 | Chord and key labels, vocabulary map, scoring; the built-in detector's baseline       | done: built-in root 0.59, sevenths 0.44, key 0.79 on unseen renders; model A is worth building | Song Deck: structured chords in the manifest, chord and key scoring in `eval:datasets` |
| C1 | Model A on renders; `surveil-chords` graph with parity; `--model chords`              | next   | C0 (done)                                                                  |
| C2 | Covers with a chord judge, GuitarSet, the owner's test set, WebAssembly, Song Deck plugin | later | C1; T4; Song Deck's chord judge and `task: "chords"` plugin            |
| C3 | Release; the npm package                                                              | later  | C2                                                                         |
| H0 | Harmony corpus with licence checks; harmony contract drafted                          | later  | C2 (model A labels ACE-Step songs); Song Deck's `dataset:harmony` and contract |
| H1 | Model B; the BackTrack Theory app (CLI, HTTP, page, WebAssembly)                      | later  | H0; the app's name                                                         |
| H2 | Song Deck ranks its chord candidates with model B (Song Deck's work)                  | later  | H1                                                                         |

## Now, next, later

**Done since the last update**

- v0.6.1 released the bass, melody and drums 0.2.0 models through the unified release workflow;
  Song Deck pins it for its one-click install (all three models) and vendors its WebAssembly
  build (Song Deck roadmap SD-11).
- Explode S0 (0.7.0): `backtrack-explode` (CLI, server on 8827, page), chunked STFT processing,
  averaging passes, the residual share-out and a placeholder mask model. Song Deck's separation
  adapter works with it unchanged and gets stems that sum to the mix.

**Now**

- The next training round: bass is the weakest (note F 0.47); ACE-Step takes and degradation
  augmentation for bass and melody, which their first models trained without.
- Finish T2: score the drum model on E-GMD and on a real drum test set.

**Next**

- Vocal training data for S1: decide the route and start sourcing; it has the longest lead time.
- C1: model A (chords and key) on renders, against C0's baseline (root 0.59, sevenths 0.44, key
  0.79); `training/chords.py` has the labels and scores.
- A real test set per stem type, bad recordings included (T2, T5, #17).
- T6: measure the gain from song context.

**Later**

- S2–S5 and the separator issues (#3, #1, #2); the separator side of augmentation (#17).
- T7: keys and guitar.
- C1–C3 and H0–H2.
- GPU builds, code signing and notarization, automatic updates ([desktop](docs/desktop.md#not-done-yet)).
- On-the-fly features for augmentation (#17), if degraded copies are not enough.
- M6, in JX Runtime and Song Deck.

## Open issues

| Issue | What it needs | Where it sits |
| ----- | ------------- | ------------- |
| [#3](https://github.com/jxburros/BackTrack/issues/3) Match the standard stem set | Vocals/instrumental, then drums, bass, vocals, other, then guitar and piano; later lead and backing vocals, drum-kit pieces, finer families | Explode's stem order: S2 (two stems), S4 (four), S5 (six and finer splits) |
| [#1](https://github.com/jxburros/BackTrack/issues/1) Split two guitars playing different parts | An on-request second split of the guitar stem with an optional MIDI or role hint; a "split into two" model and a MIDI-informed version; two-guitar renders from Song Deck varying pan, tone and register | Not placed by the plans. It needs a guitar stem, so it sits with S5's finer splits or after |
| [#2](https://github.com/jxburros/BackTrack/issues/2) Rare instruments (accordion, mallets, bells) | Sound-bank coverage and licence checks; splits of "other" by family; one model that takes the target instrument; a reliable "not present" answer | After S4, as the issue says; about 1–2 weeks per family once S1's data pipeline exists |

## Follow-ups from #17

Degradation augmentation (#17) shipped in 0.5.1 and the issue is closed. It left four things, one
of them now built:

| Follow-up | State | Where it sits |
| --------- | ----- | ------------- |
| The separator side: noise as an extra source for "other"; codec and room damage on the mix only, with clean stems as targets | Not built: Explode doesn't exist yet | S1, reusing `training/augment.py` ([training data §6](docs/training-data.md#6-worse-recordings-noise-and-degradation)) |
| On-the-fly features (the issue's option 2): fresh degraded features for part of each batch | Not built; worth it only if the degraded copies prove not enough | Later; `training/features.py` |
| An augmentation control in the desktop TRAIN tab | Built: **Worse recordings** in the TRAIN tab (the kinds, 1–4 copies, your own noise folder) writes `train.augment` | Done; `desktop/src-tauri/src/train.rs` and the hub page |
| Measuring the gain on a real bad-recording test set | The pipeline scores one (`evaluate.bad_recordings.dataset`); the set still has to be recorded ([how](docs/training-data.md#6-worse-recordings-noise-and-degradation)) | Next, with T2's and T5's real test sets |

## Cross-repository dependencies

- **Song Deck** owns the bridge contracts (transcription, separation, the planned harmony one);
  BackTrack implements them. It pins BackTrack Surveil v0.6.1 (all three models) for its one-click
  install and runs v0.6.1's WebAssembly build with the drums, bass and melody model plugins, so a
  new release reaches its users when Song Deck moves the pin (`npm run backtrack:pin`). It renders the training data (`dataset:render`, 0.19.0 or later
  for `--separate-stems`; `dataset:perform` for ACE-Step takes); model plugins need 0.18.0 or
  later. Its round-trip lab now assigns tracks to finer stems (Song Deck #176, closed); its
  rare-instrument stems wait on #2. Its separation adapter already works with Explode on port 8827;
  the one-click preset waits for S3, and M7 needs Song Deck changes
  first ([Song Deck's side](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/theory-models.md)).
- **Onyx Engine:** the ggml pin (`packaging/vendor/ggml.conf`) is kept equal to the revision
  Onyx Engine's llama.cpp pin last synced; Onyx Engine's `scripts/check-ggml-pin.sh` gives the
  commit. Both are at `f1aa6a3` (llama.cpp b11820) since 0.6.0. Its release convention (asset names, checksums) is what Song Deck's install expects.
- **JX Runtime:** M6, where it installs and runs the audio apps.
- **Release infrastructure:** the release workflow and toolkit are pinned to a shared commit
  ([.release/README.md](.release/README.md)).

## Decisions and open questions for the owner

- **Vocal training data for Explode:** licensed, commissioned or synthetic. It is the largest cost
  and risk in the plan and sets S1's schedule; [training data §4](docs/training-data.md#4-paid-and-licensable-data)
  prices the options (session singers, Sonovox, XIM Audio).
- **Code signing and notarization** for Windows and macOS.
- **Platforms and GPU backends for 1.0.** GPU builds also need a CPU-fallback scheduler first.
- **The licence of the repository and weights:** the overall and theory plans still list it as
  open, but the repository is MIT ([LICENSE](LICENSE)) and [training data](docs/training-data.md)
  says weights are released under MIT. Confirm it and close it in the plans.
- **Slakh and Lakh MIDI provenance:** [training data](docs/training-data.md) allows Slakh with a
  caveat (it was rendered with Kontakt instruments) and the pipeline imports it for training,
  while the [theory plan](docs/plans/theory.md) keeps Slakh test-only until counsel answers. One
  answer should cover both.
- **Theory models:** the harmony app's name; the chord vocabulary beyond the first 14 qualities;
  modes in the key head; which public-domain editions allow training.
- **The round-trip lab's future** (Song Deck's call): remove it once the apps evaluate themselves,
  or keep it as a benchmark.

## Planning documents

| Document | What it covers | State |
| -------- | -------------- | ----- |
| [docs/plans/overall.md](docs/plans/overall.md) | The goal, decisions, milestones M0–M7, contracts and open decisions across the projects | Current |
| [docs/plans/surveil.md](docs/plans/surveil.md) | Surveil: interfaces, how it works, models in order, training data, T0–T7 | Current |
| [docs/plans/explode.md](docs/plans/explode.md) | Explode: interfaces, design, stems in order, training data, S0–S5 | Current; S0 built |
| [docs/plans/theory.md](docs/plans/theory.md) | M7: the chords and key model, the harmony model and app, C0–C3 and H0–H2 | Current; C0 done |
| [docs/training-data.md](docs/training-data.md) | Every dataset, instrument library and paid source checked, with licences (2026-10-20); degradation; faster renders | Current research |
| [docs/training-at-home.md](docs/training-at-home.md) | Training at home step by step, the TRAIN tab, nightly runs | Current guide |
| [training/README.md](training/README.md) | Every training script and pipeline option | Current reference |
| [docs/desktop.md](docs/desktop.md) | The desktop apps, how they are built, adding Explode, what is not done | Current |
| [weights/README.md](weights/README.md) | Released models, how they were trained and how they score | Current (drums 0.1.0) |
| [wasm/README.md](wasm/README.md) | The WebAssembly build | Done (T4) |
| Song Deck [`docs/plans/README.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/README.md) | Song Deck's copy of the cross-project plan, M0–M6 | Kept in Song Deck; this repository's overall plan adds M7 |
| Song Deck [`separator-app.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/separator-app.md), [`transcriber-app.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/transcriber-app.md) | The first versions of the Explode and Surveil plans | Superseded by explode.md and surveil.md, which moved here in 0.0.1 |
| [apps/explode/README.md](apps/explode/README.md) | How Explode separates (S0), the placeholder model, where a trained model plugs in | Current |
| Song Deck [`theory-models.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/theory-models.md) | Song Deck's changes for M7, by milestone | Current |
| Song Deck [`training-data-research.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/training-data-research.md) | Generation services, datasets and libraries (2026-10-19) | Current for generation; for datasets, training-data.md is newer |

Reference docs, not plans: [building](docs/building.md), [HTTP API](docs/http-api.md),
[front end](docs/front-end.md), [GGUF metadata](docs/gguf-metadata.md), [the page](docs/page.md)
and the [design language](design/README.md).
