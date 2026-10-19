// BackTrack Surveil in WebAssembly: the JS wrapper over surveil-core.mjs and
// surveil-core.wasm (built by CMake with Emscripten; see wasm/README.md).
//
//   import { createSurveil } from './surveil.mjs';
//   const surveil = await createSurveil();            // or { wasmBinary } / { locateFile }
//   const model = surveil.loadModel(ggufBytes, 'surveil-drums-0.1.0');
//   const result = surveil.transcribe(model, mono, 44100, { source: 'drums' }, {
//     onProgress: (stage, fraction) => {},             // return true to cancel
//   });
//
// Calls are synchronous and run on the calling thread: run them in a worker.
// The result is Song Deck's TranscriptionBridgeResponse ({notes, model, tempo?,
// key?}) plus "kind" and "seconds".

import createSurveilCore from './surveil-core.mjs';

export class SurveilError extends Error {
  constructor(message, cancelled = false) {
    super(message);
    this.name = cancelled ? 'AbortError' : 'SurveilError';
    this.cancelled = cancelled;
  }
}

/**
 * @param {object} [options] Passed to the Emscripten module: `wasmBinary`
 *   (an ArrayBuffer of surveil-core.wasm) or `locateFile(name)` to find it.
 */
export async function createSurveil(options = {}) {
  const core = await createSurveilCore({ ...options });
  let onProgress = null;
  core.surveilProgress = (stage, fraction) => (onProgress ? Boolean(onProgress(stage, fraction)) : false);

  const lastError = () => core.UTF8ToString(core._surveil_last_error());
  const fail = () => {
    const message = lastError() || 'Surveil failed';
    throw new SurveilError(message === 'cancelled' ? 'Transcription cancelled' : message, message === 'cancelled');
  };
  const withString = (text, fn) => {
    const size = core.lengthBytesUTF8(text) + 1;
    const ptr = core._malloc(size);
    try {
      core.stringToUTF8(text, ptr, size);
      return fn(ptr);
    } finally {
      core._free(ptr);
    }
  };
  const info = (handle) => {
    const ptr = core._surveil_model_info(handle);
    if (!ptr) fail();
    return JSON.parse(core.UTF8ToString(ptr));
  };
  const opened = (handle) => {
    if (!handle) fail();
    return { handle, info: info(handle) };
  };

  return {
    version: core.UTF8ToString(core._surveil_version()),

    /** Loads a Surveil GGUF (Uint8Array or ArrayBuffer). Returns {handle, info}. */
    loadModel(bytes, id = 'model') {
      const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      const ptr = core._malloc(data.length);
      try {
        core.HEAPU8.set(data, ptr);
        return opened(withString(String(id), (idPtr) => core._surveil_load(ptr, data.length, idPtr)));
      } finally {
        core._free(ptr);
      }
    },

    /** A placeholder model ('pitched' or 'drums') with hand-set weights, for tests. */
    loadPlaceholder(kind = 'pitched') {
      return opened(withString(kind, (k) => core._surveil_load_placeholder(k)));
    },

    unloadModel(model) {
      core._surveil_unload(model.handle);
    },

    /**
     * Transcribes mono samples (Float32Array) at sampleRate. `request` holds the
     * transcription request's fields other than the audio: source, pitch_bends,
     * context, onset_threshold, frame_threshold.
     */
    transcribe(model, samples, sampleRate, request = {}, { onProgress: progress } = {}) {
      if (!(samples instanceof Float32Array)) samples = Float32Array.from(samples);
      const ptr = core._malloc(samples.length * 4);
      onProgress = progress ?? null;
      try {
        core.HEAPF32.set(samples, ptr >>> 2);
        const out = withString(JSON.stringify(request), (req) =>
          core._surveil_transcribe(model.handle, ptr, samples.length, sampleRate, req),
        );
        if (!out) fail();
        return JSON.parse(core.UTF8ToString(out));
      } finally {
        onProgress = null;
        core._free(ptr);
      }
    },
  };
}
