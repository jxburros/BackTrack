# BackTrack

![BACKTRACK: song in, stems and MIDI out. Runs on your machine.](docs/images/hero.jpg)

A stem separator and a MIDI transcriber, built from one repository on a shared core:

- **BackTrack Explode** splits a song into stems (`backtrack-explode`, port 8827). *Milestone S0:
  the app works end to end with a placeholder model; the first trained model is S2.*
- **BackTrack Surveil** turns audio into MIDI (`backtrack-surveil`, port 8826).

Each works on its own (command line, or the page the binary serves) and as a provider inside
[Song Deck](https://github.com/jxburros/Song-Deck), through Song Deck's separation and
transcription bridge contracts. Everything runs locally on [ggml](https://github.com/ggml-org/ggml),
with weights in GGUF; no Python at run time.

> **Status: M4 released (v0.6.1); M5 started.** Explode runs on the shared core with a
> placeholder model (masks, no training): rough stems that always sum back to the mix. Surveil has trained models for **drums**, **bass** and **melody**
> (lead lines and vocals), each well ahead of Song Deck's built-in transcription on unseen rendered
> songs ([weights/](weights/README.md): drums F 0.85 against 0.53, bass 0.47 against 0.21, sung
> leads 0.68 against 0.37), and runs in WebAssembly inside Song Deck ([wasm/](wasm/README.md)).
> Other stems (piano, guitar, the full mix) use the placeholder model (hand-set weights). Training
> runs from `training/`, at home too ([docs/training-at-home.md](docs/training-at-home.md)). See
> [the plan](docs/plans/overall.md) and [the roadmap](ROADMAP.md).

## Quick start

**Desktop apps:** from a [release](https://github.com/jxburros/BackTrack/releases), install
*BackTrack Surveil* (`-setup.exe` on Windows, `.dmg` on macOS, `.deb` or `.AppImage` on Linux)
and open it like any other app. *BackTrack*, the hub, shows every BackTrack app you have
installed in one window. See [the desktop apps](docs/desktop.md).

**Command line:** unpack the archive for your platform and run `backtrack-surveil serve`, then
open http://127.0.0.1:8826. On Windows you can also double-click `backtrack-surveil.exe`: it
starts the server and opens the page; keep its window open while you use it. To use a
downloaded model such as `surveil-drums-0.2.0.gguf`, put it in a `models` folder next to the
binary.

From source:

```bash
npm ci                                        # fetch the pinned ggml
cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j

./build/backtrack-surveil song.wav --stem bass -o bass.mid
./build/backtrack-surveil serve               # then open http://127.0.0.1:8826
./build/backtrack-explode song.wav            # stems into song-stems/
./build/backtrack-explode serve               # then open http://127.0.0.1:8827
```

## What it looks like

`backtrack-surveil serve` puts this page on `http://127.0.0.1:8826`: drop a track, tap the line,
read the case file, export the MIDI. Bright notes are the ones the model is sure of.

![The Surveil page, marked up: drop zone, waveform, settings, TAP THE LINE, REC, case file, piano roll and export](docs/images/page-tour.jpg)

The page has its own guide: [docs/page.md](docs/page.md).

## Surveil on the command line

```bash
backtrack-surveil INPUT [--stem mix|vocals|bass|drums|piano|guitar|melody|other]
                        [--key "A minor"] [--tempo 120] [--chords chords.json] [--program 33]
                        [--pitch-bends] [-o out.mid] [--json out.json|-]
                        [--activations likelihoods.json|-] [--hcqt hcqt.npy]
                        [--model ID|FILE.gguf] [--device NAME] [--threads N]
backtrack-surveil serve [--host 127.0.0.1] [--port 8826] [--api-key KEY] [--allow-origin URL]
backtrack-surveil models | devices
backtrack-surveil make-placeholder -o FILE.gguf [--kind drums] [--init random --seed N] [--head temporal]
```

Input is WAV, FLAC or MP3. Output is a Standard MIDI File and, with `--json`, the same JSON the
server returns; `--activations` and `--hcqt` write the model's raw likelihoods and its input,
for the training code's parity check ([training/README.md](training/README.md)). Song context
(key, tempo, chords) shapes note picking: out-of-key and non-chord pitches are less likely, and
note starts are pulled toward the tempo's grid. Bass, vocals and melody are transcribed one
note at a time.

## Surveil in Song Deck

Start `backtrack-surveil serve` and add a transcription provider at `http://127.0.0.1:8826`.
It implements Song Deck's `POST /transcribe` contract plus an optional `context` field, with
`GET /info`, `GET /health` and `POST /cancel`; see [docs/http-api.md](docs/http-api.md).

Song Deck can also run Surveil inside its page with nothing to install: the WebAssembly build
([wasm/](wasm/README.md)) with a model installed as a Song Deck model plugin.

## Explode on the command line

```bash
backtrack-explode INPUT [--stems vocals,drums,bass,other | --stems vocals,instrumental]
                        [--augment | --passes 1-4] [--format pcm16|pcm24|float32]
                        [-o DIR] [--json out.json|-] [--chunk SECONDS] [--model ID]
backtrack-explode serve [--host 127.0.0.1] [--port 8827] [--api-key KEY] [--allow-origin URL]
backtrack-explode models
```

Stems are 44.1 kHz stereo WAV files, one per stem, and they sum back to the input. `other` (or
`instrumental`, on request) holds whatever the other stems don't. `--augment` averages a second,
polarity-flipped and channel-swapped pass. In Song Deck, add a separation provider at
`http://127.0.0.1:8827`: it implements `POST /separate` with `GET /info`'s `stems` and
`on_request_stems` ([docs/http-api.md](docs/http-api.md#backtrack-explode)). The placeholder is
for wiring and checking the pipeline, not for real use: for good stems today use Demucs or a
RoFormer model in Song Deck.

## How it works

![How it fits: your audio and the model weights go through core/ into Surveil (and Explode, coming in S0), which you reach from the CLI, the page or Song Deck](docs/images/architecture.jpg)

Audio is resampled to 22.05 kHz mono and turned into a harmonic constant-Q spectrogram
([docs/front-end.md](docs/front-end.md)). A small convolutional network on ggml gives, per
frame, the likelihood of a note starting, sounding and ending at each of 88 pitches, plus a
pitch curve for bends (or hits per class, for drums). Note picking turns likelihoods into
notes in code, so behaviour changes without retraining. Models are GGUF files that carry their
own front-end settings and licence record ([docs/gguf-metadata.md](docs/gguf-metadata.md)).

Explode resamples to 44.1 kHz stereo and works in 20-second chunks that overlap by a second and
crossfade. Per chunk, a short-time Fourier transform goes to the model, which estimates each
stem's spectrogram; what the estimates miss is shared out by each stem's power in that bin, so
the stems sum to the mix, and the inverse transform gives the audio back
([apps/explode/README.md](apps/explode/README.md)).

## Documentation

- [The page](docs/page.md): the Surveil screen, light mode, keyboard, the design rules
- [The desktop apps](docs/desktop.md): installing BackTrack Surveil and the BackTrack hub, and
  how `desktop/` builds them
- [Building](docs/building.md): ggml vendoring, GPU backends, layout
- [HTTP API](docs/http-api.md): endpoints, and one job from start to finish
- [Front end](docs/front-end.md): how Surveil hears (harmonic CQT), and
  [GGUF metadata](docs/gguf-metadata.md): what a model file says about itself
- [Design language](design/README.md): the boards, the rules and the
  [agent spec](design/AGENT-SPEC.md)
- [Training](training/README.md): the PyTorch pipeline, GGUF export, the parity check and
  drum evaluation
- [Training at home](docs/training-at-home.md): train the drum model on your own computer with
  Song Deck's songs and ACE-Step's covers, step by step, and run it nightly
- [Training data](docs/training-data.md): every dataset, instrument library and paid source checked
  for training, with licences and prices, and how to add them (`training.libraries`)
- Plans: [overall](docs/plans/overall.md), [Surveil](docs/plans/surveil.md),
  [Explode](docs/plans/explode.md), [theory models](docs/plans/theory.md)

## Licence

MIT (see [LICENSE](LICENSE)). Third-party code in `third_party/` keeps its own licence
(cpp-httplib MIT, nlohmann/json MIT, dr_libs public domain or MIT-0); ggml is MIT.

## Unified release procedure

`release.json` is the authoritative release manifest. Keep configured embedded versions synchronized and advance affected apps (patch by default) before merging changes into main. The first adoption establishes a baseline; later PRs must pass **Release version validation**.

Use **Actions → Release → Run workflow** from **main**, choose stable or preview, and leave **dry_run** enabled first. Only apps ahead of their published version in their own namespace are selected. Build/test/sign all required packages before publishing. Tag pushes and merges no longer publish migrated apps automatically. After inspecting the dry run, dispatch with dry_run disabled and satisfy configured approvals. Published versions are never overwritten; use a new version for a correction.

See [.release/README.md](.release/README.md) for this repository's inventory, credentials, compatibility, and validation limits. Shared implementation: [JX Holdings release infrastructure](https://github.com/JX-Holdings-LLC/release-infrastructure).
