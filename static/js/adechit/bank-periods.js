import { TreeView } from '../components/treeview/treeview.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie',
  'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];

export function bindBankPeriods({ api, onChange }) {
  let tree = null; let value = '';
  function destroy() { tree?.destroy(); tree = null; value = ''; }
  async function open({ preserve = false } = {}) {
    const previous = value;
    destroy();
    const result = await api('/api/adechit/bank-extracts/periods');
    const years = new Map();
    for (const period of result.periods) {
      if (!years.has(period.year)) years.set(period.year, { id: `year:${period.year}`, label: `Anul ${period.year}`, bold: true, sortValue: period.year, sortType: 'number', sortDirection: 'desc', children: [] });
      years.get(period.year).children.push({ id: `period:${period.value}`, label: MONTHS[period.month - 1], sortValue: period.month, sortType: 'number', sortDirection: 'desc' });
    }
    tree = new TreeView(document.getElementById('ade-bank-period-tree'), {
      inline: true, autoCollapse: false, selectableLevel: 2, indent: 12, showSearchBox: false,
      onSelect: ({ id }) => {
        if (!String(id).startsWith('period:')) return;
        const next = String(id).slice(7);
        if (next === value) return;
        const previous = value;
        value = next;
        Promise.resolve(onChange(next)).then((accepted) => {
          if (accepted === false) {
            value = previous; tree?.setValue(`period:${previous}`, previous); tree?.markSelected();
          }
        });
      },
    });
    tree.setData([...years.values()]);
    const latest = (preserve ? result.periods.find((period) => period.value === previous) : null) || result.periods[0];
    value = latest?.value || '';
    if (latest) {
      tree.expandedNodes.add(`year:${latest.year}`);
      tree.setValue(`period:${value}`, MONTHS[latest.month - 1]);
    }
    tree.isTreeRendered = false; tree.renderTree(''); tree.markSelected();
  }
  return { open, destroy, value: () => value };
}
