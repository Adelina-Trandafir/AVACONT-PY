// The «Extrase de cont» page of the portal (menu Extrase). VIEW ONLY: the web counterpart of the
// desktop ExtraseForm / ExtrasePanel (mode «antet + operatii») without the download from FOREXE.
//
//   left   the tree «Toate extrasele» > month > day. A day is a DataBanca of the operations; a header
//          (FX_Extrase_H) sits under every day on which one of its operations has a DataBanca, and a
//          header with no operation sits on its statement's date
//   right  root / month: the headers on top, the operations below (all of the node's until a header is
//          chosen, then the chosen header's); a day: that day's operations and, under them, the chosen
//          operation in full

import { DataGrid } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { columnsOf } from './columns.js';

const $ = (id) => document.getElementById(id);
const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);

// the columns and the saved layout of each grid come from the catalog (columns.js)
const OPERATIONS = 'extrase.operatii';
const HEADERS = 'extrase.antete';
const makeGrid = (host, id, extra) => new DataGrid($(host), { columns: columnsOf(id), layoutId: id, rowHeight: rowHeight(), ...extra });

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie',
  'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];

const dayKey = (iso) => String(iso || '').slice(0, 10);
const roDate = (iso) => (iso ? dayKey(iso).split('-').reverse().join('.') : '');
const roMoney = (n) => new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);
const byDateThen = (dateKey, idKey) => (a, b) => (
  (a[dateKey] ? String(a[dateKey]) : '9999').localeCompare(b[dateKey] ? String(b[dateKey]) : '9999') || a[idKey] - b[idKey]
);

/** The fourteen pairs (label, text) of one operation in full, as the detail of the page and of the card show them. */
export function detailPairs(o) {
  const x = o || {};
  return [
    ['Nr. document', x.nr_doc], ['Data banca', roDate(x.data_banca)], ['Data document', roDate(x.data_doc)],
    ['Referință', x.referinta], ['Referință destinatar', x.referinta_dest], ['Plătitor', x.platitor_nume],
    ['CUI', x.platitor_cui], ['IBAN', x.platitor_iban], ['Debit', o ? roMoney(x.suma_debit) : ''],
    ['Credit', o ? roMoney(x.suma_credit) : ''], ['Cod angajament', x.cod_contract], ['Indicator', x.rand_contract],
    ['Cod program', x.cod_program], ['Explicații', x.explicatii],
  ];
}

/**
 * The tree of statements and its nodes, from the two lists of GET /date/extrase: «Toate extrasele» > month > day.
 * Used by the page and by the Extrase card of an angajament.
 * @returns {{tree: object[], nodes: Map, antetById: Map, opsByAntet: Map}}
 */
export function buildExtraseModel(antete, operatiuni) {
  const antetById = new Map(antete.map((a) => [a.idexh, a]));
  const opsByAntet = new Map();
  operatiuni.forEach((o) => {
    if (o.idfxh == null) return;
    if (!opsByAntet.has(o.idfxh)) opsByAntet.set(o.idfxh, []);
    opsByAntet.get(o.idfxh).push(o);
  });
  const nodes = new Map(); // tree id -> {title, isDay, antete, operatii}

  const orderedAntete = (list) => [...list].sort((a, b) => (
    (a.data_extras ? String(a.data_extras) : '9999').localeCompare(b.data_extras ? String(b.data_extras) : '9999')
    || String(a.clsf).localeCompare(String(b.clsf)) || a.idexh - b.idexh));
  const orderedOps = (list) => [...list].sort(byDateThen('data_banca', 'idfxe'));

  // the days a header sits on: its operations' DataBanca; none -> its statement date
  const headersByDay = new Map();
  orderedAntete(antete).forEach((a) => {
    const days = [...new Set((opsByAntet.get(a.idexh) || []).filter((o) => o.data_banca).map((o) => dayKey(o.data_banca)))];
    if (!days.length && a.data_extras) days.push(dayKey(a.data_extras));
    days.forEach((d) => {
      if (!headersByDay.has(d)) headersByDay.set(d, []);
      headersByDay.get(d).push(a);
    });
  });
  const opsByDay = new Map();
  operatiuni.filter((o) => o.data_banca).forEach((o) => {
    const d = dayKey(o.data_banca);
    if (!opsByDay.has(d)) opsByDay.set(d, []);
    opsByDay.get(d).push(o);
  });
  opsByDay.forEach((list, d) => { if (!headersByDay.has(d)) headersByDay.set(d, []); }); // orphans still get a day

  const days = [...headersByDay.keys()].sort();
  const years = new Set(days.map((d) => d.slice(0, 4)));
  const months = new Map();
  days.forEach((d) => {
    const m = d.slice(0, 7);
    if (!months.has(m)) months.set(m, []);
    months.get(m).push(d);
  });

  nodes.set('all', { title: 'Toate extrasele', isDay: false, antete: orderedAntete(antete), operatii: orderedOps(operatiuni) });
  const children = [...months.entries()].map(([m, list]) => {
    const [y, mm] = m.split('-');
    const title = MONTHS[Number(mm) - 1] + (years.size > 1 ? ` ${y}` : '');
    const monthAntete = orderedAntete([...new Set(list.flatMap((d) => headersByDay.get(d)))]);
    const monthOps = orderedOps(list.flatMap((d) => opsByDay.get(d) || []));
    nodes.set(`L_${m}`, { title, isDay: false, antete: monthAntete, operatii: monthOps });
    return {
      id: `L_${m}`,
      label: title,
      bold: true,
      children: list.map((d) => {
        const label = roDate(d);
        nodes.set(`Z_${d}`, { title: label, isDay: true, antete: [], operatii: orderedOps(opsByDay.get(d) || []) });
        return { id: `Z_${d}`, label };
      }),
    };
  });
  return { tree: [{ id: 'all', label: 'Toate extrasele', bold: true, children }], nodes, antetById, opsByAntet };
}

