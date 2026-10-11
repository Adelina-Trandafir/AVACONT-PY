// The «Clasificatii» page of the portal (menu Nomenclatoare) and its «Verificare buget FOREXE» page.
// VIEW ONLY: the web counterpart of the desktop ClasificatiiForm / BudgetCheckForm without any editing.
//
//   left   the tree Capitol (source) > Subcapitol > Articol > Alineat (TreeView); a click on any node
//          chooses it, a parent too (selectParents)
//   right  for an alineat: its budget versions of the year and its corrections; for a parent node: the
//          LAST budget and the corrections total of every classification under it; under both, one row
//          with budget + corrections, quarter by quarter
//
// The check page lists the classifications whose FOREXE credit differs from the K-BOT budget; a double
// click on a row opens that classification here.

import { DataGrid } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { columnsOf, QUARTERS } from './columns.js';

const $ = (id) => document.getElementById(id);
const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);
const sumOf = (r) => (r.trim1 || 0) + (r.trim2 || 0) + (r.trim3 || 0) + (r.trim4 || 0);

function withTotal(r) {
  return { ...r, total: sumOf(r) };
}

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * @param {{call: Function, fail: Function, say: Function, year: () => string, hasUnit: () => boolean}} deps
 *   call(method, path) -> {ok, status, data}; fail(r, fallback) shows the server's sentence (or ends the session);
 *   year() is the year chosen in the filter bar.
 */
