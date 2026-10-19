// The hub's TRAIN view: settings, the setup check, and a training run's steps, output and report.
// Everything runs in the desktop shell (src-tauri/src/train.rs); this page only asks and shows.
(() => {
  const $ = (id) => document.getElementById(id);
  const invoke = window.__TAURI_INTERNALS__.invoke;
  const STEP_NAMES = { render: 'Render songs', perform: 'ACE-Step', train: 'Train', evaluate: 'Score', parity: 'Check', report: 'Report' };
  let settings = null, next = 0, timer = null, wasRunning = false;

  const fields = {
    songdeck: 't-songdeck', acestep: 't-acestep', python: 't-python', data: 't-data',
    songs: 't-songs', epochs: 't-epochs', device: 't-device',
  };

  function read() {
    const s = { ...settings };
    for (const [k, id] of Object.entries(fields)) {
      const v = $(id).value.trim();
      s[k] = k === 'songs' ? Math.max(50, parseInt(v, 10) || settings[k])
        : k === 'epochs' ? Math.max(1, parseInt(v, 10) || settings[k]) : v;
    }
    s.use_acestep = $('t-use-acestep').checked;
    return s;
  }

  function fill() {
    for (const [k, id] of Object.entries(fields)) $(id).value = settings[k] ?? '';
    $('t-use-acestep').checked = settings.use_acestep;
    $('t-acestep-row').hidden = !settings.use_acestep;
  }

  async function save() {
    settings = read();
    $('t-acestep-row').hidden = !settings.use_acestep;
    try { await invoke('train_save', { settings }); } catch { /* the next start says why */ }
  }

  for (const [, id] of Object.entries(fields)) $(id).addEventListener('change', save);
  $('t-use-acestep').addEventListener('change', save);
  for (const b of document.querySelectorAll('[data-pick]')) {
    b.addEventListener('click', async () => {
      const input = $(b.dataset.pick);
      const label = input.closest('label').querySelector('.bt-label').textContent;
      const picked = await invoke('train_pick', { title: label, file: !!b.dataset.file });
      if (picked) { input.value = picked; save(); }
    });
  }

  // ---- the setup check ----
  async function check() {
    await save();
    const list = $('t-checks');
    list.textContent = '';
    $('t-check').disabled = true;
    $('t-check').textContent = 'Checking…';
    let checks = [];
    try { checks = await invoke('train_check', { settings }); } finally {
      $('t-check').disabled = false;
      $('t-check').textContent = 'Check setup';
    }
    for (const c of checks) {
      const li = document.createElement('li');
      li.dataset.state = c.state;
      const mark = document.createElement('span');
      mark.className = 'bt-label train-mark';
      mark.textContent = { ok: 'OK', warn: 'NOTE', fail: 'FIX' }[c.state];
      const text = document.createElement('span');
      text.innerHTML = '<b></b> ';
      text.firstChild.textContent = c.name;
      text.append(c.detail);
      li.append(mark, text);
      if (c.fix) {
        const btn = document.createElement('button');
        btn.className = 'bt-btn bt-btn--ghost bt-btn--small';
        btn.type = 'button';
        btn.textContent = c.fix === 'soundbank' ? 'Build it' : 'Install';
        btn.addEventListener('click', () => run('train_fix', { fix: c.fix }));
        li.append(btn);
      }
      list.append(li);
    }
    return checks;
  }
  $('t-check').addEventListener('click', check);

  // ---- runs ----
  async function run(cmd, extra) {
    await save();
    $('t-status').textContent = '';
    try {
      await invoke(cmd, { settings, ...extra });
      $('t-log').textContent = '';
      next = 0;
      wasRunning = true;
      poll();
    } catch (e) {
      $('t-status').textContent = String(e);
    }
  }
  $('t-start').addEventListener('click', async () => {
    const checks = await check();
    if (checks.some((c) => c.state === 'fail')) {
      $('t-status').textContent = 'Fix the items marked FIX first.';
      return;
    }
    run('train_start', { resume: false });
  });
  $('t-resume').addEventListener('click', () => run('train_start', { resume: true }));
  $('t-stop').addEventListener('click', async () => {
    $('t-stop').disabled = true;
    $('t-status').textContent = 'Stopping…';
    await invoke('train_stop');
    $('t-stop').disabled = false;
  });

  function verdict(summary) {
    const p = summary && summary.promotion;
    if (!p) return '';
    return p.summary || (p.promote ? 'Better than the previous best: kept in runs/best.' : 'Not better than the previous best.');
  }

  async function poll() {
    clearTimeout(timer);
    let p;
    try { p = await invoke('train_poll', { settings, since: next }); } catch { timer = setTimeout(poll, 2000); return; }
    const log = $('t-log');
    if (p.lines.length) {
      const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
      log.textContent = (log.textContent + p.lines.join('\n') + '\n').split('\n').slice(-1500).join('\n');
      if (atEnd) log.scrollTop = log.scrollHeight;
    }
    next = p.next;

    $('t-start').hidden = $('t-resume').hidden = p.running;
    $('t-stop').hidden = !p.running;
    $('t-check').disabled = p.running;
    const fixing = p.kind === 'songdeck-install' || p.kind === 'soundbank';
    if (p.running) {
      $('t-status').textContent = fixing ? 'Setting up Song Deck…' : 'Training: run ' + (p.run || '') + '. Keep BackTrack open.';
    } else if (wasRunning && p.exit !== null) {
      $('t-status').textContent = p.exit === 0
        ? (fixing ? 'Done. Check setup again.' : 'Run finished.')
        : p.exit === 130 ? 'Stopped. Resume last run continues it.'
        : 'It stopped with an error (code ' + p.exit + '): the last lines below say why. Fix it, then Resume last run.';
      wasRunning = false;
      if (fixing) check();
    }

    const steps = $('t-steps');
    steps.textContent = '';
    for (const [name, status] of p.steps) {
      const li = document.createElement('li');
      li.dataset.status = status;
      li.textContent = STEP_NAMES[name] + ': ' + status;
      steps.append(li);
    }
    $('t-report-panel').hidden = !p.report;
    if (p.report) {
      $('t-report').textContent = p.report;
      $('t-verdict').textContent = verdict(p.summary);
    }
    timer = setTimeout(poll, p.running ? 700 : 3000);
  }

  (async () => {
    settings = await invoke('train_settings');
    fill();
    poll();
  })();
})();
