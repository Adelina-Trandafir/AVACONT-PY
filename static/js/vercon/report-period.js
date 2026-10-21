import { ComboTree } from '../components/combotree/combotree.js';
import { el } from './ui.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const monthEnd = (year, month) => new Date(Date.UTC(year, month + 1, 0)).toISOString().slice(0, 10);

export function reportPeriod(owner, onChange) {
  const host = el('div', { className: 'vc-field__combo' });
  const node = el('label', { className: 'vc-field' }, el('span', { textContent: 'Perioada' }), host);
  let ranges = new Map();
  const combo = owner.own(new ComboTree(host, {
    selectParents: true,
    minWidth: 0,
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
      const periods = [...data.periods].sort((first, second) => second.year - first.year);
      const nodes = periods.map(period => {
        const year = period.year;
        const lastMonth = Math.max(0, Math.min(12, Number(period.last_movement_month ?? period.max?.slice(5, 7) ?? 0)));
        const range = (id, label, start, finish) => {
          ranges.set(id, { start, end: finish, label: `${label} / ${year}` });
          return { id, label, keywords: `${label} ${year} ${start.slice(5, 7)}` };
        };
        return { id: `year:${year}`, label: String(year), selectable: false, children: [
          range(`period:${year}:all`, 'Tot anul', `${year}-01-01`, `${year}-12-31`),
          ...Array.from({ length: lastMonth }, (_, index) => lastMonth - index - 1)
            .map(month => range(`period:${year}:${month}`, MONTHS[month],
              `${year}-${String(month + 1).padStart(2, '0')}-01`, monthEnd(year, month))),
        ] };
      });
      if (data.all_years) {
        const id = 'period:all-years', label = 'TOTI anii';
        ranges.set(id, { ...data.all_years, label });
        nodes.unshift({ id, label });
      }
      // Accounting periods include every calendar day, even without movements.
      const min = `${Math.min(...data.years)}-01-01`;
      const max = `${Math.max(...data.years)}-12-31`;
      const calendarYears = data.all_years
        ? Array.from({ length: Math.max(...data.years) - Math.min(...data.years) + 1 }, (_, i) => Math.min(...data.years) + i)
        : data.years;
      for (const control of [begin, end]) {
        control.input._picker.setBounds({ min, max, years: calendarYears });
      }
      const currentYear = Number(data.current_year ?? data.default_end?.slice(0, 4));
      // Older catalog responses provide latest_year but not latest_data_year.
      // Preserve an explicit null (no movements), and normalize JSON year types.
      const latestDataYear = data.latest_data_year === undefined ? data.latest_year : data.latest_data_year;
      const expandedYear = data.all_years
        ? (latestDataYear != null && Number(latestDataYear) === currentYear ? currentYear : null)
        : data.latest_year;
      const expanded = expandedYear == null ? [] : [`year:${expandedYear}`];
      combo.setData(nodes);
      combo.setOptions({ expanded });
      combo.expandedNodes = new Set(expanded);
      combo.disabled = combo.input.disabled = false;
      combo.options.placeholder = 'Alegeți perioada…';
      const id = `period:${data.latest_year}:all`, range = ranges.get(id);
      if (data.default_start && data.default_end) {
        combo.setValue('', 'Anul curent până azi');
        onChange(data.default_start, data.default_end);
      } else {
        combo.setValue(id, range.label);
        onChange(range.start, range.end);
      }
    },
    sync(start, end) {
      const match = [...ranges].find(([, range]) => range.start === start && range.end === end);
      combo.setValue(match?.[0] || '', match?.[1].label || 'Interval personalizat');
    },
  };
}
