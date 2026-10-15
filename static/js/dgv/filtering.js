// Shared column filter popup and icons; draft rows never supply checklist values.
import { ValueType, allowedOperators, operatorCaption, operandCount, formatValue, createFilter, isBlank } from './engine.js';
const FILTER_LIST_LIMIT = 500;
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
export function filterIcon() {
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 16 16');
  icon.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(icon.namespaceURI, 'path');
  path.setAttribute('d', 'M1.5 2.5h13l-5 5.5v4.5l-3 1V8z');
  icon.append(path);
  return icon;
}
export const filtering = {
  _distinctDisplayValues(col) {
    const set = new Set();
    this._rows.filter((row) => !this._opts.isBlankRow?.(row)).forEach((row) => set.add(formatValue(row[col.key], col)));
    return [...set].sort((a, b) => {
      if (a === '') return -1;
      if (b === '') return 1;
      return a.localeCompare(b, 'ro', { numeric: true, sensitivity: 'base' });
    });
  },

  _openFilter(col, anchor) {
    this._closePopup();
    const existing = this._filters.get(col.key) || createFilter();
    const values = this._distinctDisplayValues(col);
    const checked = new Set(existing.selected ? existing.selected : values);

    const pop = el('div', 'dgv__popup dgv__filter');
    pop.appendChild(el('div', 'dgv__ptitle', `Filtru: ${col.title}`));

    const search = el('input', 'dgv__search');
    search.type = 'search';
    search.placeholder = 'Caută în valori...';
    pop.appendChild(search);

    const all = el('label', 'dgv__chk');
    const allBox = el('input');
    allBox.type = 'checkbox';
    all.append(allBox, el('span', '', '(Selectează tot)'));
    pop.appendChild(all);

    const list = el('div', 'dgv__list');
    pop.appendChild(list);
    const note = el('div', 'dgv__note');
    pop.appendChild(note);

    const visibleValues = () => {
      const q = search.value.trim().toLowerCase();
      return values.filter((v) => !q || v.toLowerCase().includes(q));
    };
    const paintList = () => {
      const shown = visibleValues();
      list.textContent = '';
      shown.slice(0, FILTER_LIST_LIMIT).forEach((v) => {
        const row = el('label', 'dgv__chk');
        const box = el('input');
        box.type = 'checkbox';
        box.checked = checked.has(v);
        box.addEventListener('change', () => {
          if (box.checked) checked.add(v); else checked.delete(v);
          syncAll();
        });
        row.append(box, el('span', '', v === '' ? '(goale)' : v));
        list.appendChild(row);
      });
      note.textContent = shown.length > FILTER_LIST_LIMIT ? `Se arată primele ${FILTER_LIST_LIMIT} din ${shown.length}. Folosiți căutarea.` : '';
      syncAll();
    };
    const syncAll = () => {
      const shown = visibleValues();
      const n = shown.filter((v) => checked.has(v)).length;
      allBox.checked = shown.length > 0 && n === shown.length;
      allBox.indeterminate = n > 0 && n < shown.length;
    };
    allBox.addEventListener('change', () => {
      visibleValues().forEach((v) => { if (allBox.checked) checked.add(v); else checked.delete(v); });
      paintList();
    });
    search.addEventListener('input', paintList);

    // condition
    pop.appendChild(el('div', 'dgv__ptitle dgv__ptitle--sub', 'Condiție'));
    const opSel = el('select', 'dgv__op');
    const none = el('option', '', '(fără condiție)');
    none.value = '';
    opSel.appendChild(none);
    allowedOperators(col.valueType).forEach((op) => {
      const o = el('option', '', operatorCaption(op));
      o.value = op;
      opSel.appendChild(o);
    });
    opSel.value = existing.condition ? existing.condition.op : '';
    pop.appendChild(opSel);

    const operandType = col.valueType === ValueType.DateTime
      ? ((col.format || '').match(/generalDate|longTime|shortTime/) || (col.formatString || '').includes('HH') ? 'datetime-local' : 'date')
      : 'text';
    const mkOperand = (value) => {
      const input = el('input', 'dgv__operand');
      input.type = operandType;
      if (operandType === 'text' && col.valueType === ValueType.Number) input.inputMode = 'decimal';
      input.value = value === undefined || value === null ? '' : String(value);
      return input;
    };
    const op1 = mkOperand(existing.condition && existing.condition.v1);
    const op2 = mkOperand(existing.condition && existing.condition.v2);
    const operandBox = el('div', 'dgv__operands');
    operandBox.append(op1, op2);
    pop.appendChild(operandBox);
    const syncOperands = () => {
      const n = opSel.value ? operandCount(opSel.value) : 0;
      op1.hidden = n < 1;
      op2.hidden = n < 2;
    };
    opSel.addEventListener('change', syncOperands);
    syncOperands();

    // buttons
    const bar = el('div', 'dgv__pbar');
    const mkBtn = (label, cls, fn) => {
      const b = el('button', `dgv__btn ${cls}`, label);
      b.type = 'button';
      b.addEventListener('click', fn);
      bar.appendChild(b);
    };
    mkBtn('Șterge filtrul', '', () => { this._closePopup(); this.setFilter(col.key, null); });
    mkBtn('Anulează', '', () => { this._closePopup(); this._root.focus(); });
    mkBtn('Aplică', 'is-primary', () => {
      const filter = createFilter();
      if (checked.size !== values.length) filter.selected = new Set(checked);
      if (opSel.value) {
        const cnt = operandCount(opSel.value);
        const cond = { op: opSel.value, v1: cnt >= 1 ? op1.value : undefined, v2: cnt >= 2 ? op2.value : undefined };
        if (cnt === 0 || !isBlank(cond.v1)) filter.condition = cond;
      }
      this._closePopup();
      this.setFilter(col.key, filter);
    });
    pop.appendChild(bar);

    const rect = anchor ? anchor.getBoundingClientRect() : { left: 20, bottom: 40 };
    this._placePopup(pop, rect.left, rect.bottom + 4);
    paintList();
    search.focus();
  }
};
