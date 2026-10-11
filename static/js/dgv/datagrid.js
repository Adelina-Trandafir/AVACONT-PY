// Slice 0110-05/ADE2 -- DataGrid: the web counterpart of KBotDataView. Editing is strictly
// opt-in (`editable`, editable columns, `rowKey` and `onCellSave`); existing grids stay read-only.
//
//   import { DataGrid } from '/static/js/dgv/datagrid.js';
//   const grid = new DataGrid(container, { columns: [...], rows: [...] });
//
// What it does: virtualized rows (only the visible window is in the page), sticky header and
// footer, frozen leading columns, resizable columns, sort (click the title), per-column filter
// (checklist + one condition), multi-level grouping with header / footer bands and aggregates,
// grid footer with totals, cell tooltips for clipped text, three looks (modern / classic / dark).
// Column layout (slice 0110-11): an optional `layout` {order, hidden, widths, fill} -- or a `layoutId` the
// page-wide `DataGrid.layoutProvider` answers for -- sets which columns show, in which order, how wide,
// and which ONE column takes the free width when the grid is wider than its columns.
// Multiple selection and row dragging are opt-in; column dragging is not supported.
//
// The data logic (formats, filters, sort, grouping, aggregates) is in engine.js and has no DOM.

import {
  ValueType, Operator, allowedOperators, operatorCaption, operandCount,
  formatValue, aggregate, aggregatesFor, buildItems, groupCaption, filterIsActive, createFilter, isBlank,
} from './engine.js';
import { editing } from './editing.js';
import { selectRows } from './selection.js';
import ListenerTracker from '../listener-tracker/listener-tracker-mixin.js';
import { registerInstance, unregisterInstance } from '../instances-registry.js';
let gridSequence = 0;

const OVERSCAN = 6;
const SAMPLE_ROWS = 200;
const FILTER_LIST_LIMIT = 500;

const DEFAULTS = {
  rowHeight: 28,
  headerHeight: 32,
  frozen: 0,
  alternatingRows: true,
  theme: 'modern', // modern | classic | dark
  enableGrouping: true,
  footer: false,
  footerCaption: '{0} rânduri',
  emptyText: 'Nu există date de afișat.',
  autoSize: true,
  layoutId: null, // the id the layout provider is asked for
  layout: null, // an explicit layout; wins over the provider
  proportionalWidths: false, // treat column widths as weights of the available viewport width
};

const COLUMN_DEFAULTS = {
  width: 0, // 0 = from the content
  minWidth: 50,
  valueType: ValueType.Text,
  filter: true,
  sortable: true,
  visible: true,
  align: null, // null = numbers right, the rest left
};

