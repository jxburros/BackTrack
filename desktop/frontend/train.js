// The hub's TRAIN view: settings, the setup check, and a training run's steps, output and report.
// Everything runs in the desktop shell (src-tauri/src/train.rs); this page only asks and shows.
(() => {
  const $ = (id) => document.getElementById(id);
  const invoke = window.__TAURI_INTERNALS__.invoke;
  const STEP_NAMES = { render: 'Render songs', perform: 'ACE-Step', libraries: 'Libraries', train: 'Train', evaluate: 'Score', parity: 'Check', report: 'Report' };
  let settings = null, next = 0, timer = null, wasRunning = false;
  let shownKind = null; // the kind whose run and best model are shown (a queue's current one)

  const fields = {
    songdeck: 't-songdeck', acestep: 't-acestep', python: 't-python', data: 't-data',
    songs: 't-songs', epochs: 't-epochs', device: 't-device', kind: 't-kind',
  };
  const MODEL = { drums: 'drums', bass: 'bass', melody: 'melody' };
  const KIND = {
    drums: { word: 'drum', scrawl: 'teach surveil drums.' },
    bass: { word: 'bass', scrawl: 'teach surveil bass.' },
    melody: { word: 'melody', scrawl: 'teach surveil melody.' },
  };

  // The intro follows what is being trained.
  function showKind() {
    const k = KIND[settings.kind] || KIND.drums;
    $('t-scrawl').textContent = k.scrawl;
    $('t-intro').innerHTML =
      `Renders songs with Song Deck, has ACE-Step play them, trains Surveil's ${k.word} model on them and tells you whether it got better`
      + (settings.kind === 'drums' ? '.' : " and whether it beats Song Deck's own transcription.")
      + ' A full run takes hours; keep BackTrack open while it trains. Closing it stops the run, and <b>Resume</b> picks it up where it stopped.'
      + ' Each kind of model keeps its own songs and runs.';
    $('t-acestep-row').hidden = !settings.use_acestep;
  }

  function read() {
    const s = { ...settings };
    for (const [k, id] of Object.entries(fields)) {
      const v = $(id).value.trim();
      s[k] = k === 'songs' ? Math.max(50, parseInt(v, 10) || settings[k])
        : k === 'epochs' ? Math.max(1, parseInt(v, 10) || settings[k]) : v;
    }
    s.use_acestep = $('t-use-acestep').checked;
    s.queue = [...document.querySelectorAll('[data-queue]')].filter((b) => b.checked).map((b) => b.dataset.queue);
    s.start_at = $('t-start-at').value;
    s.libraries = [...document.querySelectorAll('[data-library]')].filter((b) => b.checked).map((b) => b.dataset.library);
    return s;
  }

  function fill() {
    for (const [k, id] of Object.entries(fields)) $(id).value = settings[k] ?? '';
    $('t-use-acestep').checked = settings.use_acestep;
    for (const b of document.querySelectorAll('[data-queue]')) b.checked = (settings.queue || []).includes(b.dataset.queue);
    $('t-start-at').value = settings.start_at || '';
    for (const b of document.querySelectorAll('[data-library]')) b.checked = (settings.libraries || []).includes(b.dataset.library);
    showKind();
  }

  async function save() {
    const kindChanged = settings && settings.kind !== $('t-kind').value;
    settings = read();
    showKind();
    if (kindChanged) { $('t-log').textContent = ''; next = 0; }
    try { await invoke('train_save', { settings }); } catch { /* the next start says why */ }
  }

  for (const [, id] of Object.entries(fields)) $(id).addEventListener('change', save);
  $('t-use-acestep').addEventListener('change', save);
  for (const b of document.querySelectorAll('[data-queue]')) b.addEventListener('change', save);
  $('t-start-at').addEventListener('change', save);
  for (const b of document.querySelectorAll('[data-library]')) b.addEventListener('change', save);
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
  // ONE AFTER ANOTHER: the ticked models in turn (training/queue.py), optionally from a set time.
  $('t-queue').addEventListener('click', async () => {
    if (!read().queue.length) {
      $('t-status').textContent = 'Tick the models to train one after another.';
      return;
    }
    const checks = await check();
    if (checks.some((c) => c.state === 'fail')) {
      $('t-status').textContent = 'Fix the items marked FIX first.';
      return;
    }
    run('train_start_queue', { resume: false });
  });
  $('t-queue-resume').addEventListener('click', () => run('train_start_queue', { resume: true }));
  $('t-stop').addEventListener('click', async () => {
    $('t-stop').disabled = true;
    $('t-status').textContent = 'Stopping…';
    await invoke('train_stop');
    $('t-stop').disabled = false;
  });

  // The best model so far, and INSTALL IN SONG DECK (its model plugin into Song Deck's plugins folder).
  function showBest(best) {
    $('t-best-panel').hidden = !best || !best.plugin;
    if (!best || !best.plugin) return;
    const f = (v) => (typeof v === 'number' ? v.toFixed(2) : '–');
    const word = (KIND[shownKind || settings.kind] || KIND.drums).word;
    let text = `Best ${word} model so far: run ${best.run}, F ${f(best.value)} on the test songs`;
    if (typeof best.songdeck === 'number') {
      text += `; Song Deck's own transcription: ${f(best.songdeck)}.`;
      if (typeof best.value === 'number' && best.value <= best.songdeck)
        text += " Song Deck's own does better so far: train more (more songs or epochs) before relying on it.";
    } else text += '.';
    text += best.installed
      ? ' It is installed in Song Deck: switch it on under Settings → Plugins (press Rescan if Song Deck is open).'
      : ` Install puts it in Song Deck's plugins folder (${best.plugins_dir}).`;
    $('t-best').textContent = text;
    $('t-install').textContent = best.installed ? 'Installed: reinstall' : 'Install in Song Deck';
  }
  $('t-install').addEventListener('click', async () => {
    $('t-install').disabled = true;
    $('t-install-status').textContent = 'Installing…';
    try {
      const where = await invoke('train_install', { settings: { ...settings, kind: shownKind || settings.kind } });
      $('t-install-status').textContent = `Installed in ${where}. In Song Deck: Settings → Plugins, press Rescan, switch it on.`;
    } catch (e) {
      $('t-install-status').textContent = String(e);
    } finally {
      $('t-install').disabled = false;
    }
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

    $('t-start').hidden = $('t-resume').hidden = $('t-queue').hidden = $('t-queue-resume').hidden = p.running;
    $('t-stop').hidden = !p.running;
    const queued = p.queue
      ? 'Queue: ' + p.queue.map(([k, st]) => `${MODEL[k] || k} ${st}`).join(' · ') + '. '
      : '';
    const shown = MODEL[p.shown] || p.shown;
    $('t-check').disabled = p.running;
    const fixing = ['songdeck-install', 'soundbank', 'uv-install', 'python-install'].includes(p.kind);
    const doing = { 'uv-install': 'Installing uv…', 'python-install': 'Installing Python and PyTorch: a few minutes, keep BackTrack open…' }[p.kind] || 'Setting up Song Deck…';
    if (p.running) {
      $('t-status').textContent = fixing ? doing
        : p.kind === 'queue' ? queued + `Now: ${shown}, run ${p.run || '(waiting for its start time)'}. Keep BackTrack open.`
        : 'Training: run ' + (p.run || '') + '. Keep BackTrack open.';
    } else if (wasRunning && p.exit !== null) {
      $('t-status').textContent = p.kind === 'queue'
        ? queued + (p.exit === 0 ? 'Every run finished.'
          : p.exit === 130 ? 'Stopped. Resume the queue continues it.'
          : 'A run failed (the lines below say why); the others ran. Fix it, then Resume the queue to retry it.')
        : p.exit === 0
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
    shownKind = p.shown;
    showBest(p.best);
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