/**
 * @param {{call: Function, fail: Function, say: Function, hasUnit: () => boolean}} deps
 *   call(method, path) -> {ok, status, data}; fail(r, fallback) shows the server's sentence.
 */
export function createExtrasePage({ call, fail, say, hasUnit }) {
  let tree = null;
  let gTop = null;
  let gOps = null;
  let loaded = false;
  let seq = 0;
  let bound = false;
  let antetById = new Map();
  let opsByAntet = new Map();
  const nodes = new Map(); // tree id -> {title, isDay, antete, operatii}

  const set = (id, text) => { $(id).textContent = text == null ? '' : String(text); };

  // ------------------------------------------------------------ building the tree
  function build(antete, operatiuni) {
    const m = buildExtraseModel(antete, operatiuni);
    antetById = m.antetById;
    opsByAntet = m.opsByAntet;
    nodes.clear();
    m.nodes.forEach((v, k) => nodes.set(k, v));
    return m.tree;
  }

  // ------------------------------------------------------------ the right side
  function withClsf(list) {
    return list.map((o) => ({ ...o, clsf: (antetById.get(o.idfxh) || {}).clsf || '' }));
  }

  function destroyGrids() {
    [gTop, gOps].forEach((g) => g && g.destroy());
    gTop = null;
    gOps = null;
  }

  function showDetail(o) {
    set('ed-nr', o && o.nr_doc);
    set('ed-banca', o && roDate(o.data_banca));
    set('ed-doc', o && roDate(o.data_doc));
    set('ed-ref', o && o.referinta);
    set('ed-refd', o && o.referinta_dest);
    set('ed-plat', o && o.platitor_nume);
    set('ed-cui', o && o.platitor_cui);
    set('ed-iban', o && o.platitor_iban);
    set('ed-deb', o && roMoney(o.suma_debit));
    set('ed-cred', o && roMoney(o.suma_credit));
    set('ed-cod', o && o.cod_contract);
    set('ed-ind', o && o.rand_contract);
    set('ed-prog', o && o.cod_program);
    set('ed-expl', o && o.explicatii);
  }

  function showNode(id) {
    const node = nodes.get(id);
    if (!node) return;
    destroyGrids();
    set('ext-title', node.title);
    const ops = withClsf(node.operatii);
    if (node.isDay) {
      $('grid-ext-ops').hidden = true;
      $('ext-detail').hidden = false;
      showDetail(null);
      gTop = makeGrid('grid-ext-top', OPERATIONS, {
        rows: ops, footer: true, footerCaption: '{0} operațiuni',
        emptyText: 'Nu există operațiuni în această zi.', onSelect: (row) => showDetail(row),
      });
      return;
    }
    $('grid-ext-ops').hidden = false;
    $('ext-detail').hidden = true;
    gOps = makeGrid('grid-ext-ops', OPERATIONS, {
      rows: ops, footer: true, footerCaption: '{0} operațiuni',
      emptyText: 'Nu există operațiuni.',
    });
    gTop = makeGrid('grid-ext-top', HEADERS, {
      rows: node.antete, footer: true, footerCaption: '{0} extrase',
      emptyText: 'Nu există extrase de cont.',
      onSelect: (row) => gOps.setRows(withClsf(row ? (opsByAntet.get(row.idexh) || []) : node.operatii)),
    });
  }

  // ------------------------------------------------------------ loading
  async function load() {
    if (!hasUnit()) {
      say('Alegeți unitatea ca să vedeți extrasele.');
      return false;
    }
    const mine = (seq += 1);
    set('ext-count', 'Se încarcă...');
    const r = await call('GET', '/api/portal/date/extrase');
    if (mine !== seq) return false;
    if (!r.ok) {
      set('ext-count', '');
      fail(r, 'Extrasele nu au putut fi citite.');
      return false;
    }
    const antete = r.data.antete || [];
    const operatiuni = r.data.operatiuni || [];
    tree.setData(build(antete, operatiuni));
    set('ext-count', `${antete.length} extrase, ${operatiuni.length} operațiuni`);
    loaded = true;
    tree.selectNodeById('all');
    return true;
  }

  function bind() {
    if (bound) return;
    bound = true;
    tree = new TreeView($('tree-ext'), {
      inline: true,
      selectParents: true,
      searchPlaceholder: 'Căutați (minimum 3 caractere)…',
      onSelect: (sel) => showNode(String(sel.id)),
    });
  }

  async function show() {
    bind();
    say('');
    $('card-app').classList.add('is-extrase');
    $('ext-view').hidden = false;
    if (!loaded) await load();
  }

  async function reload() {
    loaded = false;
    if ($('ext-view').hidden) return;
    destroyGrids();
    await show();
  }

  function hide() {
    seq += 1;
    $('card-app').classList.remove('is-extrase');
    $('ext-view').hidden = true;
  }

  return { show, reload, hide };
}
