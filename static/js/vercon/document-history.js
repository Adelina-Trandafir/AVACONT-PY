import { el, modal, snapshotPreview } from './ui.js';
import { formatMoney } from './fields.js';

const OPERATIONS = {
  'bc.update': 'Modificare bon de consum',
  'bc.stock_recalculated': 'Recalculare automată stoc',
  'lista.bc_update': 'Recalculare după modificare BC',
  'lista.balance_recalculated': 'Recalculare automată DEP/EC',
  'lista.portions_update': 'Modificare număr copii / porții',
  'lista.date_update': 'Modificare dată listă de alimente',
  'lista.update': 'Modificare listă de alimente',
};
const LABELS = { number: 'Număr', date: 'Data', name: 'Produs', variant: 'Variantă', quantity: 'Cantitate',
  day_closing_stock: 'Stoc la sfârșitul zilei bonului', prior_balance: 'Sold anterior',
  daily_balance: 'DEP/EC pe zi', closing_balance: 'Sold final', list_value: 'Valoare listă', bons_total: 'Total BC',
  ValoareBon: 'Total BC', ValoareLista: 'Valoare listă', DEP: 'Diferență listă − BC',
  NumarBC: 'Număr BC', ValoareBC: 'Valoare BC', total: 'Total', meals: 'Mese', header: 'Antet',
  Cant: 'Cantitate', PUTVA: 'Preț cu TVA', ValoareCUTVA: 'Valoare cu TVA', IdProdus: 'Variantă',
  line_details: 'Linii', lines: 'Linii', MicDejun_P: 'Porții mic dejun', MicDejun_V: 'Valoare mic dejun',
  Dejun_P: 'Porții dejun', Dejun_V: 'Valoare dejun', Cina_P: 'Porții cină/gustare', Cina_V: 'Valoare cină/gustare' };

function decode(value) {
  if (typeof value !== 'string' || !/^\s*[\[{]/.test(value)) return value;
  try { return JSON.parse(value); } catch { return value; }
}

function details(raw) {
  const value = decode(raw);
  if (value == null) return '';
  if (Array.isArray(value)) return value.map(details).join('\n');
  if (typeof value !== 'object') return String(value);
  return Object.entries(value).filter(([key]) => !['reason', 'cause'].includes(key)).map(([key, item]) => {
    const balance = ['prior_balance', 'daily_balance', 'closing_balance'].includes(key);
    const rendered = balance && item != null
      ? `${String(item).startsWith('-') ? 'Economii' : 'Depășiri'} ${formatMoney(String(item).replace(/^-/, ''))}`
      : details(item);
    return `${LABELS[key] || key}: ${rendered}`;
  }).join('\n');
}

export async function showDocumentHistory({ api, url, title, owner, isCurrent = () => true }) {
  const result = await api(url);
  if (!isCurrent()) return;
  const rows = [...result.history].reverse().map(row => {
    const after = decode(row.after);
    return { ...row, operation: OPERATIONS[row.operation] || row.operation,
      reason: after?.reason || after?.cause?.reason || '', before: details(row.before), after: details(row.after) };
  });
  const sections = [{ title: 'Operații și motivele recalculării', columns: [
    { key: 'at', title: 'Data', width: 170 }, { key: 'user', title: 'Utilizator', width: 160 },
    { key: 'operation', title: 'Operație', width: 210 }, { key: 'reason', title: 'De ce s-a recalculat', width: 340 },
    { key: 'before', title: 'Înainte', width: 350 }, { key: 'after', title: 'După', width: 350 },
  ], rows }];
  const view = modal(title, owner, { report: true });
  view.dialog.classList.add('vc-history');
  const records = el('div', { className: 'vc-history__records' });
  for (const [index, row] of rows.entries()) {
    records.append(el('details', { open: index === 0 },
      el('summary', { textContent: [row.at, row.operation, row.user].filter(Boolean).join(' • ') }),
      ...(row.reason ? [el('p', { textContent: row.reason })] : []),
      el('div', { className: 'vc-history__values' },
        el('section', {}, el('h4', { textContent: 'Înainte' }), el('pre', { textContent: row.before || '—' })),
        el('section', {}, el('h4', { textContent: 'După' }), el('pre', { textContent: row.after || '—' })))));
  }
  if (!rows.length) records.append(el('p', { textContent: 'Nu există operații înregistrate în istoric.' }));
  view.body.append(records);
  const close = el('button', { type: 'button', textContent: 'Închide' });
  close.addEventListener('click', view.close);
  const pdf = el('button', { type: 'button', textContent: 'Previzualizare PDF' });
  pdf.addEventListener('click', async () => {
    pdf.disabled = true;
    try { await snapshotPreview(title, sections, view.scope, () => !view.closed); }
    catch (error) { if (!view.closed) view.body.append(el('p', { role: 'alert', textContent: error.message })); }
    finally { if (!view.closed) pdf.disabled = false; }
  });
  view.footer.append(close, pdf);
  return view;
}
