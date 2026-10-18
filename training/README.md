# Training

Python (PyTorch) training code for Surveil's models, exporting GGUF: milestone **T1** (the
pipeline) and the code side of **T2** (the drum model). See
[docs/plans/surveil.md](../docs/plans/surveil.md#training-code-training). The separator's
training (S1) will share the front end and the GGUF writer.

What it matches, and checks it matches:

- the front end, step by step: [docs/front-end.md](../docs/front-end.md);
- the model graph, tensor names and shapes, and metadata keys:
  [docs/gguf-metadata.md](../docs/gguf-metadata.md).

Needs Python 3.10+, NumPy and PyTorch (CPU is enough; a CUDA or Apple MPS GPU is used when
present); nothing else, no downloads. Run
everything from the repository root (`python3 -m training.<module>`), with the binary built
(`cmake --build build`).

```bash
pip install numpy torch --index-url https://download.pytorch.org/whl/cpu   # if not installed
```

## Quick start: a toy drum model, end to end

```bash
python3 -m training.synth --out data/synth --songs 60 --seconds 30          # 30 min of synthetic drums
python3 -m training.train_drums --data data/synth --out models/surveil-drums-toy.gguf \
    --epochs 16 --version 0.0.1-toy --name "Surveil drums (toy, synthetic)"
python3 -m training.parity --binary build/backtrack-surveil --placeholders \
    --model models/surveil-drums-toy.gguf                                 # PyTorch == C++
python3 -m training.evaluate --model models/surveil-drums-toy.gguf --data data/synth \
    --binary build/backtrack-surveil                                      # scored through the binary
build/backtrack-surveil song.wav --stem drums --models-dir models -o drums.mid
```

## The pieces

| Module             | What it does                                                                                                     |
| ------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `frontend.py`      | WAV decoding (as dr_wav), the resampler (as `core/src/audio.cpp`) and the harmonic CQT (as `core/src/hcqt.cpp`), in NumPy |
| `model.py`         | `SurveilCNN`: the `surveil-cnn` graph in PyTorch, pitched and drums, both drum heads; `DRUM_LAYOUT`               |
| `gguf.py`          | GGUF v3 reader and writer (F32 tensors, typed metadata), no dependency beyond NumPy                              |
| `export.py`        | PyTorch model to GGUF with the full metadata, and any `surveil-cnn` GGUF back (`--check FILE` prints its layout) |
| `parity.py`        | The same clips through PyTorch and the binary; fails loudly                                                     |
| `datasets.py`      | Readers for Song Deck's dataset export and E-GMD; the General MIDI drum mapping                                  |
| `synth.py`         | Synthetic drum songs written as a Song Deck dataset (also the readers' test data)                                |
| `features.py`      | HCQT features cached on disk (float16, memory-mapped), onset targets, crops                                      |
| `train_drums.py`   | Training loop and export                                                                                         |
| `evaluate.py`      | Onset F-measure per class, through PyTorch or through the binary                                                 |
| `check_performed.py` | Checks training on performed (ACE-Step) audio on a tiny synthetic set: shifted labels, windows, masking, resume (CI) |
| `midi.py`          | Standard MIDI File reading (through the tempo map) and a minimal writer                                          |

### Front end

`frontend.hcqt_of_file(path, fe)` is the binary's front end from a WAV file: decode (8/16/24/32-bit
PCM or float, as dr_wav converts them), average to mono, resample with the same Kaiser sinc table
(a polyphase form for rational ratios such as 44.1 → 22.05 kHz or 48 → 22.05 kHz), then the HCQT
with the same sparse kernels, octave decimation and frame placement. Settings come from the
model (`FrontEnd.from_meta`); `FrontEnd.drums()` is the drum default (filter scale 0.25). Float
settings are rounded through float32, as the GGUF stores them. Only WAV is read here; the
binary also decodes FLAC and MP3.

### Model and GGUF

`SurveilCNN` keeps the GGUF tensor names and layouts: input `(batch, harmonics, frames, bins)`,
so a ggml kernel `[KW, KH, C_in, C_out]` is a PyTorch `Conv2d` weight `(C_out, C_in, KH, KW)` as
is, and `band.weight` rows index `channel × n_bins + bin`. `model.infer(x)` gives the binary's
clip semantics (zero frames beyond the clip edges, `receptive_radius` of context);
`model.ggml_parity = True` adds ggml's float16 rounding inside convolutions (see
[docs/gguf-metadata.md](../docs/gguf-metadata.md#tensors)). `export.load(path)` reads any
`surveil-cnn` file, including `backtrack-surveil make-placeholder -o ref.gguf`;
`export.save(...)` writes every metadata key, with `audio.sources` (the licence record, required)
and `audio.training` (JSON: epochs, data size, layout, valid score).

### Activations (the binary's raw likelihoods)

```bash
backtrack-surveil CLIP --model M.gguf --stem drums -o - --activations acts.json --hcqt hcqt.npy
```

`--activations FILE` (or `-` for stdout) writes one JSON object:

```jsonc
{
  "model": "M", "kind": "drums", "frames": 388, "frame_rate": 86.1328125, "outputs": 7,
  "receptive_radius": 12,
  "classes": ["kick", ...], "class_notes": [36, ...],          // drums
  "pitch_min": 21, "bins": 264,                                // pitched
  "onset": [[0.01, ...outputs], ...frames],                    // every kind
  "note": [...], "offset": [...], "contour": [[...bins], ...]  // pitched
}
```

Values are the float32 likelihoods, printed exactly. `--hcqt FILE` writes the model's input as a
NumPy `.npy` (float32, shape `[harmonics, frames, bins]`, `np.load` reads it). Both come from
the same run as the MIDI and `--json` outputs.

### Parity

```bash
python3 -m training.parity --binary build/backtrack-surveil --placeholders [--model M.gguf ...] [--clip X.wav ...]
```

For each model and clip it compares the HCQT from the WAV file (`hcqt`), the PyTorch model on
the binary's HCQT (`model`), PyTorch end to end (`e2e`) and, for drums, the Python note picking
against the binary's notes (`picks`, exact). `--placeholders` covers the binary's placeholder
models (pitched and drums, wired and random, the temporal drum head), a placeholder re-written
by `gguf.py`, and a PyTorch-built model with an extra trunk layer exported here. Default
tolerances (max abs): HCQT 1e-4, likelihoods 2e-3 (same input) and 5e-3 (end to end). Measured:
HCQT ≤ 5.3e-6 on 44.1 kHz stereo 16-bit, 48 kHz float and 22.05 kHz 24-bit clips; likelihoods
between 1e-7 and 1e-3, the larger ones on the wired placeholders, whose gain-20 kernels turn a
value landing on the other side of a float16 rounding step into a visible difference.

### Data

**Song Deck** (`--data DIR`, repeatable): the directory `npm run dataset:render` writes
(`manifest.json`, format `songdeck-dataset` version 1; splits from the manifest). `--audio`
picks what the model hears: `stem` (the drum stems summed, default), `separated` (the drum
stem separated from the mix; songs without one are skipped) or `mix`; several, comma-separated,
give one item each. Hits come from each drum stem's `notes` (or its MIDI file), mapped to the 7
classes: kick {35, 36}, snare {37–40}, toms {41, 43, 45, 47, 48, 50}, hihat_closed {42, 44},
hihat_open {46}, crash {49, 52, 55, 57}, ride {51, 53, 59}; other notes are dropped.

**Performed audio** (`--audio performed`, combinable: `--audio stem,separated,performed`):
ACE-Step performances of the songs, added to a dataset by Song Deck's `npm run dataset:perform`
(`songs[].performances[]` in the manifest). Each take's separated drums
(`performances[].separated.stems.drums`) is one item, in its song's split, with the song's
hits moved to performance time: note time + `offset` + the `shift` of the window (in song
time) holding the note. ACE-Step does not play exactly what was written, so only part of a
take is trusted:

- a take whose `offset_strength` is below `--min-offset-strength` (default 0.2) is skipped;
- of the rest, only windows with agreement `f >= --min-agreement` (default 0.6) are used; they
  become the item's accepted regions (performance time; a take with no `windows` counts as one
  window with its overall `agreement`);
- training draws each crop around a random accepted frame and **masks the loss** outside the
  accepted regions (frames there contribute nothing; crops may cross window edges, so the
  audio around an accepted frame still serves as context). Items are drawn in proportion to
  their accepted frames, and an epoch is one pass over the accepted frames;
- evaluation scores only inside the accepted regions (both reference hits and estimates).

Training prints, at the start, how many takes were used and how many minutes were accepted
(also per split). `audio.sources` gains one line per dataset and provider/model, e.g.
"ACE-Step performances of Song Deck renders (ace-step-api, acestep-v15-turbo), dataset
sd-drums-v1; licence checked by the owner 2026-10-18", and `audio.training` records the inputs
(`inputs`), the thresholds and the accepted minutes (`performed`). Features for performed audio
are cached like the others (keyed by the file).

**E-GMD** (`--egmd ROOT`, the directory holding `e-gmd-v1.0.0.csv`): splits from the CSV
(`validation` is `valid`), the same mapping plus the TD-17's 22 (closed hi-hat edge), 26 (open
hi-hat edge) and 58 (floor tom rim). It is 444 hours: `--egmd-limit N` uses about N items spread
over the list. E-GMD is believed CC BY 4.0; check the licence yourself before shipping weights
trained on it (the record goes into `audio.sources`).

