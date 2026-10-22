# BackTrack roadmap

What is done, what is in progress and what comes next, with every open issue placed on it. It
sums up the [plans](docs/plans/overall.md), the [changelog](CHANGELOG.md) and the issues as of
2026-10-22 (BackTrack 0.5.1). The plans keep the detail; update this page when a milestone moves
or an issue opens or closes.

## Where BackTrack stands

- **Milestone M4** of the [overall plan](docs/plans/overall.md): Surveil runs inside Song Deck's
  page (WebAssembly), and bass and melody models can be trained; none is trained yet.
- **Trained:** one model, Surveil drums 0.1.0 ([weights/](weights/README.md)): F 0.813 against
  Song Deck's built-in 0.495 on 38 held-out rendered songs. Not yet scored on E-GMD or real
  recordings. Bass, melody and vocals use the placeholder model.
- **Ships (latest release v0.4.0):** `backtrack-surveil` CPU builds for linux-x64, darwin-arm64,
  darwin-x64 and win32-x64; the drum model; the WebAssembly build; desktop installers for
  BackTrack Surveil and the BackTrack hub with its TRAIN tab (not code-signed or notarized).
  Song Deck pins v0.4.0.
- **Versioned, not released:** 0.5.0 (training libraries, the training queue, the training-data
  research) and 0.5.1 (degradation augmentation, #17). The next release is the first through the
  unified release workflow ([README](README.md#unified-release-procedure)), whose dry run and
  publication are not verified yet.
- **Not built:** Explode (only [its README](apps/explode/README.md)), the theory models, GPU builds.

## Milestones

Status is **done**, **in progress**, **next** (start soon) or **later**.

### Across the projects ([overall plan](docs/plans/overall.md))

| #  | Milestone                                                              | Status                       | Depends on | BackTrack's part                                       |
| -- | ---------------------------------------------------------------------- | ---------------------------- | ---------- | ------------------------------------------------------ |
| M0 | Round-trip lab in Song Deck (0.15.0)                                   | done                         | none       | None; it is the yardstick for every milestone          |
| M1 | Song Deck uses JX Runtime for LLMs                                     | done                         | none       | None                                                   |
| M2 | Repository, shared core, server, page; Surveil on a placeholder model  | done (0.0.1)                 | none       | T0                                                     |
| M3 | Training pipeline, drum model, Surveil 0.1 binaries, Song Deck preset  | done (v0.1.0)                | M2         | T1–T3; left open: T2's real-recording scores, GPU builds |
| M4 | Surveil in WebAssembly inside Song Deck; bass and melody models        | in progress                  | M3         | T4 done; T5's models to train                          |
| M5 | Explode 0.1: first model, binaries, Song Deck preset                   | next (S0)                    | M2         | S0–S3                                                  |
| M6 | JX Runtime runs the audio apps; Song Deck runs JX Runtime managed      | later                        | M1, M3     | Pinned releases and GGUF metadata, which exist; the work is in JX Runtime and Song Deck |
| M7 | Theory models: chords and key in Surveil, a harmony app, an npm package | next (C0); the rest later | M3 (M4 for WebAssembly) | C0–C3, H0–H1                                |

### Surveil, the transcriber ([plan](docs/plans/surveil.md))

| #  | What                                                        | Status                                                                                      | Depends on                                         |
| -- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| T0 | Core, server, CLI, page, placeholder model                  | done (0.0.1)                                                                                | none                                               |
| T1 | Training pipeline: reader, front end, parity check, GGUF    | done (0.1.0)                                                                                | T0                                                 |
| T2 | Drum model                                                  | in progress: trained (0.1.0); to score on E-GMD (importable since 0.5.0) and a real test set | T1; a real drum test set                           |
| T3 | Release 0.1                                                 | done for CPU (v0.1.0); GPU builds later                                                     | T2; GPU builds need a CPU-fallback scheduler       |
| T4 | WebAssembly inside Song Deck                                | done (v0.4.0)                                                                               | T3; Song Deck's `model` plugin kind (built)        |
| T5 | Bass, then melody and vocals                                | in progress: training built (`train_pitched`, TRAIN tab, libraries, augmentation); models to train at home | T1; Song Deck's `dataset:render` and `dataset:perform`; a real test set |
| T6 | Song context in note picking                                | built (0.0.1); the gain with context on versus off is not measured                          | T0; Song Deck sends `context` (built)              |
| T7 | Keys and guitar (polyphonic)                                | later                                                                                       | T5; a GuitarSet importer; a real test set          |

### Explode, the separator ([plan](docs/plans/explode.md))

| #  | What                                                                      | Status | Issues | Depends on                                             |
| -- | ------------------------------------------------------------------------- | ------ | ------ | ------------------------------------------------------ |
| S0 | App on the shared core: chunking, STFT, server, CLI, page, placeholder    | next   |        | M2 (done)                                              |
| S1 | Data pipeline: renders, Slakh, remixing, augmentation; first vocal data   | next: start sourcing vocals early | #17 follow-up | Song Deck's renders; the vocal-data decision |
| S2 | Vocal/instrumental model, competitive with Demucs two-stem                 | later  | #3     | S0, S1                                                 |
| S3 | Release 0.1: binaries, model download, Song Deck preset                    | later  |        | S2; Song Deck's separator preset (waits for M5)        |
| S4 | Four-stem model                                                           | later  | #3     | S3                                                     |
| S5 | Six stems and finer splits on request                                     | later  | #3, #1 | S4                                                     |
| —  | Rare instruments as on-request splits of "other"                          | later  | #2     | S4 (the issue says so)                                 |

### Theory models, M7 ([plan](docs/plans/theory.md))

| #  | What                                                                                  | Status | Depends on                                                                 |
| -- | ------------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------- |
| C0 | Chord and key labels, vocabulary map, scoring; the built-in detector's baseline       | next: cheap, and decides whether model A is worth building | Song Deck: structured chords in the manifest, chord and key scoring in `eval:datasets` |
| C1 | Model A on renders; `surveil-chords` graph with parity; `--model chords`              | later  | C0                                                                         |
| C2 | Covers with a chord judge, GuitarSet, the owner's test set, WebAssembly, Song Deck plugin | later | C1; T4; Song Deck's chord judge and `task: "chords"` plugin            |
| C3 | Release; the npm package                                                              | later  | C2                                                                         |
| H0 | Harmony corpus with licence checks; harmony contract drafted                          | later  | C2 (model A labels ACE-Step songs); Song Deck's `dataset:harmony` and contract |
| H1 | Model B; the BackTrack Theory app (CLI, HTTP, page, WebAssembly)                      | later  | H0; the app's name                                                         |
| H2 | Song Deck ranks its chord candidates with model B (Song Deck's work)                  | later  | H1                                                                         |

## Now, next, later

**Now**

- Train the bass and melody models at home (T5, M4) from the TRAIN tab, with libraries and
  degradation augmentation; install the best in Song Deck once it beats Song Deck's own
  transcription.
- Finish T2: score the drum model on E-GMD and on a real drum test set.
- Release 0.5.0 and 0.5.1: the first run of the unified release workflow (dry run first). Song
  Deck then moves its pin from v0.4.0.

**Next**

- Explode S0, which starts M5 ([what is left to write](apps/explode/README.md)).
- Vocal training data for S1: decide the route and start sourcing; it has the longest lead time.
- C0: chord and key labels and the built-in detector's baseline (both repositories).
- A real test set per stem type, bad recordings included (T2, T5, #17).
- T6: measure the gain from song context.
- An augmentation control in the TRAIN tab (#17).

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

Degradation augmentation (#17) shipped in 0.5.1 and the issue is closed. It left four things:

| Follow-up | State | Where it sits |
| --------- | ----- | ------------- |
| The separator side: noise as an extra source for "other"; codec and room damage on the mix only, with clean stems as targets | Not built: Explode doesn't exist yet | S1, reusing `training/augment.py` ([training data §6](docs/training-data.md#6-worse-recordings-noise-and-degradation)) |
| On-the-fly features (the issue's option 2): fresh degraded features for part of each batch | Not built; worth it only if the degraded copies prove not enough | Later; `training/features.py` |
| An augmentation control in the desktop TRAIN tab | Not built: the pipeline reads `train.augment` from its config, but the tab has no control for it | Next; `desktop/src-tauri/src/train.rs` and the hub page |
| Measuring the gain on a real bad-recording test set | The pipeline scores one (`evaluate.bad_recordings.dataset`); the set still has to be recorded ([how](docs/training-data.md#6-worse-recordings-noise-and-degradation)) | Next, with T2's and T5's real test sets |

## Cross-repository dependencies

- **Song Deck** owns the bridge contracts (transcription, separation, the planned harmony one);
  BackTrack implements them. It pins BackTrack Surveil v0.4.0 for its one-click install and runs
  Surveil's WebAssembly build with the drums model plugin, so a new release reaches its users
  when Song Deck moves the pin. It renders the training data (`dataset:render`, 0.19.0 or later
  for `--separate-stems`; `dataset:perform` for ACE-Step takes); model plugins need 0.18.0 or
  later. Its round-trip lab now assigns tracks to finer stems (Song Deck #176, closed); its
  rare-instrument stems wait on #2. Explode's preset waits for M5, and M7 needs Song Deck changes
  first ([Song Deck's side](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/theory-models.md)).
- **Onyx Engine:** the ggml pin (`packaging/vendor/ggml.conf`) is kept equal to the revision
  Onyx Engine's llama.cpp pin last synced; Onyx Engine's `scripts/check-ggml-pin.sh` gives the
  commit. Its release convention (asset names, checksums) is what Song Deck's install expects.
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
| [docs/plans/explode.md](docs/plans/explode.md) | Explode: interfaces, design, stems in order, training data, S0–S5 | Current; nothing built |
| [docs/plans/theory.md](docs/plans/theory.md) | M7: the chords and key model, the harmony model and app, C0–C3 and H0–H2 | Current; not started |
| [apps/explode/README.md](apps/explode/README.md) | What S0 reuses from the core and what is left to write | Current |
| [docs/training-data.md](docs/training-data.md) | Every dataset, instrument library and paid source checked, with licences (2026-10-20); degradation; faster renders | Current research |
| [docs/training-at-home.md](docs/training-at-home.md) | Training at home step by step, the TRAIN tab, nightly runs | Current guide |
| [training/README.md](training/README.md) | Every training script and pipeline option | Current reference |
| [docs/desktop.md](docs/desktop.md) | The desktop apps, how they are built, adding Explode, what is not done | Current |
| [weights/README.md](weights/README.md) | Released models, how they were trained and how they score | Current (drums 0.1.0) |
| [wasm/README.md](wasm/README.md) | The WebAssembly build | Done (T4) |
| Song Deck [`docs/plans/README.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/README.md) | Song Deck's copy of the cross-project plan, M0–M6 | Kept in Song Deck; this repository's overall plan adds M7 |
| Song Deck [`separator-app.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/separator-app.md), [`transcriber-app.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/transcriber-app.md) | The first versions of the Explode and Surveil plans | Superseded by explode.md and surveil.md, which moved here in 0.0.1 |
| Song Deck [`theory-models.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/theory-models.md) | Song Deck's changes for M7, by milestone | Current |
| Song Deck [`training-data-research.md`](https://github.com/jxburros/Song-Deck/blob/main/docs/plans/training-data-research.md) | Generation services, datasets and libraries (2026-10-19) | Current for generation; for datasets, training-data.md is newer |

Reference docs, not plans: [building](docs/building.md), [HTTP API](docs/http-api.md),
[front end](docs/front-end.md), [GGUF metadata](docs/gguf-metadata.md), [the page](docs/page.md)
and the [design language](design/README.md).
