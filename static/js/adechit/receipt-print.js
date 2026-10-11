// The print view has a manual retry button in case the browser blocks the automatic dialog.
document.getElementById('receipt-print').addEventListener('click', () => window.print());
window.addEventListener('load', async () => { await document.fonts.ready; window.print(); }, { once: true });
