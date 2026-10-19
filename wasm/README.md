# WebAssembly

Milestone **T4**: Surveil built with Emscripten from the same `core/` and `apps/surveil/` code as
the binary, with a small JS wrapper. Song Deck runs it in its job worker, so drums transcribe in
the page with nothing to install.

| File               | What it is                                                                 |
| ------------------ | -------------------------------------------------------------------------- |
| `surveil_wasm.cpp` | The C interface: load a model from bytes, transcribe mono samples to JSON  |
| `surveil.mjs`      | The JS wrapper (`createSurveil()`), copied next to the build output        |
| `test.mjs`         | Checks the build against the native binary (CI's `wasm` job)               |

## Build

```bash
npm ci                                            # the pinned ggml
bash scripts/install-emsdk.sh ~/emsdk             # the pinned Emscripten SDK
source ~/emsdk/emsdk_env.sh
emcmake cmake -B build-wasm -DCMAKE_BUILD_TYPE=Release && cmake --build build-wasm -j
```

`build-wasm/` then holds `surveil.mjs`, `surveil-core.mjs` and `surveil-core.wasm` (about
1.1 MB, 0.4 MB gzipped). Each release attaches them as `backtrack-surveil-wasm-<tag>.zip`.

Check it against the native build:

```bash
python3 scripts/make-test-audio.py test-audio
node wasm/test.mjs build-wasm test-audio build/backtrack-surveil
```

## Use

```js
import { createSurveil } from './surveil.mjs';

const surveil = await createSurveil();   // or { wasmBinary } or { locateFile } to find the .wasm
const model = surveil.loadModel(ggufBytes, 'surveil-drums-0.1.0');   // model.info: the GGUF metadata
const result = surveil.transcribe(model, monoFloat32, 44100, { source: 'drums', context: { tempo: 120 } }, {
  onProgress: (stage, fraction) => false,   // return true to cancel
});
// result: Song Deck's TranscriptionBridgeResponse ({notes, model, tempo?, key?}) plus kind and seconds
```

The request takes the transcription contract's fields other than the audio (`source`,
`pitch_bends`, `context`) and the note-picking thresholds. Errors throw `SurveilError`; a cancel
throws one named `AbortError`. `loadPlaceholder('pitched' | 'drums')` gives the built-in
placeholder models, for tests.

## Design

- **Single-threaded, no pthreads,** so the page needs no cross-origin isolation. Calls are
  synchronous: run them in a worker. A 4-minute song takes about 22 s with the drum model, about
  1.4× the native binary on one thread (the model is most of it). For more speed, a caller can
  split a song across workers.
- **No file system:** models load from bytes (`GgufFile::open_memory`), audio arrives as mono
  samples at any rate and is resampled inside.
- **Wasm SIMD and native exceptions** (`-msimd128`, `-fwasm-exceptions`): every current browser
  and Node 20 have both.
- Progress and cancellation use the same `Progress` as the CLI and server: the JS hook is called
  between chunks, and returning `true` stops the job at the next chunk.
