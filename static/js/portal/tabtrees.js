// Slice 0110-10 -- the trees of the cards of one angajament (computers only), the web counterpart of
// the trees of the desktop views (IstoricView, RezervariView, ReceptiiView, PlatiView, DdfView, OrdView):
// «Toate ...» > month > the day / the document, each node with its total or its count, and the
// rows of the node ready for the grid. No DOM here: data in, nodes out.
//
//   buildTabTree(tab, data) -> {nodes, info}
//     nodes  the TreeView nodes ({id, label, badge, bold, error, tooltip, children})
//     info   Map(node id -> {title, rows, doc})   rows = what the grid shows when the node is chosen;
//            doc = the signed document the node stands for (Fundamentari / Ordonantari), or null

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie',
  'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];

export const ROOT = 'all';
const NOTES_ROOT = 'note';
const UNPLACED = 'unplaced';

const ymd = (iso) => (iso ? String(iso).slice(0, 10) : '');
const roDay = (iso) => ymd(iso).split('-').reverse().join('.');
const roTime = (iso) => (String(iso || '').length >= 16 ? String(iso).slice(11, 16) : '');
const sum = (rows, key) => rows.reduce((s, r) => s + (Number(r[key]) || 0), 0);
const roMoney = (n) => new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);

/** One month label; the year is added only when the rows span several years. */
function monthTitle(key, manyYears) {
  const [y, m] = key.split('-');
  return MONTHS[Number(m) - 1] + (manyYears ? ` ${y}` : '');
}

/** [{key: 'yyyy-mm', rows}] in date order; rows with no date are left out (the root still has them). */
function byMonth(rows, dateOf) {
  const groups = new Map();
  rows.forEach((r) => {
    const d = ymd(dateOf(r));
    if (!d) return;
    const key = d.slice(0, 7);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  });
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([key, list]) => ({ key, rows: list }));
}

const manyYears = (months) => new Set(months.map((m) => m.key.slice(0, 4))).size > 1;

/** Month > day, the shape of Istoric, Plati and the statements of the angajament. */
function monthDayTree({ rows, rootLabel, dateOf, badgeOf, bold = true }) {
  const info = new Map();
  const months = byMonth(rows, dateOf);
  const years = manyYears(months);
  info.set(ROOT, { title: rootLabel, rows, doc: null });
  const children = months.map((m) => {
    const monthTitleText = monthTitle(m.key, years);
    info.set(`L_${m.key}`, { title: monthTitleText, rows: m.rows, doc: null });
    const days = new Map();
    m.rows.forEach((r) => {
      const d = ymd(dateOf(r));
      if (!days.has(d)) days.set(d, []);
      days.get(d).push(r);
    });
    return {
      id: `L_${m.key}`,
      label: monthTitleText,
      badge: badgeOf(m.rows),
      bold,
      children: [...days.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([d, list]) => {
        info.set(`Z_${d}`, { title: roDay(d), rows: list, doc: null });
        return { id: `Z_${d}`, label: roDay(d), badge: badgeOf(list) };
      }),
    };
  });
  return { nodes: [{ id: ROOT, label: rootLabel, badge: badgeOf(rows), bold, children }], info };
}

// ---------------------------------------------------------------- Istoric: month > day, with the number of rows
function istoricTree(rows) {
  return monthDayTree({ rows, rootLabel: 'Tot istoricul', dateOf: (r) => r.data_fx, badgeOf: (list) => String(list.length) });
}

// ---------------------------------------------------------------- Plati: month > day, with the sum paid
function platiTree(rows) {
  return monthDayTree({
    rows,
    rootLabel: 'Toate plățile',
    // the day the money reached the bank statement, else the day of the payment (as the desktop tree does)
    dateOf: (r) => r.data_banca || r.data_plata,
    badgeOf: (list) => roMoney(sum(list, 'suma')),
  });
}

// ---------------------------------------------------------------- Extrase of the angajament: month > day, with the number of operations
function extraseTree(rows) {
  return monthDayTree({ rows, rootLabel: 'Toate extrasele', dateOf: (r) => r.data_banca, badgeOf: (list) => String(list.length) });
}

