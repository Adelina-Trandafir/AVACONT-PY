import { el, scope, field } from './ui.js';
import { format } from './fields.js';

// Read-only details follow the selected saved NIR line, outside the ordinary grid.
export function receiptMfInfo(host) {
  const owner = scope();
  const fields = el('div', { className: 'vc-receipt-mf-info__fields' });
  const panel = el('section', { className: 'vc-receipt-mf-info', hidden: true },
    el('h4', { textContent: 'Informații Mijloc Fix' }), fields);
  host.append(panel);
  const specs = [
    { key: 'code', label: 'Număr inventar' },
    { key: 'data_intrarii', label: 'Data intrare', type: 'date' },
    { key: 'DNF', label: 'Durata normală folosire', type: 'int' },
    { key: 'DF', label: 'Durata folosită', type: 'int' },
    { key: 'ValoareLuna', label: 'Valoare lunară', type: 'decimal' },
  ];
  let visible = false;
  function select(row) {
    if (!visible) return;
    owner.clear(); fields.replaceChildren();
    for (const spec of specs) {
      const value = row?.[spec.key];
      const text = value == null || value === '' ? '' : spec.key === 'ValoareLuna'
        ? new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(Number(value))
        : format(value, { type: spec.type || 'text' }) + (['DNF', 'DF'].includes(spec.key) ? ' Luni' : '');
      const textAlign = ['code', 'data_intrarii'].includes(spec.key) ? 'center' : 'right';
      fields.append(field({ key: spec.key, label: spec.label, type: 'text', textAlign }, text, false, () => {}, owner).node);
    }
  }
  return {
    select,
    setVisible(on) { visible = on; panel.hidden = !on; owner.clear(); fields.replaceChildren(); },
    destroy() { owner.clear(); panel.remove(); },
  };
}
