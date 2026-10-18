# BackTrack design language: implementation spec for agents

This file tells an agent how to build BackTrack UI that matches the design boards in `design/boards/`.
Follow it exactly. Where it is silent, copy the boards. Where the boards and this file disagree, this
file wins.

**Files:**

| File | What it is | How to use it |
| --- | --- | --- |
| `design/AGENT-SPEC.md` | This spec: rules, components, copy, checks | Read it all before building UI |
| `design/tokens.json` | Every color, font, size and spacing value | The source of truth for values. Do not invent new ones |
| `design/backtrack.css` | Reference stylesheet with tokens and components | Copy it into `ui/` and build on its classes |
| `design/waveform.js` | Bar waveform renderer | Use it for every waveform |
| `design/preview.html` | The three reference screens built from the CSS | Open `preview.html`, `?theme=blast-zone` and `?theme=wiretap` to compare |
| `design/check_ui.py` | Automated checks from section 10 | `python design/check_ui.py [URL]` |
| `design/boards/*.jpg` | The approved visual direction | Visual reference. Never ship the images themselves as UI |

**Products and names.** Use the names exactly as written here:

| Product | Display name | Short name | Theme |
| --- | --- | --- | --- |
| Hub, the page that links both apps | BACKTRACK | BackTrack | `xerox-riot` |
| Stem separator | Backtrack Explode | EXPLODE | `blast-zone` |
| MIDI transcriber | Backtrack Surveil | SURVEIL | `wiretap` |
| Release pages, socials, changelogs | (product name) | | `gig-flyer` (not in the apps) |

In titles and headlines the names are in upper case (EXPLODE, SURVEIL, BACKTRACK). In running text
write "Backtrack Explode" or "Explode".

---

## 1. The one idea

Rough look, solid tool. The surfaces look like a photocopied punk flyer: grain, torn paper, tape,
tilted headlines and loud color. Everything the user operates or reads precisely (buttons, inputs,
sliders, numbers, status text, progress) stays straight, flat, readable and where people expect it.
A tilted stamp may echo a status, but the status itself is always plain text too. **Break the look, never
the tool.**

## 2. Hard rules

These are testable. Section 10 lists the checks.

### MUST

1. Set the theme on `<html data-theme="…">`: `xerox-riot` for the hub and light mode, `blast-zone`
   in Explode, `wiretap` in Surveil. Explode and Surveil also offer `xerox-riot` as their light mode.
2. Take every color, font and size from `tokens.json` or the `--bt-*` variables. No other hex values
   in component CSS.
3. Put **exactly one** shout on each screen: one `.bt-shout` (hub) or one `.bt-app-title` (apps).
4. Give each screen one accent color: riot red in the hub, hazard yellow in Explode, phosphor green
   in Surveil. Explode's flash orange is only for the vocals stem and the main action (DETONATE).
   Surveil's red is only for REC and errors.
5. Use square corners everywhere (`border-radius: 0`). The only round things are the REC dot and
   radio inputs.
6. Use the `bt-tilt-*` classes for tilt, only on decoration: headlines, scrawl, tape, stamps, paper
   scraps and stickers. Keep the angle between 1° and 4°, and vary the direction on a screen.
7. Show grain on the page background (`body::before`) and, optionally, on display type
   (`.bt-toner`). Use grain nowhere else.
8. Meet WCAG AA: 4.5:1 for text under 24 px (or under 18.5 px bold), 3:1 for larger text and UI
   graphics. The token pairs in `tokens.json → contrast_checked` are already measured; stick to
   them.
9. Draw waveforms as hard bars with `waveform.js`, using the theme accent as `color`.
10. Show a visible focus ring on every interactive element (`:focus-visible`, 3 px, `--bt-focus`).
11. Bundle the fonts with the app. The page must work offline, and it never loads anything from the
    network at run time.
12. Honour `prefers-reduced-motion`: no blinking, no shaking, instant state changes.