// ---------------------------------------------------------------- Rezervari: month > (day, kind)
const TIP_NAME = { initiala: 'Inițială', marire: 'Mărire', micsorare: 'Micșorare' };
const tipOf = (r) => (r.e_initiala ? 'initiala' : r.e_marire ? 'marire' : r.e_micsorare ? 'micsorare' : 'necunoscut');
const TIP_RANK = { initiala: 0, marire: 1, micsorare: 2, necunoscut: 3 };
// the value of one line in a leaf: the initial amount for the initial operation, the line value otherwise
const operationValue = (r) => (r.e_initiala ? Number(r.r_initiala) || 0 : Number(r.r_valoare) || 0);

function rezervariTree(rows) {
  // The initial reservation is ONE event: every initial line sits on the LAST day of the initial lines
  // (the day it became final), so the tree has a single «Inițială» leaf. The row keeps its own date.
  const initialDays = rows.filter((r) => r.e_initiala && r.data_rezervare).map((r) => ymd(r.data_rezervare));
  const initialDay = initialDays.length ? initialDays.reduce((a, b) => (a > b ? a : b)) : '';
  const treeDay = (r) => (r.e_initiala && initialDay ? initialDay : ymd(r.data_rezervare));

  const info = new Map();
  info.set(ROOT, { title: 'Toate rezervările', rows, doc: null });
  const months = byMonth(rows, treeDay);
  const years = manyYears(months);
  const children = months.map((m) => {
    const title = monthTitle(m.key, years);
    info.set(`LA_${m.key}`, { title, rows: m.rows, doc: null });
    const leaves = new Map();
    m.rows.forEach((r) => {
      const k = `${treeDay(r)}|${tipOf(r)}`;
      if (!leaves.has(k)) leaves.set(k, []);
      leaves.get(k).push(r);
    });
    const ordered = [...leaves.entries()].sort((a, b) => {
      const [da, ta] = a[0].split('|');
      const [db, tb] = b[0].split('|');
      return da.localeCompare(db) || TIP_RANK[ta] - TIP_RANK[tb];
    });
    return {
      id: `LA_${m.key}`,
      label: title,
      badge: roMoney(sum(m.rows, 'r_valoare')),
      bold: true,
      children: ordered.map(([k, list]) => {
        const [d, tip] = k.split('|');
        const value = list.reduce((s, r) => s + operationValue(r), 0);
        const id = `RZ_${d.replace(/-/g, '')}_${tip}`;
        const name = TIP_NAME[tip] || '';
        info.set(id, { title: `${roDay(d)}${name ? ` · ${name}` : ''}`, rows: list, doc: null });
        return { id, label: `${roDay(d)}${name ? ` · ${name}` : ''}`, badge: roMoney(value), error: value < 0 };
      }),
    };
  });
  return { nodes: [{ id: ROOT, label: 'Toate rezervările', badge: roMoney(sum(rows, 'r_valoare')), bold: true, children }], info };
}

// ---------------------------------------------------------------- Receptii: month > receptie
/** The headers of one receptie, newest first: the last one gives the value of the receptie. */
function lastHeader(list) {
  const heads = new Map();
  list.filter((r) => !r.sters_h).forEach((r) => { if (!heads.has(r.idrh)) heads.set(r.idrh, r); });
  return [...heads.values()].sort((a, b) => String(b.data_h || '').localeCompare(String(a.data_h || '')) || b.idrh - a.idrh)[0] || null;
}

/** The sum of the values of the DISTINCT receptii among `rows` (each counted by its last header). */
function receptiiTotal(rows) {
  const byRec = new Map();
  rows.forEach((r) => {
    if (!byRec.has(r.idrr)) byRec.set(r.idrr, []);
    byRec.get(r.idrr).push(r);
  });
  let total = 0;
  byRec.forEach((list) => {
    const last = lastHeader(list);
    if (last) total += Number(last.total) || 0;
  });
  return total;
}