const GROUP_DEFAULTS = {
  dir: 'asc',
  keyPattern: null,
  showHeader: true,
  showFooter: false,
  headerCaption: '{0}: {1} ({2})',
  footerCaption: 'Total {1} ({2})',
  collapsedByDefault: false,
  headerAggregates: false,
  footerAggregates: true,
  indent: 16,
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class DataGrid {
  /** (layoutId) -> {order, hidden, widths, fill} | null. Set once by the page; the grid itself knows nothing about where layouts live. */
  static layoutProvider = null;

  /** (layoutId, rows) -> void. Lets the page keep the last rows a grid showed (the layout editor previews with them). */
  static rowSink = null;

  constructor(container, options = {}) {
    if (!(container instanceof HTMLElement)) throw new Error('DataGrid needs a container element');
    this._abort = new AbortController();
    ListenerTracker.applyTo(this, { debugMode: false, logPrefix: 'DataGrid' });
    this._registryName = `DataGrid-${++gridSequence}`;
    registerInstance(this._registryName, this);
    this._opts = { ...DEFAULTS, ...options };
    const rowHeight = this._opts.rowHeight;
    const mobileRows = matchMedia('(max-width: 980px)');
    const scaleRows = () => {
      this._opts.rowHeight = rowHeight * (mobileRows.matches ? (options.mobileRowScale || 1) : 1);
      if (options.headerHeight === undefined) this._opts.headerHeight = this._opts.rowHeight;
    };
    scaleRows();
    // the header is as tall as a row unless the page asks for another height
    if (options.headerHeight === undefined) this._opts.headerHeight = this._opts.rowHeight;
    this._container = container;
    this._rows = [];
    this._columns = [];
    this._filters = new Map();
    this._sort = null;
    this._groups = [];
    this._collapsed = new Set();
    this._items = [];
    this._shownRows = 0;
    this._selected = null;
    this._selection = new Set();
    this._selectionAnchor = null;
    this._firstRendered = -1;
    this._popup = null;
    this._frame = 0;
    this._fillKey = null;
    this._extra = 0;
    this._widthScale = 1;

    this._build();
    this.setColumns(options.columns || []);
    this.setGroups(options.groups || []);
    this.setRows(options.rows || []);
    if (options.mobileRowScale) mobileRows.addEventListener('change', () => {
      scaleRows(); this._refresh({ rebuildHeader: true });
    }, { signal: this._abort.signal });
  }

  // ------------------------------------------------------------------ public API
  get shownRowCount() { return this._shownRows; }
  get rowCount() { return this._rows.length; }
  getSelectedRows() { return this._opts.multiSelect ? [...this._selection] : (this._selected ? [this._selected] : []); }
  selectRow(rowId, { notify = false } = {}) {
    const row = this._rows.find((item) => item[this._opts.rowKey] === rowId);
    if (!row) return false;
    if (notify) this._select(row);
    else {
      this._selected = row;
      if (this._opts.multiSelect) { this._selection = new Set([row]); this._selectionAnchor = row; }
      this._renderWindow(true);
    }
    return true;
  }
  get isFiltered() { return [...this._filters.values()].some(filterIsActive); }
  get isGrouped() { return this._groups.length > 0; }
  get sort() { return this._sort ? { ...this._sort } : null; }

  /** The columns as they are now, in order: {key, title, visible, width} (width = the base width, without the free space a fill column takes). */
  getColumnState() {
    return this._columns.map((c) => ({ key: c.key, title: c.title, visible: c.visible, width: c.width }));
  }

  setTheme(theme) {
    if (!['modern', 'classic', 'dark'].includes(theme)) throw new Error(`Unknown grid theme: ${theme}`);
    this._opts.theme = theme;
    this._root.dataset.theme = theme;
  }

  /** The data row shown by a rendered row node (or any element inside it); null for headers, group bands and anything outside the grid. */
  rowFromNode(node) {
    const rowNode = node?.closest?.('.dgv__row');
    const item = rowNode && this._root.contains(rowNode) ? this._items[Number(rowNode.dataset.i)] : null;
    return item?.kind === 'row' ? item.row : null;
  }

  setColumns(columns) {
    const seen = new Set();
    this._columns = columns.map((c) => {
      if (!c || !c.key) throw new Error('Every grid column needs a key');
      if (seen.has(c.key)) throw new Error(`Duplicate grid column key: ${c.key}`);
      seen.add(c.key);
      const col = { ...COLUMN_DEFAULTS, title: c.key, ...c };
      if (col.aggregate && !aggregatesFor(col.valueType).includes(col.aggregate)) {
        throw new Error(`Aggregate ${col.aggregate} is not offered for ${col.valueType} column ${col.key}`);
      }
      return col;
    });
    this._columns = this._applyLayout(this._columns);
    this._filters = new Map();
    this._sort = null;
    this._sized = false;
    this._refresh({ rebuildHeader: true });
  }

  /** Replaces the rows. With keepWidths the columns keep the widths they have (a tree node shows another part of the same rows). */
  setRows(rows, { keepWidths = false } = {}) {
    if (this.hasEdit) throw new Error('Confirmați sau anulați editarea înainte de reîncărcare.');
    if (!Array.isArray(rows)) throw new Error('setRows needs an array');
    this._rows = rows;
    this._selection.clear();
    this._selectionAnchor = null;
    if (this._opts.layoutId && typeof DataGrid.rowSink === 'function') DataGrid.rowSink(this._opts.layoutId, rows);
    this._selected = null;
    if (!keepWidths) this._sized = false;
    this._refresh({ rebuildHeader: true });
  }

  sortBy(key, dir) {
    if (key !== null && !this._col(key)) throw new Error(`Unknown column: ${key}`);
    this._sort = key === null || dir === null ? null : { key, dir: dir === 'desc' ? 'desc' : 'asc' };
    this._refresh({ rebuildHeader: true });
    this._emit('onSort', this.sort);
  }

  setFilter(key, filter) {
    if (!this._col(key)) throw new Error(`Unknown column: ${key}`);
    if (!filter || !filterIsActive(filter)) this._filters.delete(key);
    else this._filters.set(key, filter);
    this._refresh({ rebuildHeader: true });
    this._emit('onFilter', { key, filter: filter || null });
  }

  clearFilters() {
    this._filters = new Map();
    this._refresh({ rebuildHeader: true });
    this._emit('onFilter', { key: null, filter: null });
  }

  /** Replaces the group levels. Each level: {key, dir, keyPattern, showHeader, showFooter, headerCaption, footerCaption, ...}. */
  setGroups(levels) {
    this._groups = levels.map((g) => {
      if (!this._col(g.key)) throw new Error(`Cannot group by unknown column: ${g.key}`);
      const level = { ...GROUP_DEFAULTS, ...g };
      if (typeof level.keyPattern === 'string') level.keyPattern = new RegExp(level.keyPattern);
      if (level.dir !== 'asc' && level.dir !== 'desc') throw new Error('A group level needs a direction (asc or desc)');
      return level;
    });
    this._collapsed = new Set();
    this._refresh({ rebuildHeader: true });
  }

  groupBy(key, options = {}) {
    this.setGroups([...this._groups.filter((g) => g.key !== key), { key, ...options }]);
  }

  ungroup(key) {
    this.setGroups(this._groups.filter((g) => g.key !== key));
  }

  clearGrouping() { this.setGroups([]); }

  collapseAll(level) { this._setAllCollapsed(true, level); }
  expandAll(level) { this._setAllCollapsed(false, level); }

  getSelectedRow() { return this._selected; }

  /** Sizes the columns that have no explicit width from the first rows. */
  autoSizeColumns() {
    this._autoSize(true);
    this._applyWidths();
    this._renderWindow(true);
  }

  destroy() {
    this._destroyed = true;
    this.cancelEdit(true);
    this._resizeObserver?.disconnect();
    this.cleanupAllListeners();
    unregisterInstance(this._registryName);
    this._abort.abort();
    cancelAnimationFrame(this._frame);
    this._closePopup();
    this._root.remove();
  }

  // ------------------------------------------------------------------ build
  _build() {
    const { signal } = this._abort;
    const root = el('div', 'dgv');
    root.dataset.theme = this._opts.theme;
    root.tabIndex = 0;
    root.setAttribute('role', 'grid');
    this._root = root;

    this._scroll = el('div', 'dgv__scroll');
    this._head = el('div', 'dgv__head');
    this._viewport = el('div', 'dgv__viewport');
    this._window = el('div', 'dgv__window');
    this._viewport.appendChild(this._window);
    this._empty = el('div', 'dgv__empty', this._opts.emptyText);
    this._foot = el('div', 'dgv__foot');
    this._scroll.append(this._head, this._viewport, this._foot);
    root.append(this._scroll, this._empty);
    this._container.appendChild(root);

    // Synchronous: a window of rows is cheap, and a frame callback would not run in a hidden tab.
    this._scroll.addEventListener('scroll', () => this._renderWindow(false), { signal, passive: true });
    this._resizeObserver = new ResizeObserver(() => {
      if ((this._fillKey || this._opts.proportionalWidths) && this._head.childElementCount) {
        this._applyWidths();
        this._scheduleRender(true);
      } else this._scheduleRender();
    });
    this._resizeObserver.observe(this._scroll);
    this._window.addEventListener('click', (ev) => this._onRowClick(ev), { signal });
    this._window.addEventListener('dblclick', (ev) => this._onRowDblClick(ev), { signal });
    this._window.addEventListener('mouseover', (ev) => this._tipIfClipped(ev), { signal });
    root.addEventListener('keydown', (ev) => this._onKey(ev), { signal });
    document.addEventListener('pointerdown', (ev) => {
      if (this._popup && !this._popup.contains(ev.target)) this._closePopup();
    }, { signal, capture: true });
  }

  // ------------------------------------------------------------------ data -> items
  _col(key) { return this._columns.find((c) => c.key === key); }

  /** Order, visibility, widths and the fill column from the layout (explicit, or the provider's for this grid's id). */
  _applyLayout(cols) {
    const layout = this._opts.layout
      || (this._opts.layoutId && typeof DataGrid.layoutProvider === 'function' ? DataGrid.layoutProvider(this._opts.layoutId) : null);
    this._fillKey = null;
    if (!layout) return cols;
    const hidden = new Set(layout.hidden || []);
    const widths = layout.widths || {};
    cols.forEach((c) => {
      if (hidden.has(c.key)) c.visible = false;
      const w = Number(widths[c.key]);
      if (w > 0) {
        c.width = Math.max(c.minWidth, Math.round(w));
        c._auto = false;
      }
    });
    const rank = new Map((layout.order || []).map((k, i) => [k, i]));
    const place = (c) => (rank.has(c.key) ? rank.get(c.key) : 1e6 + cols.indexOf(c));
    const ordered = [...cols].sort((a, b) => place(a) - place(b));
    if (layout.fill && ordered.some((c) => c.key === layout.fill && c.visible)) this._fillKey = layout.fill;
    return ordered;
  }

  /** Width of a vertical scrollbar of this browser (measured once with a throw-away box). */
  _scrollbarWidth() {
    if (this._sbw === undefined) {
      const probe = document.createElement('div');
      probe.style.cssText = 'position:absolute;visibility:hidden;width:100px;height:100px;overflow:scroll';
      document.body.appendChild(probe);
      this._sbw = probe.offsetWidth - probe.clientWidth;
      probe.remove();
    }
    return this._sbw;
  }

  /**
   * The width the columns can use: the scroll area minus its borders and minus the vertical scrollbar --
   * the one that is there now, or the one the rows will bring (the rows are counted, not yet laid out).
   */
  _availWidth() {
    const s = this._scroll;
    const rowsHeight = this._opts.headerHeight + this._items.length * this._opts.rowHeight
      + (this._opts.footer ? this._opts.rowHeight : 0);
    const needsBar = s.clientHeight > 0 && rowsHeight > s.clientHeight;
    // the bar that is there is measured as it is (zoom rounds it differently from the probe); one that is coming is estimated
    const style = getComputedStyle(s);
    const borders = (parseFloat(style.borderLeftWidth) || 0) + (parseFloat(style.borderRightWidth) || 0);
    const present = Math.max(0, s.offsetWidth - s.clientWidth - borders);
    const inner = s.getBoundingClientRect().width - borders;
    const current = Math.min(s.clientWidth, inner - present);
    const pending = needsBar && present < 1 ? this._scrollbarWidth() : 0;
    // Fractional layout widths at browser zoom must not round the columns past the viewport.
    return Math.max(0, Math.floor(current - pending));
  }

  /** The width a column is drawn at: its own, plus the free width when it is the fill column. */
  _w(col) {
    if (!this._opts.proportionalWidths) return col.width + (col.key === this._fillKey ? this._extra : 0);
    return col.fixedWidth ? col.width : Math.max(0, col.width * this._widthScale - (col.widthDeduction || 0));
  }
  _visibleColumns() { return this._columns.filter((c) => c.visible); }

  _refresh({ rebuildHeader = false } = {}) {
    const engineColumns = this._columns.map((c) => ({
      key: c.key, valueType: c.valueType, format: c.format, formatString: c.formatString, decimals: c.decimals,
    }));
    const { items, shownRows } = buildItems({
      rows: this._rows,
      columns: engineColumns,
      filters: this._filters,
      sort: this._sort,
      groups: this._groups,
      collapsed: this._collapsed,
    });
    this._items = items;
    this._shownRows = shownRows;
    if (!this._sized) this._autoSize(false);
    if (rebuildHeader) {
      this._buildHeader();
      this._applyWidths();
    }
    this._viewport.style.height = `${items.length * this._opts.rowHeight}px`;
    this._empty.hidden = items.length > 0;
    this._buildFooter();
    this._renderWindow(true);
  }

  _setAllCollapsed(collapse, level) {
    // A group is "toggled away from its default" when its id is in the set; work against the default.
    this._groups.filter((g) => g.key).forEach((g, lv) => {
      if (level !== undefined && lv !== level) return;
      // every id of this level, including those hidden inside collapsed parents
      this._allGroupIds(lv).forEach((id) => {
        const wantsToggled = collapse !== !!g.collapsedByDefault;
        if (wantsToggled) this._collapsed.add(id);
        else this._collapsed.delete(id);
      });
    });
    this._refresh();
    this._emit('onGroupToggle', { all: true, collapsed: collapse });
  }

  _allGroupIds(level) {
    // Re-run the grouping with everything expanded to learn every id of one level.
    const engineColumns = this._columns.map((c) => ({
      key: c.key, valueType: c.valueType, format: c.format, formatString: c.formatString, decimals: c.decimals,
    }));
    const open = this._groups.map((g) => ({ ...g, collapsedByDefault: false }));
    const { items } = buildItems({
      rows: this._rows, columns: engineColumns, filters: this._filters, sort: this._sort, groups: open, collapsed: new Set(),
    });
    return items.filter((i) => i.kind === 'gh' && i.level === level).map((i) => i.id);
  }

  _toggleGroup(item) {
    if (this._collapsed.has(item.id)) this._collapsed.delete(item.id);
    else this._collapsed.add(item.id);
    this._refresh();
    this._emit('onGroupToggle', { id: item.id, key: item.key, collapsed: !item.collapsed });
  }

  // ------------------------------------------------------------------ widths
  _autoSize(force) {
    const canvas = this._canvas || (this._canvas = document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    const style = getComputedStyle(this._root);
    const baseFont = `${style.fontSize || '14px'} ${style.fontFamily || 'sans-serif'}`;
    ctx.font = baseFont;
    const sample = this._rows.slice(0, SAMPLE_ROWS);
    this._columns.forEach((col) => {
      if (!force && !this._opts.autoSize) {
        if (!col.width) col.width = 120;
        return;
      }
      // a width the page or the user set stays; a width we computed earlier is recomputed
      if (col.width && !col._auto && !force) return;
      ctx.font = `700 ${baseFont}`;
      let widest = ctx.measureText(col.title).width + 60; // title + sort arrow + filter button
      ctx.font = baseFont;
      sample.forEach((row) => {
        const w = ctx.measureText(formatValue(row[col.key], col)).width + 28;
        if (w > widest) widest = w;
      });
      if (col.aggregate) widest = Math.max(widest, 90);
      col.width = Math.round(Math.min(Math.max(widest, col.minWidth), 420));
      col._auto = true;
    });
    this._sized = true;
  }

  _applyWidths() {
    const cols = this._visibleColumns();
    const base = cols.reduce((s, c) => s + c.width, 0);
    const weighted = cols.filter((col) => !col.fixedWidth).reduce((sum, col) => sum + col.width, 0);
    const fixed = cols.filter((col) => col.fixedWidth).reduce((sum, col) => sum + col.width, 0);
    const deductions = cols.reduce((sum, col) => sum + (col.widthDeduction || 0), 0);
    this._widthScale = weighted ? Math.max(0, this._availWidth() - fixed + deductions) / weighted : 1;
    this._extra = this._fillKey ? Math.max(0, this._availWidth() - base) : 0;
    const total = this._opts.proportionalWidths ? cols.reduce((sum, col) => sum + this._w(col), 0) : base + this._extra;
    this._totalWidth = total;
    [this._head, this._window, this._foot].forEach((node) => { node.style.minWidth = `${total}px`; });
    this._viewport.style.minWidth = `${total}px`;
    this._head.querySelectorAll('.dgv__hc').forEach((cell) => {
      cell.style.width = `${this._w(this._col(cell.dataset.key))}px`;
    });
    this._positionFrozen(this._head);
    this._buildFooter();
  }

  _frozenOffsets() {
    const cols = this._visibleColumns();
    const offsets = new Map();
    let x = 0;
    cols.forEach((c, i) => {
      if (i < this._opts.frozen) offsets.set(c.key, x);
      x += this._w(c);
    });
    return offsets;
  }

  _positionFrozen(rowNode) {
    const offsets = this._frozenOffsets();
    rowNode.querySelectorAll('[data-key]').forEach((cell) => {
      if (offsets.has(cell.dataset.key)) {
        cell.classList.add('is-frozen');
        cell.style.left = `${offsets.get(cell.dataset.key)}px`;
      }
    });
  }

  // ------------------------------------------------------------------ header
  _buildHeader() {
    const { signal } = this._abort;
    this._head.textContent = '';
    this._head.style.height = `${this._opts.headerHeight}px`;
    this._visibleColumns().forEach((col) => {
      const cell = el('div', 'dgv__hc');
      cell.dataset.key = col.key;
      cell.style.width = `${this._w(col)}px`;
      if (this._isRight(col)) cell.classList.add('is-right');

      if (col.headerAction) {
        // the whole header cell is one button (an emoji): the grid's own command, e.g. the "+" of the ADE attendance grid
        const action = el('button', 'dgv__head-action', col.headerAction.text);
        action.type = 'button';
        action.title = col.headerAction.title || col.title;
        action.setAttribute('aria-label', action.title);
        action.disabled = Boolean(col.headerAction.disabled);
        action.addEventListener('click', (ev) => { ev.stopPropagation(); this._emit('onHeaderAction', { key: col.key }); }, { signal });
        cell.appendChild(action);
        cell.classList.add('is-action');
      } else {
        const title = el('span', 'dgv__title', col.title);
        title.title = col.title;
        cell.appendChild(title);
      }

      if (this._sort && this._sort.key === col.key) {
        cell.appendChild(el('span', 'dgv__sort', this._sort.dir === 'asc' ? '▲' : '▼'));
      }
      if (col.filter) {
        const btn = el('button', 'dgv__fb', '▾');
        btn.type = 'button';
        btn.title = 'Filtrează coloana';
        btn.setAttribute('aria-label', `Filtrează ${col.title}`);
        if (filterIsActive(this._filters.get(col.key))) btn.classList.add('is-active');
        btn.addEventListener('click', (ev) => { ev.stopPropagation(); this._openFilter(col, btn); }, { signal });
        cell.appendChild(btn);
      }
      if (!col.fixedWidth) {
        const grip = el('span', 'dgv__rs');
        grip.addEventListener('pointerdown', (ev) => this._startResize(ev, col), { signal });
        grip.addEventListener('click', (ev) => ev.stopPropagation(), { signal });
        cell.appendChild(grip);
      }

      if (col.sortable) cell.addEventListener('click', () => this._cycleSort(col), { signal });
      cell.addEventListener('contextmenu', (ev) => { ev.preventDefault(); this._openMenu(col, ev.clientX, ev.clientY); }, { signal });
      this._head.appendChild(cell);
    });
    this._positionFrozen(this._head);
  }

  _isRight(col) {
    return col.align ? col.align === 'right' : col.valueType === ValueType.Number;
  }

  _cycleSort(col) {
    const cur = this._sort && this._sort.key === col.key ? this._sort.dir : null;
    if (cur === null) this.sortBy(col.key, 'asc');
    else if (cur === 'asc') this.sortBy(col.key, 'desc');
    else this.sortBy(null, null);
  }

  _startResize(ev, col) {
    ev.preventDefault();
    ev.stopPropagation();
    const startX = ev.clientX;
    const startW = this._w(col);
    const grip = ev.currentTarget;
    grip.setPointerCapture(ev.pointerId);
    const move = (e) => {
      col.width = Math.max(col.minWidth, Math.round(startW + e.clientX - startX));
      col._auto = false;
      this._applyWidths();
      this._scheduleRender(true);
    };
    const up = () => {
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', up);
      this._emit('onColumnResize', { key: col.key, width: col.width });
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
  }

  // ------------------------------------------------------------------ rows (virtualized)
  _scheduleRender(force = false) {
    if (force) this._firstRendered = -1;
    cancelAnimationFrame(this._frame);
    this._frame = requestAnimationFrame(() => this._renderWindow(force));
  }

  _renderWindow(force) {
    const h = this._opts.rowHeight;
    const viewH = Math.max(0, this._scroll.clientHeight - this._opts.headerHeight - (this._foot.hidden ? 0 : this._opts.rowHeight));
    const first = Math.max(0, Math.floor(this._scroll.scrollTop / h) - OVERSCAN);
    const last = Math.min(this._items.length, Math.ceil((this._scroll.scrollTop + Math.max(viewH, h)) / h) + OVERSCAN);
    if (!force && first === this._firstRendered && this._lastRendered === last) return;
    this._firstRendered = first;
    this._lastRendered = last;

    const cols = this._visibleColumns();
    const frag = document.createDocumentFragment();
    for (let i = first; i < last; i += 1) frag.appendChild(this._renderItem(this._items[i], i, cols));
    this._window.textContent = '';
    this._window.appendChild(frag);
    this._window.style.top = `${first * h}px`;
  }

  _renderItem(item, index, cols) {
    const h = this._opts.rowHeight;
    const row = el('div', 'dgv__row');
    row.style.height = `${h}px`;
    row.dataset.i = String(index);

    if (item.kind === 'row') {
      row.classList.add('is-data');
      if (this._opts.alternatingRows && index % 2 === 1) row.classList.add('is-alt');
      if (this._opts.multiSelect ? this._selection.has(item.row) : item.row === this._selected) row.classList.add('is-selected');
      if (this._opts.dragRows) {
        row.draggable = true;
        row.addEventListener('dragstart', (event) => {
          if (this._opts.multiSelect && !this._selection.has(item.row)) this._select(item.row);
          this._emit('onRowDragStart', { event, rows: this.getSelectedRows() });
        });
        row.addEventListener('dragend', (event) => this._emit('onRowDragEnd', { event }));
      }
      const indent = this._groups.length ? this._indentOf(this._groups.length) : 0;
      cols.forEach((col, ci) => {
        const text = formatValue(item.row[col.key], col);
        const cell = this._cell(col, text);
        if (col.selectionCheckbox && this._opts.multiSelect) {
          const indicator = document.createElement('input');
          indicator.type = 'checkbox'; indicator.checked = this._selection.has(item.row);
          indicator.setAttribute('aria-label', col.title || 'Selectat');
          indicator.addEventListener('click', (event) => {
            event.stopPropagation(); this._select(item.row, { shiftKey: event.shiftKey, toggle: true });
          });
          cell.replaceChildren(indicator); cell.classList.add('is-checkbox');
        } else if (col.display === 'checkbox') {
          const indicator = document.createElement('input');
          indicator.type = 'checkbox';
          indicator.checked = Boolean(item.row[col.key]);
          indicator.disabled = col.editor !== 'checkbox' || !this.canEdit(item.row, col);
          indicator.tabIndex = indicator.disabled ? -1 : 0;
          indicator.setAttribute('aria-label', col.title || col.key);
          cell.replaceChildren(indicator);
          cell.classList.add('is-checkbox');
          if (!indicator.disabled) {
            indicator.addEventListener('click', (event) => event.stopPropagation());
            indicator.addEventListener('change', async () => {
              const checked = indicator.checked;
              indicator.checked = Boolean(item.row[col.key]);
              if (this.hasEdit && !await this.commitEdit()) return;
              if (this.beginEdit(item.row, col.key)) {
                this._edit.input.checked = checked; this.commitEdit();
              }
            });
          }
        }
        if (col.display === 'button') {
          const button = el('button', 'dgv__cell-action', col.actionText);
          button.type = 'button';
          button.setAttribute('aria-label', col.actionLabel || col.title || 'Deschide');
          button.addEventListener('click', (event) => {
            event.stopPropagation(); this._emit('onCellAction', { row: item.row, key: col.key });
          });
          cell.replaceChildren(button); cell.classList.add('is-action');
        }
        if (this.canEdit(item.row, col)) cell.classList.add('is-editable');
        if (this._edit?.row === item.row && this._edit.key === col.key) {
          cell.textContent = '';
          cell.append(this._edit.host);
        }
        if (ci === 0 && indent) cell.style.paddingLeft = `${indent + 8}px`;
        row.appendChild(cell);
      });
    } else {
      this._renderBand(row, item, cols);
    }
    this._positionFrozen(row);
    return row;
  }

  _indentOf(levels) {
    return this._groups.slice(0, levels).reduce((s, g) => s + (g.indent || 0), 0);
  }

  _cell(col, text, extraClass) {
    const cell = el('div', 'dgv__cell');
    if (extraClass) cell.classList.add(extraClass);
    cell.dataset.key = col.key;
    cell.style.width = `${this._w(col)}px`;
    if (this._isRight(col)) cell.classList.add('is-right');
    cell.textContent = text;
    return cell;
  }

  _renderBand(row, item, cols) {
    const g = this._groups.filter((x) => x.key)[item.level];
    const gcol = this._col(g.key);
    const isHeader = item.kind === 'gh';
    row.classList.add(isHeader ? 'is-gh' : 'is-gf');
    row.dataset.level = String(item.level);
    const caption = groupCaption(isHeader ? g.headerCaption : g.footerCaption, gcol.title, item.key, item.count);
    const withAggregates = isHeader ? g.headerAggregates : g.footerAggregates;
    const indent = this._indentOf(item.level);

    // The caption takes the place of every leading column that shows no aggregate, so a long
    // «Luna 01.2026 (2)» is not cut at the width of a narrow first column.
    let span = cols.length;
    if (withAggregates) {
      const firstAgg = cols.findIndex((c) => c.aggregate);
      if (firstAgg >= 0) span = Math.max(1, firstAgg);
    }

    cols.forEach((col, ci) => {
      if (ci > 0 && ci < span) return;
      let text = '';
      if (withAggregates && col.aggregate) text = this._aggregateText(col, item.rows);
      const cell = this._cell(col, '', 'is-band');
      if (ci === 0) {
        cell.style.width = `${cols.slice(0, span).reduce((sum, c) => sum + this._w(c), 0)}px`;
        const label = el('span', 'dgv__caption');
        if (isHeader) label.appendChild(el('span', 'dgv__chev', item.collapsed ? '▸' : '▾'));
        label.appendChild(document.createTextNode(caption));
        label.style.paddingLeft = `${indent}px`;
        cell.appendChild(label);
      }
      if (text) cell.appendChild(el('span', 'dgv__aggv', text));
      row.appendChild(cell);
    });
  }

  _aggregateText(col, rows) {
    const result = aggregate(col.aggregate, rows.map((r) => r[col.key]), col.valueType);
    if (result === null) return '';
    if (result.valueType === col.valueType) {
      return formatValue(result.value, { ...col, format: col.aggregateFormat || col.format });
    }
    return formatValue(result.value, { valueType: ValueType.Number, format: 'general', decimals: 0 });
  }

  // ------------------------------------------------------------------ footer
  _buildFooter() {
    const cols = this._visibleColumns();
    const hasAggregate = cols.some((c) => c.aggregate);
    const show = this._opts.footer && (hasAggregate || this._opts.footerCaption || this._opts.footerAction);
    this._foot.hidden = !show;
    this._foot.textContent = '';
    if (!show) return;
    this._foot.style.height = `${this._opts.rowHeight}px`;
    const rows = this._items.filter((i) => i.kind === 'row').map((i) => i.row);
    cols.forEach((col, ci) => {
      let text = '';
      if (col.aggregate) text = this._aggregateText(col, rows);
      else if (ci === 0 && this._opts.footerCaption) text = this._opts.footerCaption.replace('{0}', String(rows.length));
      const cell = this._cell(col, text, 'is-foot');
      if (ci === 0 && this._opts.footerAction) {
        const action = this._opts.footerAction;
        const button = el('button', 'dgv__footer-action', action.label);
        button.type = 'button';
        if (action.title) { button.title = action.title; button.setAttribute('aria-label', action.title); }
        if (action.active) button.classList.add('is-active');
        if (action.menu) button.setAttribute('aria-haspopup', 'menu');
        button.addEventListener('click', () => this._emit('onFooterAction', { button }));
        // withCaption keeps the row count beside the button instead of replacing it
        if (action.withCaption) { button.classList.add('dgv__footer-action--lead'); cell.replaceChildren(button, el('span', 'dgv__footer-caption', text)); }
        else cell.replaceChildren(button);
      }
      this._foot.appendChild(cell);
    });
    this._positionFrozen(this._foot);
  }

  // ------------------------------------------------------------------ selection + keys
  _itemFromEvent(ev) {
    const rowNode = ev.target.closest('.dgv__row');
    if (!rowNode) return null;
    return { node: rowNode, item: this._items[Number(rowNode.dataset.i)] };
  }

  async _onRowClick(ev) {
    const key = ev.target.closest('.dgv__cell')?.dataset.key;
    const hit = this._itemFromEvent(ev);
    if (!hit || !hit.item) return;
    if (this._edit && !await this.commitEdit()) return;
    if (this._destroyed) return;
    this._activeKey = key;
    if (hit.item.kind === 'gh') { this._toggleGroup(hit.item); return; }
    if (hit.item.kind !== 'row') return;
    this._select(hit.item.row, ev);
    if (this._opts.multiSelect) { this._root.focus({ preventScroll: true }); return; }
    if (matchMedia('(min-width: 981px) and (pointer: fine)').matches && this.beginEdit(hit.item.row, key)) return;
    this._root.focus({ preventScroll: true });
  }

  _onRowDblClick(ev) {
    const hit = this._itemFromEvent(ev);
    if (hit?.item?.kind === 'row' && this.beginEdit(hit.item.row, ev.target.closest('.dgv__cell')?.dataset.key)) return;
    if (hit && hit.item && hit.item.kind === 'row') this._emit('onRowDblClick', hit.item.row);
  }

  _select(row, event = {}) {
    this._selected = row;
    if (this._opts.multiSelect) {
      const ordered = this._items.filter((item) => item.kind === 'row').map((item) => item.row);
      this._selection = selectRows(this._selection, ordered, row, this._selectionAnchor, event);
      if (!event.shiftKey) this._selectionAnchor = row;
    }
    // Do not rebuild the clicked row here: replacing it between the first and
    // second click prevents the browser from dispatching `dblclick`.
    this._window.querySelectorAll('.dgv__row[data-i]').forEach((node) => {
      const item = this._items[Number(node.dataset.i)];
      const selected = this._opts.multiSelect ? this._selection.has(item?.row) : item?.row === row;
      node.classList.toggle('is-selected', selected);
      if (this._opts.multiSelect && item?.kind === 'row') {
        node.setAttribute('aria-selected', String(selected));
        node.querySelectorAll('.dgv__cell').forEach((cell) => {
          if (this._col(cell.dataset.key)?.selectionCheckbox) cell.querySelector('input').checked = selected;
        });
      }
    });
    this._emit('onSelect', row);
    if (this._opts.multiSelect) this._emit('onSelectionChange', this.getSelectedRows());
  }

  _onKey(ev) {
    if (this._edit) return;
    if (ev.key === 'Enter' && this._selected) {
      if (this.beginEdit(this._selected, this._activeKey || this._visibleColumns().find((c) => this.canEdit(this._selected, c))?.key)) ev.preventDefault();
      return;
    }
    if (this._popup) return;
    const dataIdx = this._items.map((it, i) => (it.kind === 'row' ? i : -1)).filter((i) => i >= 0);
    if (!dataIdx.length) return;
    const cur = dataIdx.findIndex((i) => this._items[i].row === this._selected);
    let next = null;
    if (ev.key === 'ArrowDown') next = Math.min(dataIdx.length - 1, cur + 1);
    else if (ev.key === 'ArrowUp') next = Math.max(0, cur < 0 ? 0 : cur - 1);
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = dataIdx.length - 1;
    if (next === null) return;
    ev.preventDefault();
    const itemIndex = dataIdx[next];
    if (this._opts.multiSelect) this._select(this._items[itemIndex].row, ev);
    else this._selected = this._items[itemIndex].row;
    this._ensureVisible(itemIndex);
    this._renderWindow(true);
    this._emit('onSelect', this._selected);
  }

  _ensureVisible(itemIndex) {
    const h = this._opts.rowHeight;
    const top = itemIndex * h;
    const viewH = this._scroll.clientHeight - this._opts.headerHeight - (this._foot.hidden ? 0 : h);
    if (top < this._scroll.scrollTop) this._scroll.scrollTop = top;
    else if (top + h > this._scroll.scrollTop + viewH) this._scroll.scrollTop = top + h - viewH;
  }

  _tipIfClipped(ev) {
    const cell = ev.target.closest('.dgv__cell');
    if (!cell || cell.classList.contains('is-band')) return;
    cell.title = cell.scrollWidth > cell.clientWidth ? cell.textContent : '';
  }

  _emit(name, payload) {
    const handler = this._opts[name];
    if (typeof handler !== 'function') return;
    try {
      handler(payload, this);
    } catch (err) {
      console.error(`[DataGrid] ${name} handler failed`, err);
    }
  }

  // ------------------------------------------------------------------ popups
  _closePopup() {
    if (this._popup) {
      this._popup.remove();
      this._popup = null;
    }
  }

  _placePopup(popup, x, y) {
    this._root.appendChild(popup);
    const r = popup.getBoundingClientRect();
    const left = Math.min(Math.max(8, x), window.innerWidth - r.width - 8);
    const top = Math.min(Math.max(8, y), window.innerHeight - r.height - 8);
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
    this._popup = popup;
    popup.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { this._closePopup(); this._root.focus(); } });
  }

  _openMenu(col, x, y) {
    this._closePopup();
    const menu = el('div', 'dgv__popup dgv__menu');
    menu.setAttribute('role', 'menu');
    const add = (label, action, disabled) => {
      const b = el('button', 'dgv__mi', label);
      b.type = 'button';
      b.disabled = !!disabled;
      b.addEventListener('click', () => { this._closePopup(); action(); });
      menu.appendChild(b);
    };
    if (col.sortable) {
      add('Sortează crescător', () => this.sortBy(col.key, 'asc'));
      add('Sortează descrescător', () => this.sortBy(col.key, 'desc'));
      add('Elimină sortarea', () => this.sortBy(null, null), !(this._sort && this._sort.key === col.key));
    }
    if (col.filter) {
      menu.appendChild(el('div', 'dgv__sep'));
      add('Filtrează...', () => this._openFilter(col, this._head.querySelector(`[data-key="${CSS.escape(col.key)}"]`)));
      add('Șterge filtrul', () => this.setFilter(col.key, null), !filterIsActive(this._filters.get(col.key)));
    }
    if (this._opts.enableGrouping) {
      menu.appendChild(el('div', 'dgv__sep'));
      const grouped = this._groups.some((g) => g.key === col.key);
      if (!grouped) add('Grupează după această coloană', () => this.groupBy(col.key, col.valueType === ValueType.DateTime ? { keyPattern: /^\S+/ } : {}));
      else add('Elimină gruparea', () => this.ungroup(col.key));
      add('Desface toate grupurile', () => this.expandAll(), !this.isGrouped);
      add('Strânge toate grupurile', () => this.collapseAll(), !this.isGrouped);
    }
    this._placePopup(menu, x, y);
    const first = menu.querySelector('button:not([disabled])');
    if (first) first.focus();
  }

  _distinctDisplayValues(col) {
    const set = new Set();
    this._rows.forEach((row) => set.add(formatValue(row[col.key], col)));
    return [...set].sort((a, b) => {
      if (a === '') return -1;
      if (b === '') return 1;
      return a.localeCompare(b, 'ro', { numeric: true, sensitivity: 'base' });
    });
  }

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
}

Object.defineProperties(DataGrid.prototype, Object.getOwnPropertyDescriptors(editing));
export { ValueType, Operator };
