import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { Combobox } from '../components/combobox/combobox.js';
import { DatePicker } from '../components/datepicker/datepicker.js';
import { showMessage as paintMessage } from '../portal/messages.js';
import { openMenu } from './menu.js';
import { startSessionTimer } from './session.js';
import { bindCatalogs } from './catalogs.js';
import { bindAnnual } from './annual.js';
import { bindReports, outputReport } from './reports.js';
import { bindHelp } from './help.js';
import { bindNoticeConfig } from './payment-notice-config.js';
import { bindBankAssociations } from './bank-associations.js';
import { confirmBox, pickBox, reasonBox } from '../utils/confirm-box.js';

const $ = (id) => document.getElementById(id);
// The notice strip stays empty unless something went wrong (operator, 10.10.2026): confirmations are not written there.
function showMessage(k_element, k_text, k_kind) { paintMessage(k_element, k_kind === 'info' ? '' : k_text, k_kind); }
const state = { context: null, unit: null, subunit: null, pending: 0, subunitCombo: null, showHiddenGroups: false, groupsSequence: 0, monthId: null, groupId: null, rows: [], shownRows: [],
  selected: null, grid: null, monthTree: null, groupTree: null, yearCombo: null, monthCombo: null, groupCombo: null, year: null, loadedYears: new Map(), groupsRequest: null, plan: { hidden: [], nameWidth: null }, ledger: 'receipt', request: 0,
  childFilter: { presence: 'all', payments: false, refunds: false }, leftCollapsed: false, annual: null };

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const PORTAL_TOKEN_KEY = 'kbot-portal-token';

function portalToken() {
  try { return window.sessionStorage.getItem(PORTAL_TOKEN_KEY) || ''; } catch (err) { console.error('[ade] sessionStorage is blocked', err); return ''; }
}

// Back to the portal, which returns here afterwards: to sign in (dropToken) or, with a live
// session and no unit opened yet, to pick the unit.
function toPortal(dropToken) {
  if (dropToken) {
    try { window.sessionStorage.removeItem(PORTAL_TOKEN_KEY); } catch (err) { console.error('[ade] sessionStorage is blocked', err); }
  }
  window.location.replace('/portal?next=' + encodeURIComponent('/adechit'));
}

