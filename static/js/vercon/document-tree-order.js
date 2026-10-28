// Two levels in every saved-document tree: month or party, then documents.
import { CheckBox, closeMenu, docNode, el, openMenu } from './ui.js';
import { ActionAddButton } from '../components/action-add-button/action-add-button.js';
import { format, formatMoney } from './fields.js';
import { numberText, numberValue } from './receipt-number.js';
import { receiptTotal } from './receipt-reconciliation.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie',
  'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const alphabet = new Intl.Collator('ro', { sensitivity: 'base' });
export const monthKey = date => /^\d{4}-(0[1-9]|1[0-2])/.test(String(date || '')) ? String(date).slice(0, 7) : 'undated';
export const documentMonthId = month => `document-month:${month}`;
export const documentMonthLabel = month => month === 'undated' ? 'Fără dată' : `${MONTHS[Number(month.slice(5)) - 1]} / ${month.slice(0, 4)}`;
export const documentPartyId = (party, field = 'party') => `${field}:${encodeURIComponent(party ?? '')}`;
const partyKey = (doc, field) => field === 'holder' ? doc.holder_id ?? doc.holder ?? '' : field === 'party' ? doc.party_cf ?? ''
  : field === 'category' ? doc.category_id ?? '' : doc[field] || '';

export function documentLeaf(doc) {
  const label = `${doc.date ? format(doc.date, { type: 'date' }) : '—'} / ${numberText(doc) ?? '—'}`
    + (doc.source_missing ? ' • Fără bon sursă' : '') + (doc.annulled ? ' • anulat' : '');
  return { ...docNode(doc, label, formatMoney(doc.total)), tooltip: label, bold: !!doc.has_efactura, italic: !!doc.has_efactura,
    sortType: 'date', sortValue: doc.date, sortDirection: 'desc',
    sortThen: [{ type: 'number', value: numberValue(doc.number) }, { type: 'number', value: doc.sub_number || 0 }, { type: 'number', value: doc.id }] };
}

// `groupings` ([{ key, label, icon }], key 'date' or a field of the document list: 'receiver', 'category'): the box is replaced by a
// button that opens a popup with these choices. Without it the tree has two groupings, month or `party`, switched by a box.
export function documentTreeOrder({ party = 'party', rootTotals = false, initialByDate = true, groupings = null } = {}) {
  const idle = groupings ? groupings.find(item => item.key !== 'date').key : party;
  let mode = initialByDate ? 'date' : idle, checkbox = null, footer = null, caption = null, changing = false, enabled = true;
  const byDate = () => mode === 'date';
  const branch = doc => byDate() ? documentMonthId(monthKey(doc.date)) : documentPartyId(partyKey(doc, mode), mode);
  const groupingLabel = () => groupings.find(item => item.key === mode).label;
  // One change of grouping: the tree is drawn again by `changed`; when that fails the previous grouping comes back.
  const change = (next, changed, failed, redraw) => {
    const previous = mode;
    mode = next; changing = true; redraw();
    Promise.resolve().then(changed).catch(error => { mode = previous; failed(error); }).finally(() => { changing = false; redraw(); });
  };
  return {
    get byDate() { return byDate(); },
    // A new default (another kind of gestiune): the box is drawn again from it when the tree is mounted.
    reset(value) { mode = value ? 'date' : idle; },
    branch,
    nodes(documents) {
      const groups = new Map();
      for (const doc of documents) {
        const id = branch(doc);
        if (!groups.has(id)) groups.set(id, { id,
          label: byDate() ? documentMonthLabel(monthKey(doc.date)) : mode === 'party' && !doc.party_cf ? 'Fără cod fiscal' : doc[mode] || '—', documents: [],
          ...(byDate() ? { sortType: 'date', sortValue: doc.date ? doc.date.slice(0, 7) + '-01' : null, sortDirection: 'desc' } : {}) });
        groups.get(id).documents.push(doc);
      }
      return [...groups.values()].sort((a, b) => byDate() ? b.id.localeCompare(a.id) : alphabet.compare(a.label, b.label))
        .map(({ documents: docs, ...node }) => ({ ...node,
          ...(rootTotals && byDate() ? { rightText: formatMoney(receiptTotal(docs.filter(doc => !doc.annulled), 'total', 4)) } : {}),
          children: [...docs].sort((a, b) => String(b.date || '').localeCompare(String(a.date || ''))
            || numberValue(b.number) - numberValue(a.number) || Number(b.sub_number || 0) - Number(a.sub_number || 0)
            || Number(b.id) - Number(a.id)).map(documentLeaf) }));
    },
    mount(host, owner, changed, canChange = true, failed = error => console.error(error)) {
      enabled = canChange;
      if (groupings) {
        const paintButton = () => {
          const current = groupings.find(item => item.key === mode);
          caption.setIcon(current.icon); caption.setLabel(current.label); caption.setEnabled(enabled && !changing);
          caption.element.title = 'Alege cum sunt grupate documentele';
        };
        if (footer?.isConnected) { paintButton(); return; }
        footer = el('div', { className: 'vc-document-tree-order' });
        caption = new ActionAddButton(footer, { className: 'vc-document-tree-group', icon: '🗂️', label: groupings[0].label,
          onClick: () => openMenu(caption.element, groupings.map(item => ({ key: item.key, icon: item.icon, label: item.label, on: item.key === mode })), {
            label: 'Grupare documente', up: true, checkable: true,
            onSelect: entry => { if (entry.key !== mode) change(entry.key, changed, failed, paintButton); } }) });
        caption.element.setAttribute('aria-haspopup', 'menu');
        host.append(footer);
        owner.own(() => { closeMenu(); caption.destroy(); footer.remove(); });
        paintButton();
        return;
      }
      if (checkbox?.element.isConnected) { checkbox.setEnabled(enabled && !changing); return; }
      checkbox = owner.own(new CheckBox(host, { label: 'Grupeaza pe luni', checked: byDate(),
        className: 'vc-document-tree-order vc-oi-tree-order', disabled: !enabled || changing,
        onChange: checked => change(checked ? 'date' : idle, changed, error => { checkbox.checked = byDate(); failed(error); },
          () => checkbox.setEnabled(enabled && !changing)) }));
    },
  };
}
