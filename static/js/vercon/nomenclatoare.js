// Grid-only nomenclatures; settings retain their dedicated form.
export { mountNomenclator } from './nomenclator-grid.js';
import { parse } from './fields.js';
import { el, scope, field, column, grid, showMessage, statusBar } from './ui.js';
// Storage is always DECIMAL(20,4); ZEC controls calculation/display precision.
export function mountSettings(host, opts) {
  const ui = scope(), inputs = new Map(); let disposed = false, data, dirty = false;
  const status = el('span', { className: 'vc-save-status', role: 'status' }), fields = el('div', { className: 'vc-grid' }), note = el('p', { className: 'vc-hint' }), counters = el('div');
  const saveButton = el('button', { type: 'submit', textContent: 'Salvează', className: 'is-primary' });
  const form = el('form', { noValidate: true }, fields, note, statusBar(el('div', { className: 'vc-actions' }), { status, end: [saveButton] }), counters);
  const root = el('section', { className: 'vc-nom' }, el('h3', { textContent: 'Setări gestiune' }), form); host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const specs = [{ key: 'zec', label: 'Zecimale afișate și de calcul (0–4)', type: 'int', required: true, min: 0 }, { key: 'scara', label: 'Zecimale stocate (fix 4)', type: 'int', required: true, min: 4 }];
  function paint() {
    ui.clear(); inputs.clear(); fields.replaceChildren(); counters.replaceChildren(); const editable = data.can_write; saveButton.hidden = !editable;
    for (const spec of specs) { const control = field(spec, data[spec.key], editable && spec.key === 'zec', () => { dirty = true; }, ui); inputs.set(spec.key, control); fields.append(control.node); }
    note.textContent = 'Valorile se stochează cu maximum 4 zecimale. Zecimalele afișate și de calcul nu modifică valorile deja salvate.';
    grid(counters, { layoutId: 'vercon.settings.counters', rowKey: 'key', rows: data.settings, editable: false, footer: false, columns: [
      column({ key: 'key', label: 'Setare' }, { width: 320, editable: false }), column({ key: 'value', label: 'Valoare', type: 'decimal' }, { editable: false }),
    ] }, ui); dirty = false;
  }
  async function load() { const query = new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== '')); const response = await opts.api('/api/vercon/settings?' + query); if (disposed) return; data = response; paint(); }
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); if (!data?.can_write || !await ui.commit()) return; const values = {}; let valid = true;
    for (const spec of specs) {
      const result = parse(inputs.get(spec.key).input.value, spec); const message = !result.ok ? result.message : result.value > 4 ? 'Valoarea maximă este 4.' : '';
      inputs.get(spec.key).error.textContent = message; if (message) valid = false; values[spec.key] = result.value;
    }
    if (valid && values.scara !== 4) { inputs.get('scara').error.textContent = 'Precizia de stocare este fixă la 4.'; valid = false; }
    if (!valid) { say('Corectați câmpurile marcate.', true); return; } root.inert = true; say('Se salvează…');
    try { const sel = opts.sel(); await opts.api('/api/vercon/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, ...values }) }); if (disposed) return; dirty = false; await load(); say('Salvat. Reîncărcați pagina pentru aplicarea zecimalelor de calcul și afișare.'); }
    catch (error) { if (!disposed && error.name !== 'AbortError') { for (const [key, text] of Object.entries(error.fields || {})) if (inputs.has(key)) inputs.get(key).error.textContent = text; say(error.message || 'Salvarea a eșuat.', true); } }
    finally { if (!disposed) root.inert = false; }
  });
  return { ready: load(), isDirty: () => dirty || ui.dirty(), destroy: () => { disposed = true; ui.clear(); host.replaceChildren(); } };
}