function receptiiTree(rows) {
  const placed = rows.filter((r) => r.idrr > 0);
  const loose = rows.filter((r) => !(r.idrr > 0));
  const info = new Map();
  info.set(ROOT, { title: 'Toate recepțiile', rows: placed, doc: null });
  const months = byMonth(placed, (r) => r.data_r);
  const years = manyYears(months);
  const children = months.map((m) => {
    const title = monthTitle(m.key, years);
    info.set(`m_${m.key}`, { title, rows: m.rows, doc: null });
    const recs = new Map();
    m.rows.forEach((r) => {
      if (!recs.has(r.idrr)) recs.set(r.idrr, []);
      recs.get(r.idrr).push(r);
    });
    return {
      id: `m_${m.key}`,
      label: title,
      badge: roMoney(receptiiTotal(m.rows)),
      bold: true,
      children: [...recs.entries()].map(([idrr, list]) => {
        const last = lastHeader(list);
        const first = list[0];
        const time = last ? roTime(last.data_h) : '';
        const label = `${roDay(first.data_r)}${time ? ` ${time}` : ''}${first.reconstituit ? ' (reconstituită)' : ''}${last && last.este_stergere ? ' (ștearsă)' : ''}`;
        info.set(`r_${idrr}`, { title: label, rows: list, doc: null });
        return {
          id: `r_${idrr}`,
          label,
          badge: roMoney(last ? last.total : 0),
          tooltip: [first.descriere_r, first.nrcrt_r != null ? `Nr. ${first.nrcrt_r}` : ''].filter(Boolean).join('\n'),
        };
      }),
    };
  });
  const nodes = [{ id: ROOT, label: 'Toate recepțiile', badge: roMoney(receptiiTotal(placed)), bold: true, children }];
  if (loose.length) {
    // headers saved before a receptie existed: no month to sit under, so a folder of their own, one leaf per header
    const heads = new Map();
    loose.forEach((r) => {
      if (!heads.has(r.idrh)) heads.set(r.idrh, []);
      heads.get(r.idrh).push(r);
    });
    info.set(UNPLACED, { title: 'Instantanee neașezate', rows: loose, doc: null });
    nodes.push({
      id: UNPLACED,
      label: 'Instantanee neașezate',
      badge: roMoney(sum([...heads.values()].map((l) => l[0]), 'total')),
      bold: true,
      children: [...heads.entries()].map(([idrh, list]) => {
        const label = `${roDay(list[0].data_h)} ${roTime(list[0].data_h)}`.trim();
        info.set(`h_${idrh}`, { title: label, rows: list, doc: null });
        return { id: `h_${idrh}`, label, badge: roMoney(list[0].total) };
      }),
    });
  }
  return { nodes, info };
}

// ---------------------------------------------------------------- the signed documents
/** The row of a revision in the list of documents (the list on a phone, and the node's `doc`). */
export function revisionDoc(r) {
  return {
    tip: 'DDF', nr: `Rev. ${r.numar_rev}`, data: r.data_rev, suma: r.total_revizie, semn: r.semnatura || '',
    pdf: !!r.pdf_sha256, path: `ddf-pdf/${r.idrev}`, id: `RC_${r.idrev}`,
  };
}

export function orderDoc(r) {
  return {
    tip: 'ORD', nr: String(r.nr_ord), data: r.data_ord, suma: r.total_ord, semn: r.semnatura || '',
    pdf: !!r.pdf_sha256, path: `ord-pdf/${r.idordp}`, id: `ORD_${r.idordp}`,
  };
}

export function noteDoc(r) {
  return {
    tip: 'Notă CAB', nr: String(r.nr_nota), data: r.data_nota, suma: null, semn: r.semnatura || '',
    pdf: !!r.pdf_sha256, path: `nc-pdf/${r.idnc}`, id: `NC_${r.idnc}`,
  };
}

const byDateThenNr = (dateKey, nrKey) => (a, b) => String(a[dateKey] || '9999').localeCompare(String(b[dateKey] || '9999')) || (a[nrKey] - b[nrKey]);

/** @param {{revizii: object[], linii: object[]}} ddf the answer of GET /date/ddf */
function fundamentariTree(ddf) {
  const revizii = [...(ddf.revizii || [])].sort(byDateThenNr('data_rev', 'numar_rev'));
  const linesOf = (r) => (ddf.linii || []).filter((l) => l.idrev === r.idrev).map((l) => ({ ...l, rev: `Rev. ${r.numar_rev}` }));
  const allLines = revizii.flatMap(linesOf);
  const info = new Map();
  info.set(ROOT, { title: 'Toate reviziile', rows: allLines, doc: null });
  const months = byMonth(revizii, (r) => r.data_rev);
  const years = manyYears(months);
  const children = months.map((m) => {
    const title = monthTitle(m.key, years);
    info.set(`LA_${m.key}`, { title, rows: m.rows.flatMap(linesOf), doc: null });
    return {
      id: `LA_${m.key}`,
      label: title,
      badge: roMoney(sum(m.rows, 'total_revizie')),
      bold: true,
      children: m.rows.map((r) => {
        const doc = revisionDoc(r);
        const label = `Rev. ${r.numar_rev} · ${roDay(r.data_rev)}`;
        info.set(doc.id, { title: label, rows: linesOf(r), doc });
        return {
          id: doc.id,
          label,
          badge: roMoney(r.total_revizie),
          error: (Number(r.total_revizie) || 0) < 0,
          tooltip: [r.desc_scurta, r.semnatura ? `Semnături: ${r.semnatura}` : ''].filter(Boolean).join('\n'),
        };
      }),
    };
  });
  return {
    nodes: [{ id: ROOT, label: 'Toate reviziile', badge: roMoney(sum(revizii, 'total_revizie')), bold: true, children }],
    info,
    docs: revizii.map(revisionDoc),
    allLines,
    linesOf: (docId) => (info.get(docId) || { rows: [] }).rows,
  };
}

