// Shows which app is starting, and why it failed if it did.
(() => {
  const THEMES = { surveil: 'wiretap', explode: 'blast-zone' };
  const $ = (id) => document.getElementById(id);
  const invoke = window.__TAURI_INTERNALS__.invoke;

  async function poll() {
    const [app] = await invoke('status');
    if (!app) return;
    document.documentElement.dataset.theme = THEMES[app.id] || 'xerox-riot';
    document.title = app.name;
    $('title').textContent = app.id.toUpperCase();
    if (app.state === 'failed') {
      $('state').textContent = 'FAILED';
      $('error').textContent = "COULDN'T START " + app.id.toUpperCase() + '. ' + app.error + ' Close this window and open ' + app.name + ' again.';
      $('error').hidden = false;
      return;
    }
    $('state').textContent = app.state === 'ready' ? 'OPENING' : 'STARTING • LOADING THE MODELS';
    setTimeout(poll, 300);
  }
  poll();
})();
