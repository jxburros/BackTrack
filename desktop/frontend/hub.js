// The BackTrack hub: a tile per app, and each installed app's own page in a frame, kept alive
// while you switch. A track dropped here goes to the app whose tile you pick.
(() => {
  const $ = (id) => document.getElementById(id);
  const invoke = window.__TAURI_INTERNALS__.invoke;
  const RELEASES = 'github.com/jxburros/BackTrack/releases';
  const tiles = {}, frames = {}, ready = {};
  let apps = [], file = null, pending = {};

  // ---- views: the hub, or one app's frame ----
  function show(view) {
    const known = view === 'hub' || view === 'train' || apps.some((a) => a.id === view && a.state !== 'missing');
    if (!known) view = 'hub';
    $('view-hub').hidden = view !== 'hub';
    $('view-train').hidden = view !== 'train';
    for (const [id, f] of Object.entries(frames)) f.hidden = id !== view;
    for (const a of document.querySelectorAll('.bt-appbar a')) {
      if (a.dataset.view === view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  }
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));

  function frameFor(app) {
    if (frames[app.id] || app.state !== 'ready') return frames[app.id];
    const f = document.createElement('iframe');
    f.className = 'hub-frame';
    f.title = app.name;
    f.src = app.url;
    f.hidden = true;
    document.body.append(f);
    frames[app.id] = f;
    return f;
  }

  // ---- a track for an app: sent once its page says it is ready ----
  function hand(id) {
    const f = frames[id];
    if (!pending[id] || !ready[id] || !f) return;
    f.contentWindow.postMessage({ type: 'backtrack-file', file: pending[id] }, new URL(f.src).origin);
    delete pending[id];
  }
  window.addEventListener('message', (e) => {
    const id = Object.keys(frames).find((k) => frames[k].contentWindow === e.source);
    if (!id || !e.data) return;
    if (e.data.type === 'backtrack-ready') {
      ready[id] = true;
      e.source.postMessage({ type: 'backtrack-hub' }, e.origin);
      hand(id);
    }
    // A download from inside a frame doesn't reach the desktop's save dialog; start it here.
    if (e.data.type === 'backtrack-download') {
      const url = URL.createObjectURL(new Blob([e.data.bytes], { type: e.data.mime }));
      const a = document.createElement('a');
      a.href = url; a.download = String(e.data.name || 'download');
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  });

  function open(app) {
    if (app.state === 'missing' || app.state === 'failed') return;
    if (file) { pending[app.id] = file; hand(app.id); }
    show(app.id);
  }

  // ---- tiles ----
  function render() {
    const nav = document.querySelectorAll('.bt-appbar a[data-view]');
    for (const a of nav) {
      const app = apps.find((x) => x.id === a.dataset.view);
      if (app) a.hidden = app.state === 'missing';
    }
    for (const app of apps) {
      let t = tiles[app.id];
      if (!t) {
        t = $('tile').content.firstElementChild.cloneNode(true);
        t.classList.add('bt-tile--' + app.id);
        t.href = '#' + app.id;
        t.querySelector('.bt-tile__name').textContent = app.id.toUpperCase();
        t.querySelector('.bt-tile__line').textContent = app.line;
        t.addEventListener('click', (e) => { e.preventDefault(); open(apps.find((x) => x.id === app.id)); });
        $('tiles').append(t);
        tiles[app.id] = t;
      }
      const state = t.querySelector('.hub-tile-state'), note = t.querySelector('.hub-tile-note');
      t.dataset.state = app.state;
      note.textContent = '';
      if (app.state === 'missing') {
        state.textContent = 'NOT INSTALLED';
        note.textContent = 'Install ' + app.name + ' from ' + RELEASES + ', then open BackTrack again.';
        t.setAttribute('aria-disabled', 'true');
      } else if (app.state === 'failed') {
        state.textContent = "COULDN'T START";
        note.textContent = app.error;
        t.setAttribute('aria-disabled', 'true');
      } else {
        state.textContent = app.state === 'ready' ? 'READY →' : 'STARTING';
        t.removeAttribute('aria-disabled');
      }
      frameFor(app);
      hand(app.id);
    }
  }

  async function poll() {
    apps = await invoke('status');
    render();
    if (location.hash.length > 1 && location.hash !== '#hub') show(location.hash.slice(1));
    if (apps.some((a) => a.state === 'starting')) setTimeout(poll, 300);
  }

  // ---- the drop zone ----
  function pick(f) {
    if (!f) return;
    if (!/\.(wav|flac|mp3)$/i.test(f.name)) {
      $('drop-text').textContent = "COULDN'T READ " + f.name + '. Use WAV, FLAC or MP3.';
      file = null;
      return;
    }
    file = f;
    const names = apps.filter((a) => a.state !== 'missing').map((a) => a.id.toUpperCase());
    $('drop-text').textContent = f.name + ' / ' + (f.size / 1048576).toFixed(1) + ' MB — now pick ' + (names.join(' or ') || 'an app');
  }
  const drop = $('drop');
  $('file').addEventListener('change', () => pick($('file').files[0]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.dataset.over = 'true'; });
  drop.addEventListener('dragleave', () => { delete drop.dataset.over; });
  drop.addEventListener('drop', (e) => { e.preventDefault(); delete drop.dataset.over; pick(e.dataTransfer.files[0]); });

  show('hub');
  poll();
})();
