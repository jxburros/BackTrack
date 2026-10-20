# Training data: what to train on, what it may be used for, and how

Every source of training data researched for BackTrack's models (Surveil's drum, bass and melody
transcribers, Explode's separator, and later the theory models), with its licence, its size, what
it helps, and how to use it here. The licences were checked on 2026-10-20 against the source named
in each row (the dataset's own page, its Zenodo record, its LICENSE file or EULA); quotes are
verbatim. Check again before shipping weights trained on a source: licences change. This is a
record of what was found, not legal advice.

**The rule for BackTrack:** model weights are released under MIT and may be used commercially, so
training data must allow training **and** releasing the trained model commercially. Non-commercial
(NC), research-only, no-derivatives (ND) and "no AI training" sources are out. Share-alike (SA)
sources are kept out too: whether share-alike reaches trained weights is unsettled, and the
weights' licence should stay clean. Non-commercial sets can still serve as private benchmarks,
never as training data.

Contents:

1. [How to use it](#1-how-to-use-it)
2. [Free datasets](#2-free-datasets)
3. [Instruments for Song Deck's renders](#3-instruments-for-song-decks-renders)
4. [Paid and licensable data](#4-paid-and-licensable-data)
5. [Sources that are not allowed](#5-sources-that-are-not-allowed)
6. [Worse recordings: noise and degradation](#6-worse-recordings-noise-and-degradation)
7. [Making the training songs faster](#7-making-the-training-songs-faster)
8. [Attribution](#8-attribution)

## 1. How to use it

### Libraries: outside datasets in the pipeline

`training/libraries.py` holds the catalogue below (`list`, `show`), downloads a library (resumable,
checked against Zenodo's checksums) and imports it as a **Song Deck-format dataset**: a
`manifest.json` whose songs have stems with their notes, like Song Deck's own renders. The
training scripts then read it with `--library DIR`, so nothing else in training changes: splits,
the feature cache, the licence record in the model's `audio.sources`.

```bash
python3 -m training.libraries list                       # what may be trained on (--all: everything checked)
python3 -m training.libraries list --use bass
python3 -m training.libraries show slakh2100             # licence, quote, URL, size, caveats
python3 -m training.libraries add babyslakh --dir ~/backtrack-training/libraries          # 1 GB: try it first
python3 -m training.libraries add slakh2100 --dir ~/backtrack-training/libraries --yes    # 105 GB
python3 -m training.libraries credits ~/backtrack-training/libraries/*                    # attribution lines
```

`add` is `fetch` (into `<dir>/downloads/<id>`) then `import` (into `<dir>/<id>`). WAV audio is
used where it is; FLAC is converted once to 22.05 kHz mono WAV (the rate the models work at), with
ffmpeg or, without it, `pip install soundfile`. `--limit N` imports about N songs.

Importers exist for **Slakh2100**, **BabySlakh**, **E-GMD** and the **Groove MIDI Dataset**. The
other trainable entries can be downloaded with `fetch` and need an importer for their labels
before training can read them (GuitarSet's JAMS, AAM's annotation files, the choral sets' f0 and
note files); they are the next ones to write, in the order of section 2's top picks.

In the pipeline config:

```json
"libraries": { "dir": "~/backtrack-training/libraries", "use": ["egmd", "slakh2100"], "audio": "stem,mix", "fetch": true }
```

The `libraries` step (between `perform` and `train`) downloads and imports each missing one, and
`train` reads them with `--library`. `audio` is what the model hears from them: `stem` (the
instrument alone) and `mix` (the whole song, the harder case). Each kind of model takes what it
can use: the drum model reads the drum stems, the bass model the bass stems, the melody model the
stems marked melody (Slakh's synth lead, reed, pipe and brass). Adding or removing a library
changes the training fingerprint, so the next run trains again. In the desktop app the same is
the **Also train on** boxes in the TRAIN tab.

By hand: `--library DIR` (repeatable) and `--library-audio stem,mix` on `train_drums`,
`train_pitched` and `evaluate`, next to `--data`.

### Several models one after another

```bash
python3 -m training.queue --config ~/backtrack-training/pipeline.json \
    --config ~/backtrack-training/pipeline-bass.json --config ~/backtrack-training/pipeline-melody.json \
    --start-at 23:30
python3 -m training.queue --resume --status ~/backtrack-training/queue-status.json
```

Each config is one pipeline run, in order; a failed run doesn't stop the others. `--start-at`
waits for that local time first. Stopping (Ctrl-C, or STOP in the app) marks the run in progress
interrupted, and `--resume` continues it. In the desktop app: **One after another** in the TRAIN
tab, with the models ticked and an optional **Start at** time.

## 2. Free datasets

**Verdicts:** *OK* training and commercial release of the weights allowed; *OK-attr* the same,
crediting the source (CC BY, MIT); *caveat* allowed by its licence, with a provenance question
noted; *SA* share-alike, kept out; *eval* non-commercial or research only; *no* not usable.
"Import" says whether `training.libraries` can import it today.

### Top picks

| For | Start with | Then |
| --- | --- | --- |
| Drums | **E-GMD** (444 h of real drumming, 43 kits; import) | Groove (import), Slakh's drum stems (import), WaivOps POP-ROK, StemGMD |
| Bass, melody | **Slakh2100** (bass and lead stems with MIDI; import) | AAM, CocoChorales, JazzSAMBA (real recordings), GuitarSet |
| Vocal melody | Choral Singing, ESMUC Choir, Cantoría (the only CC BY sets with note labels; all choral) | VocalSet, MULTIVOX (no labels: label them with an f0 tracker); paid sources (section 4) |
| Separation | Slakh2100, AAM, CocoChorales (synthetic stems) | AlbumDB and JazzSAMBA (real stems), CC BY vocals mixed over backing stems |

**The gaps:** no free set with a commercial-safe licence has pop vocals with note labels, or
large amounts of real (not rendered) four-stem multitracks. Those are what the paid sources in
section 4 are for.

### Drums

| Dataset | What it gives | Licence (quote) | Source | Verdict | Import |
| --- | --- | --- | --- | --- | --- |
| E-GMD | 444.5 h of human drumming re-recorded through 43 kits, aligned MIDI with velocity; 90 GB | "The dataset is made available by Google LLC under a Creative Commons Attribution 4.0 International (CC BY 4.0) License." | [magenta](https://magenta.tensorflow.org/datasets/e-gmd) | OK-attr | yes |
| Groove MIDI (GMD) | 13.6 h, 10 drummers, MIDI and audio; 4.8 GB | the same Google CC BY 4.0 sentence | [magenta](https://magenta.tensorflow.org/datasets/groove) | OK-attr | yes |
| StemGMD | GMD rendered as isolated per-piece drum stems on 10 kits; 1.1 TB | "The dataset is made available under a Creative Commons Attribution 4.0 International (CC BY 4.0) License." | [zenodo 7860223](https://zenodo.org/records/7860223) | OK-attr | no |
| WaivOps POP-ROK | 5,378 loops (about 24 h), about 30 acoustic kits, drum-note labels, no toms | "The POP-ROK dataset is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0)." | [zenodo 14038284](https://zenodo.org/records/14038284) | OK-attr | no |
| WaivOps EDM-TR9 | 3,780 TR-909-style loops (about 8 h), BPM labels | "The EDM-TR9 dataset is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0)." | [zenodo 10278066](https://zenodo.org/records/10278066) | OK-attr | no |
| STAR Drums | Real non-drum stems with re-synthesized drums, 18 classes | Zenodo: BSD-3-Clause, but its mixes are built from MUSDB18 items whose licences "do not permit direct distribution" | [zenodo 15690078](https://zenodo.org/records/15690078) | check each item | no |
| ADTOF | About 359 h of rhythm-game annotations | "offered free of charge for internal non-commercial use. No reverse engineering. Do not redistribute, modify..." | [zenodo 10084511](https://zenodo.org/records/10084511) | no | — |
| ENST-Drums | 3 drummers, 8-channel | "solely for scientific purposes"; "not to make any commercial use" | [licence PDF](https://perso.telecom-paristech.fr/grichard/ENST-drums/) | eval | — |
| IDMT-SMT-Drums (and -Bass, -Guitar) | Loops with onsets | Zenodo: CC BY-NC-ND 4.0 | [zenodo 7544164](https://zenodo.org/records/7544164) | no | — |
| MDB Drums | 23 MedleyDB songs | "published under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License" | [GitHub](https://github.com/CarlSouthall/MDBDrums) | eval | — |

### Bass, melody and multi-instrument (with MIDI)

| Dataset | What it gives | Licence (quote) | Source | Verdict | Import |
| --- | --- | --- | --- | --- | --- |
| Slakh2100 | 145 h: 2,100 Lakh MIDI songs rendered per instrument, stems, mix and MIDI per stem, train/validation/test; 105 GB | Zenodo: CC BY 4.0 | [zenodo 4599666](https://zenodo.org/records/4599666) | caveat | yes |
| BabySlakh | The first 20 Slakh songs at 16 kHz; 0.9 GB (a quick test) | Zenodo: CC BY 4.0 | [zenodo 4603870](https://zenodo.org/records/4603870) | caveat | yes |
| AAM | 3,000 generated songs played with real instrument samples: stems, mixes, MIDI, onsets, beats, chords, melody instrument; 190 GB | Zenodo: CC BY 4.0 | [zenodo 5794629](https://zenodo.org/records/5794629) | OK-attr | no |
| CocoChorales | More than 1,400 h of chorales (MIDI-DDSP), stems and MIDI | "is available under the Creative Commons Attribution 4.0 International (CC-BY 4.0)." | [magenta](https://magenta.tensorflow.org/datasets/cocochorales) | OK-attr | no |
| NSynth | 305,979 single notes | "made available by Google Inc. under a Creative Commons Attribution 4.0 International (CC BY 4.0) license." | [magenta](https://magenta.tensorflow.org/datasets/nsynth) | OK-attr | no |
| GuitarSet | About 3 h of acoustic guitar, hexaphonic, note and f0 labels | Zenodo: CC BY 4.0 | [zenodo 3371780](https://zenodo.org/records/3371780) | OK-attr | no |
| Guitar-TECHS | Electric guitar: techniques, excerpts, chords, scales | Zenodo: CC BY 4.0 | [zenodo 14963133](https://zenodo.org/records/14963133) | OK-attr | no |
| EGDB-PG / -NDSP | Electric guitar through amplifiers, with transcriptions | Zenodo: CC BY 4.0 | [zenodo 19789500](https://zenodo.org/records/19789500) | OK-attr | no |
| JazzSAMBA | 76 jazz standards by 8 musicians: **real** stems (drums, bass, piano, horns), mixes, MIDI, chords | Bundled MIT-style LICENSE: "Permission is hereby granted, free of charge... to use, copy, modify, merge, publish, and distribute" with the notice kept | [zenodo 22943963](https://zenodo.org/records/22943963) | caveat | no |
| MusicNet | 34 h of classical recordings with note labels | Zenodo: CC BY 4.0; "consist of Creative Commons licensed and Public Domain performances" | [zenodo 5120004](https://zenodo.org/records/5120004) | caveat | no |
| ChoraleBricks | Wind and brass SATB multitracks with f0 and notes | Zenodo: CC BY 4.0 | [zenodo 20849469](https://zenodo.org/records/20849469) | OK-attr | no |
| Lakh MIDI | 176,581 MIDI files, no audio (render them through Song Deck) | "distributed with a CC-BY 4.0 license"; "scraped from publicly-available sources on the internet" | [colinraffel.com](https://colinraffel.com/projects/lmd/) | caveat | no |
| POP909 | 909 pop songs: vocal melody, lead and piano MIDI, beats, chords; no audio | "MIT License Copyright (c) 2020 Music X Lab" | [GitHub](https://github.com/music-x-lab/POP909-Dataset) | caveat | no |
| GiantMIDI-Piano | Piano MIDI transcribed from YouTube (no audio) | "## License CC BY 4.0" | [GitHub](https://github.com/bytedance/GiantMIDI-Piano) | OK-attr (MIDI only) | no |
| MAESTRO | About 199 h of piano with MIDI | "Creative Commons Attribution Non-Commercial Share-Alike 4.0 (CC BY-NC-SA 4.0)" | [magenta](https://magenta.tensorflow.org/datasets/maestro) | eval | — |
| MAPS, PiJAMA, GAPS | Piano, jazz piano, guitar | CC BY-NC-SA 2.0 FR; CC BY-NC 4.0; CC BY-NC-SA 4.0 | Zenodo 18160555, 8354955, 17152440 | eval | — |
| URMP, Bach10 | Chamber music with notes | No licence stated (URMP); non-commercial (the Bach10 derivative) | | eval | — |

**Caveats:** *Slakh* was rendered with Native Instruments Kontakt instruments (its metadata names
the `.nkm` patches). Native Instruments' current EULA bars using its content "for purposes of data
mining ... and/or training artificial intelligence technologies"; Slakh's own licence, from its
authors in 2019, is CC BY 4.0. That is a provenance question rather than a licence conflict for
a user of Slakh, but worth knowing; Song Deck's renders and E-GMD carry no such question. *Lakh
MIDI* (and so Slakh's notes) and *POP909* are transcriptions of other people's songs. *JazzSAMBA*'s
tunes may be copyrighted compositions; the recordings are the dataset's own. *MusicNet*'s source
performances' own licences are not listed per recording.

### Vocals and singing

| Dataset | What it gives | Licence (quote) | Source | Verdict |
| --- | --- | --- | --- | --- |
| VocalSet | 10.1 h, 20 professional singers, techniques, scales, excerpts; no note labels | Zenodo: CC BY 4.0 | [zenodo 1193957](https://zenodo.org/records/1193957) | OK-attr |
| Choral Singing Dataset | 16 singers, close-miked, f0, notes and MIDI | Zenodo: CC BY 4.0 | [zenodo 2649950](https://zenodo.org/records/2649950) | OK-attr |
| ESMUC Choir Dataset | 12 singers, about 31 min, close and room mics, corrected f0 and notes | Zenodo: CC BY 4.0 | [zenodo 5848990](https://zenodo.org/records/5848990) | OK-attr |
| Cantoría | Vocal quartet, 11 songs, f0 | Zenodo: CC BY 4.0 | [zenodo 5878677](https://zenodo.org/records/5878677) | OK-attr |
| MULTIVOX | 154 a cappella group performances, one recording per singer; no labels | Zenodo: CC BY 4.0 | [zenodo 17058101](https://zenodo.org/records/17058101) | OK-attr |
| URSing | 65 songs: solo vocal and the mix | Zenodo: CC BY 4.0 | [zenodo 6404999](https://zenodo.org/records/6404999) | caveat: the backing tracks may be third-party |
| MedleyVox | Multi-singer separation test set | Zenodo field CC BY 4.0, description CC BY-NC-SA 4.0; built from MedleyDB | [zenodo 7984549](https://zenodo.org/records/7984549) | eval |
| Opencpop | 5.2 h, Mandarin | "available to download for non-commercial purposes under a CC BY-NC-ND 4.0 License" (commercial licence by email) | [opencpop](https://wenet-e2e.github.io/opencpop/) | no (ask) |
| JVS-MuSiC | 100 singers × 2 songs | academic and personal use; commercial use through the UTokyo TLO | [site](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_music) | no (ask) |
| M4Singer, GTSinger, PopCS, OpenSinger, CSD | Mandarin, multilingual and children's singing | CC BY-NC-SA 4.0 ("NonCommercial!") | their GitHub and Zenodo pages | eval |
| Kiritan, Namine Ritsu | Japanese singing databases | non-commercial; models in software need prior approval | | no |
| DAMP, MIR-1K, iKala | Karaoke vocals | non-commercial, withdrawn or from commercial karaoke | | no |
| MTG-Jamendo, JamendoLyrics, FMA | Full mixes (no stems) | MTG-Jamendo: "solely for non-commercial research"; FMA: each track's own CC licence | GitHub pages | no / filter FMA to CC BY and CC0 tracks |
| jaCappella, SingStyle111 | A cappella songs; studio singing with MIDI | no licence found (ask the authors) | | unverified |

### Real multitracks for separation

| Dataset | What it gives | Licence (quote) | Source | Verdict |
| --- | --- | --- | --- | --- |
| AlbumDB | A 10-song indie album: raw and mixed stems (vocals, drums, bass, other), masters | Zenodo: CC BY 4.0, made for "automatic mixing and source separation" | [zenodo 19683001](https://zenodo.org/records/19683001) | OK-attr |
| JazzSAMBA | Real drum, bass, keys and horn stems (no vocals) | MIT-style | see above | caveat |
| Open Multitrack Testbed, ccMixter | Per-track Creative Commons uploads | each upload's licence | multitrack.eecs.qmul.ac.uk, ccmixter.org | keep CC BY and CC0 only |
| MedleyDB Sample | A few MedleyDB songs | CC BY-SA 4.0 | [zenodo 1438309](https://zenodo.org/records/1438309) | SA |
| MUSDB18 / -HQ | 150 songs, 4 stems | "provided for educational purposes only and ... should not be used for any commercial purpose" | [zenodo 3338373](https://zenodo.org/records/3338373) | eval |
| MoisesDB | 240 songs, fine-grained stems | "distributed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License" | [GitHub](https://github.com/moises-ai/moises-db) | eval (ask Moises for a commercial licence) |
| MedleyDB 1 and 2 | 196 multitracks with melody f0 | "free of charge for non-commercial research use only" | [zenodo 1649325](https://zenodo.org/records/1649325) | eval |
| DSD100, Mixing Secrets (Cambridge-MT), Telefunken Live from the Lab, Weathervane | Multitracks | educational or non-commercial use only | | no |
| RWC | Research database | "may not be used for any purpose other than research." | | no |

## 3. Instruments for Song Deck's renders

The training songs are as varied as the instruments that play them: more drum kits, basses and
guitars in Song Deck's sound bank make every model better on real recordings. Song Deck's own
rule for its sound bank is CC0 or CC BY only (its `scripts/soundbank/sources.json`; adding an
entry there with the archive's URL, SHA-256 and licence is a Song Deck change). It already holds
16 FreePats instruments (CC0) and the MuldjordKit (CC BY 4.0). These would add the most:

| Instrument | Licence (quote) | Where | Verdict |
| --- | --- | --- | --- |
| DrumGizmo **DRSKit**, **CrocellKit** (metal), **The Aasimonster** (death metal): multi-mic acoustic kits, the same engine as the MuldjordKit | "released under the Creative Commons Attribution 4.0 International License"; credit "Drum samples provided by DrumGizmo.org" | [drumgizmo.org](https://drumgizmo.org/wiki/doku.php?id=kits:drskit); SFZ ports at github.com/sfzinstruments | OK-attr |
| Karoryfer **Big Rusty Drums**, **Swirly Drums**, **Unruly Drums**, **Frankensnare**, **The Hat With The Phat**, Virtuosity Drums | CC0 1.0 | [github.com/sfzinstruments](https://github.com/sfzinstruments/karoryfer.big-rusty-drums) | OK |
| Naked Drums (Wilkinson Audio) | "licensed under a Creative Commons Attribution 4.0 International License" | [GitHub](https://github.com/sfzinstruments/WilkinsonAudio.NakedDrums) | OK-attr |
| Salamander Drumkit | CC BY-SA 3.0, but: "The "share-alike" condition in the license only applies if you modify the samples themselves or create new sample libraries with them. Produced Music and other non-sample-library works can be licensed at will" | [GitHub](https://github.com/endolith/Salamander-Drumkit) | OK-attr (renders) |
| Karoryfer basses: **Meatbass**, **Growlybass**, **Pastabass**, **Swagbass**, **Fashionbass**, Big Little Bass, Black And Blue Basses, Ergo (electric upright), Sneakybass (double bass); D. Smolken Double Bass | CC0 1.0 | [github.com/sfzinstruments](https://github.com/sfzinstruments/karoryfer.meatbass) | OK |
| FreePats FSBS electric guitar **Direct** (DI) and **Distorted**; Karoryfer Black And Green Guitars, Emilyguitar, Shinyguitar | CC0 1.0 | [freepats](https://freepats.zenvoid.org/ElectricGuitar/clean-electric-guitar.html) | OK (run the DI through an amp simulator such as Guitarix for amp variety) |
| **Salamander Grand Piano** v3 (Yamaha C5) | "licensed under a Creative Commons Attribution 3.0 Unported License." | [GitHub](https://github.com/sfzinstruments/SalamanderGrandPiano) | OK-attr |
| Greg Sullivan E-Pianos (CP80, Pianet T, Wurlitzer) | CC BY 3.0 | [GitHub](https://github.com/sfzinstruments/GregSullivan.E-Pianos) | OK-attr |
| Osiris Piano, Splendid Grand (public domain), Headroom Piano (CC BY 4.0) | CC0 / PD / CC BY 4.0 | github.com/sfzinstruments | OK |
| VSCO-2 CE, VCSL (orchestral, percussion, many families) | CC0 1.0 | [GitHub](https://github.com/sgossner/VCSL) | OK |
| Philharmonia samples | "free to use these samples as you wish, including releasing them as part of a commercial work. The only restriction is that they must not be sold or made available 'as is'" | [philharmonia.co.uk](https://philharmonia.co.uk/audio-sound-samples/) | OK (not for Song Deck's bank, which redistributes) |
| Iowa MIS | "may be downloaded and used for any projects, without restrictions." | [uiowa](https://theremin.music.uiowa.edu/MIS.html) | OK |
| Synths rendered without a window: **Surge XT** (Python bindings), Dexed, ZynAddSubFX, Odin 2, Helm | GPL 3; "The output of a program is not, in general, covered by the copyright on the code of the program" (FSF FAQ) | their GitHub pages | OK with your own or random patches (skip Vital's factory presets: "Do not distribute the presets that come with the free version of Vital") |
| Reverb impulse responses | OpenAIR: per IR (keep CC0 and CC BY); MIT IR survey and EchoThief: no licence stated | | generate synthetic IRs instead (no licence question) |

**Leave out:** AVL Drumkits, SamsSonor, the GPL Hydrogen kits and the FlameStudios banjo (share-alike
with no exception for music); Arachno and Timbres of Heaven (non-commercial); SGM (unknown sample
origins); Spitfire LABS and Pianobook (see section 5); Ample Sound ("only in the context of
musical arrangements"). **Ask first:** SM Drums, MT Power Drum Kit, jRhodes (its author invites
commercial requests: jjlearman@gmail.com), Maestro Concert Grand.

**Singing voices:** no modern singing synthesizer allows this. Synthesizer V forbids "Using the
audio rendered by this product to train a singing synthesis or speech synthesis model ... or any
data-driven workflow that generates singing voice or speech"; VOCALOID6 forbids training "a
machine learning or artificial intelligence (AI) system" for similar products; VoiSona forbids
"AI Training (Voice)". Most DiffSinger and UTAU voicebanks forbid commercial use or say nothing
about it. The one permissive voice found is Sinsy's NIT SONG070 (CC BY 3.0, Japanese, old HMM
quality). Real singers (section 4) are the way to vocal data.

## 4. Paid and licensable data

None of the standard licences below says outright that weights trained on the data may be
released publicly under any licence. **Put this sentence into any deal:** "Licensee may publicly
distribute, under any license including open-source licenses, model weights trained on the Data,
provided the raw Data is not redistributed."

| Source | What you get | AI-training clause (quote) | Price | Verdict |
| --- | --- | --- | --- | --- |
| **Sonovox Ultimate Vocal Dataset** | Dry vocal stems (lead, doubles, harmonies, ad-libs), male and female, labelled with BPM, key and gender | §2: "Use the Vocal Tracks in the testing, training, and development of software, including via AI and machine learning." §7: "Licensee retains full ownership of all software, tools, and IP developed using the Vocal Tracks." ([licence](https://sonovox.ai/pages/vocal-data-license-agreement)) | **Published:** free 50-pack demo; Starter 1,125 packs **$7,500** (about 104 h); Pro **$15,000**; Ultimate 4,500 packs **$30,000** (about 417 h); about $72 per hour | Allowed |
| **XIM Audio** | 61,751 stems printed from real sessions, 1,486 vocal and instrumental pairs, 338,853 MIDI files | "developing, training and improving software, audio tools and other technology, including machine learning." "Models and outputs are yours." "No redistribution of the files." ([rights](https://ximaudio.com/rights)) | Quote; an evaluation set is offered | Allowed |
| **Rightsify / GCX** | Music they own, with stems, MIDI and sheet music, by genre and instrument | "We own all right, license and title to all of the data included in our datasets." (Datarade profile) | **From $10,000 per year per dataset** (Datarade listing) | Allowed by contract; get perpetual rights in trained models |
| **Defined.ai** music and vocals | Full tracks with vocals, **no stems** ("Channel separation: No") | DLA §1(c): commercial use of trained models "as long as the Data itself is not made available or it does not output recognizable content from Contributors" ([DLA](https://defined.ai/data-license-agreement)) | Quote; their speech plan is €50,000 for 315 h (about €160/h) | Allowed, but the "recognizable content" condition needs a written exception for a separator |
| **Session singers you hire** (SoundBetter and similar) | Your own dry multitrack (lead, doubles, harmonies) and the instrumental | What your work-for-hire contract says | About **$25–350 per song** on listed rates, about $150 typical (estimate from 10 profiles) | The cleanest route: the contract grants training and release |
| Soundsnap (via Datarade) | About 50,000 tracks, about half with stems | "pre-cleared for machine learning and generative AI" | From **$500,000** | Out of budget |
| Shutterstock / Pond5, Sound Ideas, GEMA PLAI, Musical AI | Large catalogues, some stems | rights-cleared, terms by contract | Enterprise quotes (reported deals of millions) | Allowed, not for a solo budget |
| MoisesDB commercial licence | 240 real multitracks | CC BY-NC-SA by default | Unpublished; ask igor@moises.ai | Ask |
| Opencpop, JVS-MuSiC commercial licences | Mandarin / Japanese singing | by request | Unpublished | Ask |

**For a modest budget, in order:** (1) commission dry vocals from session singers under a
contract that grants training and public release (about $5,000–10,000 for 40–70 songs with real
stems); (2) Sonovox Starter ($7,500; try the free demo first and get the release sentence in
writing); (3) an XIM Audio quote for real stems and MIDI; (4) the free CC BY sets above as the
base. Rightsify and Defined.ai only for more volume.

## 5. Sources that are not allowed

These licences forbid AI training outright (quotes checked 2026-10-20). Don't train on them, and
don't render training songs with their instruments, since the renders carry the same restriction:

| Source | What it says |
| --- | --- |
| Toontrack (Superior Drummer, EZdrummer, MIDI packs), EULA §6(f), Oct 2026 | may not "use any Product or Product Component, in whole or in part, including any sounds, MIDI files, or presets related thereto, as a source to develop or train any artificial intelligence technology" |
| Native Instruments (Kontakt and its libraries), EULA §3.1 | may not use "audio and visual content contained therein in any manner for purposes of data mining ... and/or training artificial intelligence technologies" |
| Spectrasonics (Omnisphere, Keyscape, Trilian) | "may not be used to train artificial intelligence (AI) systems or machine learning models of any kind" |
| Vienna Symphonic Library | "The use of the Software for the development, training, or enhancement of artificial intelligence systems ... is strictly prohibited." |
| Spitfire Audio (incl. LABS) | "The Licensee agrees not to use Spitfire Audio's libraries for the purpose of training artificial intelligence (AI) systems ... without obtaining express written consent" |
| Splice (which owns Spitfire) | "Use of content downloaded from Splice for the purposes of training/modeling data for AI is not permitted" |
| Loopmasters / Loopcloud | strictly prohibits using its products "for the purpose of training, developing or enhancing AI-generated content" |
| Epidemic Sound | may not upload them "in any music recognition systems or any machine learning application for any purpose" |
| Artlist | assets may be inputs to AI services "provided they are not used to train or fine-tune AI models" |
| Audiobro | prohibits "any form of generative AI training for sound samples" |

**No AI clause found, so treat as not allowed until you have written permission:** XLN Addictive
Drums, Steven Slate Drums, GetGood Drums, Pianoteq (physical modelling, so the best one to ask),
Groove Monkee, Black Octopus, Producer Loops, Sample Magic, Audio Network, Pianobook (its terms
allow use "within one or more musical composition(s)" only).

## 6. Worse recordings: noise and degradation

Training on a clean track with noise added does help with phone recordings, old tapes and live
rooms, as long as it is **added to** the existing training (as augmentation) rather than replacing
it. A model trained only on "one instrument plus noise" learns to ignore noise, not other
instruments, and transcribing a mix is mostly about the other instruments. The useful version:

- **What to add, at random strengths, per training crop:** background noise (pink and white, hum
  at 50/60 Hz and its harmonics, crowd and room tone from CC0 recordings); a room (convolution
  with a synthetic impulse response); a microphone or phone (band-pass 100 Hz to 4–8 kHz, a cheap
  mic's response); a lossy codec (MP3 or Opus at 32–128 kbit/s); clipping and heavy compression; a
  little wow and flutter; level changes (already there: `--gain-db`).
- **Where it goes:** in the training data loader, so every epoch hears a different version. Song
  Deck already adds white noise to half the mixes at −72 to −45 dBFS; that is far too quiet to
  teach robustness. Noise around −30 to −10 dB below the music is what real bad recordings have.
- **The catch:** BackTrack caches each item's features (HCQT) once, so augmentation that changes
  the audio would have to happen before the cache: either a few degraded copies of each item made
  once (cheap, fixed variety), or computing features on the fly for the augmented part of each
  batch (more variety, a slower epoch; fine on a good GPU).
- **Measure it:** keep a test set of real bad recordings (your own phone recordings of songs you
  have MIDI for) and score every model on it; otherwise a gain in robustness can hide a loss on
  clean audio.
- **For the separator (Explode):** add noise as a fifth source the model must put in "other" (or
  drop), and train on mixes with codec and room degradation applied to the mix only, with the clean
  stems as targets: that is what makes a separator clean up a bad recording rather than copy it.

## 7. Making the training songs faster

The two costs are rendering (CPU) and ACE-Step covers (GPU). Song Deck 0.19.0
renders the chosen stems once instead of twice, computes only the separated stems the pipeline
keeps, and asks ACE-Step for the next take before separating and judging the current one, so the
GPU no longer waits for the CPU. Measured on one job of a 4-core machine, with the sound bank,
for 60-second drum songs: 10.8 s per song before, 7.5 s after, and 4.2 s with
`render.sample_rate: 22050` as well. Against a stand-in ACE-Step server taking 8 s per take, a
take went from 12 s to 9 s.

What you can set in the pipeline config:

| Setting | Effect | Cost |
| --- | --- | --- |
| `render.sample_rate: 22050` | About 45% less render CPU, half the disk; separation of each ACE-Step take about 2.5× faster. The models only hear 22.05 kHz. | A new dataset folder; ACE-Step gets a mix with nothing above 11 kHz: compare a few covers first |
| `render.jobs`: your core count | 26% faster renders on 4 cores (the default leaves one core idle) | About 620 MB of memory per job |
| `render.chunk: 200` | 3–6% less | More disk until pruning |
| `render.seconds: 30` | Halves render and ACE-Step time | Shorter songs; a new dataset |
| `perform.songs`, `perform.split` | ACE-Step time scales with the takes | Fewer performed takes |

On the ACE-Step server, with a good GPU: don't let it offload the model to the CPU when the GPU
has the memory (that alone is often twice as slow), don't load the 5 Hz language model
("thinking") for covers, use bf16 and flash-attention if it installs, and try 4–6 diffusion steps
instead of 8 on the turbo model (compare the takes' agreement on about 20 songs before keeping
it). A GPU with a lot of memory can run two ACE-Step servers; Song Deck's perform step uses one at
a time today.

## 8. Attribution

Every CC BY or MIT source must be credited where the model is distributed. Imported libraries put
their attribution line in the model's `audio.sources`; `python3 -m training.libraries credits
<dirs>` prints the lines for a release's notes or model card. DrumGizmo kits in Song Deck's sound
bank want "Drum samples provided by DrumGizmo.org", and the sound bank's own credits are in Song
Deck's `soundbank/CREDITS.txt`.
