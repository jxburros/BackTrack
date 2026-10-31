# BackTrack Explode

The stem separator. Its plan is [docs/plans/explode.md](../../docs/plans/explode.md); the HTTP
API is in [docs/http-api.md](../../docs/http-api.md#backtrack-explode).

**Milestone S0 is built:** the app runs end to end on the shared core with a placeholder model.
Song Deck can use it as a separator and gets stems that sum to the mix. Its stems are rough: the
first trained model is S2 (vocals and instrumental), after S1's training data.

| File          | What it is                                                                         |
| ------------- | ---------------------------------------------------------------------------------- |
| `explode.h`   | The pipeline's interface: models, the stem plan, options and the result            |
| `explode.cpp` | Chunking with crossfades, averaging passes, residual share-out, the placeholder    |
| `main.cpp`    | The command line and the server (`POST /separate`, `GET /info`, port 8827)         |

It reuses from M2: `core/` (decoding, resampling, `encode_wav`, `Progress` for per-chunk progress
and cancellation, and the new `Stft` in `core/include/backtrack/stft.h`), `server/`'s
`BridgeServer`, the page in `ui/` (an `explode` view in the `blast-zone` theme) and the command-line
helpers in `apps/common/cli.h`, shared with Surveil.

## How a song is separated

1. Decode, then resample to 44.1 kHz stereo (mono is doubled; more channels are folded, even
   ones left and odd ones right). That is the mix the stems add up to.
2. Split into 20 s chunks that overlap by 1 s (`--chunk`; `Options::chunk_seconds` and
   `overlap_seconds`). Memory stays flat for long songs, and progress and cancellation work per
   chunk.
3. Per chunk and pass: the STFT (2048-point periodic Hann, hop 512, centred frames), then the
   model, which returns an estimate of each of its stems' spectrograms.
4. The residual, the mix minus the sum of the estimates, is shared out bin by bin by each stem's
   power there (as Song Deck's `reconcileParts` does), so the stems' spectra sum to the mix's.
5. The model's stems are added into the stems asked for (`plan_stems`: `other` or `instrumental`
   takes the rest), and the inverse STFT gives them back as audio. The STFT reconstructs exactly,
   so the stems sum to the chunk.
6. Chunks are crossfaded linearly; the two fades sum to one, so the stems sum to the whole mix.
7. Passes (`--augment` is 2; `--passes` up to 4): plain, polarity flipped and channels swapped,
   flipped only, swapped only. Each pass's transform is undone on its stems and the passes are
   averaged, which keeps the sum.

## The placeholder model

No training and no weights: soft masks from three cues, the same for both channels.

- **Percussive or harmonic:** median filters over the magnitude, across 17 frames (harmonic
  sounds are steady in time) and across 17 bins (hits are spread across frequency).
  Percussive share = P² / (H² + P²). That share is **drums**.
- **Low:** of the harmonic part, the share below about 150 Hz (a fourth-order roll-off) is
  **bass**.
- **Centred:** of the harmonic part above that, how alike the two channels are in a bin, and a
  120 Hz-8 kHz band, give **vocals**. Anything centred (a mono synth, a centred guitar) lands
  there too.
- **Other** is the rest.

On a synthetic mix (a vibrato voice, a bass line, kick and noise hits, a wide pad) it reaches SDR
of about 15 dB on the voice, 9 dB on the bass and 2 dB on the drums. Real songs are much harder:
use it to try the app and the wiring, and Demucs or a RoFormer model for real stems.

## A trained model (S2)

A trained model implements `explode::Model` (`separate()` returns per-stem spectra for a chunk)
and loads from GGUF with `audio.task = separate`, its stems and its STFT settings
([GGUF metadata](../../docs/gguf-metadata.md)); `Registry` lists it next to the placeholder.
Everything around it, chunking, passes, the share-out, the server and the page, is already here.