export function createClasificatiiPage({ call, fail, say, year, hasUnit }) {
  let tree = null;
  let gBudget = null;
  let gCorr = null;
  let gTotal = null;
  let catalog = null; // {items, names}
  let activeIds = new Set();
  let summaryById = new Map();
  let loadedKey = '';
  let seq = 0;
  let budgetSeq = 0;
  let bound = false;
  let checkGrid = null;
  let checkSeq = 0;

  // ------------------------------------------------------------ the tree
  function visibleItems() {
    const items = catalog.items;
    if ($('chk-clsf-forexe').checked) return items.filter((c) => c.in_forexe);
    if ($('chk-clsf-toate').checked) return items;
    return items.filter((c) => activeIds.has(c.id_clsf));
  }

  const left2 = (capitol) => (capitol.length >= 2 ? capitol.slice(0, 2) : capitol);
  const nameOf = (dict, key) => String((dict && dict[key]) || '').trim();
  const join = (a, b) => (a && b ? `${a} — ${b}` : a || b);

  function buildNodes(items) {
    const { names } = catalog;
    const chapters = new Map();
    items.forEach((c) => {
      const k = `${c.capitol}|${c.ss}`;
      if (!chapters.has(k)) chapters.set(k, []);
      chapters.get(k).push(c);
    });
    const sortedChapters = [...chapters.entries()].sort((a, b) => byText(a[0], b[0]));
    return sortedChapters.map(([k, list]) => {
      const [capitol, ss] = k.split('|');
      const subs = new Map();
      list.forEach((c) => {
        if (!subs.has(c.subcapitol)) subs.set(c.subcapitol, []);
        subs.get(c.subcapitol).push(c);
      });
      return {
        id: `C|${capitol}|${ss}`,
        label: `${capitol} (${ss})  ${join(nameOf(names.capitol, left2(capitol)), nameOf(names.ss, ss))}`,
        bold: true,
        keywords: `${capitol} ${ss}`,
        children: [...subs.entries()].sort((a, b) => byText(a[0], b[0])).map(([sub, subList]) => {
          const arts = new Map();
          subList.forEach((c) => {
            if (!arts.has(c.articol)) arts.set(c.articol, []);
            arts.get(c.articol).push(c);
          });
          return {
            id: `S|${capitol}|${ss}|${sub}`,
            label: `${sub}  ${nameOf(names.subcapitol, left2(capitol) + sub.replace('.', ''))}`,
            children: [...arts.entries()].sort((a, b) => byText(a[0], b[0])).map(([art, artList]) => ({
              id: `A|${capitol}|${ss}|${sub}|${art}`,
              label: `${art}  ${nameOf(names.articol, art)}`,
              children: artList.sort((a, b) => byText(a.alineat, b.alineat)).map((c) => ({
                id: `L|${c.id_clsf}`,
                label: `${c.articol}.${c.alineat}  ${c.denumire}`,
                tooltip: c.clsf,
                keywords: c.clsf,
              })),
            })),
          };
        }),
      };
    });
  }

  function paintTree(selectId) {
    const shown = visibleItems();
    const k_nodes = buildNodes(shown);
    tree.setData(k_nodes);
    // level 0 starts open; the other roots stay as the operator left them (several can be open at once)
    k_nodes.forEach((k_n) => { if (k_n.children && k_n.children.length) tree.expandedNodes.add(String(k_n.id)); });
    tree.isTreeRendered = false;
    tree.renderTree(tree.currentQuery);
    const total = catalog.items.length;
    const kind = $('chk-clsf-forexe').checked ? 'cele folosite în FOREXE'
      : $('chk-clsf-toate').checked ? '' : `cele cu mișcare în ${year()}`;
    $('clsf-count').textContent = kind ? `${shown.length} din ${total}, ${kind}` : `${shown.length} clasificații`;
    if (selectId) tree.selectNodeById(selectId);
  }

  // ------------------------------------------------------------ loading
  async function load() {
    const an = year();
    if (!hasUnit() || !an) {
      say('Alegeți unitatea și anul ca să vedeți clasificațiile.');
      return false;
    }
    const mine = (seq += 1);
    $('clsf-count').textContent = 'Se încarcă...';
    const [t, s] = await Promise.all([
      call('GET', '/api/portal/date/clasificatii'),
      call('GET', `/api/portal/date/clasificatii-sumar?an=${encodeURIComponent(an)}`),
    ]);
    if (mine !== seq) return false;
    const bad = [t, s].find((r) => !r.ok);
    if (bad) {
      $('clsf-count').textContent = '';
      fail(bad, 'Clasificațiile nu au putut fi citite.');
      return false;
    }
    catalog = t.data;
    summaryById = new Map(s.data.items.map((i) => [i.id_clsf, i]));
    activeIds = new Set(s.data.items.filter((i) => i.activ).map((i) => i.id_clsf));
    loadedKey = `${an}`;
    return true;
  }

  function bind() {
    if (bound) return;
    bound = true;
    tree = new TreeView($('tree-clsf'), {
      inline: true,
      autoCollapse: false,
      selectParents: true,
      searchPlaceholder: 'Căutați (minimum 3 caractere)…',
      onSelect: (sel) => choose(String(sel.id)),
    });
    ['chk-clsf-toate', 'chk-clsf-forexe'].forEach((id) => $(id).addEventListener('change', () => {
      if (catalog) paintTree(null);
    }));
    $('btn-clsf-check').addEventListener('click', () => showCheck());
    $('btn-check-back').addEventListener('click', () => show());
  }

  // ------------------------------------------------------------ the right side
  function clearRight(title) {
    $('clsf-title').textContent = title || 'Alegeți o clasificație din listă.';
    [gBudget, gCorr, gTotal].forEach((g) => g && g.destroy());
    gBudget = null;
    gCorr = null;
    gTotal = null;
    $('clsf-buget-title').textContent = '';
    $('clsf-corr-title').textContent = '';
  }

  function paintGrids({ budgetGrid, budgetRows, corrGrid, corrRows, budgetTitle, corrTitle, corrFooter }) {
    [gBudget, gCorr, gTotal].forEach((g) => g && g.destroy());
    $('clsf-buget-title').textContent = budgetTitle;
    $('clsf-corr-title').textContent = corrTitle;
    gBudget = new DataGrid($('grid-clsf-buget'), {
      columns: columnsOf(budgetGrid), layoutId: budgetGrid, rows: budgetRows, rowHeight: rowHeight(), emptyText: 'Nu există buget pentru anul ales.',
    });
    gCorr = new DataGrid($('grid-clsf-corr'), {
      columns: columnsOf(corrGrid), layoutId: corrGrid, rows: corrRows, rowHeight: rowHeight(), footer: corrFooter, footerCaption: '{0} rectificări',
      emptyText: 'Nu există rectificări în anul ales.',
    });
    // the row under both: the LAST budget + ALL the corrections, quarter by quarter
    const sums = { trim1: 0, trim2: 0, trim3: 0, trim4: 0 };
    const addTo = (r) => QUARTERS.forEach((q) => { sums[q.key] += r[q.key] || 0; });
    budgetRows.filter((r) => r.last).forEach(addTo);
    corrRows.forEach(addTo);
    const host = $('grid-clsf-total');
    host.style.height = `${2 * rowHeight() + 4}px`;
    gTotal = new DataGrid(host, {
      columns: columnsOf('clsf.total'),
      layoutId: 'clsf.total',
      rows: [withTotal({ 'eticheta': 'Buget + rectificări', ...sums })],
      rowHeight: rowHeight(),
      emptyText: '',
    });
  }

  async function choose(id) {
    const an = year();
    const parts = id.split('|');
    const label = tree.selectedText || id;
    if (parts[0] === 'L') {
      const mine = (budgetSeq += 1);
      clearRight(label);
      const r = await call('GET', `/api/portal/date/clasificatii-buget/${encodeURIComponent(parts[1])}?an=${encodeURIComponent(an)}`);
      if (mine !== budgetSeq) return;
      if (!r.ok) {
        fail(r, 'Bugetul nu a putut fi citit.');
        return;
      }
      const item = catalog.items.find((c) => String(c.id_clsf) === parts[1]);
      const budgets = r.data.budgets.map(withTotal);
      let latest = null;
      budgets.forEach((b) => { if (!latest || String(b.data_inceput) > String(latest.data_inceput)) latest = b; });
      budgets.forEach((b) => { b.last = b === latest; });
      paintGrids({
        budgetTitle: `Buget ${an}${item ? ` — ${item.clsf}` : ''}`,
        corrTitle: `Rectificări bugetare ${an}`,
        budgetGrid: 'clsf.buget',
        budgetRows: budgets,
        corrGrid: 'clsf.rectificari',
        corrRows: r.data.corrections.map(withTotal),
        corrFooter: true,
      });
      return;
    }
    // a parent node: the last budget and the corrections total of each classification under it
    budgetSeq += 1;
    const leaves = leavesUnder(parts);
    clearRight(label);
    const budgetRows = [];
    const corrRows = [];
    leaves.forEach((c) => {
      const s = summaryById.get(c.id_clsf);
      budgetRows.push(withTotal({ clsf: c.clsf, ...(s && s.budget ? s.budget : {}), last: true }));
      if (s && s.corrections) corrRows.push(withTotal({ clsf: c.clsf, ...s.corrections }));
    });
    const code = parts.slice(1).filter((_, i) => i !== 1).join('.');
    paintGrids({
      budgetTitle: `Buget ${an} — ${code} (${parts[2]}): ${leaves.length} clasificații`,
      corrTitle: `Rectificări bugetare ${an} — total pe clasificație`,
      budgetGrid: 'clsf.buget-grup',
      budgetRows,
      corrGrid: 'clsf.rectificari-grup',
      corrRows,
      corrFooter: false,
    });
  }

  /** The classifications under a chapter (C|cap|ss), sub-chapter (S|…|sub) or article (A|…|art) node. */
  function leavesUnder(parts) {
    const need = { C: 3, S: 4, A: 5 }[parts[0]];
    if (parts.length !== need) return [];
    return visibleItems()
      .filter((x) => x.capitol === parts[1] && x.ss === parts[2]
        && (parts.length < 4 || x.subcapitol === parts[3])
        && (parts.length < 5 || x.articol === parts[4]))
      .sort((a, b) => byText(a.subcapitol, b.subcapitol) || byText(a.articol, b.articol) || byText(a.alineat, b.alineat));
  }

  // ------------------------------------------------------------ the check page
  async function showCheck() {
    $('card-app').classList.remove('is-clsf');
    $('card-app').classList.add('is-clsfcheck');
    $('clsf-view').hidden = true;
    $('clsf-check-view').hidden = false;
    $('check-state').textContent = 'Se verifică bugetul față de FOREXE...';
    if (checkGrid) checkGrid.destroy();
    checkGrid = null;
    const mine = (checkSeq += 1);
    const r = await call('GET', '/api/portal/date/clasificatii-verificare');
    if (mine !== checkSeq) return;
    if (!r.ok) {
      $('check-state').textContent = '';
      fail(r, 'Bugetul nu a putut fi verificat.');
      return;
    }
    const day = String(r.data.data || '').split('-').reverse().join('.');
    $('check-title').textContent = `Verificare buget FOREXE — ${day}`;
    const all = r.data.items;
    const rows = all.filter((i) => !i.egal);
    $('check-state').textContent = rows.length === 0
      ? `Toate cele ${all.length} clasificații au aceeași valoare în FOREXE și în K-BOT.`
      : `${rows.length} din ${all.length} clasificații diferă. Un buget gol înseamnă că nu există versiune K-BOT în vigoare azi, `
        + 'respectiv nicio descărcare de indicatori. Dublu clic pe un rând deschide clasificația.';
    checkGrid = new DataGrid($('grid-check'), {
      columns: columnsOf('clsf.verificare'),
      layoutId: 'clsf.verificare',
      rows,
      rowHeight: rowHeight(),
      footer: true,
      footerCaption: '{0} diferențe',
      emptyText: 'Nicio diferență.',
      onRowDblClick: (row) => goTo(row.id_clsf),
    });
  }

  // ------------------------------------------------------------ public
  /** Shows the page (loads the data when the year or the unit changed). */
  async function show() {
    bind();
    say('');
    $('card-app').classList.remove('is-clsfcheck');
    $('card-app').classList.add('is-clsf');
    $('clsf-check-view').hidden = true;
    $('clsf-view').hidden = false;
    if (!catalog || loadedKey !== `${year()}`) {
      clearRight('');
      if (!(await load())) return;
      paintTree(null);
    }
  }

  /** Forces a new read (the unit or the year changed). */
  async function reload() {
    catalog = null;
    loadedKey = '';
    if ($('clsf-view').hidden) return;
    clearRight('');
    await show();
  }

  function hide() {
    checkSeq += 1;
    $('card-app').classList.remove('is-clsf', 'is-clsfcheck');
    $('clsf-view').hidden = true;
    $('clsf-check-view').hidden = true;
  }

  /** Opens one classification in the tree; the filters are loosened first when they hide it. */
  async function goTo(idClsf) {
    await show();
    if (!catalog || !catalog.items.some((c) => c.id_clsf === idClsf)) return;
    if (!visibleItems().some((c) => c.id_clsf === idClsf)) {
      $('chk-clsf-forexe').checked = false;
      $('chk-clsf-toate').checked = true;
    }
    paintTree(`L|${idClsf}`);
  }

  return { show, reload, hide, goTo };
}