### MUST NOT

1. Tilt, texture, tear or tape anything interactive or anything the user has to read precisely:
   buttons, inputs, sliders, toggles, numbers, timecodes, file names, progress bars, error text, or
   table and list rows.
2. Use gradients (other than the hazard stripes and scanlines in the CSS), soft drop shadows, blur,
   glassmorphism, rounded cards or pill buttons.
3. Use Permanent Marker (`.bt-scrawl`) for instructions, labels, values, errors or anything you can
   click. Scrawl is commentary only and is `aria-hidden="true"`.
4. Put Anton or the theme display face on body text. Body and UI text is Space Mono.
5. Use the stencil font below 40 px or VT323 below 20 px.
6. Bring `gig-flyer` colors (pink, acid) or the flyer fonts into the apps.
7. Use more than one hot color on a screen (see MUST 4).
8. Write marketing filler (see section 7).
9. Use emoji as icons. Icons are text glyphs (→ ● ✓ ×) or simple square line icons drawn with a 2-3 px
   stroke.

## 3. Tokens

All values are in `tokens.json` and in `backtrack.css` as `--bt-*` variables.

### 3.1 Colors by role

| Role | Variable | xerox-riot | blast-zone | wiretap |
| --- | --- | --- | --- | --- |
| Page background | `--bt-bg` | `#F1EFE9` bone | `#111110` | `#07110A` CRT |
| Panel | `--bt-surface` | `#F7F5EF` | `#1A1A18` | `#0A170E` |
| Inset / row | `--bt-surface-2` | `#BDB8AD` xerox | `#232320` | `#06100A` |
| Track (empty progress) | `--bt-track` | `#BDB8AD` | `#2B2B27` | `#123A1E` |
| Text | `--bt-ink` | `#0D0D0D` | `#F1EFE9` | `#39FF6A` |
| Secondary text | `--bt-ink-muted` | `#5D5A53` | `#8C8670` | `#3F8F55` |
| Accent (fills, borders) | `--bt-accent` | `#E10600` | `#FFD400` hazard | `#39FF6A` phosphor |
| Accent as small text | `--bt-accent-text` | `#C20500` | `#FFD400` | `#39FF6A` |
| Text on accent | `--bt-on-accent` | `#F1EFE9` | `#0D0D0D` | `#07110A` |
| Primary button | `--bt-primary` | `#0D0D0D` | `#FF6A00` flash | `#39FF6A` |
| Text on primary | `--bt-on-primary` | `#F1EFE9` | `#0D0D0D` | `#07110A` |
| Error | `--bt-danger` | `#C20500` | `#FF5A4A` | `#FF5A4A` |
| Focus ring | `--bt-focus` | `#E10600` | `#FFD400` | `#39FF6A` |
| Tape | `--bt-tape` | `#E8DCB0` at 78% opacity | same | not used |

Theme-only variables: `--bt-flash` (Explode, `#FF6A00`), `--bt-rec` (Surveil, `#E10600`),
`--bt-grid`, `--bt-grid-strong`, `--bt-note-dim` (Surveil piano roll).

`#E10600` on bone is 4.32:1, so riot red text under 24 px uses `--bt-accent-text` (`#C20500`, 5.51:1).
Surveil's REC red (`#E10600` on CRT, 3.7:1) is for the 52 px REC label only; error text in the
dark themes uses `--bt-danger`.

### 3.2 Type

