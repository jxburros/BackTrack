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
| `train_pitched.py` | The same for pitched models (bass, melody, vocals): note, onset, offset and contour targets from the stems' notes and bends |
| `evaluate.py`      | Onset F-measure per class (drums) or note F (pitched), through PyTorch or through the binary; `pick_pitched` ports the binary's pitched note picking |
| `check_performed.py` | Checks training on performed (ACE-Step) audio on a tiny synthetic set: shifted labels, windows, masking, resume (CI) |
| `pipeline.py`      | The whole run in one command: render, perform, train, evaluate, parity, report; resumable and schedulable ([below](#the-pipeline-one-command)) |
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

## Pitched models: bass, melody and vocals (T5)

```bash
npm run dataset:render -- --out ~/data/sd-bass --songs 400 --seconds 60 --only-stems bass --separate   # in Song Deck
python3 -m training.train_pitched --data ~/data/sd-bass --stem bass --audio stem,separated \
    --out models/surveil-bass-0.1.0.gguf --epochs 30 --patience 6
python3 -m training.evaluate --model models/surveil-bass-0.1.0.gguf --data ~/data/sd-bass \
    --audio separated --split test --binary build/backtrack-surveil
```

`--stem` names the stem kinds in Song Deck's manifest to learn (`bass`, `vocals`, `melody`,
`piano`, `guitar`, `other`), comma-separated: `--stem melody,vocals` for one melody model. The
first is the model's source for note picking, so bass, melody and vocals are picked one note at a
time, as the binary does for them. `--audio separated` reads the separator's stem that holds the
part (`bass` → bass, `vocals` → vocals, the others → other; `datasets.SEPARATED_STEM`). The model
writes `audio.stems` = the `--stem` list, so `backtrack-surveil` (and Song Deck's model plugins)
use it for those stems.

The targets are the pitched graph's four heads: **note** (1 on the frames a note sounds),
**onset** and **offset** (a Gaussian of 0.6 frames around each start and end), and **contour**
(the pitch curve at 3 bins per semitone, a Gaussian of 1 bin around the note's pitch plus its
bend at that frame, so models learn the bends Song Deck's renders carry). The loss is a weighted
BCE on each (`--pos-weight` for the positives, `--offset-weight` for the offset head). The valid
score is the note F (onset within ±50 ms at the same pitch, notes picked by
`evaluate.pick_pitched`, which the parity script checks exact against the binary); the best
epoch is exported. `--audio performed` trains on ACE-Step takes judged for the part (Song Deck's `dataset:perform
--parts bass` or `melody`; `datasets.read_songdeck_notes`): the notes move to each take's timing
(offset plus window shift, as for drums) and only windows with note F ≥ `--min-agreement` count, in
the loss and in validation. `evaluate.py --audio performed` scores those windows too. The rest works
as `train_drums` does: the feature cache, `--resume`,
`--patience`, `--minutes`, `--device`, `<out>.history.json` and `<out>.summary.json`, and the
`train_pitched: OK|FAILED|INTERRUPTED` last line.

The default layout (`PITCHED_LAYOUT`: a 5×5 and a 3×3 trunk conv to 16 and 8 channels, a 3×3
contour head, a 32-channel pool, 5-frame note, onset and offset heads; about 5,700 parameters)
costs about twice the drum model per frame, so it stays usable in the page (Song Deck runs
models in WebAssembly on one thread). `--layout` takes another as JSON, and the binary reads any
layout of the same graph. `npm run eval:datasets -- --transcriber model=<plugin folder>` in Song
Deck compares a model with Song Deck's own transcription on its datasets (a model becomes a Song
Deck plugin with a manifest naming the GGUF and its SHA-256; see Song Deck's `docs/PLUGINS.md`).

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

## The pipeline: one command

[docs/training-at-home.md](../docs/training-at-home.md) is the step-by-step guide (hardware,
installing everything, ACE-Step, a first small run, automation, troubleshooting). In short:

```bash
cp training/pipeline.example.json ~/backtrack-training/pipeline.json      # then edit the paths
python3 -m training.pipeline --config ~/backtrack-training/pipeline.json --dry-run   # prints every command
python3 -m training.pipeline --config ~/backtrack-training/pipeline.json
python3 -m training.pipeline --config ~/backtrack-training/pipeline.json --run latest   # resume a failed run
python3 -m training.pipeline schedule --print linux|macos|windows --config ~/backtrack-training/pipeline.json
```

Steps, in order (`--steps` picks some; with `--run`, the steps named run again even if done):

| Step       | What runs                                                                                                                       |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `render`   | Song Deck's `npm run dataset:render -- --skip-existing` into `dataset`, `render.chunk` songs at a time: songs already there (and their performances) are kept, missing ones rendered, and Song Deck refuses if the options differ from the dataset's. After each chunk, separated stems other than `keep_separated` are deleted. |
| `perform`  | Song Deck's `npm run dataset:perform -- --skip-existing` (ACE-Step plays the songs), with `--judge backtrack-surveil=<url>` when a trained model exists (`perform.judge`). Starts ACE-Step first when `perform.server.command` is set and nothing answers, and stops it afterwards, so training gets the GPU. Exit 1 (some takes failed) is a warning when `allow_failed_takes`; the next run retries them. Exit 3 (ACE-Step unreachable) stops the run unless `perform.optional`. |
| `train`    | `python3 -m training.train_drums` into `runs/<id>/<file_stem>-<version>.gguf`. Skipped when the data, the `train`/`model` settings and the BackTrack commit are the same as the last finished run (`skip_if_unchanged`; `--force` trains anyway). On a resumed run, `--resume` continues the checkpoint only if every argument is the same. |
| `evaluate` | `python3 -m training.evaluate --binary` on the separated test split and on the performed test windows (`eval-separated.json`, `eval-performed.json`), the same for `runs/best` (`eval-best-*.json`), and Song Deck's `npm run eval:datasets -- --dataset songdeck --input separated --tasks transcribe --transcriber internal,backtrack-surveil=<url>` (`eval-songdeck.json`) with this run's model served by `backtrack-surveil serve --models-dir runs/<id>/serve-models` on a free port. No performed take passing the thresholds in the test split is not an error. |
| `parity`   | `python3 -m training.parity --binary ... --model <this run's model>`.                                                           |
| `report`   | `report.md` and `report.json`; the model as a Song Deck model plugin in `runs/<id>/songdeck-plugin/<file_stem>-trained/` (manifest with its SHA-256, the test score as its reliability; copied to `runs/best/` with the model);  copies the model to `runs/best/` when it beats the previous best (re-scored on today's test split) and parity passed. `weights/` is never written. |
| `rejudge`  | Only after a promotion (`perform.rejudge_after_promotion`): Song Deck's `dataset:perform --rejudge --judge backtrack-surveil=<url>` with the new best model served, re-scoring the takes' windows without ACE-Step. |

Each step's output goes to the console and `runs/<id>/<step>.log`; `runs/<id>/state.json` records
what is done. The first failing step stops the run with exit code 1 and prints the command
that resumes it. `runs/pipeline.lock` keeps two runs from overlapping (a lock left by a process
that is gone is removed). `--dry-run` creates nothing and prints every command.

### The config, field by field

JSON has no comments, so they are here. Keys starting with `_` are ignored (use `"_comment"`);
an unknown key is an error. Relative paths are relative to the BackTrack checkout; `~` works.
`null` means "leave it to the script's default".

| Field | Example default | Meaning |
| --- | --- | --- |
| `songdeck` | `../Song-Deck` | Song Deck checkout (with `npm install` and `npm run soundbank:build` done) |
| `backtrack_binary` | `build/backtrack-surveil` | the built binary (`.exe` and `build/Release/` are tried on Windows) |
| `python`, `npm` | `null` | the Python that runs training (default: the one running the pipeline) and npm (default: found on PATH, `npm.cmd` on Windows) |
| `dataset` | `~/backtrack-training/sd-drums` | the dataset directory (Song Deck's layout); the feature cache goes in its `.cache` |
| `runs_dir` | `~/backtrack-training/runs` | one folder per run, `best/`, the lock and `scheduled.log` |
| `low_priority` | `true` | run the steps at low CPU priority (nice 10 / below normal), so the computer stays usable |
| `model.kind` | `drums` | what to train: `drums`, `bass` or `melody` (melody and vocal stems). Bass and melody train with `train_pitched` (performed audio: ACE-Step takes judged for that part by Song Deck's `dataset:perform --parts`, the judge being the best model of the kind once it beats Song Deck's built-in transcription, run in-process with `--judge model=<file>`), are scored by note F and compared with Song Deck by running the model in Song Deck's harness (`--transcriber model=<file>`; needs a Song Deck with model plugins). Give each kind its own `dataset` and `runs_dir`, and set `render.only_stems` (`bass`; `melody,vocals`) and `render.keep_separated` (`["bass"]`; `["vocals", "other"]`) to match |
| `model.name`, `.version`, `.file_stem` | `Surveil drums`, `0.1.0`, `surveil-drums` | `general.name`, `general.version`, and the file name `<file_stem>-<version>.gguf` (its stem is the model id) |
| `render.songs` | `400` | songs in the dataset; raise it to add songs |
| `render.grow_per_run`, `.max_songs` | `0`, `0` | add this many songs on every run (nightly growth), up to `max_songs` (0: no cap) |
| `render.seconds` | `60` | cut songs to their first seconds (`null`: whole songs, about 3× the disk and time) |
| `render.genres` | `null` | genre ids (`null`: every built-in genre, used in turn) |
| `render.seed` | `1000` | song *i* uses seed + *i*; keep it for the life of a dataset |
| `render.only_stems` | `drums` | stems to write (the mix is always full) |
| `render.separate`, `.keep_separated` | `true`, `["drums"]` | also write the built-in separator's stems of the mix (training's `separated` input), and keep only these |
| `render.chunk` | `50` | songs per `dataset:render` call: the four separated stems of at most one chunk wait for pruning, and an interrupted render keeps every finished chunk |
| `render.sample_rate`, `.jobs`, `.soundbank`, `.extra_args` | `44100`, `null`, `null`, `[]` | render rate; songs in parallel (default CPU cores − 1); sound bank folder; more `dataset:render` options |
| `perform.enabled` | `true` | `false`: no ACE-Step (train on renders only; drop `performed` from `train.audio` too) |
| `perform.provider`, `.url` | `ace-step-api`, `http://127.0.0.1:8001` | ACE-Step 1.5's own API server; `ace-step-local` with `http://127.0.0.1:8810` for the v1 bridge |
| `perform.strength` | `0.35` | how far ACE-Step may depart from the song (lower follows it more closely, and labels fit better) |
| `perform.takes` | `1` | performances per song (each has its own seed); raise it later to add takes |
| `perform.split`, `.songs`, `.seed` | all splits, `null`, `null` | which splits get performances, at most this many songs, the seed (default: the dataset's) |
| `perform.window`, `.min_offset_strength`, `.timeout_minutes` | `4`, `0.2`, `20` | agreement window (s), weakest alignment kept, per-take timeout |
| `perform.allow_failed_takes` | `true` | a run with a few failed takes still trains (they are listed in the report and retried next run) |
| `perform.optional` | `false` | `true`: when ACE-Step cannot be reached, carry on without new performances instead of stopping |
| `perform.judge` | `auto` | who scores each take's windows: `auto` serves the best model (`runs/best`, else `weights/surveil-drums-*.gguf`) with `backtrack-surveil serve` and passes `--judge backtrack-surveil=<url>` (falls back to the built-in transcription when there is no model or Song Deck has no `--judge`); `internal`; or `backtrack-surveil=<url>` for a server you run |
| `perform.rejudge_after_promotion` | `true` | the `rejudge` step: after a model is promoted, `dataset:perform --rejudge --judge backtrack-surveil=<url>` re-scores every take with it (no ACE-Step), so the next run trains on better-judged windows |
| `perform.server.command`, `.cwd` | `null` | e.g. `["uv", "run", "acestep-api"]` in your ACE-Step-1.5 folder: started when nothing answers at `url` |
| `perform.server.startup_timeout_minutes`, `.stop_after` | `15`, `true` | how long to wait for it (the first start downloads its models); stop it after the step |
| `train.audio` | `stem,separated,performed` | what the model hears; `performed` is dropped with a warning when the dataset has no performances |
| `train.epochs`, `.batch` | `30`, `16` | epochs; crops per step (`null`: train_drums' default, 16 on CPU and 64 on a GPU, not yet compared) |
| `train.device` | `auto` | `auto` (CUDA, then MPS, then CPU), `cuda`, `mps` or `cpu` |
| `train.min_agreement`, `.min_offset_strength` | `0.6`, `0.2` | which performed windows are trusted (training and the performed evaluation) |
| `train.patience`, `.minutes` | `8`, `0` | stop after N epochs without a better valid score; stop after M minutes (0: off) |
| `train.threads`, `.seed`, `.steps_per_epoch` | `null`, `0`, `null` | PyTorch CPU threads, seed, steps per epoch (default: one pass) |
| `train.resume` | `true` | when a run is resumed, continue its checkpoint (only if the arguments are unchanged) |
| `train.skip_if_unchanged` | `true` | do not retrain on the same data and settings (nightly runs with nothing new end quickly) |
| `train.egmd`, `.egmd_limit`, `.extra_args` | `null` | also train on E-GMD (check its licence), and more `train_drums` options |
| `evaluate.split` | `test` | the split scored |
| `evaluate.performed` | `auto` | score performed test windows when there are any (`true`/`false` to force) |
| `evaluate.previous_best` | `true` | re-score `runs/best` on the same split, for a fair comparison as the dataset grows |
| `evaluate.songdeck_baseline`, `.serve_port`, `.songdeck_extra_args` | `true`, `0`, `[]` | run Song Deck's harness with its built-in transcriber; port for `serve` (0: a free one) |
| `promote.metric`, `.min_improvement`, `.require_parity` | `mean_class_f`, `0`, `true` | what "better" means on the separated test split (`mean_class_f` or `overall_f`) |
| `disk.reserve_gb` | `5` | render and perform refuse to start unless their estimate plus this much is free |

The disk estimate: 10.6 MB per minute per 16-bit stereo file (mix, stems, the four separated
stems before pruning), 0.25 GB per hour of audio per `train.audio` input for the feature cache,
and for each take its audio and separated drums at 1.5× that (a margin: the files are written at the song's rate, as large as the mix).

## Results so far

Toy model, default layout (89,567 parameters), trained for 30 epochs (about 20 minutes on 4
cores) on 24 minutes of synthetic drums (48 songs; 6 valid, 6 test), scored on the test split
through the binary: overall F 0.758 (P 0.866, R 0.675), mean class F 0.752; kick 0.977, snare
0.891, toms 0.867, crash 0.821, ride 0.762, closed hi-hat 0.611, open hi-hat 0.336; matched
hits within 4.6 ms on average (frames are 11.6 ms). PyTorch and the binary agree to 4e-4 on its
likelihoods and exactly on its hits. That says the pipeline works, not how the model does on
real drums: that is T2, on Song Deck's renders, E-GMD and the real test set.
