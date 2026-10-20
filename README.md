# BackTrack

![BACKTRACK: song in, stems and MIDI out. Runs on your machine.](docs/images/hero.jpg)

A stem separator and a MIDI transcriber, built from one repository on a shared core:

- **BackTrack Explode** splits a song into stems (`backtrack-explode`, port 8827). *Coming in
  milestone S0.*
- **BackTrack Surveil** turns audio into MIDI (`backtrack-surveil`, port 8826).

Each works on its own (command line, or the page the binary serves) and as a provider inside
[Song Deck](https://github.com/jxburros/Song-Deck), through Song Deck's separation and
transcription bridge contracts. Everything runs locally on [ggml](https://github.com/ggml-org/ggml),
with weights in GGUF; no Python at run time.

> **Status: milestone M4.** Surveil has its first trained model, **drums**
> ([weights/](weights/README.md): F 0.81 against Song Deck's built-in 0.50 on held-out rendered
> songs), and runs in WebAssembly inside Song Deck ([wasm/](wasm/README.md)). Bass, melody and
> vocal models train with `training.train_pitched`; until they are trained, those stems use the
> placeholder model (hand-set weights). Training runs from `training/`, at home too
> ([docs/training-at-home.md](docs/training-at-home.md)). See [the plan](docs/plans/overall.md).

## Quick start

**Desktop apps:** from a [release](https://github.com/jxburros/BackTrack/releases), install
*BackTrack Surveil* (`-setup.exe` on Windows, `.dmg` on macOS, `.deb` or `.AppImage` on Linux)
and open it like any other app. *BackTrack*, the hub, shows every BackTrack app you have
installed in one window. See [the desktop apps](docs/desktop.md).

**Command line:** unpack the archive for your platform and run `backtrack-surveil serve`, then
open http://127.0.0.1:8826. On Windows you can also double-click `backtrack-surveil.exe`: it
starts the server and opens the page; keep its window open while you use it. To use a
downloaded model such as `surveil-drums-0.1.0.gguf`, put it in a `models` folder next to the
binary.

From source:

```bash
npm ci                                        # fetch the pinned ggml
cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j

./build/backtrack-surveil song.wav --stem bass -o bass.mid
./build/backtrack-surveil serve               # then open http://127.0.0.1:8826
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

## How it works

![How it fits: your audio and the model weights go through core/ into Surveil (and Explode, coming in S0), which you reach from the CLI, the page or Song Deck](docs/images/architecture.jpg)


Audio is resampled to 22.05 kHz mono and turned into a harmonic constant-Q spectrogram
([docs/front-end.md](docs/front-end.md)). A small convolutional network on ggml gives, per
frame, the likelihood of a note starting, sounding and ending at each of 88 pitches, plus a
pitch curve for bends (or hits per class, for drums). Note picking turns likelihoods into
notes in code, so behaviour changes without retraining. Models are GGUF files that carry their
own front-end settings and licence record ([docs/gguf-metadata.md](docs/gguf-metadata.md)).

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
