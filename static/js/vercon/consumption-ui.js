import { ComboTree } from '../components/combotree/combotree.js';
import { el, docNode } from './ui.js';
import { format, formatMoney } from './fields.js';
import { decimalText } from './decimal-text.js';

export function renderConsumptionStock(panel, data) {
  const quantity = value => `${decimalText(value, data.zec)} ${data.um || ''}`.trim();
  panel.replaceChildren(el('h4', { textContent: 'Stoc produs' }),
    el('p', { className: 'vc-receipt-card__identity', textContent: data.name, title: data.name }),
    el('p', { className: 'vc-receipt-card__note', textContent: `La data ${format(data.date, { type: 'date' })} • Preț cu TVA: ${decimalText(data.price, data.zec)} lei` }),
    el('dl', { className: 'vc-bc-stock-summary' },
      el('dt', { textContent: 'Total intrări' }), el('dd', { textContent: quantity(data.incoming) }),
      el('dt', { textContent: 'Total ieșiri' }), el('dd', { textContent: quantity(data.outgoing) })),
    el('footer', { className: 'vc-receipt-card__total', textContent: `Stoc disponibil: ${quantity(data.closing)}` }));
}

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];

export function consumptionBranch(receiver, date) {
  const party = encodeURIComponent(receiver || '—');
  return [`receiver:${party}`, `receiver-month:${party}|${String(date || '').slice(0, 7)}`];
}

export function consumptionNodes(documents) {
  const receivers = new Map();
  for (const doc of documents) {
    const receiver = doc.receiver || '—', month = String(doc.date || '').slice(0, 7);
    if (!receivers.has(receiver)) receivers.set(receiver, new Map());
    const months = receivers.get(receiver);
    if (!months.has(month)) months.set(month, []);
    months.get(month).push(doc);
  }
  return [...receivers].map(([receiver, months]) => ({
    id: `receiver:${encodeURIComponent(receiver)}`, label: receiver,
    children: [...months].sort(([a], [b]) => b.localeCompare(a)).map(([month, docs]) => ({
      id: `receiver-month:${encodeURIComponent(receiver)}|${month}`,
      label: month ? `${MONTHS[Number(month.slice(5)) - 1]}/${month.slice(0, 4)}` : 'Fără dată',
      sortType: 'date', sortValue: month ? `${month}-01` : null, sortDirection: 'desc',
      children: docs.map(doc => docNode(doc,
        [format(doc.date, { type: 'date' }), doc.number, doc.annulled ? 'anulat' : ''].filter(value => value !== '' && value != null).join(' • '),
        formatMoney(doc.total))),
    })),
  }));
}

// One account per BC, selected from the same tree control used by NIR.
export function consumptionCategory(owner, spec, value, editable, onChange, onTab) {
  const node = el('label', { className: 'vc-field vc-receipt-accounts', 'data-field': spec.key },
    el('span', { textContent: spec.label + (editable ? ' *' : '') }));
  const host = el('div'), input = el('input', { type: 'hidden', value: value == null ? '' : String(value) });
  node.append(host, input);
  let message = '', pending = null;
  const combo = owner.own(new ComboTree(host, {
    nodes: [{ id: 'categories', label: 'Categorii / conturi', children: spec.options.map(item => ({ id: String(item.value), label: item.label })) }],
    expanded: ['categories'], placeholder: 'Alegeți categoria…', onTab,
    onSelect: selection => {
      input.value = selection.id;
      error.textContent = '';
      pending = Promise.resolve(onChange(selection.id)).catch(problem => { error.textContent = problem.message; });
      pending.finally(() => { pending = null; });
    },
  }));
  combo.setValue(input.value, spec.options.find(item => String(item.value) === input.value)?.label || spec.fallback || '');
  combo.disabled = !editable;
  combo.input.disabled = !editable;
  combo.input.setAttribute('aria-label', spec.label);
  combo.arrow.setAttribute('aria-disabled', String(!editable));
  const error = {
    get textContent() { return message; },
    set textContent(text) { message = text || ''; combo.input.classList.toggle('is-invalid', !!message); node.title = message; },
  };
  owner.own({
    get hasEdit() { return !!pending; },
    async commitEdit() { await pending; return !message; },
    validate() { error.textContent = editable && !input.value ? 'Alegeți o categorie.' : message; return { ok: !message }; },
  });
  return { node, input, error };
}
