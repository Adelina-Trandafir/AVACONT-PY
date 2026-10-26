// Two levels in every saved-document tree: month or party, then documents.
import { CheckBox, docNode } from './ui.js';
import { format, formatMoney } from './fields.js';
import { numberText } from './receipt-number.js';
import { receiptTotal } from './receipt-reconciliation.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie',
  'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const alphabet = new Intl.Collator('ro', { sensitivity: 'base' });
export const monthKey = date => /^\d{4}-(0[1-9]|1[0-2])/.test(String(date || '')) ? String(date).slice(0, 7) : 'undated';
export const documentMonthId = month => `document-month:${month}`;
export const documentMonthLabel = month => month === 'undated' ? 'Fără dată' : `${MONTHS[Number(month.slice(5)) - 1]} / ${month.slice(0, 4)}`;
export const documentPartyId = (party, field = 'party') => `${field}:${encodeURIComponent(party ?? '')}`;
const partyKey = (doc, field) => field === 'holder' ? doc.holder_id ?? doc.holder ?? '' : field === 'party' ? doc.party_cf ?? '' : doc[field] || '';

export function documentLeaf(doc) {
  const label = `${doc.date ? format(doc.date, { type: 'date' }) : '—'} / ${numberText(doc) ?? '—'}`
    + (doc.source_missing ? ' • Fără bon sursă' : '') + (doc.annulled ? ' • anulat' : '');
  return { ...docNode(doc, label, formatMoney(doc.total)), tooltip: label, bold: !!doc.has_efactura, italic: !!doc.has_efactura,
    sortType: 'date', sortValue: doc.date, sortDirection: 'desc',
    sortThen: [{ type: 'number', value: doc.number }, { type: 'number', value: doc.sub_number || 0 }, { type: 'number', value: doc.id }] };
}

export function documentTreeOrder({ party = 'party', rootTotals = false, initialByDate = true } = {}) {
  let byDate = initialByDate, checkbox = null, changing = false, enabled = true;
  const branch = doc => byDate ? documentMonthId(monthKey(doc.date)) : documentPartyId(partyKey(doc, party), party);
  return {
    get byDate() { return byDate; },
    // A new default (another kind of gestiune): the box is drawn again from it when the tree is mounted.
    reset(value) { byDate = value; },
    branch,
    nodes(documents) {
      const groups = new Map();
      for (const doc of documents) {
        const id = branch(doc);
        if (!groups.has(id)) groups.set(id, { id,
          label: byDate ? documentMonthLabel(monthKey(doc.date)) : party === 'party' && !doc.party_cf ? 'Fără cod fiscal' : doc[party] || '—', documents: [],
          ...(byDate ? { sortType: 'date', sortValue: doc.date ? doc.date.slice(0, 7) + '-01' : null, sortDirection: 'desc' } : {}) });
        groups.get(id).documents.push(doc);
      }
      return [...groups.values()].sort((a, b) => byDate ? b.id.localeCompare(a.id) : alphabet.compare(a.label, b.label))
        .map(({ documents: docs, ...node }) => ({ ...node,
          ...(rootTotals && byDate ? { rightText: formatMoney(receiptTotal(docs.filter(doc => !doc.annulled), 'total', 4)) } : {}),
          children: [...docs].sort((a, b) => String(b.date || '').localeCompare(String(a.date || ''))
            || Number(b.number) - Number(a.number) || Number(b.sub_number || 0) - Number(a.sub_number || 0)
            || Number(b.id) - Number(a.id)).map(documentLeaf) }));
    },
    mount(host, owner, changed, canChange = true, failed = error => console.error(error)) {
      enabled = canChange;
      if (checkbox?.element.isConnected) { checkbox.setEnabled(enabled && !changing); return; }
      checkbox = owner.own(new CheckBox(host, { label: 'Grupeaza pe luni', checked: byDate,
        className: 'vc-document-tree-order vc-oi-tree-order', disabled: !enabled || changing,
        onChange: checked => {
          const previous = byDate;
          byDate = checked; changing = true; checkbox.setEnabled(false);
          Promise.resolve().then(changed).catch(error => {
            byDate = previous; checkbox.checked = previous; failed(error);
          }).finally(() => { changing = false; checkbox.setEnabled(enabled); });
        } }));
    },
  };
}