**Synthetic** (`python3 -m training.synth --out DIR`): songs with their own synthesised kit,
pattern, tempo, fills and level, plus a bass line and pad as the other stems, written in the
Song Deck layout. Toy data: it proves the pipeline, it is no substitute for Song Deck's renders.

Features are computed once per item, in parallel, into `<first data dir>/.cache/hcqt-<settings>/`
and read memory-mapped, so an epoch costs only the model. The cache holds the one extended
transform every harmonic channel reads (401 bins at the defaults, float16), not the 8 × 264
stack: exact, and about 0.25 GB per hour of audio per `--audio` choice (`--cache DIR` to move
it).

### Training

```bash
python3 -m training.train_drums --data DIR [--data DIR2] [--egmd ROOT --egmd-limit 2000] \
    --out models/surveil-drums-0.1.0.gguf [--audio stem,separated,performed] [--epochs 30] [--minutes 0] \
    [--device auto] [--patience 0] [--resume] [--min-agreement 0.6] [--min-offset-strength 0.2]
```

Each step takes random 256-frame crops (`--crop`) with the receptive radius of context each
side, exactly as the binary runs chunks, applies a random level change (a shift on the log
scale, `--gain-db`), and trains onset logits against Gaussian targets around each hit's exact
frame (`sigma` 0.6 frames), with onset frames weighted ×4 (`--pos-weight`), scaled per class
by `(median hits / class hits) ** 0.5` within 0.5–4 (`--class-balance`), so crashes and open
hi-hats are not drowned out by closed hi-hats. An epoch is one
pass over the training frames (`--steps-per-epoch` to change). After each epoch the valid
split is scored as the binary would pick hits; the best epoch is exported with
`general.name` "Surveil drums", `general.version` (`--version`, default 0.1.0),
`general.license` MIT, `audio.stems` ["drums"], `audio.kind` "drums", `audio.sources` (one
line per dataset, plus `--source`) and `audio.training`. A `.pt` checkpoint next to `--out`
allows `--resume`; `.history.json` keeps the loss and valid score per epoch.

