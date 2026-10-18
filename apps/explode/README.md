# BackTrack Explode

The stem separator. Its plan is [docs/plans/explode.md](../../docs/plans/explode.md).

Nothing is built here yet: Explode's first milestone, **S0** (chunked processing, server, CLI
and page with a placeholder model), starts now that M2 is done. It reuses what M2 built:

- `core/`: decoding (`decode_audio`), resampling, `encode_wav` (16/24-bit and float stem
  output), the FFT, `Progress` for per-chunk progress and cancellation, GGUF loading and the
  audio-model metadata (`read_audio_model_info`), and device selection.
- `server/`: `BridgeServer` with Song Deck's bridge rules; Explode adds a `job("/separate", ...)`
  handler and `/info` with `models`, `stems` and `on_request_stems`, on port 8827.
- `ui/index.html`: the page renders by `/info`'s `app` field; an `explode` view goes next to
  the `surveil` one.

Still to write for S0: the STFT/iSTFT, overlap-add chunking with crossfades, the residual
share-out that makes stems sum to the mix (as Song Deck's `reconcileParts` does), averaging
passes, and a placeholder mask model.
