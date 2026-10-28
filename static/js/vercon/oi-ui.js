import { numberValue } from './receipt-number.js';
import { consumptionCategory } from './consumption-ui.js';
import { docNode } from './ui.js';
import { format, formatMoney } from './fields.js';

export function oiAccount(owner, spec, value, enabled, onChange, onTab) {
  return consumptionCategory(owner, spec, value, enabled, onChange, onTab);
}

export function oiProductNodes(items, categories, held = false) {
  const groups = new Map();
  items.forEach((item, index) => {
    const account = held ? item.IdMasina : item.category;
    if (!groups.has(account)) groups.set(account, {
      id: `oi-account:${account}`, label: categories.find(row => String(row.id) === String(account))?.label || item.account || 'Fără cont', children: [],
    });
    const details = held
      ? `lot ${item.origin} / ${format(item.date, { type: 'date' })} / disponibil ${format(item.left, { type: 'decimal' })}`
      : item.code || item.id;
    groups.get(account).children.push({ id: String(held ? item.choiceIndex ?? index : item.id),
      label: `${item.name} / ${format(held ? item.PUTVA : item.putva, { type: 'decimal' })} lei / ${details}` });
  });
  return [...groups.values()];
}

export function oiDocumentNodes(documents, family) {
  const holders = new Map();
  for (const doc of documents) {
    const holder = doc.holder_id ?? doc.holder ?? '—';
    if (!holders.has(holder)) holders.set(holder, []);
    holders.get(holder).push(doc);
  }
  return [...holders].map(([holder, docs]) => ({
    id: `receiver:${encodeURIComponent(holder)}`, label: docs[0].holder || '—',
    children: [...docs].sort((a, b) => numberValue(b.number) - numberValue(a.number)
      || String(b.date || '').localeCompare(String(a.date || '')) || b.id - a.id)
      .map(doc => docNode(doc, `${doc.number}${doc.annulled ? ' • anulat' : ''}`, formatMoney(doc.total))),
  }));
}
