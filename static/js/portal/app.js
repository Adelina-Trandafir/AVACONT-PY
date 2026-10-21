// Slices 0110-06 / 0110-10 / 0110-11 -- the signed-in page of the web area: unit / year / source pickers
// (the existing Combobox), the list of angajamente and, for the one picked, its cards in the read-only
// DataGrid. VIEW ONLY: nothing here sends data to the server except the choice of the unit; the data
// routes are GET (routes/portal/date.py).
//
// The shapes of the answers are those of the desktop app's routes (routes/forexe/*), because the
// portal calls the same functions.
//
// Slice 0110-10: on a computer every card of the angajament (Istoric, Rezervari, Receptii, Extrase, Plati,
// Fundamentari, Ordonantari) has its own tree beside its grid, as the desktop views have -- «Toate ...» >
// month > day / document (tabtrees.js). A phone has no trees: the grid shows every row, never grouped by
// date. The old «Documente» card is gone: its signed documents live in Fundamentari (the DDF revisions)
// and Ordonantari (the ORD documents and the CAB correction notes).

import { Combobox } from '../components/combobox/combobox.js';
import { TreeView } from '../components/treeview/treeview.js';
import { createPdfView } from './pdfview.js';
import { createClasificatiiPage } from './clasificatii.js';
import { createParteneriPage } from './parteneri.js';
import { createExtrasePage } from './extrase.js';
import { TABS, DOC_TABS, makeGrid } from './cards.js';
import { applyPortalAccess, canOpenTab, isAccountant } from './access.js';
import { buildTabTree, hasTree, ROOT } from './tabtrees.js';
import { createExtraseCard } from './extrasecard.js';
import { showMessage } from './messages.js';

const ALL_SOURCES = '*';
const isPc = () => window.matchMedia('(min-width: 901px)').matches;
const roDay = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('.') : '');
const roMoney = (n) => new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);

const $ = (id) => document.getElementById(id);

// The angajamente tree is flat, as in the desktop app: one node per angajament, a status mark
// in front (the desktop's coloured status icon, by the same words of Stare, first match wins),
// bold when it has indicators, red when a reception chain does not close.
const STATUS_MARKS = [
  ['derulare', '🟢'], ['anulat', '🔴'], ['reziliat', '🔴'], ['suspendat', '🟠'],
  ['initial', '⚪'], ['arhivat', '🔵'], ['manual', '⚪'], ['definitivare', '🔵'],
];

