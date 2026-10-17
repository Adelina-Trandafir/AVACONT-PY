import { consumptionCategory, consumptionNodes } from './consumption-ui.js';
import { format } from './fields.js';

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
    groups.get(account).children.push({ id: String(held ? index : item.id),
      label: `${item.name} / ${format(held ? item.PUTVA : item.putva, { type: 'decimal' })} lei / ${details}` });
  });
  return [...groups.values()];
}

export function oiDocumentNodes(documents, family) {
  return consumptionNodes(documents.map(doc => ({ ...doc,
    receiver: family === 'bt' ? doc.old : doc.holder,
    number: `${family.toUpperCase()} ${doc.number}${family === 'bt' ? ' → ' + doc.holder : ''}`,
  })));
}
