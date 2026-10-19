# The desktop apps

Each BackTrack app also comes as a desktop app: an installer, a Start-menu or Applications entry,
and its own window. There is a third app, **BackTrack**, that holds the others: it finds the ones
you have installed and shows them side by side.

| App | What it is | Installer |
| --- | --- | --- |
| BackTrack Surveil | Surveil's page in its own window, with the drum model built in | `BackTrack-Surveil-<version>-<platform>` |
| BackTrack Explode | The same for Explode | when Explode ships (milestone M5) |
| BackTrack | The hub: drop a track, pick an app; each installed app is a tab | `BackTrack-<version>-<platform>` |

Everything still runs on your machine. The window shows the same page `backtrack-surveil serve`
serves; the app starts that server itself, out of sight, and stops it when you close the window.

## Installing

Download from the [releases page](https://github.com/jxburros/BackTrack/releases). Install
BackTrack Surveil first; install BackTrack too if you want the hub.

**Windows:** run `BackTrack-Surveil-<version>-win32-x64-setup.exe`. It installs for your user
only and needs no administrator rights. The installers are not code-signed yet, so Windows
SmartScreen may say it "protected your PC": click *More info*, then *Run anyway*. The app uses
Microsoft Edge WebView2, which Windows 10 and 11 already have; the installer fetches it if not.

**macOS:** open the `.dmg` (`darwin-arm64` for Apple silicon, `darwin-x64` for Intel) and drag
the app to Applications. The apps are not notarized yet, so the first time macOS says it can't
check the app: open *System Settings → Privacy & Security* and click *Open Anyway* next to the
app's name (or right-click the app, choose *Open*, then *Open*). macOS 11 or later.

**Linux:** `sudo apt install ./BackTrack-Surveil-<version>-linux-x64.deb` on Debian or Ubuntu
(22.04 or later), or make the `.AppImage` executable and run it. The `.deb` also puts
`backtrack-surveil` on your `PATH`.

## Using it

- **BackTrack Surveil** opens on Surveil's page; see [the page](page.md). Drop a track, tap the
  line, export. EXPORT .MID and EXPORT .JSON ask where to save.
- **BackTrack** opens on the hub. Drop a track (or click to pick one), then click an app's tile:
  the app opens with the track loaded. HUB, EXPLODE and SURVEIL in the top bar switch between
  them; each app keeps its place while you look at another. An app that isn't installed says so
  on its tile.
- **Song Deck** finds the app as usual: BackTrack Surveil serves on its usual port (8826) when
  it's free. If Song Deck (or another window) already runs Surveil there, the app uses that
  server instead of starting a second one.
- **More models:** put `.gguf` files in the shared models folder and reopen the app. It is
  `%LOCALAPPDATA%\BackTrack\models` on Windows, `~/Library/Application Support/BackTrack/models`
  on macOS and `~/.local/share/BackTrack/models` on Linux.

- **Train** (the hub only): trains Surveil's drum model on your machine, the same pipeline as
  [training at home](training-at-home.md). Choose your Song Deck folder and, for more realistic
  audio, your ACE-Step 1.5 folder; **Check setup** lists what's missing (BackTrack Surveil,
  Python with PyTorch, Node.js, Song Deck and its sound bank, ACE-Step) and installs Song Deck's
  dependencies or sound bank when that's all it needs; **Start training** runs it, with each
  step, the output, **Stop**, **Resume last run** and the report. Settings are kept in
  `<local data>/BackTrack/training/settings.json`, runs in the training data folder you chose.
  Keep the hub open while it trains.

If an app can't start its server, its window says why (for example, a damaged install) and what
to do.

## How it's built