/** @param {{ord: {ordonantari: object[], linii: object[]}, note: {note: object[]}}} data the answers of GET /date/ord and /date/note */
function ordonantariTree(data) {
  const ords = [...((data.ord || {}).ordonantari || [])].sort(byDateThenNr('data_ord', 'nr_ord'));
  const notes = [...((data.note || {}).note || [])].sort(byDateThenNr('data_nota', 'nr_nota'));
  const linesOf = (o) => ((data.ord || {}).linii || []).filter((l) => l.idordp === o.idordp)
    .map((l) => ({ ...l, ord: `${o.nr_ord} - ${roDay(o.data_ord)}` }));
  const allLines = ords.flatMap(linesOf);
  const info = new Map();
  info.set(ROOT, { title: 'Toate ordonanțările', rows: allLines, doc: null });
  const months = byMonth(ords, (o) => o.data_ord);
  const years = manyYears(months);
  const children = months.map((m) => {
    const title = monthTitle(m.key, years);
    info.set(`LA_${m.key}`, { title, rows: m.rows.flatMap(linesOf), doc: null });
    return {
      id: `LA_${m.key}`,
      label: title,
      badge: roMoney(sum(m.rows, 'total_ord')),
      bold: true,
      children: m.rows.map((o) => {
        const doc = orderDoc(o);
        const label = `${o.nr_ord} - ${roDay(o.data_ord)}`;
        info.set(doc.id, { title: label, rows: linesOf(o), doc });
        return {
          id: doc.id,
          label,
          badge: roMoney(o.total_ord),
          error: (Number(o.total_ord) || 0) < 0,
          tooltip: [o.nume_partener, o.semnatura ? `Semnături: ${o.semnatura}` : ''].filter(Boolean).join('\n'),
        };
      }),
    };
  });
  const nodes = [{ id: ROOT, label: 'Toate ordonanțările', badge: roMoney(sum(ords, 'total_ord')), bold: true, children }];
  if (notes.length) {
    // the correction notes (F1135) have a signed PDF but no lines: a folder of their own, one leaf per note
    info.set(NOTES_ROOT, { title: 'Note de corecție CAB', rows: [], doc: null });
    nodes.push({
      id: NOTES_ROOT,
      label: 'Note de corecție CAB',
      badge: String(notes.length),
      bold: true,
      children: notes.map((n) => {
        const doc = noteDoc(n);
        const label = `Nota ${n.nr_nota} · ${roDay(n.data_nota)}`;
        info.set(doc.id, { title: label, rows: [], doc });
        return { id: doc.id, label, tooltip: n.semnatura ? `Semnături: ${n.semnatura}` : '' };
      }),
    });
  }
  return { nodes, info, docs: [...ords.map(orderDoc), ...notes.map(noteDoc)], allLines };
}

const BUILDERS = {
  istoric: istoricTree,
  rezervari: rezervariTree,
  receptii: receptiiTree,
  extrase: extraseTree,
  plati: platiTree,
  fundamentari: fundamentariTree,
  ordonantari: ordonantariTree,
};

/** The tabs that have a tree (Sumar has none, as in the desktop app). */
export const hasTree = (tab) => Object.prototype.hasOwnProperty.call(BUILDERS, tab);

export function buildTabTree(tab, data) {
  if (!hasTree(tab)) throw new Error(`The tab has no tree: ${tab}`);
  return BUILDERS[tab](data);
}
