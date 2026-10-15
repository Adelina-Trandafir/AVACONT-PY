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
  // newRowAlwaysVisible: the grid always ends with one empty "new row" (like an Access datasheet). The host creates it (onNewRow) and
  // recognises an empty one (isBlankRow); the grid only keeps track of it. Returns the new-row slot, or null when the option is off.
  ensureNewRow() {
    if (!this._opts.newRowAlwaysVisible || !this._opts.editable || typeof this._opts.onNewRow !== 'function') return null;
    if (this._opts.singleNewRow && this._pendingRow) return null;
    const k_last = this._rows[this._rows.length - 1];
    if (k_last && this._opts.isBlankRow?.(k_last)) return (this._newSlot = k_last);
    const k_row = this._opts.onNewRow();
    if (!k_row) return null;
    if (!this._rows.includes(k_row)) this._rows.push(k_row);
    return (this._newSlot = k_row);
  },
  /** Starts editing the cell `key` of the empty new row (the "add row" command of a grid that keeps one always visible). */
  async beginEditNewRow(key) {
    const k_row = this.ensureNewRow();
    if (k_row && !this._items.some((k_item) => k_item.row === k_row)) this._refresh();
    if (!k_row || !await this._leaveRow(k_row)) return false;
    return this.beginEdit(k_row, key);
  },
  // Leaving a row that was edited: it is judged on the spot (rowIssue(row) -> '' or the reason). An incomplete row asks the operator
  // (confirmRowIssue({ row, message }) -> 'continue' | 'cancel'): continue (the default) goes back into the row, cancel drops a new row or
  // puts an existing one back as it was when the editing of it began. Needs both options; without them nothing is checked.
  async _leaveRow(nextRow) {
    if (this._opts.suspendRowLeave?.()) return false;
    const k_e = this._editedRow;
    if (!k_e || k_e.row === nextRow) return true;
    if (this._leaving) return false;
    const k_check = typeof this._opts.rowIssue === 'function' && typeof this._opts.confirmRowIssue === 'function' && !this._opts.isBlankRow?.(k_e.row);
    const k_message = k_check ? this._opts.rowIssue(k_e.row) : '';
    this._leaving = true;
    try {
      if (!k_message) {
        if (await this._opts.onRowLeave?.({ row: k_e.row, nextRow }) === false || this._destroyed) return false;
        this._editedRow = null; return true;
      }
      const k_answer = await this._opts.confirmRowIssue({ row: k_e.row, message: k_message });
      if (this._destroyed) return true;
      if (k_answer === 'cancel') {
        this._editedRow = null;
        if (this._pendingRow?.row === k_e.row) this.cancelPendingRow();
        else { Object.assign(k_e.row, k_e.snapshot); this._refresh(); this._emit('onCellSaved', { row: k_e.row, key: null }); }
        return true;
      }
      const k_columns = this._visibleColumns().filter((k_c) => this.canEdit(k_e.row, k_c));
      const k_column = k_columns.find((k_c) => k_e.row[k_c.key] == null || String(k_e.row[k_c.key]).trim() === '') || k_columns[0];
      if (k_column) this.beginEdit(k_e.row, k_column.key);
      return false;
    } finally { this._leaving = false; }
  },
  // The focus left the grid: no editor stays open (the cell is saved, or dropped when it cannot be saved), then the row is judged.
  _onFocusOut(ev) {
    if (this._destroyed || this._leaving || (ev.relatedTarget && this._root.contains(ev.relatedTarget))) return;
    setTimeout(async () => {
      if (this._destroyed || this._leaving || this._edit?.pending || !document.hasFocus() || this._root.contains(document.activeElement)) return;
      if (this._edit) { if (!await this.commitEdit() && this._edit && !this._edit.pending) this.cancelEdit(true); }
      if (!this._destroyed && !this._edit) await this._leaveRow(null);
    }, 0);
  },
  // A new row (made by beginNewRowEdit, or the new-row slot once the operator starts on it) stays "not validated" until the operator
  // moves to another row or presses Esc. Leaving it empty drops it; Esc drops it with its content.
  _removeRow(row, handler) {
    if (handler) { handler(row); return; }
    const k_at = this._rows.indexOf(row);
    if (k_at >= 0) { this._rows.splice(k_at, 1); this._refresh(); }
  },
  _leavePendingRow(row) {
    const k_p = this._pendingRow;
    if (!k_p || k_p.row === row) return;
    this._pendingRow = null;
    if (this._opts.isBlankRow?.(k_p.row)) this._removeRow(k_p.row, k_p.onCancelRow || this._opts.onCancelNewRow);
  },
  /** Esc with no cell in edit: cancels the whole row that is not validated yet. True when there was one. */
  cancelPendingRow() {
    const k_p = this._pendingRow;
    if (!k_p) return false;
    this._pendingRow = null;
    this._removeRow(k_p.row, k_p.onCancelRow || this._opts.onCancelNewRow);
    if (!this._destroyed) { this.ensureNewRow(); this._renderWindow(true); }
    return true;
  },
  beginEdit(row, key, { onCancelRow } = {}) {
    if (this._edit || this._destroyed) return false;
    const column = this._col(key);
    if (!this.canEdit(row, column)) return false;
    // editGuard(row, column) returns a reason text when the grid must stay locked for now (e.g. the header above it is incomplete)
    const k_blocked = typeof this._opts.editGuard === 'function' ? this._opts.editGuard(row, column) : '';
    if (k_blocked) { this._emit('onEditBlocked', { message: k_blocked }); return false; }
    if (!this._opts.rowKey || row[this._opts.rowKey] == null || !this._opts.onCellSave) {
      throw new Error('Editing requires rowKey and onCellSave');
    }
    if (this._editedRow?.row !== row) this._editedRow = { row, snapshot: { ...row } };
    this._leavePendingRow(row);
    if (onCancelRow) this._pendingRow = { row, onCancelRow };
    else if (this._opts.newRowAlwaysVisible && row === this._newSlot) {   // the operator starts on the new row: it is pending, the next empty one appears
      this._pendingRow = { row, onCancelRow: null };
      this._newSlot = null;
      this.ensureNewRow();
      this._refresh();
    }
    const host = document.createElement('div');
    host.className = 'dgv__editor';
    const state = { row, key, column, host, raw: row[key] ?? '', pending: false,
      abort: new AbortController(), value: row[key], onCancelRow };
    this._edit = state;
    this._activeKey = key;
    const markRequired = () => {
      const value = column.editor === 'list' ? state.value : state.input?.value;
      state.host.closest('.dgv__cell')?.classList.toggle('is-required-empty', !!column.required && this._requiredShown(state.row, key) && (value == null || String(value).trim() === ''));
    };
    if (column.editor === 'list') {
      const items = typeof column.options === 'function' ? column.options(row) : (column.options || []);
      state.combo = new Combobox(host, { staticData: items, readonly: !column.searchable, showArrow: column.showArrow, dropDownOnEnter: column.dropDownOnEnter, maxResults: column.maxResults, dropDownMinWidth: column.dropDownMinWidth,
        onSearch: column.onSearch ? (query) => column.onSearch(query, row) : async (query) => items.filter((item) => String(item.label || item.text || '').toLocaleLowerCase('ro').includes(query.toLocaleLowerCase('ro'))),
        onSelect: (value) => { state.raw = value; state.value = value; markRequired(); } });
      const selected = items.find((item) => String(item.value) === String(row[key]));
      state.combo.setValue(row[key], selected?.label ?? selected?.text ?? String(row[key] ?? ''));
      state.input = state.combo.input;
      if (column.searchable) state.input.addEventListener('input', () => { state.value = null; markRequired(); }, { signal: state.abort.signal });
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
    state.input.addEventListener('input', markRequired, { signal: state.abort.signal });
    host.addEventListener('click', (event) => event.stopPropagation(), { signal: state.abort.signal });
    host.addEventListener('dblclick', (event) => event.stopPropagation(), { signal: state.abort.signal });
    host.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation();
      this.cancelEdit();   // Esc in a cell: only the cell goes back to what it was; the row is cancelled by a second Esc
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
  // A required cell is not marked while untouched: only after it was left (a cell the operator has been in) or once validate() ran.
  // The always-visible new row (empty) is never judged: nothing in it is marked as required.
  _isNewRow(row) { return !!this._opts.newRowAlwaysVisible && (row === this._newSlot || !!this._opts.isBlankRow?.(row)); },
  _requiredShown(row, key) { return !this._isNewRow(row) && (!!this._requiredAll || !!this._requiredSeen?.get(row)?.has(key)); },
  _seen(row, key) {
    this._requiredSeen ||= new WeakMap();
    let k_keys = this._requiredSeen.get(row);
    if (!k_keys) this._requiredSeen.set(row, k_keys = new Set());
    k_keys.add(key);
  },
  /** The validation event: every empty required editable cell is marked from now on. Returns { ok }. */
  validate() {
    this._requiredAll = true;
    let k_empty = false;
    const k_columns = this._visibleColumns();
    for (const k_row of this._rows) for (const k_col of k_columns) {
      if (this._isNewRow(k_row)) continue;
      if (k_col.required && this.canEdit(k_row, k_col) && (k_row[k_col.key] == null || String(k_row[k_col.key]).trim() === '')) k_empty = true;
    }
    if (!this._destroyed) this._renderWindow(true);
    return { ok: !k_empty };
  },
  cancelEdit(force = false) {
    const state = this._edit;
    if (!state || (state.pending && !force)) return false;
    this._edit = null;
    this._seen(state.row, state.key);   // the cell was left: from now on it shows "required and empty"
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
        const invalid = state.input.classList.contains('is-invalid');
        if (invalid && !this._opts.allowIncompleteEdits) throw new Error('Introduceți o dată validă, în format zz.ll.aaaa.');
        state.raw = invalid ? state.input.value : state.source.value;
        state.picker.close();
      }
      let value;
      try {
        value = state.column.editor === 'checkbox' ? state.input.checked
          : state.column.editor === 'list' ? state.value : parseCell(String(state.raw), state.column);
      } catch (error) {
        // Document drafts preserve unfinished input; document Save performs strict validation.
        if (!this._opts.allowIncompleteEdits) throw error;
        value = String(state.raw).trim() === '' ? null : state.raw;
      }
      const message = state.column.validate?.(value, state.row);
      if (message && !this._opts.allowIncompleteEdits) throw new Error(message);
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
            if (!await this._leaveRow(rows[index])) return false;
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
            if (next.row !== state.row && !await this._leaveRow(next.row)) return false;
            if (next.row !== state.row && await this._opts.onRowValidate?.({ row: state.row }) === false) return false;
            this.beginEdit(next.row, next.column.key); return true;
          }
        }
        if (!await this._leaveRow(null)) return false;
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