**Unattended runs.** The checkpoint (`<out>.pt`, with the history) and `<out>.history.json`
are rewritten atomically after every epoch, so an interrupted run loses at most the epoch in
progress: rerun the same command with `--resume` (keep `--epochs`, `--batch` and the data the
same; the learning-rate schedule depends on them). `--resume` with no checkpoint starts from
scratch. `--patience N` stops when the valid mean class F has not improved for N epochs
(0, the default, is off); `--minutes M` stops after M minutes. The GGUF is also written
atomically. The last line is one summary:

    train_drums: OK wrote OUT: best epoch 12/17 (of 30, patience), valid mean class F 0.812, 41.3 min on cuda
    train_drums: FAILED: <reason>                         (exit code 1, traceback above it)
    train_drums: INTERRUPTED; ... rerun with --resume     (exit code 130)

and `<out>.summary.json` holds the same as JSON (`ok`, `out`, `best_epoch`, `epochs`,
`max_epochs`, `valid_mean_class_f`, `stopped`: completed | patience | time limit, `device`,
`minutes`, `training`, `sources`). Exit code 0 means the GGUF was written.

**Device.** `--device auto` (the default) picks CUDA, then MPS, then CPU; `cpu`, `cuda`, `mps`
force one (an unavailable one is an error). Features stay NumPy on the CPU and cached. Batches
are assembled in a background thread from the cached float16 transform (401 bins), copied to
the device as such (pinned memory on CUDA) and expanded to the 8 × 264 harmonic stack there,
so the copy is 5.3 times smaller than the model's input and the GPU is not left waiting for
the data. The default batch is 16 crops on CPU and 64 on a GPU (`--batch` to choose; with 64
an epoch has a quarter of the steps at the same learning rate, `--batch 16` reproduces the CPU
schedule). `--threads` sets PyTorch's CPU threads.

