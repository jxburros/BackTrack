/**
 * The promo's soundtrack: written with Luthier, arranged, drummed and rendered by Song Deck.
 *
 *     scripts/promo/make_video.py runs this with Song Deck's tsx:
 *     SONG_DECK=../Song-Deck LUTHIER=../Luthier <Song-Deck>/node_modules/.bin/tsx scripts/promo/score.ts
 *
 * 1. Luthier writes the chord chart (E minor power chords, one a bar) with its strum patterns:
 *    eighth-note downstrokes ("Eighth pump"), palm-muted in the Breakdown, ringing in the Intro. It
 *    exports the guitar and the bass part as MIDI, the way its Export menu does.
 * 2. Song Deck composes a punk song on the same form and chords, imports Luthier's two MIDI parts in
 *    place of its own guitars and bass, and keeps its drummer.
 * 3. Song Deck renders it as stems; the stems are summed with the film's solo moves (shot 04 solos
 *    the drums, then the bass) and mastered for streaming. No AI provider, no samples.
 *
 * Writes promo/song.json (read by the scenes), promo/luthier/ (chart and MIDI) and promo/audio/.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..');
const PROMO = join(ROOT, 'promo');
const SONG_DECK = resolve(process.env.SONG_DECK ?? join(ROOT, '..', 'Song-Deck'));
const LUTHIER = resolve(process.env.LUTHIER ?? join(ROOT, '..', 'Luthier'));
const load = (base: string, rel: string) => import(pathToFileURL(join(base, rel)).href);

const SEED = Number(process.env.SCORE_SEED ?? 1977);
const TEMPO = 160; // one bar = 1.5 s, so every cut in scenes.py lands on a bar line
const BAR = 240 / TEMPO;
const SR = 48000;

// E minor in Luthier's terms: root index 4, degrees 0-based (0 i, 2 III, 3 iv, 5 VI, 6 VII).
const KEY_ROOT = 4;
const KEY_MODE = 'minor';

// Song sections, the film's cuts. Each has its own Luthier progression, one chord a bar.
const FORM = [
  { name: 'Intro', kind: 'intro', bars: 4, energy: 45, energyEnd: 70, degrees: [0, 5, 2, 6] },
  { name: 'Build', kind: 'build', bars: 4, energy: 80, energyEnd: 92, degrees: [0, 5, 2, 6] },
  { name: 'Verse 1', kind: 'verse', bars: 8, energy: 72, degrees: [0, 0, 5, 6] },
  { name: 'Pre-Chorus 1', kind: 'pre-chorus', bars: 4, energy: 76, energyEnd: 88, degrees: [5, 6, 5, 6] },
  { name: 'Chorus 1', kind: 'chorus', bars: 8, energy: 94, degrees: [2, 6, 0, 5] },
  { name: 'Verse 2', kind: 'verse', bars: 8, energy: 74, degrees: [0, 0, 5, 6] },
  { name: 'Pre-Chorus 2', kind: 'pre-chorus', bars: 4, energy: 78, energyEnd: 90, degrees: [5, 6, 5, 6] },
  { name: 'Chorus 2', kind: 'chorus', bars: 8, energy: 95, degrees: [2, 6, 0, 5] },
  { name: 'Breakdown', kind: 'breakdown', bars: 4, energy: 50, degrees: [0, 0, 3, 3] },
  { name: 'Bridge', kind: 'bridge', bars: 4, energy: 70, energyEnd: 96, degrees: [5, 5, 6, 6] },
  { name: 'Final Chorus', kind: 'final-chorus', bars: 8, energy: 98, degrees: [2, 6, 0, 5] },
  { name: 'Outro', kind: 'outro', bars: 4, energy: 92, energyEnd: 70, degrees: [5, 6, 0, 0] },
] as const;

const DRUM_EDITS: [string, string[]][] = [
  ['Double time on the drums', ['Build', 'Verse 1', 'Chorus 1', 'Verse 2', 'Chorus 2', 'Bridge', 'Final Chorus', 'Outro']],
  ['Make the drums busier', ['Pre-Chorus 1', 'Pre-Chorus 2']],
];

// Solo moves (seconds, stem groups) that shot 04 shows on Explode's M/S buttons.
const PRE1 = FORM.slice(0, 3).reduce((s, x) => s + x.bars, 0) * BAR;
const SOLOS = [
  { from: PRE1, to: PRE1 + 2 * BAR, only: 'drums' },
  { from: PRE1 + 2 * BAR, to: PRE1 + 3 * BAR, only: 'bass' },
];

async function luthier() {
  // Luthier is a Vite app (its modules read import.meta.env), so load it through Vite's SSR loader.
  const { createServer } = await load(LUTHIER, 'node_modules/vite/dist/node/index.js');
  const vite = await createServer({
    root: LUTHIER,
    configFile: false,
    logLevel: 'error',
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true },
  });
  const T = await vite.ssrLoadModule('/src/theory.js');
  const X = await vite.ssrLoadModule('/src/export.js');
  await vite.close();

  const diatonic = T.buildDiatonicChords(KEY_ROOT, KEY_MODE);
  const power = (q: string) => T.QUALITY_VARIATIONS[q].find((v: { id: string }) => v.id === 'power');
  const chordsOf = (f: (typeof FORM)[number]) =>
    Array.from({ length: f.bars }, (_, i) => {
      const def = diatonic[f.degrees[i % f.degrees.length]];
      return T.makeChordEntry(def, power(def.quality), null);
    });
  type Entry = { strums: { voicing: string }[]; bass?: { pattern: string }; rest?: boolean };
  const part = (shape: (f: (typeof FORM)[number], c: Entry) => Entry) =>
    FORM.map((f) => ({ id: T.uid(), name: f.name, lyrics: '', chords: chordsOf(f).map((c: Entry) => shape(f, c)) }));
  const pump = (c: Entry) => T.applyStrumPreset(c, 'pump', true, '4/4');

  // Guitar: the Intro rings one strum a bar, the Breakdown chugs palm-muted, everything else pumps eighths.
  const guitar = part((f, c) => {
    if (f.kind === 'intro') return c;
    const p = pump(c);
    return f.kind === 'breakdown' ? { ...p, strums: p.strums.map((s: object) => ({ ...s, voicing: 'closed' })) } : p;
  });
  // Bass: out for the Intro, root eighths, octave pops in the choruses.
  const bass = part((f, c) => {
    if (f.kind === 'intro') return { ...c, rest: true };
    const pattern = f.kind.includes('chorus') ? 'root8' : 'root';
    return { ...pump(c), bass: { pattern } };
  });

  const meta = {
    title: 'Take It Apart',
    keyLabel: T.keyLabelFor(KEY_ROOT, KEY_MODE),
    keyRootIndex: KEY_ROOT,
    keyMode: KEY_MODE,
    tempo: TEMPO,
    timeSignature: '4/4',
  };
  const opts = { keyRootIndex: KEY_ROOT, keyMode: KEY_MODE, humanize: false };
  return {
    chart: X.buildTextChart(guitar, meta) as string,
    guitarMidi: X.buildMidiFile(guitar, TEMPO, 'electric', '4/4', opts) as Uint8Array,
    bassMidi: X.buildMidiFile(bass, TEMPO, 'bass', '4/4', opts) as Uint8Array,
    // Song Deck takes the progression as roman numerals.
    romans: guitar.map((s) => FORM.find((f) => f.name === s.name)!.degrees.map((d) => diatonic[d].roman)),
    labels: guitar.map((s) => s.chords.map((c: { label: string }) => c.label)),
    keyLabel: meta.keyLabel,
  };
}

async function main() {
  const lut = await luthier();
  const C = await load(SONG_DECK, 'packages/core/src/composer/index.ts');
  const R = await load(SONG_DECK, 'packages/audio/src/dsp/renderer.ts');
  const M = await load(SONG_DECK, 'packages/audio/src/dsp/mastering.ts');
  const W = await load(SONG_DECK, 'packages/audio/src/dsp/codecs/wav.ts');
  const { midiToSong } = await load(SONG_DECK, 'packages/core/src/io/midi.ts');
  const { interpretEditInstruction } = await load(SONG_DECK, 'packages/core/src/musician/edit-interpreter.ts');
  const { applyOperations } = await load(SONG_DECK, 'packages/core/src/edit/apply.ts');

  const genreBlend = [{ genreId: 'punk', weight: 1 }];
  const base = C.defaultBlueprint({ genreBlend, seed: SEED });
  const bp = C.defaultBlueprint({
    title: 'Take It Apart',
    prompt: 'Fast garage punk in E minor: two distorted guitars, bass and drums, no vocals.',
    genreBlend,
    tempo: TEMPO,
    key: { tonic: KEY_ROOT, mode: KEY_MODE },
    moods: ['Raw', 'Urgent', 'Anthemic chorus'],
    structure: FORM.map((f, i) => ({
      name: f.name,
      kind: f.kind,
      bars: f.bars,
      energy: f.energy,
      ...('energyEnd' in f ? { energyEnd: f.energyEnd } : {}),
      harmony: lut.romans[i],
    })),
    instrumentation: base.instrumentation.filter((t: { role: string }) => t.role !== 'vocal'),
    vocal: null,
    seed: SEED,
  });
  let song = C.composeSong(bp, undefined, { seed: SEED });

  // Luthier's parts replace Song Deck's guitars and bass. The right guitar doubles the left a few
  // ticks late, the way a punk record double-tracks one riff.
  const imported = (bytes: Uint8Array) => midiToSong(bytes).tracks.flatMap((t: { notes: object[] }) => t.notes);
  const guitarNotes = imported(lut.guitarMidi);
  const bassNotes = imported(lut.bassMidi);
  song = {
    ...song,
    tracks: song.tracks.map((t: { role: string; name: string }) => {
      if (t.role === 'bass') return { ...t, notes: bassNotes };
      if (t.role !== 'rhythm-guitar') return t;
      const late = t.name.endsWith('R') ? 6 : 0;
      return { ...t, notes: guitarNotes.map((n: { tick: number }) => ({ ...n, tick: n.tick + late })) };
    }),
  };

  // The drummer's own pattern is a backbeat; the plain-language edits from Song Deck's Change panel
  // turn it into a skate beat and push the pre-choruses.
  const drums = song.tracks.filter((t: { role: string }) => t.role === 'drums').map((t: { id: string }) => t.id);
  const edits: { instruction: string; sections: string[]; explanation: string }[] = [];
  for (const [instruction, names] of DRUM_EDITS) {
    const sectionIds = song.sections.filter((s: { name: string }) => names.includes(s.name)).map((s: { id: string }) => s.id);
    const e = interpretEditInstruction(song, instruction, { trackIds: drums, sectionIds }, { seed: SEED });
    if (!e.operations.length) throw new Error(`edit not understood: ${e.explanation}`);
    song = applyOperations(song, e.operations).song;
    edits.push({ instruction, sections: names, explanation: e.explanation });
  }

  const stems: Record<string, { channels: Float32Array[] }> = R.renderStems(song, {
    sampleRate: SR,
    tailSeconds: 3,
    by: 'stemGroup',
  });
  const names = Object.keys(stems);
  const frames = Math.max(...names.map((n) => stems[n].channels[0].length));
  mkdirSync(join(PROMO, 'audio', 'stems'), { recursive: true });
  for (const n of names)
    writeFileSync(join(PROMO, 'audio', 'stems', `${n}.wav`), W.encodeWav({ sampleRate: SR, ...stems[n] }, { bitDepth: 16 }));

  // Sum the stems, following the solo moves with 10 ms ramps.
  const mix = [new Float32Array(frames), new Float32Array(frames)];
  const ramp = Math.round(0.01 * SR);
  for (const n of names) {
    const g = new Float32Array(frames).fill(1);
    for (const s of SOLOS) {
      if (s.only === n) continue;
      const a = Math.round(s.from * SR),
        b = Math.round(s.to * SR);
      for (let i = a - ramp; i < b + ramp; i++) {
        if (i < 0 || i >= frames) continue;
        const edge = Math.min(i - (a - ramp), b + ramp - i) / ramp;
        g[i] = Math.min(g[i], Math.max(0, 1 - edge));
      }
    }
    stems[n].channels.forEach((ch, c) => {
      for (let i = 0; i < ch.length; i++) mix[c][i] += ch[i] * g[i];
    });
  }
  const mastered = M.masterAudio(
    { sampleRate: SR, channels: mix },
    { ...song.mastering, method: 'builtin', target: 'streaming' },
  ).output;
  writeFileSync(join(PROMO, 'audio', 'music.wav'), W.encodeWav(mastered, { bitDepth: 16 }));

  mkdirSync(join(PROMO, 'luthier'), { recursive: true });
  writeFileSync(join(PROMO, 'luthier', 'chords.txt'), lut.chart.endsWith('\n') ? lut.chart : lut.chart + '\n');
  writeFileSync(join(PROMO, 'luthier', 'guitar.mid'), lut.guitarMidi);
  writeFileSync(join(PROMO, 'luthier', 'bass.mid'), lut.bassMidi);

  const seconds = (tick: number) => (tick / song.ppq) * (60 / TEMPO);
  let at = 0;
  const data = {
    title: bp.title,
    seed: SEED,
    tempo: TEMPO,
    key: lut.keyLabel,
    prompt: bp.prompt,
    edits,
    solos: SOLOS,
    stems: names,
    sections: FORM.map((f, i) => {
      const out = { name: f.name, kind: f.kind, start: at, bars: f.bars, chords: lut.labels[i], romans: lut.romans[i] };
      at += f.bars * BAR;
      return out;
    }),
    tracks: song.tracks.map((t: { name: string; role: string; stemGroup?: string; notes: { tick: number; duration: number; pitch: number; velocity: number }[] }) => ({
      name: t.name,
      role: t.role,
      stem: t.stemGroup,
      notes: t.notes.map((n) => [+seconds(n.tick).toFixed(4), +seconds(n.duration).toFixed(4), n.pitch, n.velocity]),
    })),
  };
  writeFileSync(join(PROMO, 'song.json'), JSON.stringify(data) + '\n');
  console.log(
    `"${bp.title}": ${(mastered.channels[0].length / SR).toFixed(1)} s, stems ${names.join(', ')};`,
    `tracks ${song.tracks.map((t: { name: string }) => t.name).join(', ')}`,
  );
}

main();
