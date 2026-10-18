# GGUF metadata for BackTrack audio models

BackTrack owns this contract (overall plan, "Contracts between the projects"); the apps, JX
Runtime and Song Deck's `"model"` plugin kind read it. New keys stay optional.

Weights are GGUF files: tensors in F32, metadata as below. Every model carries its own
front-end settings, so a model trained with different settings runs correctly without code
changes.

## Keys every audio model has

| Key                    | Type          | Meaning                                                                        |
| ---------------------- | ------------- | ------------------------------------------------------------------------------ |
| `general.architecture` | string        | Graph the binary builds: `surveil-cnn` (transcription). Explode adds its own. |
| `general.name`         | string        | Display name                                                                   |
| `general.version`      | string        | Model version                                                                  |
| `general.license`      | string        | Licence of the weights                                                         |
| `audio.task`           | string        | `transcribe` or `separate`                                                     |
| `audio.arch`           | string        | Same as `general.architecture`                                                 |
| `audio.stems`          | string array  | Song Deck sources (transcription) or stems produced (separation)               |
| `audio.sample_rate`    | uint32        | Rate the model runs at                                                         |
| `audio.sources`        | string array  | Licence record: every dataset or render set that went into training            |
| `audio.placeholder`    | bool          | Untrained placeholder (optional, default false)                                |

## Transcription (`surveil-cnn`)

| Key                        | Type         | Default                    | Meaning                                                   |
| -------------------------- | ------------ | -------------------------- | --------------------------------------------------------- |
| `audio.kind`               | string       | `pitched`                  | `pitched` (notes) or `drums` (hits per class)             |
| `audio.hop`                | uint32       | 256                        | Samples per frame                                         |
| `audio.fmin`               | float32      | 27.5                       | Centre frequency of the lowest pitch                      |
| `audio.bins_per_semitone`  | uint32       | 3                          | Front-end resolution                                      |
| `audio.n_bins`             | uint32       | 264                        | Front-end bins (88 pitches × 3)                           |
| `audio.harmonics`          | float32 array| 0.5, 1, 2, 3, 4, 5, 6, 7   | Harmonic channels                                         |
| `audio.top_fraction`       | float32      | 0.9                        | Bins above this fraction of Nyquist are zero              |
| `audio.filter_scale`       | float32      | 1                          | Kernel length multiplier (drums use less, for timing)     |
| `audio.pitch_min`          | uint32       | 21                         | Pitched: MIDI note of output 0                            |
| `audio.classes`            | string array | —                          | Drums: class names, e.g. `kick`, `snare`, `toms`, ...     |
| `audio.class_notes`        | int32 array  | —                          | Drums: General MIDI note per class                        |
| `audio.placeholder_init`   | string       | —                          | Placeholders: `wired` or `random`                         |

The front end these keys describe is in [front-end.md](./front-end.md).

### Tensors

Kernel shapes are ggml order, `[KW (frequency), KH (time), C_in, C_out]`; kernel sizes must be
odd (except the pool), and the graph pads to keep the frame count.

| Tensor                               | Shape                       | Kind    |
| ------------------------------------ | --------------------------- | ------- |
| `conv1.weight`, `conv1.bias`         | `[kw, kh, harmonics, C1]`, `[C1]` | both |
| `conv2.weight`, `conv2.bias`         | `[kw, kh, C1, C2]`, `[C2]`  | both    |
| `contour.weight`, `contour.bias`     | `[kw, kh, C2, 1]`, `[1]`    | pitched |
| `pool.weight`, `pool.bias`           | `[bins_per_semitone, 1, C2, C3]`, `[C3]` (stride `bins_per_semitone` in frequency) | pitched |
| `note.weight`, `note.bias`           | `[1, kh, C3, 1]`, `[1]`     | pitched |
| `onset.weight`, `onset.bias`         | `[1, kh, C3, 1]`, `[1]`     | pitched |
| `offset.weight`, `offset.bias`       | `[1, kh, C3, 1]`, `[1]`     | pitched |
| `band.weight`, `band.bias`           | `[n_bins × C2, classes]`, `[classes]` | drums |
| `onset.weight`, `onset.bias`         | `[1, kh, 1, 1]`, `[classes]`| drums   |

Activations: ReLU after `conv1`, `conv2`, `pool` and `band`; sigmoid on every head. Rows of
`band.weight` index the trunk as `channel × n_bins + bin`.

`backtrack-surveil make-placeholder -o model.gguf [--kind drums] [--init random --seed N]`
writes a file in this format; the training code's export (T1) writes the same.
