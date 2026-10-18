// Fetch each pinned vendor source tree (packaging/vendor/<name>.conf) at its
// exact upstream commit without depending on an unpublished npm package.
// Which specs to install comes from package.json's "vendor" array
// (default ["llama.cpp"]); pass spec names as arguments to override.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const specs = process.argv.length > 2 ? process.argv.slice(2) : (pkg.vendor ?? ['llama.cpp']);

function readSpec(name) {
  const file = path.join(root, 'packaging', 'vendor', `${name}.conf`);
  const text = fs.readFileSync(file, 'utf8');
  const get = (key, required = true) => {
    const m = text.match(new RegExp(`^${key}="([^"]*)"`, 'm'));
    if (!m && required) throw new Error(`${file}: ${key} is missing`);
    return m ? m[1] : '';
  };
  const spec = {
    name: get('VENDOR_NAME'),
    upstream: get('VENDOR_UPSTREAM'),
    commit: get('VENDOR_COMMIT'),
    pkgName: get('PKG_NAME'),
    sparse: get('VENDOR_SPARSE', false).split(/\s+/).filter(Boolean),
    required: get('VENDOR_REQUIRED', false).split(/\s+/).filter(Boolean),
  };
  if (!/^[0-9a-f]{40}$/.test(spec.commit)) throw new Error(`${file}: VENDOR_COMMIT must be 40 hex characters`);
  return spec;
}

function install(spec) {
  const destination = path.join(root, 'node_modules', ...spec.pkgName.split('/'));
  const stamp = path.join(destination, '.vendor-source-commit');
  if (fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8').trim() === spec.commit &&
      spec.required.every((p) => fs.existsSync(path.join(destination, p)))) {
    console.log(`${spec.name} ${spec.commit} is already installed`);
    return;
  }

  fs.rmSync(destination, { recursive: true, force: true });
  fs.mkdirSync(destination, { recursive: true });
  const git = (...args) => execFileSync('git', args, { cwd: destination, stdio: 'inherit' });
  try {
    git('init', '-q');
    git('remote', 'add', 'origin', spec.upstream);
    if (spec.sparse.length) {
      git('sparse-checkout', 'init', '--cone');
      git('sparse-checkout', 'set', ...spec.sparse);
    }
    git('fetch', '--depth', '1', 'origin', spec.commit);
    git('checkout', '-q', 'FETCH_HEAD');
    const actual = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: destination, encoding: 'utf8' }).trim();
    if (actual !== spec.commit) throw new Error(`unexpected ${spec.name} commit ${actual}`);
    fs.rmSync(path.join(destination, '.git'), { recursive: true, force: true });
    for (const p of spec.required) {
      if (!fs.existsSync(path.join(destination, p))) throw new Error(`${spec.name} checkout is missing ${p}`);
    }
    fs.writeFileSync(stamp, `${spec.commit}\n`);
  } catch (error) {
    fs.rmSync(destination, { recursive: true, force: true });
    throw error;
  }
}

for (const name of specs) install(readSpec(name));
