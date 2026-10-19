// Gets desktop/ ready for `tauri build` or `tauri dev`:
//   dist/                     the shell's pages, with ui/backtrack.css and ui/fonts beside them
//   src-tauri/binaries/       the app's command-line binary, named as Tauri wants a sidecar
//                             (<name>-<target triple>[.exe])
//   src-tauri/models/         the models the app ships with (weights/*.gguf)
//   src-tauri/trainer/        the hub's training code (training/*.py and weights/*.gguf, so the
//                             pipeline finds the model that judges ACE-Step takes)
//
// usage: node prepare.mjs                          pages only (the hub)
//        node prepare.mjs path/to/backtrack-surveil  pages, sidecar and models (an app)
import { execFileSync } from 'node:child_process';
import { copyFileSync, cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

const dist = join(here, 'dist');
rmSync(dist, { recursive: true, force: true });
cpSync(join(here, 'frontend'), dist, { recursive: true });
copyFileSync(join(root, 'ui', 'backtrack.css'), join(dist, 'backtrack.css'));
cpSync(join(root, 'ui', 'fonts'), join(dist, 'fonts'), { recursive: true });
console.log('pages -> ' + dist);

const trainer = join(here, 'src-tauri', 'trainer');
rmSync(trainer, { recursive: true, force: true });
mkdirSync(join(trainer, 'training'), { recursive: true });
mkdirSync(join(trainer, 'weights'), { recursive: true });
for (const f of readdirSync(join(root, 'training')).filter((f) => f.endsWith('.py'))) {
  copyFileSync(join(root, 'training', f), join(trainer, 'training', f));
}
for (const f of readdirSync(join(root, 'weights')).filter((f) => f.endsWith('.gguf'))) {
  copyFileSync(join(root, 'weights', f), join(trainer, 'weights', f));
}
console.log('training code -> ' + trainer);

const binary = process.argv[2];
if (binary) {
  const triple = process.env.TAURI_TARGET_TRIPLE ||
    execFileSync('rustc', ['-vV'], { encoding: 'utf8' }).match(/^host: (\S+)$/m)[1];
  const ext = binary.endsWith('.exe') ? '.exe' : '';
  const name = basename(binary, ext);
  const bins = join(here, 'src-tauri', 'binaries');
  mkdirSync(bins, { recursive: true });
  const target = join(bins, `${name}-${triple}${ext}`);
  copyFileSync(binary, target);
  console.log('sidecar -> ' + target);

  const models = join(here, 'src-tauri', 'models');
  rmSync(models, { recursive: true, force: true });
  mkdirSync(models, { recursive: true });
  for (const f of readdirSync(join(root, 'weights')).filter((f) => f.endsWith('.gguf'))) {
    copyFileSync(join(root, 'weights', f), join(models, f));
    console.log('model -> ' + join(models, f));
  }
}