| Style | Class | Font | Size / line height | Notes |
| --- | --- | --- | --- | --- |
| Shout | `.bt-shout` | Anton | clamp(64-160 px) / 0.95 | Upper case. One per screen |
| App title | `.bt-app-title` | Stencil (Explode), VT323 (Surveil) | clamp(72-150 px) / 0.95 | In the accent color |
| Heading | `.bt-heading` | Anton | 48 / 1.0 | Upper case. Section titles |
| Body | `body` | Space Mono 400 | 18 / 1.45 | Sentence case |
| UI | | Space Mono 700 | 16 / 1.3 | Values, menus |
| Button | `.bt-btn` | Space Mono 700 (VT323 36 px in Surveil) | 22, tracking 0.08em | Upper case |
| Label | `.bt-label` | Space Mono 700 | 15, tracking 0.14em | Upper case, above values |
| Caption | `.bt-caption` | Space Mono 400 | 14 / 1.4 | Muted color |
| Readout | `.bt-readout` | VT323 | 30 / 1.1 | Surveil only |
| Stem name | `.bt-stem__name` | Theme display face | 46 | Explode only |
| Scrawl | `.bt-scrawl` | Permanent Marker | 26-44 | Decoration only, `aria-hidden` |

Use sentence case for body text and lower case for scrawl and quiet captions ("runs on your
machine. nothing leaves it."). Upper case is for shouts, headings, labels and buttons.

### 3.3 Space, borders, layout

- 8 px grid: 4, 8, 16, 24, 32, 48, 64, 96 (`--bt-s1` … `--bt-s8`).
- Page margin is 64 px on desktop and 16 px under 720 px (`--bt-margin`). No horizontal scroll at
  390 px.
- Borders are 2 px (hairline), 3 px (standard) or 4 px (heavy panel and tile edges). Always solid,
  except the drop zone (3 px dashed).
- Square corners only.
- Layout: left-aligned with a big headline top left, controls in a predictable column, the primary
  action bottom right of its panel in the apps (bottom left in the hub). Asymmetry comes from the
  headline and decoration, never from controls.
- Hard offset shadow only: `6px 6px 0` in the accent on button hover, and the 14 px ink block behind
  the hub screen. No blurred shadows.

## 4. Texture and decoration

Decoration is optional and sparse: on any screen at most **3 tape strips**, **1 scrawl**, **1
stamp** and **2 torn edges**. Decoration never covers text or controls, never takes pointer events
and is always `aria-hidden="true"`.

| Element | How | Rules |
| --- | --- | --- |
| Paper grain | `body::before` in the CSS (SVG `feTurbulence`) | Opacity 0.05-0.09. Inverted (light specks) in dark themes |
| Toner on display type | `.bt-toner` | Only on `.bt-shout` and `.bt-app-title` |
| Tape | `.bt-tape`, absolutely placed over a corner | 100-210 × 40 px, 78% opacity, rotated 12-30°, half on and half off the edge |
| Torn edge | `.bt-torn` (clip-path) | On drop zones, paper scraps and hero panels. Never on a control |
| Stamp | `.bt-stamp` | Status words only (DONE, FAILED, SAVED), rotated -7°. The word is also given in text (section 8) |
| Scrawl | `.bt-scrawl` | One short aside per screen, at most 5 words, rotated -3 to -10° |
| Hazard tape | `.bt-hazard` | Explode only: the top edge of the main panel |
| Scanlines | `.bt-screen::after` | Surveil only: 2 px line every 6 px, black at 22% |
| Redaction | `.bt-redact` | Surveil only: a value that is unknown or hidden. Give it a `title` or visually hidden text |

Give each torn element its own edge, made once with a fixed seed so it never changes between renders:

```js
// Returns a clip-path polygon with ripped top and bottom edges. Same seed, same edge.
function tornClipPath(seed, { teeth = 14, depth = 5 } = {}) {
  let s = seed >>> 0;
  const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  const top = [], bottom = [];
  for (let i = 0; i <= teeth; i++) {
    const x = (i / teeth) * 100;
    top.push(`${x.toFixed(1)}% ${(r() * depth).toFixed(1)}%`);
    bottom.unshift(`${x.toFixed(1)}% ${(100 - r() * depth).toFixed(1)}%`);
  }
  return `polygon(${top.join(", ")}, ${(99 + r()).toFixed(1)}% 50%, ${bottom.join(", ")}, ${r().toFixed(1)}% 50%)`;
}
// el.style.clipPath = tornClipPath(42);
```

## 5. Components

Use these classes from `backtrack.css`. Markup patterns are in `preview.html`.

### 5.1 Buttons (`.bt-btn`)

- **Primary** `.bt-btn`: a flat slab in `--bt-primary`, 56 px tall, 32 px side padding, upper case.
  Use one per view. Hover: moves -2 px with a `6px 6px 0` accent shadow. Active: moves +2 px, no
  shadow. Disabled: 45% opacity, no hover.
- **Secondary** `.bt-btn--ghost`: transparent with a 4 px inset ink border (3 px in Surveil).
- **Explode main action** `.bt-btn .bt-btn--detonate`: flash orange with stencil type, labelled
  DETONATE.
- **Surveil buttons** use VT323 at 36 px. Write the secondary action in brackets: `[ TAP THE LINE ]`.
- Use a verb as the label, with an arrow for "go": `RIP IT →`, `DETONATE`, `EXPORT .MID`, `CANCEL`.
- Hit area is at least 44 × 44 px. Never tilt a button.

### 5.2 App bar (`.bt-appbar`, hub)

64 px ink bar: the BACKTRACK wordmark (Anton 36) on the left, nav links (Space Mono 700, 16 px,
tracking 0.14em, upper case) on the right. The current page is marked with `aria-current="page"`
(a 4 px accent underline). Under 720 px it wraps to two lines.

### 5.3 Drop zone (`.bt-dropzone`)

A torn xerox scrap with a dashed inner border and the text `WAV / FLAC / MP3 — or click to pick`.
It is a `<label>` wrapping a hidden `<input type="file">`, so it works with the keyboard. While a
file is dragged over it, set `data-over="true"`: the fill turns accent and the text `on-accent`. One
or two tape strips may hold it down.

### 5.4 App tiles (`.bt-tile--explode`, `.bt-tile--surveil`, hub)

Flat tiles in each app's own colors, with a 4 px ink border: the app name in its display face
(stencil 84 px, VT323 92 px) and one plain line (`split into stems`, `turn it into MIDI`). The
whole tile is a link.

### 5.5 Stem row (`.bt-stem`, Explode)

Grid `200px 1fr auto`, at least 88 px tall, on `--bt-surface-2`, with 16 px between rows. From
left to right: the stem name (stencil 46, upper case), a waveform `<canvas class="bt-stem__wave">`
64 px tall, then the M and S toggles (`.bt-ms`, 44 px squares, `aria-pressed`, `aria-label="Mute
vocals"`). Vocals use `data-stem="vocals"` and show in flash orange; every other stem is hazard
yellow. A muted stem's waveform drops to 25% opacity. Stems are listed in this order: vocals, drums,
bass, guitar, piano, other. Under 720 px the row stacks.

### 5.6 Waveform (`waveform.js`)

`drawBars(canvas, peaks)`, where `peaks` holds the 0-1 peak of each bucket. Bars are 3 px wide
with 2 px gaps, hard edges and no rounding or smoothing, and quiet passages drop to a 2 px line.
The color comes from the canvas's CSS `color`. Redraw it when the canvas is resized.

### 5.7 Progress (`.bt-progress`)

A flat 26 px bar: accent fill on `--bt-track`, `role="progressbar"` with `aria-valuenow`. Under it
goes a `.bt-progress__meta` line with real counts: `CHUNK 14/22 • PASS 2/2 (flip + swap)`. Never
show a spinner without a count, and never animate the bar beyond what has actually been done. The
cancel button sits next to it.

### 5.8 Surveil screen (`.bt-screen`, `.bt-rec`, `.bt-roll`, `.bt-note`)

- `.bt-screen`: a 3 px phosphor border with scanlines.
- Header: SURVEIL (VT323) on the left; on the right `REC ●`, shown only while transcribing, with
  `CAM 02  YYYY-MM-DD  HH:MM:SS:FF` under it in muted VT323. Use the real date and the elapsed time.
- Case file lines in `.bt-readout`: `CASE FILE: bass.mid`, then
  `SUBJECT bass (mono)   KEY E minor   TEMPO 128`. An unknown value is a `.bt-redact` bar with
  `title="unknown"`.
- Piano roll `.bt-roll`: 25 px pitch lanes, beat lines, and stronger bar lines every 4 beats. Notes
  are `.bt-note`, bright phosphor at confidence 0.7 or more and `data-conf="low"` (dim green with a
  phosphor outline) below that. The roll gets a short legend (or tooltip) saying bright means
  confident.
- Summary readout: `NOTES 212   CONF 0.87   BENDS ON`.
- Actions: `[ TAP THE LINE ]` (ghost, start) and `EXPORT .MID` (primary).

### 5.9 Status, errors, empty states

- **Done:** a `.bt-stamp--ok` stamp (DONE) next to a plain sentence: `4 stems written to stems/`.
- **Error:** the plain message in `--bt-danger`, Space Mono 700, never tilted, with the cause and
  the fix: `COULDN'T READ song.m4a. Use WAV, FLAC or MP3.` A FAILED stamp can sit next to it.
- **Empty:** the screen's one shout plus one line, e.g. `NOTHING HERE YET.` / `Drop a track to start.`
- **Toasts:** a square ink slab bottom left, Space Mono, auto-dismissed after 5 s, and announced
  with `role="status"`.

## 6. Themes in detail

**xerox-riot (hub, light mode).** Bone background, ink, xerox grey insets and one riot red. The
hub screen: app bar, the shout `DROP A TRACK.` tilted -2° with toner, the scrawl
`we'll take it apart.`, a torn drop zone with tape, two app tiles side by side, `RIP IT →` and the
line `runs on your machine. nothing leaves it.`

**blast-zone (Explode).** Near-black background. The panel has a 4 px hazard border and hazard tape
across the top. The title EXPLODE is in the hazard stencil with toner, and the file line sits top
right: `song.wav / 3:41 / 44.1k stereo` with `4 stems. sums back to the mix.` under it. Then the
stem rows, the hazard progress bar with the chunk/pass line, the scrawl `boom.` and DETONATE
bottom right.

**wiretap (Surveil).** CRT black with phosphor text and scanlines. SURVEIL top left, REC and the
timestamp top right, the case file lines, the piano roll, then the summary readout and actions.
It is VT323 everywhere except body sentences, which stay in Space Mono.

**gig-flyer (release pages only).** Bone background, a gig-pink torn poster, acid-on-ink EXPLODE
or SURVEIL in Anton, the blackletter "Backtrack" (UnifrakturMaguntia), ransom-note version lettering
(each letter on its own scrap, in its own font, tilted ±5°) and tear-off tabs. Never inside the apps.

## 7. Voice and copy

Short, blunt, lower-key than the visuals. Active verbs. Say what happens and what the user gets.

| Use | Avoid |
| --- | --- |
| DROP A TRACK. | Get started by uploading your audio |
| RIP IT → | Submit |
| DETONATE | Start separation process |
| [ TAP THE LINE ] | Begin transcription |
| 4 stems. sums back to the mix. | Powered by cutting-edge AI |
| runs on your machine. nothing leaves it. | Your privacy is our priority |
| COULDN'T READ song.m4a. Use WAV, FLAC or MP3. | Oops! Something went wrong 😢 |

- Use the real numbers: stem count, duration, sample rate, chunk/pass, note count, confidence.
- Never promise quality you haven't measured ("studio quality", "perfect stems").
- No swearing in the product. "No mercy" is fine on the cover; inside the apps the attitude comes
  from the look, not from the text.
- Errors never joke. They say what failed and what to do.

## 8. Accessibility

- Contrast: section 3.1. Check any new pair before using it.
- Decoration is `aria-hidden="true"` and `pointer-events: none`.
- A stamp or redaction is never the only signal: the status is also given in text (visually hidden
  if need be), and a redaction has a `title` or `aria-label`.
- Low-confidence notes use brightness and an outline, and there is a numeric confidence on hover or
  focus.
- Tilted headlines are real text: selectable, and the reading order matches the DOM.
- REC blinks at 1 Hz; with reduced motion it does not blink.
- Everything works by keyboard: the drop zone is a label for a file input, toggles use
  `aria-pressed`, progress uses `role="progressbar"`.

## 9. Placing it in the code

- The shared `ui/` page (see the overall plan) imports `backtrack.css` and `waveform.js` and the
  bundled font files. Each binary sets its theme: the transcriber serves
  `<html data-theme="wiretap">`, the separator `<html data-theme="blast-zone">`, and a light-mode
  toggle switches either to `xerox-riot` (stored in `localStorage` as `bt-theme`).
- When Song Deck embeds an app, keep the theme inside the app's own panel. Do not restyle Song Deck.
- Fonts: Anton, Space Mono 400 and 700, Permanent Marker, Big Shoulders Stencil Display 900 and
  VT323, as WOFF2 in `ui/fonts/` with their licences (all OFL except Permanent Marker, which is
  Apache 2.0). Declare them with `@font-face` and `font-display: swap`.
  The fallbacks are in `tokens.json`.
- Every file in `ui/` is embedded in the binaries (`cmake/EmbedPage.cmake`) and served beside the
  page. `ui/backtrack.css` and `ui/waveform.js` must match `design/` byte for byte (a CTest checks
  it): change them in `design/` and copy.
- Beyond section 5, `backtrack.css` has form fields (`.bt-field`, `.bt-input`, `.bt-select`,
  `.bt-check`), `.bt-btn--small` (44 px), `.bt-error`, `.bt-toast` and `.bt-sr-only`, and the
  Surveil roll colors (`--bt-roll-bg`, `--bt-grid`, `--bt-grid-strong`, `--bt-note-dim`, `--bt-rec`)
  for its xerox-riot light mode, where the scanlines are off.
- If you need a value that isn't a token, add it to `tokens.json` and `backtrack.css` together, with
  a contrast check if it is a color, and mention it in the PR.

## 10. Acceptance checks

Before calling a UI change done, check all of these. `python design/check_ui.py [URL]` runs the
first five automatable checks in every theme at 1280 and 390 px wide and exits non-zero on a failure;
add the rest (axe-core, network, fonts) to the app's own tests.

**Automatable**

- [ ] Every screen has exactly one `.bt-shout` or `.bt-app-title`.
- [ ] No computed `border-radius` above 0, except on the REC dot.
- [ ] No computed `transform` with a rotation on `button`, `input`, `select`, `textarea`,
      `[role=button]`, `[role=progressbar]` or `.bt-ms`.
- [ ] No `box-shadow` with a blur radius above 0, and no gradients outside `.bt-hazard`,
      `.bt-screen::after` and `.bt-roll`.
- [ ] All text passes WCAG AA contrast (axe-core `color-contrast`).
- [ ] No horizontal scroll at 390 px wide.
- [ ] `.bt-scrawl`, `.bt-tape`, `.bt-stamp` and `.bt-hazard` are `aria-hidden` or carry no meaning
      on their own.
- [ ] No network requests at run time (fonts are bundled).
- [ ] Fonts in use: only the families in `tokens.json`; no gig-flyer families in the apps.

**By eye** (compare with `preview.html` and the boards)

- [ ] One hot color per screen, used as section 2 MUST 4 says.
- [ ] At most 3 tape strips, 1 scrawl, 1 stamp and 2 torn edges.
- [ ] The headline reads first, the primary action is easy to find, and the numbers are easy to
      read.
- [ ] The copy follows section 7.
