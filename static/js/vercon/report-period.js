import { ComboTree } from '../components/combotree/combotree.js';
import { el } from './ui.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const monthEnd = (year, month) => new Date(Date.UTC(year, month + 1, 0)).toISOString().slice(0, 10);

export function reportPeriod(owner, onChange) {
  const host = el('div', { className: 'vc-field__combo' });
  const node = el('label', { className: 'vc-field' }, el('span', { textContent: 'Perioada' }), host);
  let ranges = new Map();
  const combo = owner.own(new ComboTree(host, {
    placeholder: 'Se încarcă perioadele…', searchPlaceholder: 'Tastează anul sau luna…',
    onSelect: selection => {
      const range = ranges.get(selection.id);
      if (!range) return;
      combo.setValue(selection.id, range.label);
      onChange(range.start, range.end);
    },
  }));
  combo.input.setAttribute('aria-label', 'Perioada');
  combo.disabled = combo.input.disabled = true;
  return {
    node,
    configure(data, begin, end) {
      ranges = new Map();
      const periods = data.periods;
      const nodes = periods.map(period => {
        const year = period.year;
        const range = (id, label, start, finish) => {
          ranges.set(id, { start, end: finish, label: `${label} / ${year}` });
          return { id, label, keywords: `${label} ${year} ${start.slice(5, 7)}` };
        };
        return { id: `year:${year}`, label: String(year), children: [
          range(`period:${year}:all`, 'Tot anul', `${year}-01-01`, `${year}-12-31`),
          ...MONTHS.map((label, month) => range(`period:${year}:${month}`, label,
            `${year}-${String(month + 1).padStart(2, '0')}-01`, monthEnd(year, month))),
        ] };
      });
      // Accounting periods include every calendar day, even without movements.
      const min = `${Math.min(...data.years)}-01-01`;
      const max = `${Math.max(...data.years)}-12-31`;
      for (const control of [begin, end]) {
        control.input._picker.setBounds({ min, max, years: data.years });
      }
      combo.options.expanded = [`year:${data.latest_year}`];
      combo.setData(nodes);
      combo.disabled = combo.input.disabled = false;
      combo.options.placeholder = 'Alegeți perioada…';
      const id = `period:${data.latest_year}:all`, range = ranges.get(id);
      combo.setValue(id, range.label);
      onChange(range.start, range.end);
    },
    sync(start, end) {
      const match = [...ranges].find(([, range]) => range.start === start && range.end === end);
      combo.setValue(match?.[0] || '', match?.[1].label || 'Interval personalizat');
    },
  };
}