`desktop/` is one small [Tauri 2](https://tauri.app) program, built once per product. The bundle
identifier in the build's config decides which product it is:

| Config | Identifier | Product |
| --- | --- | --- |
| `src-tauri/tauri.conf.json` | `com.jxburros.backtrack.hub` | BackTrack (the hub) |
| `src-tauri/tauri.surveil.conf.json` (merged over it) | `com.jxburros.backtrack.surveil` | BackTrack Surveil |

```
desktop/
  frontend/          the shell's own pages: start.html (an app starting) and hub.html
  prepare.mjs        copies the pages, ui/backtrack.css and ui/fonts into dist/, and the training
                     code (training/*.py, weights/*.gguf) into src-tauri/trainer/ for the hub;
                     with a binary, also the sidecar (src-tauri/binaries/) and weights/*.gguf
                     (src-tauri/models/)
  collect.mjs        renames the installers for the release
  src-tauri/src/
    main.rs          the window, the hub's tabs, the save dialog for downloads
    server.rs        starts `<binary> serve --watch-stdin` on the app's port (or reuses a server
                     already there) and waits for GET /health
    apps.rs          the apps the shell knows, and how the hub finds them
    train.rs         the hub's TRAIN view: settings, the setup check, running the pipeline
```

- **An app** bundles its command-line binary as a Tauri sidecar and its models as resources.
  Its window shows `start.html` while the server comes up, then moves to `http://127.0.0.1:<port>/`.
  The server is started with `--watch-stdin`, so it stops when the app does, even if the app
  crashes.
- **The hub** finds each app from the record the app writes when it starts
  (`<local data>/BackTrack/apps/<id>.json`), then from the installers' default locations, so an
  app installed but never opened is found too. It starts each app's server the same way and
  shows each page in a frame. A track picked on the hub, and each download from an app's page,
  passes between the hub and the frame with `postMessage` (`ui/app.js` accepts only the desktop
  shell's origins).
- **Downloads** from the pages go to a temporary file, then a save dialog moves them.
- The apps' pages, served from 127.0.0.1, get no access to Tauri's API
  (`src-tauri/capabilities/default.json` covers only the shell's own pages).

### Building locally

Needs Node 20+, Rust (stable) and, on Linux, the WebKitGTK packages (`libwebkit2gtk-4.1-dev
libayatana-appindicator3-dev librsvg2-dev patchelf`). Build `backtrack-surveil` first
([building](building.md)), then:

```bash
cd desktop
npm ci                                              # the Tauri CLI

node prepare.mjs ../build/backtrack-surveil        # Windows: ../build/Release/backtrack-surveil.exe
npx tauri build --config src-tauri/tauri.surveil.conf.json   # BackTrack Surveil

node prepare.mjs
npx tauri build                                     # BackTrack (the hub)
```

Installers land in `src-tauri/target/release/bundle/`. Add `--debug --no-bundle` for a quick
build to run from `src-tauri/target/debug/`. To point the hub at a development binary, set
`BACKTRACK_SURVEIL_BIN=/path/to/backtrack-surveil` (or `BACKTRACK_EXPLODE_BIN`).

CI (`.github/workflows/desktop.yml`) builds all four platforms' installers whenever `desktop/`
or `ui/` changes, and `release.yml` attaches them to each release.

### Adding Explode

`apps.rs` already knows Explode (`backtrack-explode`, port 8827, theme `blast-zone` on the start
page). Once the binary exists: add `tauri.explode.conf.json` (copy the Surveil one: product name,
`mainBinaryName`, identifier `com.jxburros.backtrack.explode`, sidecar, icons in
`icons/explode/`), a build step for it in `desktop.yml`, and the hand-off in Explode's view in
`ui/app.js` (the same `backtrack-ready` / `backtrack-file` / `backtrack-download` messages
Surveil's view uses).

### Not done yet

- **Code signing and notarization** (Windows Authenticode, Apple Developer ID) are an open
  decision in the [overall plan](plans/overall.md); until then the first-run warnings above
  apply.
- **Automatic updates.** Install a newer version over the old one.
- **GPU builds.** The apps carry the CPU build, as the release archives do.
