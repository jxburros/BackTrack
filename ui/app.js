// The page the BackTrack binaries serve. The server sets data-app and data-theme on <html>;
// the look and its rules are in design/AGENT-SPEC.md.
(() => {
  const html = document.documentElement;
  const app = html.dataset.app || '';
  const $ = (id) => document.getElementById(id);
  const NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
  const noteName = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);
  const DRUMS = { 35: 'kick', 36: 'kick', 38: 'snare', 40: 'snare', 41: 'tom', 43: 'tom', 45: 'tom', 47: 'tom', 48: 'tom', 50: 'tom',
                  42: 'hi-hat', 44: 'hi-hat', 46: 'open hi-hat', 49: 'crash', 57: 'crash', 51: 'ride', 59: 'ride' };
  const MONO = ['bass', 'vocals', 'melody'];
  const pad = (n, w = 2) => String(Math.floor(n)).padStart(w, '0');

  const session = {
    get(k) { try { return sessionStorage.getItem(k) || ''; } catch { return ''; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch {} },
  };

  // ---- theme: the app's own theme, or xerox-riot as its light mode ----
  const toggle = $('theme-toggle');
  const appTheme = html.dataset.appTheme || 'xerox-riot';
  toggle.hidden = appTheme === 'xerox-riot';
  const syncToggle = () => toggle.setAttribute('aria-pressed', String(html.dataset.theme === 'xerox-riot'));
  syncToggle();
  toggle.addEventListener('click', () => {
    const light = html.dataset.theme !== 'xerox-riot';
    html.dataset.theme = light ? 'xerox-riot' : appTheme;
    try { localStorage.setItem('bt-theme', light ? 'xerox-riot' : appTheme); } catch {}
    syncToggle();
    window.dispatchEvent(new Event('bt-theme'));
  });

  // ---- toasts ----
  let toastTimer = 0;
  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
  }

  // ---- server ----
  function headers(extra) {
    const h = Object.assign({}, extra);
    const t = $('token').value.trim();
    if (t) h.Authorization = 'Bearer ' + t;
    return h;
  }

  async function api(path, opts = {}) {
    let res;
    try {
      res = await fetch(path, Object.assign({}, opts, { headers: headers(opts.headers) }));
    } catch {
      const e = new Error('the server did not answer');
      e.status = 0;
      throw e;
    }
    let body = null;
    try { body = await res.json(); } catch {}
    if (!res.ok) {
      const e = new Error((body && body.error) || res.status + ' ' + res.statusText);
      e.status = res.status;
      throw e;
    }
    return body;
  }

  let info = null;
  let onHealth = () => {};

  function setHealth(text, state) {
    const h = $('health');
    if (h.textContent !== text) h.textContent = text;
    h.dataset.state = state;
  }

  async function refreshHealth() {
    try {
      const h = await (await fetch('/health')).json();
      if (h.status === 'ok') setHealth('READY', 'ok');
      else if (h.status === 'loading') setHealth('LOADING MODEL', 'wait');
      else setHealth(String(h.status).toUpperCase(), 'error');
      $('auth').hidden = !h.auth_required;
      onHealth(h);
      return h;
    } catch {
      setHealth('OFFLINE', 'error');
      return null;
    }
  }

  async function loadInfo() {
    try {
      info = await api('/info');
    } catch (e) {
      if (e.status === 401) toast('KEY REJECTED. Enter the key the server was started with.');
      return;
    }
    document.title = info.name || 'BackTrack';
    $('meta').textContent = [info.name, info.version && 'v' + info.version, info.device && 'on ' + info.device].filter(Boolean).join(' / ');
    if (views[app] && views[app].info) views[app].info(info);
  }

  // ---- views ----
  const views = { surveil: { mount: mountSurveil } };

  function mount() {
    const view = views[app];
    const tpl = $(view ? 'view-' + app : 'view-none');
    $('view').replaceChildren(tpl.content.cloneNode(true));
    if (view) view.mount(view);
  }

  // ---------------------------------------------------------------- Surveil
  function mountSurveil(view) {
    let file = null, result = null, busy = null, startedAt = 0, peaks = null, selected = -1;
    let baseName = 'transcription', camTimer = 0, poll = 0;

    // CAM line: real date, elapsed time of the current (or last) run as HH:MM:SS:FF at 25 fps.
    function cam(elapsedMs) {
      const d = new Date();
      const date = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
      const s = elapsedMs / 1000;
      $('cam').textContent = 'CAM 02  ' + date + '  ' + pad(s / 3600) + ':' + pad((s / 60) % 60) + ':' + pad(s % 60) + ':' + pad((s * 25) % 25);
    }
    cam(0);

    view.info = (i) => {
      const src = $('source');
      const keep = src.value;
      src.replaceChildren(...(i.sources || ['mix']).map((s) => new Option(s, s)));
      src.value = (i.sources || []).includes(keep) ? keep : 'mix';
      const models = $('model');
      models.length = 1;
      for (const m of i.models || []) {
        models.add(new Option(m.name + (m.placeholder ? ' (placeholder)' : '') + ' / ' + (m.stems || []).join(', '), m.id));
      }
    };

    onHealth = (h) => {
      if (!busy) return;
      const job = (h.active_jobs || []).find((j) => j.id === busy);
      if (!job) return;
      if (job.state === 'queued') return setProgress(0, 'QUEUED • waiting for the job ahead');
      const p = job.progress || 0;
      setProgress(p, (job.stage || 'working').toUpperCase() + ' • ' + Math.round(p * 100) + '% • ' + (job.seconds || 0).toFixed(1) + ' S');
    };

    function setProgress(fraction, meta) {
      const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
      $('progress').setAttribute('aria-valuenow', String(pct));
      $('progress-bar').style.width = pct + '%';
      $('progress-meta').textContent = meta;
    }

    function stamp(word, kind) {
      const s = document.createElement('span');
      s.className = 'bt-stamp bt-stamp--' + kind;
      s.setAttribute('aria-hidden', 'true');
      s.textContent = word;
      return s;
    }

    function showDone(text) {
      const p = document.createElement('p');
      p.textContent = text;
      $('status').replaceChildren(stamp('Done', 'ok'), p);
    }

    function showError(text) {
      const p = document.createElement('p');
      p.className = 'bt-error';
      p.textContent = text;
      $('status').replaceChildren(stamp('Failed', 'fail'), p);
    }

    function explain(e, name) {
      const msg = (e.message || '').replace(/\.$/, '');
      if (e.status === 0) return "COULDN'T REACH THE SERVER. Check that backtrack-surveil serve is still running, then try again.";
      if (e.status === 401) { $('auth').hidden = false; return 'KEY REJECTED. Enter the key the server was started with.'; }
      if (/unreadable|unrecognised audio|empty|no samples|sample rate|channel count/i.test(msg)) return "COULDN'T READ " + name + '. Use WAV, FLAC or MP3.';
      if (/longer than/i.test(msg)) return "COULDN'T READ " + name + '. ' + msg[0].toUpperCase() + msg.slice(1) + '; trim it and try again.';
      if (e.status === 409 && /busy/i.test(msg)) return 'SERVER BUSY. ' + msg + '. Try again when the current job ends.';
      if (e.status === 503) return 'NOT READY. ' + msg[0].toUpperCase() + msg.slice(1) + '; try again in a few seconds.';
      return "COULDN'T TRANSCRIBE " + name + '. ' + msg[0].toUpperCase() + msg.slice(1) + '.';
    }

    // ---- file in ----
    function pickFile(f) {
      if (!f) return;
      if (!/\.(wav|flac|mp3)$/i.test(f.name)) {
        showError("COULDN'T READ " + f.name + '. Use WAV, FLAC or MP3.');
        return;
      }
      file = f;
      baseName = f.name.replace(/\.[^.]+$/, '') || 'transcription';
      $('drop-text').textContent = f.name + ' / ' + (f.size / 1048576).toFixed(1) + ' MB — click to pick another';
      $('status').replaceChildren();
      const player = $('player');
      if (player.src) URL.revokeObjectURL(player.src);
      player.src = URL.createObjectURL(f);
      $('source-row').hidden = false;
      $('go').disabled = !!busy;
      const guess = ['bass', 'drums', 'vocals', 'piano', 'guitar', 'melody'].find((s) => f.name.toLowerCase().includes(s));
      if (guess && [...$('source').options].some((o) => o.value === guess)) $('source').value = guess;
      peaks = null;
      drawWave();
      readPeaks(f).then((p) => { if (file === f) { peaks = p; drawWave(); } });
    }

    async function readPeaks(f) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      const ctx = new Ctx();
      try {
        const audio = await ctx.decodeAudioData(await f.arrayBuffer());
        const n = 2000, len = audio.length, out = new Float32Array(n);
        for (let c = 0; c < audio.numberOfChannels; c++) {
          const d = audio.getChannelData(c);
          for (let i = 0; i < n; i++) {
            let m = out[i];
            for (let j = Math.floor(len * i / n), e = Math.floor(len * (i + 1) / n); j < e; j++) { const v = Math.abs(d[j]); if (v > m) m = v; }
            out[i] = m;
          }
        }
        const top = Math.max(1e-6, ...out);
        return Array.from(out, (v) => v / top);
      } catch {
        return null;
      } finally {
        ctx.close && ctx.close();
      }
    }

    function drawWave() {
      const c = $('wave');
      if ($('source-row').hidden) return;
      c.parentElement.hidden = !peaks;
      if (peaks) drawBars(c, peaks);
    }

    const readClock = (t) => pad(t / 60) + ':' + pad(t % 60) + '.' + Math.floor((t * 10) % 10);
    function updateClock() {
      const p = $('player'), dur = isFinite(p.duration) ? p.duration : 0;
      $('clock').textContent = readClock(p.currentTime) + ' / ' + readClock(dur);
      $('wave-head').style.left = (dur ? (p.currentTime / dur) * 100 : 0) + '%';
    }

    // ---- transcription ----
    function readBase64(f) {
      return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).replace(/^data:[^,]*,/, ''));
        r.onerror = () => reject(Object.assign(new Error('the browser could not read the file'), { status: -1 }));
        r.readAsDataURL(f);
      });
    }

    async function transcribe() {
      if (!file || busy) return;
      const f = file;
      const body = { source: $('source').value, include_midi: true };
      if ($('model').value) body.model = $('model').value;
      if ($('bends').checked) body.pitch_bends = true;
      const context = {};
      if ($('key').value.trim()) context.key = $('key').value.trim();
      if ($('tempo').value) context.tempo = Number($('tempo').value);
      if (Object.keys(context).length) body.context = context;

      busy = Math.random().toString(36).slice(2, 12);
      startedAt = performance.now();
      $('go').disabled = true;
      $('cancel').hidden = false;
      $('progress-row').hidden = false;
      $('rec').hidden = false;
      $('status').replaceChildren();
      setProgress(0, 'READING ' + f.name);
      camTimer = setInterval(() => cam(performance.now() - startedAt), 40);
      poll = setInterval(refreshHealth, 400);
      try {
        body.audio_base64 = await readBase64(f);
        setProgress(0, 'UPLOADING ' + (f.size / 1048576).toFixed(1) + ' MB');
        result = await api('/transcribe', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'X-Job-Id': busy } });
        result.source = body.source;
        result.pitch_bends = !!body.pitch_bends;
        result.file = f.name;
        result.base = baseName;
        selected = -1;
        showResult();
        const n = (result.notes || []).length;
        showDone(n + (n === 1 ? ' note' : ' notes') + ' from ' + f.name + '. Export when ready.');
      } catch (e) {
        if (/cancelled/i.test(e.message)) {
          toast('Cancelled. Nothing was written.');
        } else {
          showError(explain(e, f.name));
        }
      } finally {
        clearInterval(poll);
        clearInterval(camTimer);
        cam(performance.now() - startedAt);
        busy = null;
        $('go').disabled = !file;
        $('cancel').hidden = true;
        $('progress-row').hidden = true;
        $('rec').hidden = true;
      }
    }

    function showResult() {
      const notes = result.notes || [];
      const model = ((info && info.models) || []).find((m) => m.id === result.model);
      $('case-empty').hidden = true;
      $('case-file').hidden = false;
      $('placeholder-note').hidden = !(model && model.placeholder);
      $('case-title').textContent = 'CASE FILE: ' + result.base + '.' + result.source + '.mid';

      const sub = $('case-subject');
      sub.replaceChildren();
      const field = (label, value) => {
        const s = document.createElement('span');
        s.append(label + ' ');
        if (value == null || value === '') {
          const r = document.createElement('span');
          r.className = 'bt-redact';
          r.title = 'unknown';
          r.setAttribute('role', 'img');
          r.setAttribute('aria-label', 'unknown');
          s.append(r);
        } else {
          s.append(String(value));
        }
        sub.append(s);
      };
      const kind = result.source === 'drums' ? 'hits' : MONO.includes(result.source) ? 'mono' : 'poly';
      field('SUBJECT', result.source + ' (' + kind + ')');
      field('KEY', result.key);
      field('TEMPO', result.tempo != null ? Math.round(result.tempo * 10) / 10 : null);
      field('SOURCE', result.file);
      field('MODEL', result.model);

      const conf = notes.length ? notes.reduce((a, n) => a + (n.confidence ?? 0), 0) / notes.length : null;
      $('summary').textContent = 'NOTES ' + notes.length + '   CONF ' + (conf == null ? '-' : conf.toFixed(2)) + '   BENDS ' + (result.pitch_bends ? 'ON' : 'OFF');
      $('roll').setAttribute('aria-label', 'Piano roll: ' + notes.length + ' notes' + (conf == null ? '' : ', mean confidence ' + conf.toFixed(2)));
      $('tip').textContent = '';
      drawRoll();
      const wrap = $('roll-wrap');
      if (geom && notes.length) {
        const mid = notes.reduce((a, n) => a + geom.rowOf(n.pitch), 0) / notes.length;
        wrap.scrollTop = Math.max(0, mid * geom.rowH - wrap.clientHeight / 2);
      }
    }

    // ---- piano roll ----
    const roll = $('roll');
    let geom = null;

    function drawRoll() {
      if (!result) return;
      const notes = result.notes || [];
      const drums = result.source === 'drums';
      const css = getComputedStyle(roll.parentElement);
      const col = (n, fb) => (css.getPropertyValue(n).trim() || css.getPropertyValue(fb).trim());
      const player = $('player');
      const end = Math.max(1, ...notes.map((n) => n.end), isFinite(player.duration) ? player.duration : 0);
      const lo = Math.min(...notes.map((n) => n.pitch), 60) - 2, hi = Math.max(...notes.map((n) => n.pitch), 72) + 2;
      const lanes = drums ? [...new Set(notes.map((n) => n.pitch))].sort((a, b) => a - b) : null;
      const rows = drums ? Math.max(1, lanes.length) : hi - lo + 1;
      const rowH = 25;
      const gutter = drums ? 110 : 56;
      const wrapWidth = roll.parentElement.clientWidth || 600;
      const pxPerSec = Math.max((wrapWidth - gutter) / end, 40);
      const width = Math.ceil(gutter + end * pxPerSec);
      const height = rows * rowH;
      const dpr = window.devicePixelRatio || 1;
      roll.width = width * dpr; roll.height = height * dpr;
      roll.style.width = width + 'px'; roll.style.height = height + 'px';
      const g = roll.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = col('--bt-roll-bg', '--bt-surface-2'); g.fillRect(0, 0, width, height);
      const rowOf = (p) => drums ? lanes.indexOf(p) : hi - p;
      const grid = col('--bt-grid', '--bt-track'), strong = col('--bt-grid-strong', '--bt-ink-muted');

      // Beat lines from the tempo, stronger every 4 beats; seconds when the tempo is unknown.
      const beat = result.tempo ? 60 / result.tempo : 1;
      for (let i = 0, t = 0; t <= end; i++, t = i * beat) {
        g.fillStyle = i % 4 === 0 ? strong : grid;
        g.fillRect(Math.round(gutter + t * pxPerSec), 0, i % 4 === 0 ? 2 : 1, height);
      }
      g.font = css.font;
      g.textBaseline = 'middle';
      for (let r = 0; r < rows; r++) {
        const p = drums ? lanes[r] : hi - r;
        g.fillStyle = grid;
        g.fillRect(0, r * rowH + rowH - 1, width, 1);
        g.fillStyle = col('--bt-ink-muted', '--bt-ink');
        g.fillText(drums ? (DRUMS[p] || String(p)) : noteName(p), 6, r * rowH + rowH / 2 + 1);
      }
      g.fillStyle = strong;
      g.fillRect(gutter - 2, 0, 2, height);

      const ink = col('--bt-ink', '--bt-ink'), dim = col('--bt-note-dim', '--bt-track');
      notes.forEach((n, i) => {
        const x = Math.round(gutter + n.start * pxPerSec), w = Math.max(4, Math.round((n.end - n.start) * pxPerSec));
        const y = rowOf(n.pitch) * rowH + 3, h = rowH - 6;
        if ((n.confidence ?? 1) >= 0.7) {
          g.fillStyle = ink; g.fillRect(x, y, w, h);
        } else {
          g.fillStyle = dim; g.fillRect(x, y, w, h);
          g.strokeStyle = ink; g.lineWidth = 1; g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
        }
        if (i === selected) {
          g.strokeStyle = col('--bt-focus', '--bt-ink'); g.lineWidth = 3;
          g.strokeRect(x - 3.5, y - 3.5, w + 7, h + 7);
        }
      });
      if (!player.paused || player.currentTime > 0) {
        g.fillStyle = ink;
        g.fillRect(Math.round(gutter + player.currentTime * pxPerSec), 0, 2, height);
      }
      geom = { gutter, pxPerSec, rowH, rowOf, notes, drums };
    }

    function describe(n) {
      const name = geom.drums ? (DRUMS[n.pitch] || n.pitch) : noteName(n.pitch);
      return name + '  ' + n.start.toFixed(2) + '-' + n.end.toFixed(2) + ' S  VEL ' + n.velocity + '  CONF ' + (n.confidence ?? 0).toFixed(2);
    }

    roll.addEventListener('mousemove', (e) => {
      if (!geom) return;
      const rect = roll.getBoundingClientRect();
      const t = (e.clientX - rect.left - geom.gutter) / geom.pxPerSec;
      const row = Math.floor((e.clientY - rect.top) / geom.rowH);
      const n = geom.notes.find((n) => geom.rowOf(n.pitch) === row && t >= n.start && t <= Math.max(n.end, n.start + 0.05));
      $('tip').textContent = n ? describe(n) : (selected >= 0 ? describe(geom.notes[selected]) : '');
    });
    roll.addEventListener('click', (e) => {
      if (!geom || !file) return;
      const rect = roll.getBoundingClientRect();
      $('player').currentTime = Math.max(0, (e.clientX - rect.left - geom.gutter) / geom.pxPerSec);
    });
    // Keyboard: arrows step through the notes in time order and show each one's numbers.
    roll.addEventListener('keydown', (e) => {
      if (!geom || !geom.notes.length) return;
      const order = geom.notes.map((n, i) => i).sort((a, b) => geom.notes[a].start - geom.notes[b].start || geom.notes[b].pitch - geom.notes[a].pitch);
      let k = order.indexOf(selected);
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') k = Math.min(order.length - 1, k + 1);
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') k = Math.max(0, k < 0 ? 0 : k - 1);
      else if (e.key === 'Home') k = 0;
      else if (e.key === 'End') k = order.length - 1;
      else return;
      e.preventDefault();
      selected = order[k];
      drawRoll();
      const n = geom.notes[selected];
      $('tip').textContent = describe(n);
      const wrap = $('roll-wrap');
      const x = geom.gutter + n.start * geom.pxPerSec, y = geom.rowOf(n.pitch) * geom.rowH;
      if (x < wrap.scrollLeft + geom.gutter || x > wrap.scrollLeft + wrap.clientWidth - 40) wrap.scrollLeft = x - wrap.clientWidth / 3;
      if (y < wrap.scrollTop || y > wrap.scrollTop + wrap.clientHeight - geom.rowH) wrap.scrollTop = y - wrap.clientHeight / 2;
    });

    // ---- playback ----
    const player = $('player');
    let raf = 0;
    const tick = () => { updateClock(); drawRoll(); raf = player.paused ? 0 : requestAnimationFrame(tick); };
    player.addEventListener('play', () => { $('play').textContent = '[ Pause ]'; if (!raf) raf = requestAnimationFrame(tick); });
    player.addEventListener('pause', () => { $('play').textContent = '[ Play ]'; updateClock(); });
    player.addEventListener('ended', () => { $('play').textContent = '[ Play ]'; });
    player.addEventListener('seeked', () => { updateClock(); drawRoll(); });
    player.addEventListener('loadedmetadata', () => { updateClock(); drawRoll(); });
    $('play').addEventListener('click', () => { if (player.paused) player.play().catch(() => {}); else player.pause(); });
    $('wave-wrap').addEventListener('click', (e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      if (isFinite(player.duration)) player.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * player.duration;
    });

    // ---- downloads ----
    function download(name, bytes, type) {
      const url = URL.createObjectURL(new Blob([bytes], { type }));
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    $('dl-midi').addEventListener('click', () => {
      if (!result || !result.midi_base64) return;
      const bin = atob(result.midi_base64), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const name = result.base + '.' + result.source + '.mid';
      download(name, bytes, 'audio/midi');
      toast('Saved ' + name + '.');
    });
    $('dl-json').addEventListener('click', () => {
      if (!result) return;
      const { midi_base64, base, file: _f, ...rest } = result;
      const name = result.base + '.' + result.source + '.json';
      download(name, JSON.stringify(rest, null, 2), 'application/json');
      toast('Saved ' + name + '.');
    });

    // ---- wiring ----
    const drop = $('drop');
    $('file').addEventListener('change', () => pickFile($('file').files[0]));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.dataset.over = 'true'; });
    drop.addEventListener('dragleave', () => { delete drop.dataset.over; });
    drop.addEventListener('drop', (e) => { e.preventDefault(); delete drop.dataset.over; pickFile(e.dataTransfer.files[0]); });
    $('go').addEventListener('click', transcribe);
    $('cancel').addEventListener('click', () => {
      if (busy) api('/cancel', { method: 'POST', body: JSON.stringify({ job_id: busy }), headers: { 'Content-Type': 'application/json' } }).catch(() => {});
    });
    let resizeTimer = 0;
    const redraw = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { drawWave(); drawRoll(); }, 60); };
    window.addEventListener('resize', redraw);
    window.addEventListener('bt-theme', redraw);
    document.fonts && document.fonts.ready.then(() => drawRoll());
  }

  // ---- start ----
  mount();
  $('token').value = session.get('backtrack-token');
  $('token').addEventListener('change', () => { session.set('backtrack-token', $('token').value.trim()); loadInfo(); });
  refreshHealth().then(loadInfo);
  setInterval(refreshHealth, 5000);
})();
