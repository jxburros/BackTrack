// Moves what `tauri build` just made into one folder, under release names:
//   <Name>-<version>-<platformKey>-setup.exe | .dmg | .deb | .AppImage
// and clears the bundle folder so the next product's build starts empty.
//
// usage: node collect.mjs <Name> <platformKey> <out dir>
import { mkdirSync, readFileSync, readdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const [name, key, out] = process.argv.slice(2);
if (!name || !key || !out) {
  console.error('usage: node collect.mjs <Name> <platformKey> <out dir>');
  process.exit(2);
}
const here = dirname(fileURLToPath(import.meta.url));
const version = JSON.parse(readFileSync(join(here, '..', 'package.json'), 'utf8')).version;
const bundle = join(here, 'src-tauri', 'target', 'release', 'bundle');
const kinds = { nsis: ['.exe', '-setup.exe'], dmg: ['.dmg', '.dmg'], deb: ['.deb', '.deb'], appimage: ['.AppImage', '.AppImage'] };

mkdirSync(out, { recursive: true });
let n = 0;
for (const [dir, [ext, suffix]] of Object.entries(kinds)) {
  let files = [];
  try { files = readdirSync(join(bundle, dir)).filter((f) => f.endsWith(ext)); } catch { continue; }
  if (files.length !== 1) throw new Error(`expected one ${ext} in ${join(bundle, dir)}, found ${files.length}`);
  const target = join(out, `${name}-${version}-${key}${suffix}`);
  renameSync(join(bundle, dir, files[0]), target);
  console.log(target);
  n++;
}
if (!n) throw new Error('no installers in ' + bundle);
rmSync(bundle, { recursive: true, force: true });
