# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.0.1] - Unreleased

### Changed

- **The page follows the design language** (`design/AGENT-SPEC.md`). Surveil opens in the
  `wiretap` theme: CCTV screen with scanlines, REC and a running CAM timestamp while
  transcribing, a torn drop zone, a bar waveform of the dropped file with a play button, a
  progress bar with the stage, percentage and seconds, a case file with redacted
  unknowns, and a piano roll with 25 px lanes, beat and bar lines from the tempo, and confidence
  shown as brightness (arrow keys step through notes and read out their numbers). Plain DONE and
  FAILED states with the cause and the fix, toasts, and a light mode (`xerox-riot`, remembered as
  `bt-theme`). The fonts (Anton, Space Mono, Permanent Marker, Big Shoulders Stencil Display,
  VT323) are bundled, so the page loads nothing from the network.
- **The server serves the page's files.** `ServerOptions::page_html` is replaced by `page` (every
  file in `ui/`, embedded by `cmake/EmbedPage.cmake`) and `theme`; the page's files need no API key.

Milestone M2: the repository, shared core, server and page, with BackTrack Surveil running a
placeholder model end to end.

### Added

- **Build.** CMake on standalone ggml `36da571` (the revision Onyx Engine's llama.cpp pin last
  synced), vendored with Onyx Engine's scripts and a `packaging/vendor/ggml.conf` spec; GPU
  switches (`BACKTRACK_CUDA`, `_VULKAN`, `_METAL`, `_HIP`, `_BLAS`) as in Onyx Engine.
- **`core/`.** WAV, FLAC and MP3 decoding from memory (dr_libs), band-limited resampling, WAV
  writing, an FFT, the harmonic constant-Q front end (documented step by step in
  `docs/front-end.md`), key and chord parsing, Song Deck's tempo and key estimators, a Standard
  MIDI File writer with pitch bends, GGUF and device helpers, and progress and cancellation.
- **`server/`.** Song Deck's bridge rules in C++: loopback by default, DNS-rebinding
  protection, optional bearer key, CORS for the Song Deck origins, JSON errors, one job at a
  time with a queue, cancellation on disconnect and `POST /cancel`, progress in `/health`.
- **`ui/`.** The page the binaries serve: drop a file, choose the stem and context, watch
  progress, preview a piano roll in time with the audio, download MIDI or JSON.
- **BackTrack Surveil** (`backtrack-surveil`): CLI, `serve` (port 8826) with `POST /transcribe`,
  `GET /info`, `GET /health`, `POST /cancel`; the `surveil-cnn` graph on ggml with GGUF weights;
  built-in placeholder models (pitched and drums) and `make-placeholder` to write them;
  note picking with onsets, a best-path search for monophonic stems, pitch bends, drum hits
  and song-context priors (key, chords, instrument range, tempo grid).
- **Contract proposal.** An optional `context` field on `POST /transcribe` (key, tempo, chords,
  program), described in `docs/http-api.md` for Song Deck to adopt.
- Unit tests (`tests/core_tests.cpp`) and an end-to-end smoke test (`scripts/smoke-test.py`).
- Plans moved here from Song Deck as `docs/plans/`, with the app names decided.
