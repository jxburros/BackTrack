// Checks the WebAssembly build against the native binary on the synthetic
// clips from scripts/make-test-audio.py: the same notes from the same model,
// plus cancellation and bad input.
//
//   node wasm/test.mjs BUILD_WASM_DIR TEST_AUDIO_DIR [NATIVE_BINARY]
//
// Without NATIVE_BINARY only the WebAssembly side runs (no comparison).
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [buildDir, audioDir, native] = process.argv.slice(2);
if (!buildDir || !audioDir) {
  console.error('usage: node wasm/test.mjs BUILD_WASM_DIR TEST_AUDIO_DIR [NATIVE_BINARY]');
  process.exit(2);
}
const { createSurveil } = await import(pathToFileURL(resolve(buildDir, 'surveil.mjs')).href);

// 16-bit PCM WAV to mono float samples (all make-test-audio.py writes).
function readWav(path) {
  const b = readFileSync(path);
  let off = 12, rate = 0, channels = 0, data = null;
  while (off + 8 <= b.length) {
    const id = b.toString('ascii', off, off + 4), size = b.readUInt32LE(off + 4);
    if (id === 'fmt ') {
      channels = b.readUInt16LE(off + 10);
      rate = b.readUInt32LE(off + 12);
    } else if (id === 'data') data = b.subarray(off + 8, off + 8 + size);
    off += 8 + size + (size & 1);
  }
  const frames = data.length / 2 / channels, mono = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let s = 0;
    for (let c = 0; c < channels; c++) s += data.readInt16LE((i * channels + c) * 2);
    mono[i] = s / channels / 32768;
  }
  return { rate, mono };
}

let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`);
  if (!ok) failed++;
};

const surveil = await createSurveil();
check(/^\d+\.\d+\.\d+/.test(surveil.version), `version ${surveil.version}`);

const models = [
  { label: 'placeholder', model: surveil.loadPlaceholder('pitched'), clip: 'melody.wav', source: 'melody', arg: 'placeholder' },
  { label: 'placeholder-drums', model: surveil.loadPlaceholder('drums'), clip: 'drums.wav', source: 'drums', arg: 'placeholder-drums' },
];
const weights = resolve(new URL('..', import.meta.url).pathname, 'weights');
for (const f of readdirSync(weights).filter((f) => f.endsWith('.gguf'))) {
  const id = f.replace(/\.gguf$/, '');
  const model = surveil.loadModel(readFileSync(join(weights, f)), id);
  check(model.info.id === id && model.info.stems.length > 0, `${id} loads (${model.info.kind}, ${model.info.stems.join(', ')})`);
  const source = model.info.stems[0];
  const clip = source === 'drums' || source === 'bass' ? `${source}.wav` : 'lead.wav';
  models.push({ label: id, model, clip, source, arg: join(weights, f) });
}

for (const m of models) {
  const { rate, mono } = readWav(join(audioDir, m.clip));
  const stages = new Set();
  const out = surveil.transcribe(m.model, mono, rate, { source: m.source }, { onProgress: (s) => void stages.add(s) });
  check(out.notes.length > 0 && stages.has('model') && stages.has('done'), `${m.label}: ${out.notes.length} notes from ${m.clip}`);
  if (native) {
    const json = execFileSync(native, [join(audioDir, m.clip), '--stem', m.source, '--model', m.arg, '--json', '-', '-o', process.platform === 'win32' ? 'NUL' : '/dev/null', '--quiet'], { stdio: ['ignore', 'pipe', 'ignore'] });
    const ref = JSON.parse(json.toString());
    const same =
      ref.notes.length === out.notes.length &&
      ref.notes.every((n, i) => n.pitch === out.notes[i].pitch && Math.abs(n.start - out.notes[i].start) < 2e-3 && Math.abs(n.end - out.notes[i].end) < 2e-3);
    check(same, `${m.label}: same notes as the native binary (${ref.notes.length})`);
  }
}

const { rate, mono } = readWav(join(audioDir, 'drums.wav'));
try {
  surveil.transcribe(models[1].model, mono, rate, { source: 'drums' }, { onProgress: (_s, f) => f > 0.1 });
  check(false, 'cancel from the progress hook');
} catch (e) {
  check(e.name === 'AbortError', 'cancel from the progress hook');
}
try {
  surveil.transcribe(models[1].model, mono, rate, { source: 'kazoo' });
  check(false, 'unknown source is refused');
} catch (e) {
  check(e.name === 'SurveilError' && /kazoo/.test(e.message), 'unknown source is refused');
}
try {
  surveil.loadModel(new Uint8Array([1, 2, 3, 4]), 'junk');
  check(false, 'junk GGUF is refused');
} catch (e) {
  check(e.name === 'SurveilError', 'junk GGUF is refused');
}
// The model still works after the errors.
check(surveil.transcribe(models[1].model, mono, rate, { source: 'drums' }).notes.length > 0, 'still works after errors');

console.log(failed ? `${failed} failed` : 'all passed');
process.exit(failed ? 1 : 0);