async function api(path, options = {}) {
  const headers = { Accept: 'application/json', 'X-Ade-Unit': state.unit, 'X-Portal-Token': portalToken(), ...subunitHeader(), ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['Idempotency-Key'] = options.key || idempotencyKey();
  const writing = Boolean(options.method && options.method !== 'GET');
  if (writing) state.pending += 1;   // a save in flight blocks a subunit switch
  let response; let result;
  try {
    response = await fetch(path, { ...options, headers });
    result = await response.json().catch(() => ({}));
  } finally { if (writing) state.pending -= 1; }
  if (response.status === 401 || result.reason === 'UNITATE_NEDESCHISA') { toPortal(response.status === 401); throw new Error(result.error || 'Autentificați-vă.'); }
  if (!response.ok) throw Object.assign(new Error(result.error || `Eroare HTTP ${response.status}`), { reason: result.reason, details: result.details });
  return result;
}

// SLICE-ADE10: the working context is the unit (DC) plus the subunit chosen in THIS browser tab.
// sessionStorage is per tab, so two tabs can work in two subunits; the server checks both on every request.
const subunitKey = (unit) => `ade.subunit.${unit}`;
function subunitHeader() { return state.subunit == null ? {} : { 'X-Ade-Subunit': String(state.subunit) }; }
function storedSubunit(unit) {
  try { return window.sessionStorage.getItem(subunitKey(unit)); } catch (err) { console.error('[ade] sessionStorage is blocked', err); return null; }
}
function rememberSubunit(unit, id) {
  try {
    if (id == null) window.sessionStorage.removeItem(subunitKey(unit)); else window.sessionStorage.setItem(subunitKey(unit), String(id));
  } catch (err) { console.error('[ade] sessionStorage is blocked', err); }
}

function report(error) { showMessage($('ade-message'), error.message, 'error'); }
function money(value) { return Number(value || 0).toLocaleString('ro-RO', { maximumFractionDigits: 2 }); }
function dateText(value) { return value ? new Intl.DateTimeFormat('ro-RO').format(new Date(`${String(value).slice(0, 10)}T12:00:00`)) : ''; }
function selectedMonth() { return state.context.months.find((row) => row.IDL === state.monthId); }
function selectedGroup() { return state.context.groups.find((row) => row.IDG === state.groupId); }

// Group names are shown without the word «Grupa», trimmed and in capitals; the database keeps them as written.
const groupLabel = (k_name) => String(k_name ?? '').replace(/^\s*grupa\s+/i, '').trim().toLocaleUpperCase('ro-RO');
const MONTHS_SHORT = ['Ian.', 'Feb.', 'Mar.', 'Apr.', 'Mai', 'Iun.', 'Iul.', 'Aug.', 'Sep.', 'Oct.', 'Nov.', 'Dec.'];

function lastClosedMonthId() {
  const k_closed = state.context.months.filter((row) => row.Inchisa);
  return k_closed.length ? Math.max(...k_closed.map((row) => row.IDL)) : null;
}

// The lock icons sit at the right end of the month rows: an open month can be closed, the last closed one reopened.
function monthNodes() {
  const k_short = state.leftCollapsed;
  const k_lastClosed = lastClosedMonthId();
  const k_icon = (row) => {
    const k_title = (text) => (k_short ? undefined : text);
    // the padlock tells the state: 🔓 open, 🔒 closed; clicking acts on the open month (close) and on the last closed one (reopen)
    if (!row.Inchisa) return { icon: '🔓', action: 'close', title: k_title('Închide luna') };
    return { icon: '🔒', action: row.IDL === k_lastClosed ? 'reopen' : 'none', title: row.IDL === k_lastClosed ? k_title('Redeschide luna') : undefined };
  };
  return state.context.years.map((year) => ({
    id: `year:${year}`, label: k_short ? String(year) : `Anul ${year}`, bold: true,
    children: state.loadedYears.has(year) ? state.context.months.filter((row) => row.Anul === year)
      .sort((a, b) => b.Luna - a.Luna).map((row) => ({ id: `month:${row.IDL}`,
        label: k_short ? MONTHS_SHORT[row.Luna - 1] : row.LunaT,
        error: Boolean(row.Inchisa), rightIcon: k_icon(row) }))
      : [{ id: `pending:${year}`, label: 'Se încarcă…' }],
  }));
}

function renderMonthTree() {
  const k_open = new Set(state.monthTree.expandedNodes);   // setData forgets which years were open
  state.monthTree.setData(monthNodes());
  k_open.forEach((k_id) => state.monthTree.expandedNodes.add(k_id));
  state.monthTree.isTreeRendered = false; state.monthTree.renderTree('');
}

function groupNodes() {
  return state.context.groups.map((row) => {
    const k_label = groupLabel(row.Grupa);
    // folded panel: the whole name and the educators on hover; unfolded: the educators
    return { id: `group:${row.IDG}`, label: k_label, tooltip: state.leftCollapsed ? [k_label, row.Educators].filter(Boolean).join('\n') : (row.Educators || '') };
  });
}

function renderGroupTree() {
  state.groupTree.setData(groupNodes());
  state.groupTree.isTreeRendered = false; state.groupTree.renderTree('');
}

async function loadYear(year) {
  if (!state.loadedYears.has(year)) {
    const pending = api(`/api/adechit/rows/LunaD?Anul=${year}`).then(({ rows }) => {
      state.context.months = [...state.context.months.filter((row) => row.Anul !== year), ...rows];
    });
    state.loadedYears.set(year, pending);
    pending.catch(() => state.loadedYears.delete(year));
  }
  await state.loadedYears.get(year);
  renderMonthTree();
  syncPickers();
}

// The phone has no «show hidden groups» checkbox: its group combobox always lists every group.
function hiddenGroupsWanted() { return state.showHiddenGroups || mobileQuery.matches; }

async function loadGroups() {
  if (state.monthId == null) {
    state.context.groups = []; state.groupId = null;
    state.groupTree.setData([]); state.groupTree.isTreeRendered = false; state.groupTree.renderTree('');
    syncPickers();
    return;
  }
  if (!state.groupsRequest) {
    const own = ++state.groupsSequence;
    state.context.groups = [];
    state.groupTree.setData([]); state.groupTree.isTreeRendered = false; state.groupTree.renderTree('');
    syncPickers();
    state.groupsRequest = api(`/api/adechit/catalog-data?IDL=${state.monthId}` + (hiddenGroupsWanted() ? '&include_hidden=1' : '')).then(({ groups }) => {
      if (own !== state.groupsSequence) return;
      state.context.groups = [...groups].sort((a, b) => String(a.Grupa || '').localeCompare(String(b.Grupa || ''), 'ro', { sensitivity: 'base' }));
      if (!state.context.groups.some((row) => row.IDG === state.groupId)) state.groupId = null;
      renderGroupTree();
      syncPickers();
    }).catch((error) => { if (own === state.groupsSequence) state.groupsRequest = null; throw error; });
  }
  await state.groupsRequest;
}

async function toggleHiddenGroups(checked) {
  state.showHiddenGroups = checked;
  $('ade-show-hidden-groups').checked = checked;
  state.groupsRequest = null;
  // Invalidate an in-flight reply even before the next group request starts.
  state.groupsSequence += 1;
  if (!state.groupTree) return;
  await loadGroups();
  await loadSituation();
}

function buildTrees() {
  state.monthTree = new TreeView($('ade-month-tree'), { inline: true, autoCollapse: false, selectableLevel: 2, indent: state.leftCollapsed ? 0 : 12,
    showSearchBox: false, onSelect: ({ id }) => { if (String(id).startsWith('month:')) chooseMonth(Number(String(id).split(':')[1])).catch(report); } });
  const months = monthNodes();
  state.monthTree.setData(months);
  const toggle = state.monthTree.toggleNode.bind(state.monthTree);
  state.monthTree.toggleNode = async (id, skip) => {
    try {
      if (!state.monthTree.expandedNodes.has(String(id))) await loadYear(Number(String(id).split(':')[1]));
      toggle(id, skip);
    } catch (error) { report(error); }
  };
  state.monthTree.isTreeRendered = false;
  state.monthTree.renderTree('');

  state.groupTree = new TreeView($('ade-group-tree'), { inline: true, autoCollapse: false, showSearchBox: false,
    onSelect: ({ id }) => chooseGroup(Number(String(id).split(':')[1])).catch(report) });
  state.groupTree.setData([]);
  state.groupTree.isTreeRendered = false;
  state.groupTree.renderTree('');
}

// Phone layout: year, month and group use comboboxes instead of the two trees.
function buildPickers() {
  const k_make = (k_id, k_label, k_onPick, k_extra = {}) => {
    const k_combo = new Combobox($(k_id), { readonly: true, placeholder: k_label, staticData: [], onSelect: k_onPick, ...k_extra });
    k_combo.input.id = `${k_id}-input`;
    k_combo.input.setAttribute('aria-label', k_label);
    return k_combo;
  };
  state.yearCombo = k_make('ade-year-combo', 'Anul', async (value) => {
    state.year = Number(value); state.monthId = null; state.groupId = null;
    state.groupsRequest = null; state.groupsSequence += 1;
    await loadGroups();
    loadSituation(); syncPickers();
    try { await loadYear(state.year); } catch (error) { report(error); }
  });
  // the month list carries the padlock (allowHtml), and is 30% wider than the field (phone)
  state.monthCombo = k_make('ade-month-combo', 'Luna', (k_value) => chooseMonth(Number(k_value)).catch(report), { allowHtml: true });
  state.monthCombo.dropdown.style.setProperty('--combobox-grow', '1.3');
  state.monthLock = document.createElement('span'); state.monthLock.className = 'ade-combo-lock'; state.monthLock.hidden = true;
  state.monthLock.setAttribute('aria-hidden', 'true'); state.monthCombo.container.append(state.monthLock);
  state.groupCombo = k_make('ade-group-combo', 'Grupa', (k_value) => chooseGroup(Number(k_value)).catch(report));
}

// The trees show the chosen month / group even when it was chosen elsewhere (a combobox, a reopen, a reload).
function syncTreeSelection() {
  if (state.monthTree) { state.monthTree.selectedValue = state.monthId == null ? null : `month:${state.monthId}`; state.monthTree.markSelected(); }
  if (state.groupTree) { state.groupTree.selectedValue = state.groupId == null ? null : `group:${state.groupId}`; state.groupTree.markSelected(); }
}

function syncPickers() {
  if (!state.monthCombo || !state.context) return;
  syncTreeSelection();
  state.yearCombo.options.staticData = state.context.years.map((year) => ({ value: String(year), label: String(year) }));
  if (state.year) state.yearCombo.setValue(String(state.year), String(state.year)); else state.yearCombo.clear();
  const k_months = state.context.months.filter((row) => row.Anul === state.year)
    .sort((a, b) => String(a.LunaT || '').localeCompare(String(b.LunaT || ''), 'ro', { sensitivity: 'base' }))
    .map((row) => ({ value: String(row.IDL), label: `<span class="ade-mo-lock">${row.Inchisa ? '🔒' : '🔓'}</span>${escapeHtml(row.LunaT)}`, text: row.LunaT, closed: Boolean(row.Inchisa) }));
  state.monthCombo.options.staticData = k_months;
  const k_month = k_months.find((item) => item.value === String(state.monthId));
  if (k_month) state.monthCombo.setValue(k_month.value, k_month.text); else state.monthCombo.clear();
  state.monthLock.hidden = !k_month; if (k_month) state.monthLock.textContent = k_month.closed ? '🔒' : '🔓';
  const k_groups = state.context.groups.map((row) => ({ value: String(row.IDG), label: groupLabel(row.Grupa) }));
  state.groupCombo.options.staticData = k_groups;
  const k_group = k_groups.find((item) => item.value === (state.groupId == null ? '' : String(state.groupId)));
  if (k_group) state.groupCombo.setValue(k_group.value, k_group.label); else state.groupCombo.clear();
}

function situationColumns(canEdit) {
  const number = (key, title, width = 90) => ({ key, title, valueType: ValueType.Number, width, align: 'right', filter: false, aggregate: 'sum' });
  return [
    { key: 'Nume', title: 'Copil', valueType: ValueType.Text, width: 140, filter: true },
    number('SID', 'S.I. Debit', 100), number('SIC', 'S.I. Credit', 100),
    { ...number('ZilePrezenta', 'Prezență', 100), editable: canEdit, editor: 'number', nullable: false,
      validate: (value) => Number.isInteger(value) && value >= 0 && value <= 31 ? '' : 'Introduceți un număr întreg între 0 și 31.' },
    number('ValoareContract', 'Valoare', 100), number('TotalPlati', 'Plăți', 100),
    number('Retur', 'Restituiri', 100), number('TotalLuna', 'TOTAL', 100),
  ];
}

function decorateRows(rows) {
  return rows.map((row) => ({ ...row, TotalPlati: Number(row.Plata || 0) + Number(row.Plati || 0),
    TotalLuna: row.SFD > 0 ? row.SFD : row.SFC || 0 }));
}

async function saveAttendance({ row, value }) {
  await api('/api/adechit/attendance', { method: 'POST', body: JSON.stringify({ id: row.IDZ,
    version: row.Version, values: { ZilePrezenta: value } }) });
  const result = await api(`/api/adechit/situation/${state.monthId}?IDG=${state.groupId}`);
  const saved = decorateRows(result.rows).find((item) => item.IDZ === row.IDZ);
  showMessage($('ade-message'), 'Prezența și valorile lunare au fost recalculate.', 'info');
  return saved;
}

const childFilterActive = () => state.childFilter.presence !== 'all' || state.childFilter.payments || state.childFilter.refunds;

// The group, the search box and the footer filters (presence / payments / refunds) together decide which children are listed.
function visibleRows() {
  const k_query = $('ade-search').value.trim().toLocaleLowerCase('ro-RO');
  const k_filter = state.childFilter;
  return state.rows.filter((row) => (state.groupId == null || row.IDG === state.groupId)
    && (!k_query || `${row.Nume || ''} ${row.CNP || ''}`.toLocaleLowerCase('ro-RO').includes(k_query))
    && (k_filter.presence === 'all' || (Number(row.ZilePrezenta || 0) > 0) === (k_filter.presence === 'with'))
    && (!k_filter.payments || Number(row.TotalPlati || 0) !== 0)
    && (!k_filter.refunds || Number(row.Retur || 0) !== 0));
}

function filterGrid() {
  state.shownRows = visibleRows();
  state.selected = null;
  $('ade-selected-child').textContent = 'Selectați un copil din tabel.';
  state.grid?.setRows(state.shownRows, { keepWidths: true });
  renderLedger();
}

// Phone layout (keep in step with the 980px breakpoint in adechit.css): the grid keeps Copil, Prezență and TOTAL;
// a long press on a row shows the other figures, the next long press hides them.
const mobileQuery = matchMedia('(max-width: 980px)');
const MOBILE_HIDDEN = ['SID', 'SIC', 'ValoareContract', 'TotalPlati', 'Retur'];
const LONG_PRESS_MS = 500;
// When the grid is too narrow its columns drop out in this order (the phone layout is the last step); the grid never needs a sideways scroll.
const HIDE_STEPS = [[], ['Retur'], ['Retur', 'TotalPlati'], ['Retur', 'TotalPlati', 'ValoareContract'], MOBILE_HIDDEN];
const GRID_SLACK = 64; // vertical scrollbar + borders + the 40px add / move column

function planColumns() {
  const k_avail = $('ade-grid').clientWidth;
  const k_columns = situationColumns(false);
  const k_steps = mobileQuery.matches ? [MOBILE_HIDDEN] : HIDE_STEPS;
  const k_need = (k_hidden, k_skip) => k_columns.filter((c) => !k_hidden.includes(c.key) && c.key !== k_skip)
    .reduce((sum, c) => sum + c.width, 0) + GRID_SLACK;
  if (!k_avail) return { hidden: k_steps[0], nameWidth: null };
  const k_fit = k_steps.find((k_hidden) => k_need(k_hidden) <= k_avail);
  if (k_fit) return { hidden: k_fit, nameWidth: null };
  const k_last = k_steps.at(-1);
  return { hidden: k_last, nameWidth: Math.max(80, k_avail - k_need(k_last, 'Nume')) };
}

function hideRowDetail() { $('ade-row-detail').hidden = true; }

function showRowDetail(k_row, k_node) {
  const k_card = $('ade-row-detail');
  // every figure of the row, in grid order, plus Alte plăți which the grid never shows
  const k_figures = situationColumns(false).filter((c) => c.key !== 'Nume').map((c) => [c.title, k_row[c.key]]);
  k_figures.splice(k_figures.findIndex(([k_title]) => k_title === 'Restituiri'), 0, ['Alte plăți', k_row.Plati]);
  k_card.innerHTML = `<strong>${escapeHtml(k_row.Nume ?? '')}</strong>${k_figures
    .map(([k_label, k_value]) => `<span>${k_label}</span><b>${money(k_value)}</b>`).join('')}`;
  k_card.hidden = false;
  const k_host = k_card.offsetParent.getBoundingClientRect();
  const k_rowBox = k_node.getBoundingClientRect();
  const k_below = k_rowBox.bottom - k_host.top + 2;
  k_card.style.top = `${k_below + k_card.offsetHeight > k_host.height ? Math.max(0, k_rowBox.top - k_host.top - k_card.offsetHeight - 2) : k_below}px`;
}

function wireLongPress() {
  const k_grid = $('ade-grid');
  let k_timer = 0;
  let k_start = null;
  let k_closed = false;
  const k_cancel = () => { clearTimeout(k_timer); k_timer = 0; };
  // Any touch while the card is open closes it (the press that closes it never starts a new long press).
  document.addEventListener('pointerdown', (k_event) => {
    if ($('ade-row-detail').hidden) return;
    hideRowDetail();
    k_closed = true;
    setTimeout(() => { k_closed = false; }, 0);
  }, true);
  k_grid.addEventListener('pointerdown', (k_event) => {
    if (k_closed) { k_closed = false; return; }
    if (!state.plan.hidden.length || !k_event.target.closest('.dgv__row.is-data')) return;
    k_start = { x: k_event.clientX, y: k_event.clientY };
    const k_node = k_event.target.closest('.dgv__row');
    k_timer = setTimeout(() => {
      k_timer = 0;
      const k_row = state.grid?.rowFromNode(k_node);
      if (k_row) showRowDetail(k_row, k_node);
    }, LONG_PRESS_MS);
  });
  k_grid.addEventListener('pointermove', (k_event) => {
    if (k_timer && k_start && Math.hypot(k_event.clientX - k_start.x, k_event.clientY - k_start.y) > 10) k_cancel();
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((k_name) => k_grid.addEventListener(k_name, k_cancel));
  k_grid.addEventListener('contextmenu', (k_event) => { if (mobileQuery.matches) k_event.preventDefault(); });
  mobileQuery.addEventListener('change', () => { hideRowDetail(); if (state.grid) createGrid(); });
  let k_frame = 0;
  const k_refit = () => {
    clearTimeout(k_frame);
    k_frame = setTimeout(() => {
      const k_plan = planColumns();
      if (state.grid && `${k_plan.hidden}|${k_plan.nameWidth}` !== `${state.plan.hidden}|${state.plan.nameWidth}`) createGrid();
    }, 100);
  };
  new ResizeObserver(k_refit).observe(k_grid);
  addEventListener('resize', k_refit);
}

// First column: the header "+" adds a child (child form, then payer form); a row's button moves the child to another group
// (PC: drag the row onto a group of the left tree; phone: the button opens the list of groups).
function actionColumn(k_canMove) {
  return { key: 'Mutare', title: 'Adaugă / mută', width: 40, fixedWidth: true, minWidth: 0, filter: false, sortable: false, valueType: ValueType.Text,
    display: k_canMove ? 'button' : undefined, actionText: '↕️', actionLabel: 'Mută copilul în altă grupă',
    headerAction: { text: '➕', title: 'Adaugă copil', disabled: state.groupId == null } };
}

function createGrid() {
  hideRowDetail();
  state.grid?.destroy();
  state.plan = planColumns();
  const canEdit = Boolean(state.monthId && state.groupId) && !selectedMonth()?.Inchisa;
  // The days of the last closed month stay editable for an account that holds the edit_closed right (role AD_EDIT_INCHISA).
  const canEditDays = canEdit || (Boolean(state.monthId && state.groupId) && selectedMonth()?.IDL === lastClosedMonthId()
    && state.context.permissions.includes('edit_closed') && state.context.permissions.includes('attendance'));
  const canMove = canEdit && !mobileQuery.matches;   // dragging: PC only; the phone uses the icon's group list
  state.grid = new DataGrid($('ade-grid'), { columns: [actionColumn(canEdit), ...situationColumns(canEditDays)], rows: state.shownRows, headerHeight: 32,
    rowKey: 'IDZ', mobileRowScale: 1.2, editable: canEditDays, layoutId: 'ade.attendance', layout: { order: ['Mutare'], fill: 'Nume', hidden: state.plan.hidden, widths: state.plan.nameWidth ? { Nume: state.plan.nameWidth } : {} }, footer: true,
    footerCaption: '{0} copii', frozen: 2,
    footerAction: { label: '🔍', withCaption: true, menu: true, active: childFilterActive(), title: 'Filtrează lista de copii' },
    onFooterAction: ({ button }) => openChildFilterMenu(button), onSelect: selectChild, onCellSave: saveAttendance,
    onCellSaved: ({ row }) => { state.selected = row; loadLedger().catch(report); },
    onEditError: ({ error }) => report(error),
    dragRows: canMove, onRowDragStart: onChildDragStart, onRowDragEnd: onChildDragEnd,
    onRowDblClick: (k_row) => { if (k_row?.IDG != null && k_row.IDP != null) state.payers.openForChild(k_row.IDG, k_row.IDP).catch(report); },
    onHeaderAction: () => addChild().catch(report), onCellAction: ({ row }) => pickGroupFor(row).catch(report) });
  state.grid.sortBy('Nume', 'asc');
}

async function loadSituation() {
  const own = ++state.request;
  state.rows = []; state.shownRows = []; state.selected = null; createGrid(); renderLedger();
  $('ade-selected-child').textContent = 'Selectați un copil din tabel.';
  const month = selectedMonth();
  state.reports?.syncDates();
  $('ade-period').textContent = month ? `${month.LunaT} ${month.Anul}` : 'Alegeți luna';
  $('ade-group-caption').textContent = selectedGroup() ? groupLabel(selectedGroup().Grupa) : 'Alegeți grupa';
  $('ade-refresh').disabled = !month || state.groupId == null;
  if (!month || state.groupId == null) return;
  const result = await api(`/api/adechit/situation/${state.monthId}?IDG=${state.groupId}`);
  if (own !== state.request) return;
  state.rows = decorateRows(result.rows || []);
  state.shownRows = visibleRows();
  state.selected = null;
  createGrid();
  $('ade-period').textContent = `${month.LunaT} ${month.Anul}`;
  $('ade-group-caption').textContent = selectedGroup() ? groupLabel(selectedGroup().Grupa) : 'Alegeți grupa';
  $('ade-selected-child').textContent = 'Selectați un copil din tabel.';
  renderLedger();
}

async function chooseMonth(id) {
  if (!Number.isFinite(id) || id === state.monthId) return;
  const k_previousYear = state.year;
  state.monthId = id;
  state.groupsRequest = null; state.groupsSequence += 1;
  state.year = selectedMonth()?.Anul;
  if (state.monthTree && state.year != null && state.year !== k_previousYear) {
    state.monthTree.expandedNodes.delete(`year:${k_previousYear}`);
    state.monthTree.expandedNodes.add(`year:${state.year}`);
    renderMonthTree();
  }
  syncPickers();
  await loadGroups();
  await loadSituation();
}

async function chooseGroup(id) {
  if (!Number.isFinite(id) || id === state.groupId) return;
  state.groupId = id;
  syncPickers();
  await loadSituation();
}

async function moveChild(k_row, k_targetId) {
  const k_name = (k_id) => groupLabel(state.context.groups.find((k_group) => k_group.IDG === k_id)?.Grupa ?? '');
  if (k_targetId === k_row.IDG || !state.context.groups.some((k_group) => k_group.IDG === k_targetId)) return;
  if (!await confirmBox(`Ești sigur/ă că vrei să muți copilul ${k_row.Nume} din grupa ${k_name(k_row.IDG)} în grupa ${k_name(k_targetId)}?\n\nMutarea se aplică lunilor deschise; lunile închise rămân cu grupa lor.`,
    { title: 'Mutare copil', yes: 'Mută', no: 'Renunță' })) return;
  await withLoading(workHosts(), async () => {
    try {
      clearWork();
      const k_child = (await api(`/api/adechit/rows/Platitori?IDG=${k_row.IDG}`)).rows.find((k_item) => k_item.IDP === k_row.IDP);
      if (!k_child) throw new Error('Copilul nu a fost găsit. Reîncărcați datele.');
      await api('/api/adechit/child-save', { method: 'POST', body: JSON.stringify({ id: k_row.IDP, version: k_child.Version, values: { IDG: k_targetId } }) });
      await loadSituation();
      showMessage($('ade-message'), `Copilul ${k_row.Nume} a fost mutat în grupa ${k_name(k_targetId)}.`, 'info');
    } catch (k_error) { report(k_error); await loadSituation().catch(report); }
  });
}

// Phone (and keyboard): the row's button lists the groups; the chosen one goes to the confirmation.
async function pickGroupFor(k_row) {
  const k_groups = state.context.groups.filter((k_group) => k_group.IDG !== k_row.IDG).map((k_group) => ({ value: k_group.IDG, label: groupLabel(k_group.Grupa) }));
  const k_target = await pickBox('GRUPE', k_groups);
  if (k_target != null) await moveChild(k_row, k_target);
}

// PC: the row is dragged onto a group of the left tree (never onto a month or year).
let draggedChild = null;
function onChildDragStart({ event }) {
  const k_row = state.grid?.rowFromNode(event.target.closest?.('.dgv__row'));
  if (!k_row) return;
  draggedChild = k_row;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', k_row.Nume || '');
  document.body.classList.add('is-dragging-child');
}
function onChildDragEnd() {
  draggedChild = null;
  document.body.classList.remove('is-dragging-child');
  document.querySelectorAll('.is-drop-target').forEach((k_node) => k_node.classList.remove('is-drop-target'));
}
function wireChildDrop() {
  const k_tree = $('ade-group-tree');
  const k_target = (k_event) => {
    const k_item = draggedChild ? k_event.target.closest?.('.treeview-item') : null;
    const k_value = String(k_item?.dataset.value || '');
    const k_id = k_value.startsWith('group:') ? Number(k_value.slice(6)) : null;
    return k_id != null && k_id !== draggedChild.IDG ? { item: k_item, id: k_id } : null;
  };
  k_tree.addEventListener('dragover', (k_event) => {
    const k_hit = k_target(k_event);
    k_tree.querySelectorAll('.is-drop-target').forEach((k_node) => { if (k_node !== k_hit?.item) k_node.classList.remove('is-drop-target'); });
    if (!k_hit) return;
    k_event.preventDefault(); k_event.dataTransfer.dropEffect = 'move'; k_hit.item.classList.add('is-drop-target');
  });
  k_tree.addEventListener('dragleave', (k_event) => { if (!k_tree.contains(k_event.relatedTarget)) k_tree.querySelectorAll('.is-drop-target').forEach((k_node) => k_node.classList.remove('is-drop-target')); });
  k_tree.addEventListener('drop', (k_event) => {
    const k_hit = k_target(k_event);
    if (!k_hit) return;
    k_event.preventDefault();
    const k_row = draggedChild; onChildDragEnd();
    moveChild(k_row, k_hit.id).catch(report);
  });
}

// "+" of the grid: the child form, then the payer form of the new child; the child lands in the open month by itself.
async function addChild() {
  if (state.groupId == null) throw new Error('Selectați grupa copilului.');
  await state.payers.addChildWithPayer(state.groupId);
}

function setLedger(k_kind) {
  state.ledger = k_kind;
  document.querySelector('.ade-ledger')?.setAttribute('data-kind', k_kind);
  document.querySelectorAll('[data-ledger]').forEach((k_tab) => k_tab.setAttribute('aria-selected', String(k_tab.dataset.ledger === k_kind)));
}

// Receipts first, then the other payments, then refunds; a child with nothing leaves the tab as it was.
function ledgerFor(k_row) {
  if (Number(k_row.Plata || 0) !== 0) return 'receipt';
  if (Number(k_row.Plati || 0) !== 0) return 'other';
  if (Number(k_row.Retur || 0) !== 0) return 'refund';
  return null;
}

function selectChild(row) {
  state.selected = row;
  const k_kind = ledgerFor(row);
  if (k_kind) setLedger(k_kind);
  $('ade-selected-child').textContent = `${row.Nume} · SID ${money(row.SID)} · SIC ${money(row.SIC)}`;
  loadLedger().catch(report);
}

// One entry per visible column; the first column is always the document number. The width classes are
// in adechit.css (ade-col-*), the explanation column takes the remaining width so the table never scrolls sideways.
function ledgerSpec(kind) {
  const dateInput = '<input id="ade-ledger-date" type="date" aria-label="Data documentului">';
  const valueInput = '<input id="ade-ledger-value" inputmode="decimal" aria-label="Valoarea documentului">';
  const explanationInput = '<input id="ade-ledger-explanation" aria-label="Explicația documentului">';
  const date = (row) => dateText(kind === 'other' ? row.DataDoc : row.Data);
  const value = (row) => money(kind === 'refund' ? row.Suma : row.Valoare);
  const common = [
    { title: 'Data', cls: 'ade-col-date', cell: date, draft: dateInput },
    { title: 'Valoare', cls: 'ade-col-value is-number', cell: value, draft: valueInput },
    { title: 'Explicație', cls: 'ade-col-explanation', cell: (row) => row.Explicatie, draft: explanationInput },
  ];
  if (kind === 'receipt') return [{ title: 'Număr', cls: 'ade-col-number', cell: (row) => row.Numar }, ...common];
  if (kind === 'other') return [{ title: 'Număr', cls: 'ade-col-number', cell: (row) => row.NrDoc }, ...common];
  return [{ title: 'Număr', cls: 'ade-col-number', cell: (row) => row.NrDoc }, ...common];
}

function applyChildFilter(k_change) {
  Object.assign(state.childFilter, k_change);
  state.shownRows = visibleRows();
  state.selected = null;
  $('ade-selected-child').textContent = 'Selectați un copil din tabel.';
  createGrid();
  renderLedger();
}

// Popup of the footer button: every entry has an emoji as icon; the active filters carry a check mark.
function openChildFilterMenu(k_button) {
  const k_filter = state.childFilter;
  const k_toggle = (k_key, k_value) => () => applyChildFilter({ [k_key]: k_filter[k_key] === k_value ? 'all' : k_value });
  openMenu(k_button, [
    { icon: '✅', label: 'Doar copii cu prezență', on: k_filter.presence === 'with', run: k_toggle('presence', 'with') },
    { icon: '🚫', label: 'Doar copii fără prezență', on: k_filter.presence === 'without', run: k_toggle('presence', 'without') },
    { icon: '💰', label: 'Doar copii cu plăți', on: k_filter.payments, run: () => applyChildFilter({ payments: !k_filter.payments }) },
    { icon: '↩️', label: 'Doar copii cu restituiri', on: k_filter.refunds, run: () => applyChildFilter({ refunds: !k_filter.refunds }) },
    { icon: '👥', label: 'Arată toți copiii', rule: true, disabled: !childFilterActive(), run: () => applyChildFilter({ presence: 'all', payments: false, refunds: false }) },
  ], { label: 'Filtre copii', up: true, checkable: true });
}

const REFUND_EXPLANATION = 'Restituire sumă';
let receiptMenuCleanup = null;

function receiptActionButton(id, latest = false) {
  return `<button class="ade-cell-button ade-receipt-actions" type="button" data-receipt-id="${id || ''}" ${id ? '' : 'disabled'}
    aria-haspopup="menu" aria-expanded="false" title="${latest ? 'Opțiuni pentru ultima chitanță salvată' : 'Opțiuni chitanță'}" aria-label="Opțiuni chitanță">📥</button>`;
}

// A refund has a payment order (Dispoziție de plată) to print for the cash desk.
function orderActionButton(k_id) {
  return `<button class="ade-cell-button ade-order-actions" type="button" data-order-id="${k_id}" aria-haspopup="menu" aria-expanded="false"
    title="Dispoziție de plată" aria-label="Dispoziție de plată">📥</button>`;
}

// Cancelling is offered in the open month and in the LAST closed month only; older closed months show no button
// (the server refuses them too), and refunds cannot be cancelled yet (server rule M03).
const documentId = (k_kind, k_row) => (k_kind === 'receipt' ? k_row.IDC : k_row.IDA);
function canCancelDocument(k_kind, k_row) {
  const k_month = selectedMonth();
  if (!state.context.permissions.includes('cancel') || !k_month || k_kind === 'refund' || k_row.Cancelled || documentId(k_kind, k_row) == null) return false;
  return !k_month.Inchisa || k_month.IDL === lastClosedMonthId();
}

async function cancelDocument(k_kind, k_row) {
  const k_label = k_kind === 'receipt' ? `chitanța ${k_row.Serie || ''} ${k_row.Numar ?? ''}`.replace(/\s+/g, ' ').trim() : `documentul ${k_row.NrDoc || ''}`.trim();
  const k_reason = await reasonBox(`Ești sigur/ă că vrei să anulezi ${k_label} în valoare de ${money(k_row.Valoare)}?\n\nSituația copilului va fi recalculată, iar anularea nu poate fi retrasă.`,
    { title: 'Anulare document', label: 'Motivul anulării', yes: 'Anulează', no: 'Renunță' });
  if (!k_reason) return;
  await api('/api/adechit/cancel', { method: 'POST', body: JSON.stringify({
    kind: k_kind, id: documentId(k_kind, k_row), version: k_row.Version, reason: k_reason }) });
  await reloadAfterDocument();
  showMessage($('ade-message'), 'Documentul a fost anulat și situația a fost recalculată.', 'info');
}

// After a document is saved or cancelled: the situation is read again and the selected child keeps its place.
async function reloadAfterDocument() {
  const k_selectedId = state.selected.IDZ;
  await loadSituation();
  state.selected = state.rows.find((row) => row.IDZ === k_selectedId) || null;
  if (state.selected) $('ade-selected-child').textContent = `${state.selected.Nume} · SID ${money(state.selected.SID)} · SIC ${money(state.selected.SIC)}`;
  await loadLedger();
}

async function fetchOutput(path, accept) {
  const response = await fetch(path, { headers: { 'X-Ade-Unit': state.unit, 'X-Portal-Token': portalToken(), ...subunitHeader(), Accept: accept } });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (response.status === 401 || result.reason === 'UNITATE_NEDESCHISA') toPortal(response.status === 401);
    throw new Error(result.error || `Eroare HTTP ${response.status}`);
  }
  return response;
}

const fetchReceiptOutput = (id, kind) => fetchOutput(`/api/adechit/receipts/${id}/${kind}`, kind === 'pdf' ? 'application/pdf' : 'text/html');

async function outputReceipt(id, kind) {
  // Open synchronously from the menu click so the browser accepts the print window.
  const printWindow = kind === 'print' ? window.open('', '_blank') : null;
  try {
    if (kind === 'print' && !printWindow) throw new Error('Browserul a blocat fereastra de listare. Permiteți ferestrele popup pentru această pagină.');
    if (printWindow) { printWindow.document.title = 'Chitanță'; printWindow.document.body.textContent = 'Se pregătește chitanța…'; }
    const response = await fetchReceiptOutput(id, kind);
    if (kind === 'print') {
      const html = await response.text();
      if (printWindow.closed) return;
      printWindow.document.open(); printWindow.document.write(html); printWindow.document.close();
      printWindow.opener = null;
    } else {
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `Chitanta_${id}.pdf`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
  } catch (error) { printWindow?.close(); throw error; }
}

async function openReceiptMenu(button, receipt) {
  receiptMenuCleanup?.();
  if (mobileQuery.matches || !receipt || !state.selected) return;
  const child = state.selected.IDP;
  const payers = await api(`/api/adechit/rows/Platitori_sub?IDP=${child}`);
  if (mobileQuery.matches || !button.isConnected || state.selected?.IDP !== child) return;
  receiptMenuCleanup?.();
  const payer = payers.rows.find((row) => row.IDS === receipt.PayerId);
  const menu = document.createElement('div'); menu.className = 'ade-receipt-menu';
  menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', 'Opțiuni chitanță');
  const listeners = new AbortController();
  const close = (focus = false) => {
    listeners.abort(); menu.remove(); button.setAttribute('aria-expanded', 'false');
    if (focus && button.isConnected) button.focus();
    if (receiptMenuCleanup === close) receiptMenuCleanup = null;
  };
  receiptMenuCleanup = close;
  const entries = [{ icon: '🖨️', label: 'Listare', kind: 'print' }];
  if (payer?.EMail?.trim()) entries.push({ icon: '✉️', label: 'Trimitere pe mail', disabled: true });
  entries.push({ icon: '📄', label: 'Descarcă PDF', kind: 'pdf' });
  for (const entry of entries) {
    const item = document.createElement('button'); item.type = 'button'; item.setAttribute('role', 'menuitem');
    item.disabled = Boolean(entry.disabled);
    const icon = document.createElement('span'); icon.className = 'ade-receipt-menu__icon'; icon.setAttribute('aria-hidden', 'true'); icon.textContent = entry.icon;
    const label = document.createElement('span'); label.textContent = entry.label; item.append(icon, label);
    if (entry.kind) item.addEventListener('click', () => { close(true); outputReceipt(receipt.IDC, entry.kind).catch(report); });
    menu.append(item);
  }
  document.body.append(menu); button.setAttribute('aria-expanded', 'true');
  menu.style.zIndex = String(window.ZIndexManager?.getNext() || 1001);
  const rect = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(rect.bottom + menu.offsetHeight > innerHeight - 8 ? rect.top - menu.offsetHeight : rect.bottom, innerHeight - menu.offsetHeight - 8))}px`;
  const items = [...menu.querySelectorAll('button:not(:disabled)')]; items[0]?.focus();
  menu.addEventListener('keydown', (event) => {
    const index = items.indexOf(document.activeElement);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); items[(index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault(); items[event.key === 'Home' ? 0 : items.length - 1]?.focus();
    }
  });
  document.addEventListener('pointerdown', (event) => { if (!menu.contains(event.target) && !button.contains(event.target)) close(); }, { capture: true, signal: listeners.signal });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); close(true); } else if (event.key === 'Tab') close(); }, { signal: listeners.signal });
  addEventListener('resize', () => close(), { signal: listeners.signal });
  addEventListener('scroll', () => close(), { capture: true, signal: listeners.signal });
}

function renderLedger(rows = []) {
  receiptMenuCleanup?.();
  const kind = state.ledger;
  const spec = ledgerSpec(kind);
  const body = $('ade-ledger-body');
  if (!state.selected) { body.innerHTML = '<div class="ade-ledger-empty">Selectați copilul pentru a vedea documentele și a adăuga un rând.</div>'; return; }
  const existing = [...rows].sort((a, b) => String(a.Explicatie || '').localeCompare(String(b.Explicatie || ''), 'ro', { sensitivity: 'base' }))
    .map((row) => {
      const k_buttons = (kind === 'receipt' ? receiptActionButton(row.IDC) : '')
        + (kind === 'refund' && !row.Cancelled ? orderActionButton(row.IDR) : '')
        + (canCancelDocument(kind, row) ? `<button class="ade-cell-button ade-cancel-document" type="button" data-cancel-id="${documentId(kind, row)}" title="Anulare document" aria-label="Anulare document">❌</button>` : '');
      const k_cell = k_buttons ? `<div class="ade-ledger-buttons">${k_buttons}</div>` : row.Cancelled ? 'Anulat' : '';
      return `<tr class="${row.Cancelled ? 'is-cancelled' : ''}">${spec
        .map((column) => `<td class="${column.cls}">${escapeHtml(column.cell(row) ?? '')}</td>`).join('')}<td class="ade-row-actions">${k_cell}</td></tr>`;
    }).join('');
  const draftCells = spec.map((column) => `<td class="${column.cls}">${column.draft || ''}</td>`).join('');
  const latestReceipt = kind === 'receipt' ? [...rows].sort((a, b) => b.IDC - a.IDC)[0] : null;
  body.innerHTML = `<table class="ade-ledger-table${kind === 'receipt' ? ' ade-ledger-table--receipts' : ''}"><colgroup>${spec.map((column) => `<col class="${column.cls.split(' ')[0]}">`).join('')}<col class="ade-col-actions"></colgroup>
    <thead><tr>${spec.map((column) => `<th>${column.title}</th>`).join('')}<th class="ade-row-actions"></th></tr></thead>
    <tbody>${existing}<tr class="is-draft">${draftCells}<td class="ade-row-actions"><div class="ade-ledger-buttons"><button class="ade-cell-button" id="ade-ledger-save" type="button" title="Salvează documentul nou și recalculează situația" aria-label="Salvează documentul nou">💾</button>${kind === 'receipt' ? receiptActionButton(latestReceipt?.IDC, true) : ''}</div></td></tr></tbody></table>`;
  body.querySelectorAll('[data-receipt-id]').forEach((button) => button.addEventListener('click', () => {
    openReceiptMenu(button, rows.find((row) => row.IDC === Number(button.dataset.receiptId))).catch(report);
  }));
  body.querySelectorAll('[data-order-id]').forEach((k_button) => k_button.addEventListener('click', () => {
    const k_id = Number(k_button.dataset.orderId);
    const k_output = (k_pdf) => outputReport({ fetchOutput, path: `/api/adechit/reports/payment-order/${k_pdf ? 'pdf' : 'print'}?id=${k_id}`, pdf: k_pdf, title: 'Dispoziție de plată' }).catch(report);
    openMenu(k_button, [{ icon: '🖨️', label: 'Listare', run: () => k_output(false) }, { icon: '📄', label: 'Descarcă PDF', run: () => k_output(true) }],
      { label: 'Opțiuni dispoziție de plată', align: 'right' });
  }));
  body.querySelectorAll('[data-cancel-id]').forEach((k_button) => k_button.addEventListener('click', () => {
    cancelDocument(kind, rows.find((row) => documentId(kind, row) === Number(k_button.dataset.cancelId))).catch(report);
  }));
  // the calendar opens on today when today is in the selected month, otherwise on the first day of that month
  new DatePicker($('ade-ledger-date'), { startDate: () => {
    const k_month = selectedMonth();
    const k_now = new Date();
    return !k_month || (k_now.getMonth() + 1 === k_month.Luna && k_now.getFullYear() === k_month.Anul) ? k_now : new Date(k_month.Anul, k_month.Luna - 1, 1);
  } });
  const explanation = $('ade-ledger-explanation');
  let touched = false;
  explanation.addEventListener('input', () => { touched = true; });
  $('ade-ledger-date').addEventListener('change', () => {
    fillSuggestedAmount();
    fillSuggestedExplanation(explanation, () => touched).catch(report);
  });
  $('ade-ledger-save').addEventListener('click', () => saveLedger().catch(report));
}

function escapeHtml(value) {
  const node = document.createElement('span');
  node.textContent = String(value);
  return node.innerHTML;
}

function fillSuggestedAmount() {
  const amount = state.ledger === 'refund' ? state.selected.SIC : state.selected.SID;
  if (amount > 0) $('ade-ledger-value').value = amount;
}

// Fills the explanation when the date is chosen; never overwrites what the operator typed.
async function fillSuggestedExplanation(k_input, k_touched) {
  if (state.ledger === 'other') return;
  const k_month = state.monthId;
  const k_text = state.ledger === 'refund' ? REFUND_EXPLANATION
    : (await api(`/api/adechit/receipt-defaults?IDL=${k_month}`)).explanation;
  if (!k_touched() && k_month === state.monthId && k_input.isConnected) k_input.value = k_text;
}

async function ledgerData() {
  const idz = state.selected.IDZ;
  const month = state.monthId;
  return (await api(`/api/adechit/ledger/${idz}?IDL=${month}&kind=${state.ledger}`)).rows;
}

let ledgerRequest = 0;
async function loadLedger() {
  const own = ++ledgerRequest;
  if (!state.selected) { renderLedger(); return; }
  const childId = state.selected.IDZ;
  const month = state.monthId; const kind = state.ledger;
  const rows = await ledgerData();
  if (own === ledgerRequest && state.selected?.IDZ === childId && state.monthId === month && state.ledger === kind) renderLedger(rows);
}

async function saveLedger() {
  const date = $('ade-ledger-date').value;
  const raw = $('ade-ledger-value').value.trim().replace(',', '.');
  const amount = Number(raw);
  if (!date) throw new Error('Completați data documentului.');
  if (!raw || !Number.isFinite(amount) || amount === 0) throw new Error('Completați o valoare numerică diferită de zero.');
  const payers = await api(`/api/adechit/rows/Platitori_sub?IDP=${state.selected.IDP}`);
  const payer = payers.rows.find((row) => row.Activ);
  if (!payer) throw new Error('Copilul nu are un plătitor activ.');
  const kind = state.ledger;
  const body = { kind, attendance_id: state.selected.IDZ, payer_id: payer.IDS, date, amount, explanation: $('ade-ledger-explanation').value.trim() };
  if (kind === 'other') Object.assign(body, { number: 'Fără număr' });
  await api('/api/adechit/documents', { method: 'POST', body: JSON.stringify(body) });
  await reloadAfterDocument();
  showMessage($('ade-message'), 'Documentul a fost salvat și situația a fost recalculată.', 'info');
}

// Lists waiting for the server turn into a loading box (spinner, nothing to click) until the work is done.
async function withLoading(k_hosts, k_work) {
  const k_nodes = k_hosts.filter(Boolean);
  k_nodes.forEach((k_host) => { k_host.classList.add('ade-loading'); k_host.setAttribute('aria-busy', 'true'); });
  try { return await k_work(); }
  finally { k_nodes.forEach((k_host) => { k_host.classList.remove('ade-loading'); k_host.removeAttribute('aria-busy'); }); }
}

// The grid and the documents are emptied while the server works: no row is left that the user could still act on.
function clearWork() {
  state.rows = []; state.shownRows = []; state.selected = null; createGrid(); renderLedger();
}
const workHosts = () => [$('ade-grid'), document.querySelector('.ade-ledger')];

// Closing / reopening a month: month, group lists (the phone's comboboxes too), grid and documents all load until the server is done.
async function command(path, success, k_monthId = state.monthId) {
  const k_hosts = [$('ade-month-tree'), $('ade-group-tree'), document.querySelector('.ade-pickers'), ...workHosts()];
  return withLoading(k_hosts, async () => {
    try {
      clearWork();
      await api(path, { method: 'POST', body: JSON.stringify({ id: k_monthId }) });
      await loadContext(true);
      showMessage($('ade-message'), success, 'info');
      return true;
    } catch (error) { report(error); return false; }
  });
}

// The lock icons of the month tree. Closing asks first: the icon is always in sight.
async function monthIconAction(k_action, k_monthId) {
  const k_month = state.context.months.find((row) => row.IDL === k_monthId);
  if (!k_month) return;
  const k_name = `${k_month.LunaT} ${k_month.Anul}`;
  if (k_action === 'close') {
    if (k_month.Luna === 8) {   // the annual closing has its own window and its own confirmation
      if (state.monthId !== k_monthId) await chooseMonth(k_monthId);
      await state.annual(k_monthId);
      return;
    }
    if (!await confirmBox(`Închideți luna ${k_name}?`, { title: 'Închidere lună', yes: 'Închide', no: 'Renunță' })) return;
    await command('/api/adechit/close', 'Luna a fost închisă.', k_monthId);
  } else if (k_action === 'reopen') {
    // the month open right now (the one after this) is deleted by the server, with every attendance entered in it
    const k_following = state.context.months.find((row) => !row.Inchisa && row.Anul * 12 + row.Luna === k_month.Anul * 12 + k_month.Luna + 1);
    const k_open = k_following ? `${k_following.LunaT} ${k_following.Anul}` : 'luna deschisă în prezent';
    if (!await confirmBox(`Redeschideți luna ${k_name}?

Atenție: luna deschisă în prezent (${k_open}) va fi ȘTEARSĂ. Toate datele ei se vor pierde, inclusiv prezența introdusă, iar acțiunea nu poate fi anulată.

Dacă luna ${k_open} are deja plăți sau restituiri, redeschiderea poate fi blocată.`,
      { title: 'Redeschidere lună – datele se pierd', yes: 'Redeschide', no: 'Renunță', warning: true })) return;
    // the reopened month becomes the selected one, once the server is done
    if (await command('/api/adechit/reopen', 'Luna a fost redeschisă.', k_monthId) && state.monthId !== k_monthId) await withLoading(workHosts(), () => chooseMonth(k_monthId)).catch(report);
  }
}

// The hour is over: the server session is closed and the sign-in page opens (unsaved work is lost by design).
async function logoutNow() {
  try { await fetch('/api/portal/logout', { method: 'POST', headers: { 'X-Portal-Token': portalToken() } }); }
  catch (k_error) { console.error('[ade] logout call failed', k_error); }
  toPortal(true);
}

function bind() {
  const catalogs = state.payers = bindCatalogs({ api, context: () => state.context, refresh: () => loadContext(true).catch(report), moveToSubunit });
  const bankAssociations = bindBankAssociations({ api, context: () => state.context, refresh: () => loadContext(true), monthId: () => state.monthId });
  const annual = bindAnnual({ api, refresh: () => loadContext(true), message: (text) => showMessage($('ade-message'), text, 'info') });
  state.annual = annual;
  wireLongPress();
  bindHelp({ fetchOutput, report });
  const noticeConfig = bindNoticeConfig({ api, context: () => state.context, report });
  state.reports = bindReports({ fetchOutput, report,
    snapshot: () => ({ month: state.context ? selectedMonth() : null, monthId: state.monthId, groupId: state.groupId, selected: state.selected }) });
  $('ade-search').addEventListener('input', filterGrid);
  $('ade-show-hidden-groups').checked = false;
  $('ade-show-hidden-groups').addEventListener('change', (event) => toggleHiddenGroups(event.target.checked).catch(report));
  $('ade-refresh').addEventListener('click', () => loadSituation().catch(report));
  const k_iconClick = (k_event) => {
    const k_icon = k_event.target.closest('.treeview-righticon');
    if (!k_icon || (k_event.type === 'keydown' && k_event.key !== 'Enter' && k_event.key !== ' ')) return;
    k_event.stopPropagation(); k_event.preventDefault();   // the row itself is not selected by this click
    monthIconAction(k_icon.dataset.action, Number(String(k_icon.dataset.nodeId).split(':')[1])).catch(report);
  };
  $('ade-month-tree').addEventListener('click', k_iconClick, true);
  $('ade-month-tree').addEventListener('keydown', k_iconClick, true);
  wireChildDrop();
  document.querySelectorAll('[data-ledger]').forEach((button) => button.addEventListener('click', () => {
    state.ledger = button.dataset.ledger;
    setLedger(button.dataset.ledger);
    loadLedger().catch(report);
  }));
  const toggleTheme = () => {
    const k_dark = document.documentElement.dataset.theme === 'dark';
    document.documentElement.dataset.theme = k_dark ? 'light' : 'dark';
    state.grid?.setTheme(k_dark ? 'modern' : 'dark');
  };
  // The application menu sits next to the brand, like the portal menu.
  $('ade-menu').addEventListener('click', () => {
    const k_dark = document.documentElement.dataset.theme === 'dark';
    openMenu($('ade-menu'), [
      { icon: '👥', label: 'Plătitori', run: () => $('ade-payers').click() },
      { icon: '🏦', label: 'Asociere extrase cu copii', run: () => bankAssociations.open() },
      { icon: '💶', label: 'Taxe', run: () => $('ade-taxes').click() },
      { icon: '📝', label: 'Text situație de plată', run: () => noticeConfig.open() },
      { icon: k_dark ? '☀️' : '🌙', label: k_dark ? 'Tema luminoasă' : 'Tema întunecată', rule: true, run: toggleTheme },
    ], { label: 'Meniu', below: $('ade-menu').closest('header') });
  });
  // The «A» opens the applications of the account (ADECHIT is this page, so it is not listed).
  $('ade-apps').addEventListener('click', () => {
    openMenu($('ade-apps'), [
      { icon: '🏢', label: $('ade-context').textContent || 'Contul curent', disabled: true },
      { icon: '🤖', label: 'K-BOT', rule: true, run: () => { window.location.href = '/portal'; } },
      { icon: '🏗️', label: 'VERCON', run: () => { window.location.href = '/vercon'; } },
      { icon: '📒', label: 'Avacont · în curând', disabled: true },
    ], { label: 'Aplicații', below: $('ade-apps').closest('header') });
  });
  if (document.documentElement.dataset.adePreview !== 'true' && portalToken()) {
    startSessionTimer({ token: portalToken, host: $('ade-session-timer'), onExpired: logoutNow });
  }
  const shell = document.querySelector('.ade-shell');
  const reportsToggle = $('ade-reports-toggle');
  const setReportsCollapsed = (collapsed) => {
    shell.classList.toggle('is-reports-collapsed', collapsed);
    document.body.classList.toggle('is-reports-collapsed', collapsed);   // the header follows the shell's columns
    reportsToggle.setAttribute('aria-expanded', String(!collapsed));
    reportsToggle.textContent = collapsed ? '‹' : '›';
    reportsToggle.title = collapsed ? 'Extinde panoul Rapoarte' : 'Restrânge panoul Rapoarte';
    localStorage.setItem('ade.reports.collapsed', collapsed ? '1' : '0');
  };
  setReportsCollapsed(localStorage.getItem('ade.reports.collapsed') === '1');
  reportsToggle.addEventListener('click', () => setReportsCollapsed(!shell.classList.contains('is-reports-collapsed')));
  // The left panel folds like the right one. Folded: no tooltips, short months, groups with ellipsis (full name on hover).
  // the month tree is as tall as the main grid (header and footer included)
  new ResizeObserver(() => document.querySelector('.ade-sidebar').style.setProperty('--ade-grid-h', `${$('ade-grid').offsetHeight + 2}px`)).observe($('ade-grid'));
  const k_leftToggle = $('ade-left-toggle');
  const setLeftCollapsed = (k_collapsed) => {
    state.leftCollapsed = k_collapsed;
    document.body.classList.toggle('is-left-collapsed', k_collapsed);
    k_leftToggle.setAttribute('aria-expanded', String(!k_collapsed));
    k_leftToggle.textContent = k_collapsed ? '›' : '‹';
    k_leftToggle.title = k_collapsed ? '' : 'Restrânge panoul din stânga';
    $('ade-refresh').title = k_collapsed ? '' : 'Reîncarcă situația lunii';
    try { localStorage.setItem('ade.left.collapsed', k_collapsed ? '1' : '0'); } catch (k_error) { console.error('[ade] localStorage is blocked', k_error); }
    state.monthTree?.setOptions({ indent: k_collapsed ? 0 : 12 });
    if (state.monthTree) renderMonthTree();
    if (state.groupTree && state.context) renderGroupTree();
  };
  let k_savedLeft = false;
  try { k_savedLeft = localStorage.getItem('ade.left.collapsed') === '1'; } catch (k_error) { console.error('[ade] localStorage is blocked', k_error); }
  setLeftCollapsed(k_savedLeft);
  k_leftToggle.addEventListener('click', () => setLeftCollapsed(!state.leftCollapsed));
  addEventListener('pagehide', () => { receiptMenuCleanup?.(); state.grid?.destroy(); state.monthTree?.destroy?.(); state.groupTree?.destroy?.(); }, { once: true });
}

// The server resolves the subunit: the stored one when valid, the only one when the unit has a single evidence.
async function readContext(rebuildTrees) {
  if (!rebuildTrees) {
    // The portal opens /adechit without a unit query parameter. Resolve the session's
    // actual unit before reading its saved subunit; the initial unit may be "preview"
    // or a stale URL hint belonging to another unit.
    state.subunit = null;
    const context = await api('/api/adechit/context');
    state.unit = context.unit;
    const saved = storedSubunit(state.unit);
    if (saved == null || saved === String(context.subunit?.id)) return context;
    state.subunit = saved;
  }
  try { return await api('/api/adechit/context'); } catch (error) {
    if (error.reason !== 'SUBUNIT_CONTEXT') throw error;
    rememberSubunit(state.unit, null); state.subunit = null;   // the remembered subunit is gone: ask again
    return api('/api/adechit/context');
  }
}

function showSubunits(context) {
  const items = (context.subunits || []).map((row) => ({ value: String(row.id), label: row.name }));
  if (!state.subunitCombo) {
    state.subunitCombo = new Combobox($('ade-subunit-combo'), { readonly: true, placeholder: 'Subunitate', staticData: items, onSelect: switchSubunit });
    state.subunitCombo.input.id = 'ade-subunit-combo-input';
    state.subunitCombo.input.setAttribute('aria-label', 'Subunitate');
  }
  state.subunitCombo.options.staticData = items;
  const current = items.find((item) => item.value === String(state.subunit));
  if (current) state.subunitCombo.setValue(current.value, current.label); else state.subunitCombo.clear();
  $('ade-subunit').hidden = items.length < 2;   // one subunit is chosen silently; the selector exists only for several
}

// A switch reloads the page: every grid, tree, catalog, cache and late response of the old subunit disappears with it.
function unsavedWork() {
  return Boolean(document.querySelector('dialog[open]'))
    || [...document.querySelectorAll('#ade-ledger-body tr.is-draft input')].some((input) => input.type !== 'checkbox' && input.value.trim() !== '');
}
async function switchSubunit(value) {
  const id = Number(value);
  if (!Number.isInteger(id) || id === state.subunit) return;
  const current = (state.context.subunits || []).find((row) => row.id === state.subunit);
  // A cell still being edited is saved first (like Enter); if that fails, the switch is refused.
  const cellSaved = !state.grid?.hasEdit || await state.grid.commitEdit();
  if (!cellSaved || state.pending > 0 || unsavedWork()) {
    if (current) state.subunitCombo.setValue(String(current.id), current.name);
    showMessage($('ade-message'), 'Salvați sau renunțați la modificările în curs înainte de a schimba subunitatea.', 'error');
    return;
  }
  rememberSubunit(state.unit, id);
  window.location.reload();
}

// A link to a child of another subunit: remember the target, switch (the page reloads) and open it once loaded.
const PENDING_OPEN_KEY = 'ade.pendingOpen';
function moveToSubunit(k_id, k_target) {
  if (state.pending > 0) { report(new Error('Așteptați terminarea salvării în curs.')); return; }
  try { window.sessionStorage.setItem(PENDING_OPEN_KEY, JSON.stringify({ ...k_target, subunit_id: k_id })); }
  catch (k_error) { console.error('[ade] sessionStorage is blocked', k_error); }
  rememberSubunit(state.unit, k_id);
  window.location.reload();
}
function takePendingOpen() {
  try {
    const k_raw = window.sessionStorage.getItem(PENDING_OPEN_KEY);
    window.sessionStorage.removeItem(PENDING_OPEN_KEY);
    return k_raw ? JSON.parse(k_raw) : null;
  } catch (k_error) { console.error('[ade] pending open unreadable', k_error); return null; }
}

async function loadContext(rebuildTrees = false) {
  const context = await readContext(rebuildTrees);
  state.context = { ...context, months: [], groups: [], years: [] }; state.unit = context.unit;
  state.subunit = context.subunit ? context.subunit.id : null;
  showSubunits(context);
  if (state.subunit == null) {
    // No subunit chosen yet (several in this unit, or none): nothing of the evidence is read until one is picked.
    $('ade-context').textContent = `${context.unit} · ${context.email}`;
    showMessage($('ade-message'), context.subunits.length
      ? 'Alegeți subunitatea în care lucrați.' : 'Unitatea nu are nicio subunitate. Importați evidența cu ADE.Migrator.', context.subunits.length ? 'info' : 'error');
    return;
  }
  rememberSubunit(state.unit, state.subunit);
  state.loadedYears.clear(); state.groupsRequest = null; state.groupsSequence += 1;
  state.context.years = (await api('/api/adechit/years')).years;
  $('ade-context').textContent = `${context.unit} · ${context.email}`;
  if (!rebuildTrees) {
    buildTrees(); buildPickers();
    // the current year when the database has it, else the latest one
    const thisYear = new Date().getFullYear();
    const latestYear = state.context.years.includes(thisYear) ? thisYear : state.context.years[0];
    if (latestYear != null && !state.year) state.year = latestYear;
    state.monthTree.expandedNodes.clear();
    if (latestYear != null) {
      await loadYear(latestYear);
      state.monthTree.expandedNodes.add(`year:${latestYear}`);
      state.monthTree.isTreeRendered = false; state.monthTree.renderTree('');
    }
  }
  else {
    if (state.year) await loadYear(state.year);
    // Every year that was open stays open (and loaded): after a save the selected month must still be visible.
    for (const k_id of state.monthTree.expandedNodes) {
      const k_year = Number(String(k_id).split(':')[1]);
      if (String(k_id).startsWith('year:') && k_year !== state.year) await loadYear(k_year);
    }
    renderMonthTree();
    if (!state.context.months.some((row) => row.IDL === state.monthId)) state.monthId = null;
    await loadGroups();
  }
  if (!state.context.months.some((row) => row.IDL === state.monthId)) state.monthId = null;
  if (!state.context.groups.some((row) => row.IDG === state.groupId)) state.groupId = null;
  syncPickers();
  await loadSituation();
}

// The portal sends a user here without a unit opened when ADECHIT was chosen on its sign-in card:
// the unit is picked in this page's header (one choice, no portal screen in between).
// Returns true when a unit is open and the page can load.
async function ensureUnit() {
  const k_response = await fetch('/api/portal/me', { headers: { Accept: 'application/json', 'X-Portal-Token': portalToken() }, cache: 'no-store' });
  const k_me = await k_response.json().catch(() => ({}));
  if (k_response.status === 401) { toPortal(true); return false; }
  if (!k_response.ok) throw new Error(k_me.error || `Eroare HTTP ${k_response.status}`);
  if (k_me.db_name) return true;
  const k_units = k_me.units || [];
  if (!k_units.length) { showMessage($('ade-message'), 'Contul nu are nicio unitate.', 'error'); return false; }
  const k_combo = new Combobox($('ade-unit-combo'), {
    readonly: true, placeholder: 'Unitate',
    staticData: k_units.map((k_row) => ({ value: k_row.DC, label: k_row.NumeUnitate })),
    onSelect: async (k_value) => {
      try {
        const k_opened = await fetch('/api/portal/unit', { method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Portal-Token': portalToken() }, body: JSON.stringify({ db_name: k_value }) });
        if (!k_opened.ok) { const k_body = await k_opened.json().catch(() => ({})); throw new Error(k_body.error || 'Unitatea nu a putut fi deschisă.'); }
        window.location.reload();
      } catch (k_error) { console.error('[ade] opening the unit failed', k_error); report(k_error); }
    },
  });
  k_combo.input.id = 'ade-unit-combo-input';
  k_combo.input.setAttribute('aria-label', 'Unitate');
  $('ade-unit').hidden = false;
  showMessage($('ade-message'), 'Alegeți unitatea în care lucrați.', 'info');
  return false;
}

async function start() {
  state.unit = new URLSearchParams(location.search).get('unit') || 'preview';
  bind();
  if (document.documentElement.dataset.adePreview !== 'true' && !(await ensureUnit())) return;
  await loadContext();
  const k_pending = takePendingOpen();
  if (k_pending && k_pending.subunit_id === state.subunit) await state.payers.openForChild(k_pending.group_id, k_pending.child_id);
}

start().catch(report);
