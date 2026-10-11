// SLICE-ADE9-01: the report page toolbar. Nothing prints by itself: the report opens on the screen only.
const $ = (id) => document.getElementById(id);
$('report-print').addEventListener('click', () => window.print());
$('report-printer').addEventListener('click', () => window.print());   // the browser cannot pick a printer silently: its dialog is where it is chosen
$('report-back').addEventListener('click', () => window.close());
$('report-pdf').addEventListener('click', () => { if (window.adePdf) window.adePdf(); else window.print(); });

let k_zoom = 100;
function applyZoom(k_step) {
  k_zoom = Math.min(250, Math.max(50, k_zoom + k_step));
  for (const k_part of document.querySelectorAll('main, .rpt-footer')) k_part.style.zoom = String(k_zoom / 100);
  $('report-zoom-level').textContent = k_zoom + '%';
}
$('report-zoom-in').addEventListener('click', () => applyZoom(10));
$('report-zoom-out').addEventListener('click', () => applyZoom(-10));

// Switching to another browser tab closes the preview (it only ever belongs to the screen it was opened from).
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') window.close(); });
