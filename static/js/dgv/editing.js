// SLICE-ADE2-01/02: opt-in cell editing, independent of recycled row elements.
import { Combobox } from '../components/combobox/combobox.js';
import { DatePicker } from '../components/datepicker/datepicker.js';
import eventBus from '../event-bus/event-bus.js';

export function parseCell(raw, column) {
  if (column.parse) return column.parse(raw);
  if (raw === '' && column.nullable) return null;
  if (column.editor === 'number') {
    if (!/^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)$/.test(raw.trim())) {
      throw new Error('Introduceți un număr fără separatori de mii.');
    }
    const value = Number(raw.replace(',', '.'));
    if (!Number.isFinite(value)) throw new Error('Număr în afara limitelor.');
    return value;
  }
  if (column.editor === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    throw new Error('Introduceți o dată validă.');
  }
  return raw;
}

export const editing = {
  get hasEdit() { return !!this._edit; },
  focusEdit() { this._edit?.input.focus(); },
  canEdit(row, column) {
    return !!this._opts.editable && !!column?.editable
      && (typeof column.editable !== 'function' || column.editable(row));
  },
  beginNewRowEdit(row, key, onCancelRow) {
    return this.beginEdit(row, key, { onCancelRow });
  },
  beginEdit(row, key, { onCancelRow } = {}) {
    if (this._edit || this._destroyed) return false;
    const column = this._col(key);
    if (!this.canEdit(row, column)) return false;
    if (!this._opts.rowKey || row[this._opts.rowKey] == null || !this._opts.onCellSave) {
      throw new Error('Editing requires rowKey and onCellSave');
    }
    const host = document.createElement('div');
    host.className = 'dgv__editor';
    const state = { row, key, column, host, raw: row[key] ?? '', pending: false,
      abort: new AbortController(), value: row[key], onCancelRow };
    this._edit = state;
    this._activeKey = key;
    if (column.editor === 'list') {
      state.combo = new Combobox(host, { staticData: column.options || [],
        onSelect: (value) => { state.raw = value; state.value = value; } });
      const selected = (column.options || []).find((item) => item.value === row[key]);
      state.combo.setValue(row[key], selected?.text ?? String(row[key] ?? ''));
      state.input = state.combo.input;
    } else {
      state.input = document.createElement('input');
      state.input.type = column.editor === 'checkbox' ? 'checkbox' : column.editor === 'date' ? 'date' : 'text';
      if (column.editor === 'number') state.input.inputMode = 'decimal';
      state.input.value = state.raw;
      if (column.editor === 'monthYear') {
        state.input.value = column.formatter?.(state.raw) || state.raw;
        state.input.placeholder = 'll.aaaa'; state.input.inputMode = 'numeric'; state.input.maxLength = 7;
      }
      if (column.editor === 'checkbox') state.input.checked = Boolean(row[key]);
      host.append(state.input);
      state.input.addEventListener('input', () => { state.raw = column.editor === 'checkbox' ? state.input.checked : state.input.value; }, { signal: state.abort.signal });
    }
    state.input.setAttribute('aria-label', column.title);
    if (column.editor === 'date') {
      state.source = state.input;
      state.source.addEventListener('change', () => { state.raw = state.source.value; }, { signal: state.abort.signal });
      state.picker = new DatePicker(state.source);
      state.input = state.picker.field;
    }
    host.addEventListener('click', (event) => event.stopPropagation(), { signal: state.abort.signal });
    host.addEventListener('dblclick', (event) => event.stopPropagation(), { signal: state.abort.signal });
    host.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation();
      if (this.cancelEdit()) state.onCancelRow?.(state.row);
    }, { capture: true, signal: state.abort.signal });
    host.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault(); this.commitEdit(event.shiftKey ? -1 : 1);
      } else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && column.editor !== 'list' && column.editor !== 'date') {
        // Up/Down save (only when changed) and move the editor to the same column of the next/previous row.
        event.preventDefault(); this.commitEdit(event.key === 'ArrowDown' ? 'down' : 'up');
      }
    }, { signal: state.abort.signal });
    this._selected = row;
    this._ensureVisible(this._items.findIndex((item) => item.row === row));
    this._renderWindow(true);
    state.input.focus();
    state.input.select?.();
    this._emit('onEditState', { state: 'editing', key });
    return true;
  },
  cancelEdit(force = false) {
    const state = this._edit;
    if (!state || (state.pending && !force)) return false;
    this._edit = null;
    state.abort.abort();
    state.combo?.destroy();
    state.picker?.destroy();
    state.host.remove();
    if (!this._destroyed) { this._renderWindow(true); this._root.focus(); }
    this._emit('onEditState', { state: 'idle', key: state.key });
    return true;
  },
  async commitEdit(direction = 0) {
    const state = this._edit;
    if (!state || state.pending) return false;
    try {
      if (state.picker) {
        state.picker.commitText();
        if (state.input.classList.contains('is-invalid')) throw new Error('Introduceți o dată validă, în format zz.ll.aaaa.');
        state.raw = state.source.value;
        state.picker.close();
      }
      const value = state.column.editor === 'checkbox' ? state.input.checked
        : state.column.editor === 'list' ? state.value : parseCell(String(state.raw), state.column);
      const message = state.column.validate?.(value, state.row);
      if (message) throw new Error(message);
      // Nothing changed: no write on the server (new rows always save).
      const unchanged = !state.onCancelRow && (value === state.row[state.key] || (value == null && state.row[state.key] == null));
      state.pending = true;
      state.input.disabled = true;
      state.host.inert = true;
      state.host.setAttribute('aria-busy', 'true');
      this._emit('onEditState', { state: 'saving', key: state.key });
      if (unchanged) {
        this.cancelEdit(true);
      } else {
        const saved = await this._opts.onCellSave({ row: { ...state.row }, key: state.key, value,
          previous: state.row[state.key], rowId: state.row[this._opts.rowKey] });
        if (this._destroyed || this._edit !== state) return false;
        Object.assign(state.row, saved || { [state.key]: value });
        this.cancelEdit(true);
        this._refresh({ rebuildHeader: true });
        eventBus.emit('dgv:cell-saved', { grid: this._opts.layoutId, key: state.key });
        this._emit('onCellSaved', { row: state.row, key: state.key });
      }
      if (direction === 'up' || direction === 'down') {
        const rows = this._items.filter((item) => item.kind === 'row').map((item) => item.row);
        const at = rows.indexOf(state.row);
        for (let index = at + (direction === 'down' ? 1 : -1); at >= 0 && index >= 0 && index < rows.length; index += direction === 'down' ? 1 : -1) {
          if (this.canEdit(rows[index], state.column)) {
            if (await this._opts.onRowValidate?.({ row: state.row }) === false) return false;
            this.beginEdit(rows[index], state.key); return true;
          }
        }
        return true;
      }
      if (direction) {
        const columns = this._visibleColumns();
        const cells = this._items.filter((item) => item.kind === 'row')
          .flatMap((item) => columns.map((column) => ({ row: item.row, column })));
        const current = cells.findIndex((cell) => cell.row === state.row && cell.column.key === state.key);
        for (let index = current + direction; current >= 0 && index >= 0 && index < cells.length; index += direction) {
          const next = cells[index];
          if (this.canEdit(next.row, next.column)) {
            if (next.row !== state.row && await this._opts.onRowValidate?.({ row: state.row }) === false) return false;
            this.beginEdit(next.row, next.column.key); return true;
          }
        }
        if (await this._opts.onRowValidate?.({ row: state.row }) === false) return false;
      }
      return true;
    } catch (error) {
      if (this._destroyed || this._edit !== state) return false;
      state.pending = false;
      state.input.disabled = false;
      state.host.inert = false;
      state.input.setAttribute('aria-invalid', 'true');
      state.host.setAttribute('aria-busy', 'false');
      state.host.title = error.message;
      this._emit('onEditError', { error, key: state.key, row: state.row });
      console.error('[DataGrid] Cell save failed', error);
      state.input.focus();
      return false;
    }
  },
};
