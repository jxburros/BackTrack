// BackTrack waveform: hard-edged bars, no smoothing, no rounded caps. Color comes from CSS `color`.
// peaks: numbers 0..1 (max |sample| per bucket, any length). Redraw on resize.
function drawBars(canvas, peaks, { barWidth = 3, gap = 2, minHeight = 2 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = getComputedStyle(canvas).color;
  const n = Math.max(1, Math.floor(w / (barWidth + gap)));
  for (let i = 0; i < n; i++) {
    const a = peaks.length * i / n, b = peaks.length * (i + 1) / n;
    let p = 0;
    for (let j = Math.floor(a); j < Math.max(Math.floor(a) + 1, Math.ceil(b)); j++) p = Math.max(p, peaks[j] || 0);
    const bh = Math.max(minHeight, Math.round(p * h));
    ctx.fillRect(Math.round(i * (barWidth + gap)), Math.round((h - bh) / 2), barWidth, bh);
  }
}