Speed on a 4-core CPU (no GPU), default layout: about 9,000 training frames per second with the
machine to itself, so an epoch over one hour of audio (310k frames) takes about 35 s, plus
valid scoring (about 0.3 s per minute of audio). Features cost about 0.4 s per minute of
44.1 kHz audio on 4 processes, once.

### Evaluation

```bash
python3 -m training.evaluate --model M.gguf --data DIR [--split test] [--audio stem] [--binary build/backtrack-surveil] \
    [--json report.json] [--device auto]
```

Onset F-measure per class within ±50 ms, as Song Deck's `eval:datasets` measures notes
(`packages/audio/test/eval/metrics.ts`: greedy one-to-one matching, closest pairs first, same
class), plus the overall F, the mean F over classes and the timing error of matched hits. With
`--binary` each item is transcribed by the binary (`--stem drums --json`) and its notes are
scored, so what is measured is what ships; without, PyTorch runs the model and
`evaluate.pick_drums` (a port of `notes.cpp`'s drum picking, checked exact by the parity
script) picks the hits.

`--audio performed` scores only the accepted windows of each take (same `--min-agreement` and
`--min-offset-strength` as training); the printed header says so and the JSON gains
`"performed": {"accepted_windows_only": true, "items", "accepted_minutes", "minutes",
"min_agreement", "min_offset_strength"}`. Every report carries `"audio"` (the inputs scored).

## On Song Deck's drum export

For a directory written by `npm run dataset:render` (e.g. `sd-drums-v1`: 400 songs of about
61 s; the 357 with a drum stem give items):

```bash
python3 -m training.train_drums --data /path/to/sd-drums-v1 --audio stem,separated \
    --out models/surveil-drums-0.1.0.gguf --epochs 30
python3 -m training.evaluate --model models/surveil-drums-0.1.0.gguf --data /path/to/sd-drums-v1 \
    --audio separated --split test --binary build/backtrack-surveil --json test-separated.json
```

`stem,separated` trains on the clean drum stem and on the drum stem separated from the mix (the
input Song Deck sends), with the same labels: about 9.4 hours, 2.9M frames, 712 steps per epoch,
about 5.5 minutes per epoch on 4 CPU cores plus about 30 s of valid scoring. The feature cache
for both inputs is 2.6 GB (`<data>/.cache`; computing it took about 4.5 minutes). Percussion
outside the 7 classes (tambourine, cowbell, congas...) stays in the audio with no label, so the
model learns to ignore it.

## Results so far

Toy model, default layout (89,567 parameters), trained for 30 epochs (about 20 minutes on 4
cores) on 24 minutes of synthetic drums (48 songs; 6 valid, 6 test), scored on the test split
through the binary: overall F 0.758 (P 0.866, R 0.675), mean class F 0.752; kick 0.977, snare
0.891, toms 0.867, crash 0.821, ride 0.762, closed hi-hat 0.611, open hi-hat 0.336; matched
hits within 4.6 ms on average (frames are 11.6 ms). PyTorch and the binary agree to 4e-4 on its
likelihoods and exactly on its hits. That says the pipeline works, not how the model does on
real drums: that is T2, on Song Deck's renders, E-GMD and the real test set.
