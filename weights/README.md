# Released models

Model files the release workflow attaches to each GitHub Release as separate downloads (with
their SHA-256 in `checksums.txt`). Each file carries its own training record in its GGUF metadata
(`audio.sources`, `audio.training`); read it with `python3 -m training.export --check FILE`.

| File                        | Parts                                 | Parameters | Size   | Licence |
| --------------------------- | ------------------------------------- | ---------- | ------ | ------- |
| `surveil-drums-0.2.0.gguf`  | drums (seven classes)                 | 89,567     | 361 KB | MIT     |
| `surveil-bass-0.1.0.gguf`   | bass, one note at a time              | 5,732      | 25 KB  | MIT     |
| `surveil-melody-0.1.0.gguf` | melody and vocals, one note at a time | 5,732      | 25 KB  | MIT     |

With all three in a `models` folder next to the binary (or with `--models-dir weights`), Surveil
picks each part's model from the request's stem: drums, bass, melody and vocals. Other stems use
the placeholder model.

## How they score

Scored on 2026-10-24 with Song Deck 0.19.1's `npm run eval:datasets -- --dataset songdeck
--input separated --split all --tasks transcribe --transcriber internal,model=FILE.gguf`, on songs
none of the models trained on: for each part, 60 songs of 60 s rendered with `npm run
dataset:render -- --songs 60 --seed 900000 --seconds 60 --separate` and that part's
`--only-stems` (the training sets used seeds from 1000). The input is the stem Song Deck's
built-in separator takes out of the full mix, which is what Song Deck sends in use. Notes match
on exact pitch with the onset within ±50 ms; drum hits on class within ±50 ms. Drum scores pool
the hits of every stem; note scores are the mean over stems.

| Part                                    | Stems | Surveil F | P     | R     | Song Deck built-in F | P     | R     |
| --------------------------------------- | ----- | --------- | ----- | ----- | -------------------- | ----- | ----- |
| Drums (drums 0.2.0)                     | 55    | **0.848** | 0.860 | 0.837 | 0.527                | 0.486 | 0.576 |
| Drums (drums 0.1.0, for comparison)     | 55    | 0.837     | 0.838 | 0.835 |                      |       |       |
| Bass (bass 0.1.0)                       | 53    | **0.472** | 0.496 | 0.478 | 0.211                | 0.154 | 0.387 |
| Instrumental leads (melody 0.1.0)       | 14    | **0.917** | 0.955 | 0.883 | 0.757                | 0.678 | 0.877 |
| Sung leads, guide singer (melody 0.1.0) | 48    | **0.684** | 0.738 | 0.664 | 0.372                | 0.302 | 0.537 |

Drums per class (F): drums 0.2.0 kick 0.99, snare 0.93, toms 0.76, closed hat 0.74, open hat 0.75,
crash 0.75, ride 0.75; built-in 0.81, 0.49, 0.22, 0.56, 0.37, 0.16, 0.00.

What this does not show yet: real recordings. The test songs come from the same renderer and
instruments as the training songs, so they flatter the models; the sung leads are Song Deck's
guide singer, not a person. E-GMD (`--dataset egmd`) and the plan's real test sets are still to
be scored.

All three files pass `python -m training.parity` (PyTorch against `backtrack-surveil`) with
ggml `f1aa6a3`, and the WebAssembly build gives the native binary's notes on each
(`wasm/test.mjs`).

## `surveil-drums-0.2.0.gguf`

Drum hits in seven classes (kick, snare, toms, closed and open hi-hat, crash, ride). Same
network as 0.1.0, trained on more songs and on ACE-Step performances.

- **Training data:** Song Deck's training-data export (dataset `sd-drums`, Song Deck 0.17.0):
  700 training and 103 validation items from the clean drum stems, the drums the built-in
  separator took out of the mix, and ACE-Step performances (`acestep-v15-turbo`, licence checked
  by the owner 2026-10-18). Of 357 takes (366 minutes), 186 were accepted by the judge: 7.98
  minutes of windows that agree with the score (6.82 for training, 0.39 for validation); 72 were
  rejected for a weak offset and 99 for no agreeing window.
- **Training:** the TRAIN tab's pipeline on a CUDA GPU, 30 epochs (`--audio
stem,separated,performed`); best validation epoch 25 (mean class F 0.835).
- **Version:** trained as 0.1.0 and released as 0.2.0, since 0.1.0 was already a released file;
  only `general.version` was changed in the file.

## `surveil-bass-0.1.0.gguf`

Bass notes, one at a time (`train_pitched`, the pitched network with a 5×5 first layer).

- **Training data:** dataset `sd-bass` (Song Deck 0.17.0): 540 training and 84 validation items
  from the clean bass stems and the bass the built-in separator took out of the mix. No ACE-Step
  audio.
- **Training:** the TRAIN tab's pipeline on a CUDA GPU, 30 epochs (`--audio stem,separated`);
  best validation epoch 21 (note F 0.685).

## `surveil-melody-0.1.0.gguf`

Lead lines and vocals, one note at a time: Song Deck's `melody` and `vocals` stems.

- **Training data:** dataset `sd-melody` (Song Deck 0.17.0): 562 training and 86 validation
  items from the clean melody and vocal stems and the built-in separator's vocals and other
  stems. The vocals are Song Deck's guide singer. No ACE-Step audio.
- **Training:** the TRAIN tab's pipeline on a CUDA GPU, 30 epochs (`--audio stem,separated`);
  best validation epoch 26 (note F 0.745).
- **Known limit:** it stays silent on thin tones (four partials, like `melody.wav` from
  `scripts/make-test-audio.py`) and transcribes fuller ones (`lead.wav`, eight partials).

## Earlier models

`surveil-drums-0.1.0.gguf` (releases v0.1.0 to v0.4.0) was the first trained model: dataset
`sd-drums-v1` (Song Deck 0.16.0, 400 songs, no ACE-Step audio), 20 epochs on 4 CPU cores, F 0.813
against Song Deck's built-in 0.495 on 38 held-out songs of its own dataset. Song Deck's one-click
install of v0.4.0 still downloads it from that release.