function statusMark(stare) {
  const norm = String(stare || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const hit = STATUS_MARKS.find(([token]) => norm.includes(token));
  return hit ? hit[1] : '⚪';
}

const nameCmp = new Intl.Collator('ro', { sensitivity: 'base', numeric: true });

/**
 * The list order, as the desktop's tree sorts it: by name, or by the day the angajament was made
 * (the ones with no such day go last, by name); the code breaks every tie so two loads never swap rows.
 */
function sortRows(rows, by, desc) {
  const sign = desc ? -1 : 1;
  const name = (r) => String(r.Descriere || '').trim();
  const cod = (r) => String(r.CodAngajament || '');
  const tie = (a, b) => nameCmp.compare(name(a), name(b)) || cod(a).localeCompare(cod(b));
  if (by === 'name') return [...rows].sort((a, b) => sign * nameCmp.compare(name(a), name(b)) || cod(a).localeCompare(cod(b)));
  const day = (r) => (r.DataAngajamentNou ? Date.parse(r.DataAngajamentNou) : NaN);
  const dated = rows.filter((r) => !Number.isNaN(day(r)));
  const undated = rows.filter((r) => Number.isNaN(day(r)));
  dated.sort((a, b) => sign * (day(a) - day(b)) || tie(a, b));
  undated.sort(tie);
  return dated.concat(undated);
}

/** One tree row of GET /tree -> one TreeView node. */
function angajamentNode(row) {
  const cod = String(row.CodAngajament || '');
  const desc = String(row.Descriere || '').trim().toUpperCase();
  const lines = [row.Descriere, `Cod: ${cod}`, row.Stare ? `Stare: ${row.Stare}` : '',
    row.Surse ? `Surse: ${String(row.Surse).split(';').join(', ')}` : ''].filter(Boolean);
  return {
    id: cod,
    label: `${statusMark(row.Stare)} ${desc || cod}`,
    badge: cod,
    bold: !!row.AreIndicatori,
    error: Array.isArray(row.LantNeinchis) ? row.LantNeinchis.length > 0 : !!row.LantNeinchis,
    tooltip: lines.join('\n'),
    keywords: `${cod} ${row.Surse || ''}`,
  };
}

// ---------------------------------------------------------------- the page
/**
 * @param {{call: Function, callBytes: Function, onUnauthorized: Function}} deps
 *   call(method, path, body) -> {ok, status, data}, as in portal.js;
 *   callBytes(path) -> the same, with the raw bytes (ArrayBuffer) as `data` when ok.
 */
export function createApp({ call, callBytes, onUnauthorized, onUnitOpened, onAccessChanged }) {
  let me = null;
  let unitCombo = null;
  let anCombo = null;
  let ssCombo = null;
  let treeAng = null;
  const rowsByCod = new Map(); // CodAngajament -> the tree row
  let treeRows = []; // the rows as the server sent them
  const order = { by: 'name', desc: false }; // the list order chosen in the options menu
  let gridTab = null;
  let gridTabKey = ''; // `${cod}|${tab}` of the grid shown: the same card of the same angajament keeps its grid and column widths
  let treeTab = null; // the tree of the open card (computers)
  let treeMode = ''; // 'rows' | 'docs' | '' -- what a click in treeTab does
  let rowsState = null; // {name, rows, built} of the open row card
  let docState = null; // {name, built} of the open document card
  let gridLines = null;
  let extCard = null; // the Extrase card (computers), slice 0110-12
  let gridValori = null; // the values under the grid of Istoric
  let detailUpdate = null; // (row) => paints the detail under the grid of Istoric / Plati
  let docPage = 'view'; // the page of a document card on a computer: 'view' | 'doc'
  let pendingDoc = null; // the document of the chosen node: shown in the viewer when the viewer is on screen
  let openedDocId = ''; // the document the viewer holds
  let nodeLines = []; // the lines of the chosen node of a document card
  let orderFilter = ''; // the beneficiary chosen in the header of Ordonantari
  let tab = 'sumar';
  let picked = null; // the selected angajament row
  let treeSeq = 0; // the answer of an older request must not replace a newer one
  let tabSeq = 0;
  const cache = new Map(); // `${cod}|${tab}` -> the data of the card
  let gridDocs = null;
  let pdfView = null;
  let docSeq = 0; // the same rule for documents
  let docBlob = null; // {bytes, name} of the document shown, for the download button

  const say = (text) => {
    showMessage($('msg-app'), text, 'error');
  };
  const hint = (text) => {
    $('app-lead').textContent = text || '';
    $('app-lead').hidden = !text;
  };

  // A failed GET: 401 means the session is over; anything else shows the server's sentence.
  function fail(r, fallback) {
    if (r.status === 401) {
      onUnauthorized(r.data.error);
      return;
    }
    say(r.data.error || fallback);
  }

  function makeCombo(id, placeholder, onSelect) {
    const combo = new Combobox($(id), { readonly: true, placeholder, staticData: [], onSelect });
    combo.input.id = `${id}-input`;
    // no visible label (the chosen value says what it is); the name stays for screen readers
    const label = document.querySelector(`label[for="${id}"]`);
    if (label) {
      combo.input.setAttribute('aria-label', label.textContent.trim());
      label.remove();
    }
    return combo;
  }

  function setOptions(combo, items, value) {
    combo.options.staticData = items;
    const hit = items.find((i) => i.value === value);
    if (hit) combo.setValue(hit.value, hit.label);
    else combo.clear();
  }

  function ensureWidgets() {
    if (unitCombo) return;
    unitCombo = makeCombo('unit-combo', 'Alegeți unitatea', (value) => chooseUnit(value));
    anCombo = makeCombo('an-combo', 'An', () => {
      fillSources();
      if (activePage === 'clasificatii') pages.clasificatii.reload();
      else if (!activePage) loadTree();
    });
    ssCombo = makeCombo('ss-combo', 'Sursa', () => loadTree());
    treeAng = new TreeView($('grid-ang'), {
      inline: true,
      searchPlaceholder: 'Căutați (minimum 3 caractere)…',
      onSelect: (sel) => pick(rowsByCod.get(String(sel.id))),
    });
    pdfView = createPdfView($('pdf-host'));
    setupModeMenu();
    document.querySelectorAll('#doc-nav button').forEach((b) => b.addEventListener('click', () => setDocPage(b.dataset.page)));
    $('btn-docdl').addEventListener('click', downloadDoc);
    document.querySelectorAll('.pa__tabs button[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    // crossing between a phone and a computer width changes what the card shows (trees only on a computer)
    window.matchMedia('(min-width: 901px)').addEventListener('change', () => { if (picked) loadTab(); });
    // The unit field (label + combo) is always exactly as wide as the angajamente tree's search box.
    const unitField = document.querySelector('.pa__f--unit');
    const matchTreeWidth = () => {
      if (window.matchMedia('(max-width: 900px)').matches) {
        // phone: one line, the css decides the widths
        unitField.style.cssText = '';
        document.querySelector('.pa__f--an').style.marginLeft = '';
        return;
      }
      const w = $('grid-ang').getBoundingClientRect().width;
      if (w <= 0) return; // the list is hidden: keep the last width
      unitField.style.flex = '0 0 auto';
      unitField.style.minWidth = '0';
      unitField.style.maxWidth = 'none';
      unitField.style.width = `${Math.round(w)}px`;
      // pc: the year field starts exactly where the data card (pa__right) starts
      const an = document.querySelector('.pa__f--an');
      an.style.marginLeft = '';
      if (window.matchMedia('(min-width: 901px)').matches && !$('pa-body').hidden) {
        const bar = document.querySelector('.pa__bar');
        const gap = parseFloat(getComputedStyle(bar).columnGap) || 0;
        const shift = document.querySelector('.pa__right').getBoundingClientRect().left
          - unitField.getBoundingClientRect().right - gap;
        if (shift > 0) an.style.marginLeft = `${Math.round(shift)}px`;
      }
    };
    new ResizeObserver(matchTreeWidth).observe($('grid-ang'));
    setupTreeMenu();
    $('btn-back').addEventListener('click', () => $('card-app').classList.remove('is-detail'));
  }

  // ------------------------------------------------------------ unit -> year / source
  function fillPeriods() {
    const years = [...new Set(me.periods.map((p) => p.AN))].sort((a, b) => b - a);
    setOptions(anCombo, years.map((y) => ({ value: String(y), label: String(y) })), years.length ? String(years[0]) : '');
    fillSources();
  }

  function fillSources() {
    const year = Number(anCombo.getSelectedValue());
    const sources = [...new Set(me.periods.filter((p) => p.AN === year).map((p) => p.SS))].sort();
    const items = [{ value: ALL_SOURCES, label: 'Toate...' }, ...sources.map((s) => ({ value: s, label: s }))];
    setOptions(ssCombo, items, ALL_SOURCES);
  }

  async function chooseUnit(dbName) {
    say('');
    if (!dbName || dbName === me.db_name) return;
    const r = await call('POST', '/api/portal/unit', { db_name: dbName });
    if (!r.ok) {
      fail(r, 'Unitatea nu a putut fi deschisă.');
      setOptions(unitCombo, unitItems(), me.db_name);
      return;
    }
    me.db_name = r.data.db_name;
    me.role = r.data.role;
    me.periods = r.data.periods;
    me.sections = r.data.sections;
    treeSeq += 1;
    if (!isAccountant(me.role)) closePage();
    tab = applyPortalAccess(me.role, tab);
    if (onAccessChanged) onAccessChanged(me.role);
    if (onUnitOpened) onUnitOpened();
    cache.clear();
    await resetSelection();
    fillPeriods();
    if (activePage) await pages[activePage].reload();
    else await loadTree();
  }

  const unitItems = () => me.units.map((u) => ({ value: u.DC, label: u.NumeUnitate }));

  // ------------------------------------------------------------ list of angajamente
  async function loadTree() {
    say('');
    const an = anCombo.getSelectedValue();
    const ss = ssCombo.getSelectedValue() || ALL_SOURCES;
    if (!me.db_name || !an) {
      $('pa-body').hidden = true;
      hint(me.db_name ? 'Unitatea nu are perioade configurate.' : 'Alegeți unitatea ca să vedeți angajamentele.');
      return;
    }
    hint('');
    $('pa-body').hidden = false;
    const mine = (treeSeq += 1);
    $('app-count').textContent = 'Se încarcă...';
    const r = await call('GET', `/api/portal/date/tree?an=${encodeURIComponent(an)}&ss=${encodeURIComponent(ss)}`);
    if (mine !== treeSeq) return; // a newer choice is already on its way
    if (!r.ok) {
      $('app-count').textContent = '';
      fail(r, 'Lista angajamentelor nu a putut fi citită.');
      return;
    }
    cache.clear();
    await resetSelection();
    if (mine !== treeSeq) return;
    treeRows = r.data.rows;
    rowsByCod.clear();
    treeRows.forEach((row) => rowsByCod.set(String(row.CodAngajament), row));
    paintTree();
    if (!treeRows.length) {
      $('grid-ang').querySelector('.treeview-dropdown').innerHTML =
        '<div class="treeview-no-results">Niciun angajament pentru anul și sursa alese.</div>';
    }
    $('app-count').textContent = `${r.data.count} angajamente`;
  }

  /** Puts the rows in the chosen order into the tree; the picked row stays picked. */
  function paintTree() {
    treeAng.setData(sortRows(treeRows, order.by, order.desc).map(angajamentNode));
    document.querySelectorAll('#tree-menu button').forEach((b) => {
      const on = b.dataset.sort === order.by;
      b.setAttribute('aria-checked', String(on));
      const base = b.dataset.sort === 'name' ? 'Ordonare după nume' : 'Ordonare după dată';
      b.textContent = on ? `${base} ${order.desc ? '↓' : '↑'}` : base;
    });
  }

  function setupTreeMenu() {
    const menu = $('tree-menu');
    const opener = $('btn-tree-opt');
    const close = () => {
      menu.hidden = true;
      opener.setAttribute('aria-expanded', 'false');
    };
    opener.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      opener.setAttribute('aria-expanded', String(open));
    });
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-sort]');
      if (!b) return;
      // the same choice again flips the direction
      if (order.by === b.dataset.sort) order.desc = !order.desc;
      else {
        order.by = b.dataset.sort;
        order.desc = false;
      }
      paintTree();
      close();
    });
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target)) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') close();
    });
    paintTree();
  }

  async function resetSelection() {
    tabSeq += 1;
    picked = null;
    $('card-app').classList.remove('is-detail');
    if (treeAng) treeAng.clearSelection();
    $('pa-sel').textContent = 'Alegeți un angajament din listă.';
    if (gridTab) gridTab.setRows([]);
    gridTabKey = '';
    rowsState = null;
    docState = null;
    pendingDoc = null;
    nodeLines = [];
    treeMode = '';
    if (extCard) extCard.destroy();
    if (treeTab) {
      treeTab.setData([]);
      treeTab.clearSelection();
    }
    if (gridLines) gridLines.setRows([]);
    if (gridDocs) gridDocs.setRows([]);
    if (detailUpdate) detailUpdate(null);
    await clearViewer();
    showPane('grid', false);
    setupDetail('');
  }

  // ------------------------------------------------------------ one angajament
  async function pick(row) {
    if (!row) return;
    picked = row;
    $('card-app').classList.add('is-detail'); // phone: the data replaces the list (CSS only reacts there)
    $('pa-sel').textContent = `${row.CodAngajament} — ${row.Descriere || ''}${row.Stare ? ` (${row.Stare})` : ''}`;
    await loadTab();
  }

  function switchTab(name) {
    if (!TABS[name] && !DOC_TABS[name]) throw new Error(`Unknown tab: ${name}`);
    if (!canOpenTab(me?.role, name)) return;
    tab = name;
    document.querySelectorAll('.pa__tabs button[data-tab]').forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', String(on));
    });
    loadTab();
  }

  /** Makes the tree of the card (once, when its host is on screen: the tree measures the box it sits in). */
  function ensureTabTree() {
    if (treeTab) return;
    treeTab = new TreeView($('tab-tree'), {
      inline: true,
      selectParents: true,
      showSearchBox: false, // the trees of the cards have no search box
      onSelect: (sel) => onTreeSelect(String(sel.id)),
    });
  }

  // ---- the display menu in the header of the Extrase tree (slice 0110-12), as the icon of the desktop tree header
  function closeModeMenu() {
    $('ext-mode-menu').hidden = true;
    $('btn-ext-mode').setAttribute('aria-expanded', 'false');
  }

  function setupModeMenu() {
    const menu = $('ext-mode-menu');
    const opener = $('btn-ext-mode');
    const paint = () => menu.querySelectorAll('button[data-mode]').forEach((b) => {
      b.setAttribute('aria-checked', String(!!extCard && b.dataset.mode === extCard.mode));
    });
    opener.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu.hidden;
      paint();
      menu.hidden = !open;
      opener.setAttribute('aria-expanded', String(open));
    });
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-mode]');
      if (!b || !extCard) return;
      extCard.setMode(b.dataset.mode);
      closeModeMenu();
    });
    document.addEventListener('click', (e) => { if (!menu.hidden && !menu.contains(e.target)) closeModeMenu(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModeMenu(); });
  }

  // ---- the detail under the grid of Istoric and Plati (slice 0110-12; computers): what the desktop views show there
  const ISTORIC_VALUES = [
    ['Rezervare inițială', 'val_rezervare_i'], ['Rezervare definitivă', 'val_rezervare_d'],
    ['Rezervare anterioară', 'val_rezervare_ant'], ['Rezervare diferență', 'val_rezervare_dif'],
    ['Angajament legal', 'val_ang_leg'], ['Recepție', 'val_receptie'], ['Plată', 'val_plata'],
  ];

  const div = (cls, text) => {
    const d = document.createElement('div');
    d.className = cls;
    if (text !== undefined) d.textContent = text;
    return d;
  };

  function fillPairs(dl, pairs) {
    dl.textContent = '';
    pairs.forEach(([label, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = value == null ? '' : String(value);
      dl.append(dt, dd);
    });
  }

  /** Builds the detail area of the card (or hides it): sets `detailUpdate(row)`. */
  function setupDetail(name) {
    const host = $('tab-detail');
    host.textContent = '';
    if (gridValori) {
      gridValori.destroy();
      gridValori = null;
    }
    detailUpdate = null;
    if (!isPc() || (name !== 'istoric' && name !== 'plati')) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    if (name === 'istoric') {
      // IstoricView: the description (Observatii) of the chosen row, and its non-zero values (Tip | Valoare)
      host.className = 'pa__detail pa__detail--istoric';
      const left = div('pa__det-desc');
      const text = div('pa__det-text');
      left.append(div('pa__det-cap', 'Descriere'), text);
      const right = div('pa__det-vals');
      right.id = 'grid-valori';
      host.append(left, right);
      gridValori = makeGrid('grid-valori', 'ang.istoric-valori', { rows: [], emptyText: '' });
      detailUpdate = (row) => {
        text.textContent = row ? (row.observatii || '') : '';
        gridValori.setRows(row
          ? ISTORIC_VALUES.map(([tip, key]) => ({ tip, valoare: Number(row[key]) || 0 })).filter((v) => v.valoare !== 0)
          : []);
      };
    } else {
      // PlatiView: the bank statement line behind the chosen payment
      host.className = 'pa__detail pa__detail--plati';
      const msg = div('pa__det-msg');
      const dl = document.createElement('dl');
      dl.className = 'kv pa__kv';
      host.append(msg, dl);
      detailUpdate = (row) => {
        const noExtras = row && row.idfxe == null && !row.nr_doc_extras && !row.platitor_nume;
        msg.textContent = !row ? 'Selectați o plată.' : (noExtras ? 'Fără extras bancar asociat.' : '');
        msg.hidden = !msg.textContent;
        dl.hidden = !row || noExtras;
        if (dl.hidden) return;
        fillPairs(dl, [
          ['Nr. document', row.nr_doc_extras], ['Data bancă', roDay(row.data_banca)], ['Data document', row.data_doc],
          ['Referință', row.referinta], ['Plătitor', row.platitor_nume], ['CUI', row.platitor_cui],
          ['IBAN', row.platitor_iban], ['Sumă debit', roMoney(row.suma_debit)], ['Sumă credit', roMoney(row.suma_credit)],
          ['Explicații', row.explicatii],
        ]);
      };
    }
    detailUpdate(null);
  }

  /** The grid of a row card: the same grid (and widths) is kept while the node changes, so the columns do not jump. */
  function paintTab(name, nodeRows, allRows) {
    const key = `${picked.CodAngajament}|${name}`;
    if (gridTab && gridTabKey === key) {
      gridTab.clearFilters();
      gridTab.setRows(nodeRows, { keepWidths: true });
      if (detailUpdate) detailUpdate(null);
      return;
    }
    if (gridTab) gridTab.destroy();
    const def = TABS[name];
    gridTab = makeGrid('grid-tab', def.grid, {
      rows: allRows,
      emptyText: 'Nu există date pentru acest angajament.',
      onSelect: (row) => { if (detailUpdate) detailUpdate(row); },
      ...def.options,
    });
    gridTabKey = key;
    if (nodeRows !== allRows) gridTab.setRows(nodeRows, { keepWidths: true });
  }

  /** Fills the tree of the card with the root open (months stay closed), as the desktop trees start. */
  function setTabTree(nodes) {
    treeTab.setData(nodes);
    if (!treeTab.expandedNodes.has(ROOT)) treeTab.toggleNode(ROOT, true);
  }

  /** A click in the tree of the open card. */
  function onTreeSelect(id) {
    if (treeMode === 'rows' && rowsState && rowsState.built) {
      const node = rowsState.built.info.get(id);
      if (node) paintTab(rowsState.name, node.rows, rowsState.rows);
    } else if (treeMode === 'docs' && docState) {
      chooseDocNode(id);
    } else if (treeMode === 'ext' && extCard) {
      extCard.show(id);
    }
  }

  /** What is on screen under the tabs: 'grid' (a row card), 'docs' (lines + viewer) or 'ext' (the Extrase card). */
  function showPane(kind, withTree) {
    $('grid-tab').hidden = kind !== 'grid';
    $('docs-tab').hidden = kind !== 'docs';
    $('ext-tab').hidden = kind !== 'ext';
    $('tab-tree-host').hidden = !withTree;
    $('tab-tree-head').hidden = kind !== 'ext';
    closeModeMenu();
    if (withTree) ensureTabTree();
  }

  /** The data of a card: the rows of a row card; the answers of the routes of a document card. Null when the read failed. */
  async function fetchTab(name, cod) {
    if (!canOpenTab(me?.role, name)) return null;
    const q = encodeURIComponent(cod);
    if (TABS[name]) {
      const r = await call('GET', `/api/portal/date/${TABS[name].path}?cod=${q}`);
      if (!r.ok) {
        fail(r, 'Datele nu au putut fi citite.');
        return null;
      }
      const rows = TABS[name].rows(r.data);
      if (name === 'extrase') rows.raw = r.data; // the Extrase card on a computer needs the headers too
      return rows;
    }
    if (name === 'fundamentari') {
      const r = await call('GET', `/api/portal/date/ddf?cod=${q}`);
      if (!r.ok) {
        fail(r, 'Documentele nu au putut fi citite.');
        return null;
      }
      return r.data;
    }
    const [ord, note] = await Promise.all([
      call('GET', `/api/portal/date/ord?cod=${q}`),
      isAccountant(me.role) ? call('GET', `/api/portal/date/note?cod=${q}`)
        : Promise.resolve({ ok: true, data: { note: [] } }),
    ]);
    const bad = [ord, note].find((r) => !r.ok);
    if (bad) {
      fail(bad, 'Documentele nu au putut fi citite.');
      return null;
    }
    return { ord: ord.data, note: note.data };
  }

  async function loadTab() {
    if (!picked || !canOpenTab(me?.role, tab)) return;
    say('');
    const isDoc = !!DOC_TABS[tab];
    const key = `${picked.CodAngajament}|${tab}`;
    const mine = (tabSeq += 1);
    let data = cache.get(key);
    if (data === undefined) {
      data = await fetchTab(tab, picked.CodAngajament);
      if (mine !== tabSeq || data === null) return; // an older answer, or the read failed (already said)
      cache.set(key, data);
    }
    if (extCard) extCard.destroy();
    if (isDoc) {
      await renderDocTab(tab, data);
    } else {
      await clearViewer(); // a document still loading for the card we just left must not paint
      if (mine !== tabSeq) return;
      renderRowsTab(tab, data);
    }
  }

  function renderRowsTab(name, rows) {
    docState = null;
    if (name === 'extrase' && isPc()) {
      renderExtraseCard(rows);
      return;
    }
    const withTree = isPc() && hasTree(name);
    showPane('grid', withTree);
    setupDetail(name);
    treeMode = withTree ? 'rows' : '';
    rowsState = { name, rows, built: null };
    if (!withTree) {
      paintTab(name, rows, rows);
      return;
    }
    rowsState.built = buildTabTree(name, rows);
    setTabTree(rowsState.built.nodes);
    treeTab.selectNodeById(ROOT); // paints the grid through onSelect
  }

  /** Slice 0110-12: the Extrase card on a computer: tree + display menu + grids, as ExtraseView. */
  function renderExtraseCard(rows) {
    showPane('ext', true);
    setupDetail('extrase'); // hides the detail area of Istoric / Plati
    treeMode = 'ext';
    rowsState = null;
    if (!extCard) extCard = createExtraseCard({ top: 'xt-top', ops: 'xt-ops', detail: 'xt-detail' });
    setTabTree(extCard.load(rows.raw || { antete: [], operatiuni: rows }));
    treeTab.selectNodeById(ROOT);
  }

  // ------------------------------------------------------------ the document cards (slices 0110-08, 0110-10, 0110-12)
  const docMsg = (text) => {
    showMessage($('msg-doc'), text, 'error');
  };

  async function clearViewer() {
    docSeq += 1;
    docMsg('');
    $('btn-docdl').hidden = true;
    docBlob = null;
    openedDocId = '';
    if (pdfView) await pdfView.clear();
  }

  /** The page of the card on a computer (the navbar of DdfView / OrdView): 'view' = header + lines, 'doc' = the PDF. */
  function setDocPage(page) {
    docPage = page;
    $('doc-page-view').classList.toggle('pa__page--off', page !== 'view');
    $('doc-page-doc').classList.toggle('pa__page--off', page !== 'doc');
    document.querySelectorAll('#doc-nav button').forEach((b) => {
      const on = b.dataset.page === page;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', String(on));
    });
    syncDoc();
  }

  /**
   * Puts the PDF of the chosen document into the viewer when it is on screen (the Document page on a computer; always
   * on a phone, where both parts are shown). A viewer that is not on screen has no width to draw into, so it waits.
   */
  async function syncDoc() {
    if (!docState || (isPc() && docPage !== 'doc')) return;
    if (!pendingDoc) {
      await clearViewer();
      docMsg(DOC_TABS[docState.name].pick);
      return;
    }
    if (openedDocId === pendingDoc.id && docBlob) return;
    openedDocId = pendingDoc.id;
    await openDoc(pendingDoc);
  }

  const beneficiaryText = (a) => {
    if (!a) return '';
    if (!a.part_ang) return 'Fără partener';
    const name = String(a.nume_partener || '').trim();
    if (!name) return '';
    return a.cod_fiscal ? `${name} (CIF ${String(a.cod_fiscal).trim()})` : name;
  };

  /** DdfVizualizarePage header: the document's header data. OrdVizualizarePage header: the beneficiary filter. */
  function renderDocHead(name, data, built) {
    const head = $('doc-head');
    head.textContent = '';
    head.hidden = !isPc();
    orderFilter = '';
    if (name === 'fundamentari') {
      const a = (data.antet || [])[0] || null;
      const dl = document.createElement('dl');
      dl.className = 'kv pa__kv pa__kv--head';
      fillPairs(dl, [
        ['Cod angajament', (a && a.cod_angajament) || picked.CodAngajament], ['Data creare', a ? roDay(a.data_creare) : ''],
        ['Compartimentul', a && a.comp], ['CUAL', a && a.cual != null ? a.cual : ''],
        ['Beneficiar', beneficiaryText(a)], ['Obiect DDF', a && a.obiect_ddf],
      ]);
      head.appendChild(dl);
      return;
    }
    const names = [...new Set(built.allLines.map((l) => String(l.den_bene || '').trim()).filter(Boolean))]
      .sort((x, y) => nameCmp.compare(x, y));
    const label = document.createElement('label');
    label.className = 'pa__bene';
    label.append('Caută beneficiar ');
    const select = document.createElement('select');
    select.id = 'sel-bene';
    select.add(new Option('(toți beneficiarii)', ''));
    names.forEach((n) => select.add(new Option(n, n)));
    select.addEventListener('change', () => {
      orderFilter = select.value;
      paintLines();
    });
    label.appendChild(select);
    head.appendChild(label);
  }

  /** The lines of the node, narrowed to the chosen beneficiary on an Ordonantari card. */
  function paintLines() {
    if (!gridLines) return;
    const rows = orderFilter ? nodeLines.filter((l) => String(l.den_bene || '').trim() === orderFilter) : nodeLines;
    gridLines.setRows(rows, { keepWidths: true });
    renderDocFoot(null);
  }

  /** OrdVizualizarePage footer: the beneficiary data of the chosen line. */
  function renderDocFoot(line) {
    const foot = $('doc-foot');
    foot.hidden = !(isPc() && docState && docState.name === 'ordonantari');
    if (foot.hidden) return;
    const l = line || {};
    fillPairs(foot, [
      ['Beneficiar', l.den_bene], ['Cod fiscal', l.cod_fiscal], ['Cont IBAN', l.cont_iban],
      ['Doc. justificative', l.doc_just], ['Obiect DDF', l.obiect_ddf],
    ]);
  }

  async function renderDocTab(name, data) {
    const cfg = DOC_TABS[name];
    const pc = isPc();
    const tabTag = tabSeq;
    await clearViewer();
    showPane('docs', pc);
    const built = buildTabTree(name, data);
    if (tabTag !== tabSeq) return; // another card was chosen while the viewer was clearing
    docState = { name, built };
    rowsState = null;
    pendingDoc = null;
    nodeLines = built.allLines;
    treeMode = pc ? 'docs' : '';
    setupDetail(name);
    $('grid-docs').hidden = pc; // the list of documents is for a phone: on a computer the tree is the list
    if (gridLines) gridLines.destroy();
    if (gridDocs) gridDocs.destroy();
    gridDocs = null;
    renderDocHead(name, data, built);
    gridLines = makeGrid('grid-lines', cfg.grid, {
      rows: built.allLines, footer: true, footerCaption: '{0} linii', emptyText: cfg.emptyLines,
      onSelect: (row) => renderDocFoot(row),
    });
    renderDocFoot(null);
    setDocPage('view');
    if (pc) setTabTree(built.nodes);
    if (!built.docs.length) {
      docMsg(cfg.none);
      return;
    }
    if (pc) {
      // One document: its node is chosen at once; several: the root, and the person chooses.
      treeTab.selectNodeById(built.docs.length === 1 ? built.docs[0].id : ROOT);
      return;
    }
    gridDocs = makeGrid('grid-docs', cfg.listGrid, {
      rows: built.docs, emptyText: cfg.emptyList, onSelect: (row) => chooseDoc(row),
    });
    const openable = built.docs.filter((d) => d.pdf);
    if (openable.length === 1) chooseDoc(openable[0]);
    else docMsg(cfg.pickList);
  }

  /** Phone: a row of the list of documents. */
  function chooseDoc(row) {
    if (!row || !docState) return;
    const node = docState.built.info.get(row.id);
    if (node) nodeLines = node.rows;
    paintLines();
    pendingDoc = row;
    syncDoc();
  }

  /** Computer: a node of the tree. A document node shows its lines and (on the Document page) its PDF; a folder shows the lines of all it holds. */
  async function chooseDocNode(id) {
    if (!docState) return;
    const node = docState.built.info.get(id);
    if (!node) return;
    nodeLines = node.rows;
    paintLines();
    pendingDoc = node.doc;
    await syncDoc();
  }

  async function openDoc(row) {
    if (!row) return;
    const mine = (docSeq += 1);
    docMsg('');
    $('btn-docdl').hidden = true;
    docBlob = null;
    await pdfView.clear();
    if (!row.pdf) {
      docMsg('Documentul nu are un PDF semnat pe server. Îl găsiți în K-BOT.');
      return;
    }
    docMsg('Se încarcă documentul...');
    const r = await callBytes(`/api/portal/date/${row.path}`);
    if (mine !== docSeq) return;
    if (!r.ok) {
      docMsg('');
      fail(r, 'PDF-ul nu a putut fi citit.');
      return;
    }
    docBlob = { bytes: r.data, name: fileName(row) };
    $('btn-docdl').hidden = false;
    try {
      await pdfView.open(r.data);
    } catch (err) {
      if (mine === docSeq) docMsg(err.message);
      return;
    }
    if (mine === docSeq) docMsg('');
  }

  function fileName(row) {
    const clean = (t) => String(t).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '');
    return `${clean(row.tip)}_${clean(row.nr)}_${clean(picked.CodAngajament)}.pdf`;
  }

  function downloadDoc() {
    if (!docBlob) return;
    const url = URL.createObjectURL(new Blob([docBlob.bytes], { type: 'application/pdf' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = docBlob.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // ------------------------------------------------------------ real rows for the column editor (slice 0110-11)
  // The «Coloane» preview shows what the grid would show for the unit that is open: the rows of the first
  // angajamente that have any (cards), or the unit's own lists (statements, partners, classifications).
  const GRID_TAB = {
    'ang.sumar': 'sumar', 'ang.istoric': 'istoric', 'ang.rezervari': 'rezervari', 'ang.receptii': 'receptii',
    'ang.extrase': 'extrase', 'ang.plati': 'plati', 'ang.fundamentari': 'fundamentari',
    'ang.fundamentari-lista': 'fundamentari', 'ang.ordonantari': 'ordonantari', 'ang.ordonantari-lista': 'ordonantari',
  };
  const QUARTER_KEYS = ['trim1', 'trim2', 'trim3', 'trim4'];
  const withTotal = (r) => ({ ...r, total: QUARTER_KEYS.reduce((s, k) => s + (Number(r[k]) || 0), 0) });

  async function getJson(path, fallback) {
    const r = await call('GET', path);
    if (!r.ok) {
      fail(r, fallback);
      return null;
    }
    return r.data;
  }

  /** @returns {Promise<{rows: object[], why: string}>} why = a sentence when there are no rows */
  async function realRows(id) {
    if (!me || !me.db_name) return { rows: [], why: 'Alegeți mai întâi o unitate din bara de sus: grila se vede cu datele ei.' };
    const none = (why) => ({ rows: [], why });
    if (GRID_TAB[id]) {
      const name = GRID_TAB[id];
      const rows = [];
      const candidates = sortRows(treeRows, 'name', false).slice(0, 15);
      if (!candidates.length) return none('Unitatea nu are angajamente pentru anul și sursa alese.');
      for (const cand of candidates) {
        const key = `${cand.CodAngajament}|${name}`;
        let data = cache.get(key);
        if (data === undefined) {
          data = await fetchTab(name, cand.CodAngajament);
          if (data === null) return none('Datele nu au putut fi citite.');
          cache.set(key, data);
        }
        if (name === 'fundamentari' || name === 'ordonantari') {
          const built = buildTabTree(name, data);
          rows.push(...(id.endsWith('-lista') ? built.docs : built.allLines));
        } else rows.push(...data);
        if (rows.length >= 40) break;
      }
      return rows.length ? { rows, why: '' } : none('Primele angajamente ale unității nu au date pentru această grilă.');
    }
    if (id.startsWith('extrase.')) {
      const d = await getJson('/api/portal/date/extrase', 'Extrasele nu au putut fi citite.');
      if (!d) return none('Extrasele nu au putut fi citite.');
      if (id === 'extrase.antete') return { rows: d.antete || [], why: '' };
      const heads = new Map((d.antete || []).map((a) => [a.idexh, a]));
      return { rows: (d.operatiuni || []).map((o) => ({ ...o, clsf: (heads.get(o.idfxh) || {}).clsf || '' })), why: '' };
    }
    if (id === 'parteneri.coduri') {
      const d = await getJson('/api/portal/date/parteneri', 'Partenerii nu au putut fi citiți.');
      return d ? { rows: d.partners.flatMap((p) => p.coduri || []), why: '' } : none('Partenerii nu au putut fi citiți.');
    }
    if (id.startsWith('clsf.')) {
      const an = encodeURIComponent(anCombo.getSelectedValue());
      if (id === 'clsf.verificare') {
        const d = await getJson('/api/portal/date/clasificatii-verificare', 'Verificarea nu a putut fi citită.');
        return d ? { rows: d.items, why: '' } : none('Verificarea nu a putut fi citită.');
      }
      const [cat, sum] = await Promise.all([
        getJson('/api/portal/date/clasificatii', 'Clasificațiile nu au putut fi citite.'),
        getJson('/api/portal/date/clasificatii-sumar?an=' + an, 'Clasificațiile nu au putut fi citite.'),
      ]);
      if (!cat || !sum) return none('Clasificațiile nu au putut fi citite.');
      const code = new Map(cat.items.map((c) => [c.id_clsf, c.clsf]));
      const active = sum.items.filter((i) => i.activ);
      if (id === 'clsf.buget-grup') return { rows: active.filter((i) => i.budget).map((i) => withTotal({ clsf: code.get(i.id_clsf), ...i.budget })), why: '' };
      if (id === 'clsf.rectificari-grup') return { rows: active.filter((i) => i.corrections).map((i) => withTotal({ clsf: code.get(i.id_clsf), ...i.corrections })), why: '' };
      if (id === 'clsf.total') {
        const sums = { eticheta: 'Buget + rectificări' };
        QUARTER_KEYS.forEach((k) => { sums[k] = active.reduce((s, i) => s + ((i.budget && i.budget[k]) || 0) + ((i.corrections && i.corrections[k]) || 0), 0); });
        return { rows: [withTotal(sums)], why: '' };
      }
      // one classification: the first active ones until a budget (or corrections) turns up
      const wanted = id === 'clsf.buget' ? 'budgets' : 'corrections';
      const rows = [];
      for (const item of active.slice(0, 10)) {
        const d = await getJson('/api/portal/date/clasificatii-buget/' + encodeURIComponent(item.id_clsf) + '?an=' + an, 'Bugetul nu a putut fi citit.');
        if (d) rows.push(...d[wanted].map(withTotal));
        if (rows.length >= 20) break;
      }
      return rows.length ? { rows, why: '' } : none('Clasificațiile active ale anului nu au ' + (wanted === 'budgets' ? 'buget.' : 'rectificări.'));
    }
    return none('Grila nu are un cititor.');
  }

  // ------------------------------------------------------------ the pages of the header menu
  // Each page module offers show / hide / reload and is made on first use; only one is open at a time.
  const pages = {};
  let activePage = '';
  const pageMakers = {
    clasificatii: () => createClasificatiiPage({
      call, fail, say, year: () => anCombo.getSelectedValue(), hasUnit: () => !!(me && me.db_name),
    }),
    parteneri: () => createParteneriPage({ call, fail, say, hasUnit: () => !!(me && me.db_name) }),
    extrase: () => createExtrasePage({ call, fail, say, hasUnit: () => !!(me && me.db_name) }),
  };

  function closePage() {
    if (activePage) pages[activePage].hide();
    activePage = '';
  }

  async function openPage(name) {
    if (!pageMakers[name]) throw new Error(`Unknown page: ${name}`);
    if (!isAccountant(me?.role)) return;
    if (!me || !me.db_name) {
      say('Alegeți mai întâi unitatea.');
      return;
    }
    closePage();
    if (!pages[name]) pages[name] = pageMakers[name]();
    activePage = name;
    await pages[name].show();
  }

  // ------------------------------------------------------------ public
  return {
    openPage,
    closePage,
    /** Shows the page for the account `meData` ({email, units, db_name, role, periods}). */
    async open(meData) {
      me = meData;
      treeSeq += 1;
      ensureWidgets();
      closePage();
      tab = applyPortalAccess(me.role, tab);
      if (onAccessChanged) onAccessChanged(me.role);
      say('');
      cache.clear();
      await resetSelection();
      setOptions(unitCombo, unitItems(), me.db_name);
      if (me.db_name) {
        fillPeriods();
        await loadTree();
      } else {
        setOptions(anCombo, [], '');
        setOptions(ssCombo, [], '');
        $('pa-body').hidden = true;
        $('app-count').textContent = '';
        hint('Contul dumneavoastră are acces la mai multe unități. Alegeți una din bara de sus.');
      }
    },
  };
}
