import { ComboTree } from '../components/combotree/combotree.js';
import { el } from './ui.js';
import { numberText } from './receipt-number.js';
import { format } from './fields.js';
import { decimalText } from './decimal-text.js';

export function sourceLabel(doc) {
  return `NIR ${numberText(doc)} • ${doc.invoice || ''} • ${format(doc.date, { type: 'date' })}`;
}

export function stornoSourceField(owner, sources, value, fallback, editable, onChange, onTab) {
  const node = el('label', { className: 'vc-field', 'data-field': 'source' },
    el('span', { textContent: `Recepția stornată${editable ? ' *' : ''}` }));
  const host = el('div'), input = el('input', { type: 'hidden', value: value == null ? '' : String(value) });
  node.append(host, input);
  const suppliers = new Map();
  for (const doc of sources) {
    const id = String(doc.party_cf ?? '');
    if (!suppliers.has(id)) suppliers.set(id, { id: `supplier:${id}`, label: doc.party_cf ? doc.party || doc.party_cf : 'Fără cod fiscal', children: [] });
    suppliers.get(id).children.push({ id: String(doc.id), label: sourceLabel(doc) });
  }
  const combo = owner.own(new ComboTree(host, { nodes: [...suppliers.values()], placeholder: 'Alegeți NIR-ul…',
    minWidth: 480, selectParents: false, onTab,
    onSelect: selection => {
      input.value = String(selection.id);
      error.textContent = '';
      const pending = Promise.resolve(onChange(Number(selection.id)));
      combo._sourceChange = pending;
      pending.catch(() => {}).finally(() => { if (combo._sourceChange === pending) combo._sourceChange = null; });
    } }));
  const selected = sources.find(doc => String(doc.id) === input.value);
  combo.setValue(input.value, selected ? sourceLabel(selected) : fallback || '');
  combo.setEnabled(editable);
  combo.arrow.setAttribute('aria-disabled', String(!editable));
  combo.input.setAttribute('aria-label', 'Recepția stornată');
  const error = { set textContent(message) { node.title = message || ''; combo.input.classList.toggle('is-invalid', !!message); } };
  combo.validate = () => { const ok = !editable || !!input.value; error.textContent = ok ? '' : 'Alegeți recepția stornată.'; return { ok }; };
  Object.defineProperty(combo, 'hasEdit', { get: () => !!combo._sourceChange });
  combo.commitEdit = async () => { try { await combo._sourceChange; return true; } catch { return false; } };
  return { node, input, error };
}

export function stornoProductKey(line) {
  return JSON.stringify([String(line.product ?? `variant:${line.variant}`), decimalText(line.PUTVA ?? '0', 4)]);
}

export function stornoProductNodes(eligible, categories, selected = []) {
  const used = new Set(selected.filter(line => line.variant != null).map(stornoProductKey));
  const lines = eligible.filter(line => !used.has(stornoProductKey(line)));
  return categories.map(category => ({ id: `category:${category.id}`, label: category.label,
    children: lines.filter(line => String(line.category) === String(category.id)).map(productNode) }))
    .filter(node => node.children.length)
    .concat(lines.some(line => !categories.some(category => String(category.id) === String(line.category)))
      ? [{ id: 'category:other', label: 'Produse din recepție', children: lines.filter(line => !categories.some(category => String(category.id) === String(line.category))).map(productNode) }] : []);
}

function productNode(line) {
  return { id: String(line.variant), label: `${line.name} • ${format(line.PUTVA, { type: 'decimal' })} lei • disponibil ${format(line.left, { type: 'decimal' })} ${line.um}` };
}

export function calculateStorno(line, decimals) {
  const quantity = line.Cant == null || line.Cant === '' ? null : Number(String(line.Cant).replace(',', '.'));
  const net = quantity != null && line.Pret != null ? Number((quantity * Number(line.Pret)).toFixed(decimals)) : null;
  const gross = quantity != null && line.PUTVA != null ? Number((quantity * Number(line.PUTVA)).toFixed(decimals)) : null;
  line.Valoare = net == null ? null : net.toFixed(decimals);
  line.ValoareCUTVA = gross == null ? null : gross.toFixed(decimals);
  line.TVA = gross == null || net == null ? null : (gross - net).toFixed(decimals);
  return line;
}
