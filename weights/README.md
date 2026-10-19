# Released models

Model files the release workflow attaches to each GitHub Release as separate downloads (with
their SHA-256 in `checksums.txt`). Each file carries its own training record in its GGUF metadata
(`audio.sources`, `audio.training`); read it with `python3 -m training.export --check FILE`.

## `surveil-drums-0.1.0.gguf`

Surveil's first trained model: drum hits in seven classes (kick, snare, toms, closed and open
hi-hat, crash, ride). 89,567 parameters, 360 KB, MIT.

- **Training data:** Song Deck's training-data export (`npm run dataset:render`, Song Deck 0.16.0),
  400 songs of 60 s with `--seed 1000 --only-stems drums --separate`, of which 357 have drums:
  308 train, 47 valid, 45 test songs. Inputs: the clean drum stem and the drums the built-in
  separator took out of the mix (`--audio stem,separated`), 9.4 h of training audio. Kits:
  FreePats MuldjordKit (recorded) in 290 songs, Song Deck's synthesized acoustic kit in 68 and
  electronic kit in 67. No ACE-Step audio.
- **Training:** `python3 -m training.train_drums --data sd-drums-v1 --audio stem,separated
  --out weights/surveil-drums-0.1.0.gguf --epochs 20`, on 4 CPU cores (about 5 minutes per
  epoch); best validation epoch 15 (mean class F 0.824).
- **Parity:** PyTorch and `backtrack-surveil` agree within 3.3e-4 on likelihoods, with identical
  picked hits.

### How it scores

Song Deck's `npm run eval:datasets -- --dataset songdeck --input separated --split test`, on the
38 held-out test songs' drums separated from the mix (what Song Deck sends in use), onsets within
±50 ms per class:

| Transcriber              | F     | Precision | Recall | Kick | Snare | Toms | Closed hat | Open hat | Crash | Ride |
| ------------------------ | ----- | --------- | ------ | ---- | ----- | ---- | ---------- | -------- | ----- | ---- |
| Song Deck built-in       | 0.495 | 0.494     | 0.496  | 0.77 | 0.53  | 0.12 | 0.49       | 0.37     | 0.19  | 0.00 |
| **Surveil drums 0.1.0**  | 0.813 | 0.841     | 0.787  | 0.98 | 0.89  | 0.79 | 0.72       | 0.69     | 0.84  | 0.65 |

What this does not show yet: real recordings. The test songs come from the same renderer and kits
as the training songs, so they flatter the model. The plan's real test set (hand-corrected real
drums) and E-GMD are still to be scored (`--dataset egmd`); ACE-Step performances
(`docs/training-at-home.md`) are the next step towards realistic audio.
