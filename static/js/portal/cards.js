// Portal card definitions and their common grid factory.
import { DataGrid } from '../dgv/datagrid.js';
import { columnsOf } from './columns.js';

const $ = (id) => document.getElementById(id);

/** Row height of the grids (group bands included): tight, tighter still on a phone. */
function gridRowHeight() {
  return window.matchMedia('(max-width: 900px)').matches ? 20 : 23;
}

// The cards whose data is one list of rows. `grid` is the id of the grid in columns.js (its columns and
// its saved layout). No card groups its rows by date any more: on a computer the tree does that job.
export const TABS = {
  sumar: {
    path: 'sumar',
    rows: (d) => d.rows || [],
    grid: 'ang.sumar',
    options: { frozen: 1, footer: true, footerCaption: '{0} indicatori' },
  },
  istoric: {
    path: 'istoric',
    rows: (d) => d.randuri || [],
    grid: 'ang.istoric',
    options: { footer: true, footerCaption: '{0} rânduri' },
  },
  rezervari: {
    path: 'rezervari',
    rows: (d) => d.rows || [],
    grid: 'ang.rezervari',
    options: { footer: true, footerCaption: '{0} rezervări' },
  },
  receptii: {
    path: 'receptii',
    rows: (d) => d.receptii || [],
    grid: 'ang.receptii',
    options: { footer: true, footerCaption: '{0} rânduri' },
  },
  extrase: {
    path: 'extrase',
    // the operations of the angajament; the statement's date and classification come from its header
    rows: (d) => {
      const heads = new Map((d.antete || []).map((a) => [a.idexh, a]));
      return (d.operatiuni || []).map((o) => {
        const a = heads.get(o.idfxh) || {};
        return { ...o, clsf: a.clsf || '', data_extras: a.data_extras || null };
      });
    },
    grid: 'ang.extrase',
    options: { footer: true, footerCaption: '{0} operațiuni' },
  },
  plati: {
    path: 'plati',
    rows: (d) => d.plati || [],
    grid: 'ang.plati',
    options: { footer: true, footerCaption: '{0} plăți' },
  },
};

// The cards of signed documents (slice 0110-10): the lines of the documents in a grid, the chosen one's
// PDF under it. grid / listGrid are ids in columns.js: the lines, and (phone only) the list of documents.
export const DOC_TABS = {
  fundamentari: {
    grid: 'ang.fundamentari',
    listGrid: 'ang.fundamentari-lista',
    emptyLines: 'Nu există linii de fundamentare.',
    emptyList: 'Angajamentul nu are fundamentări.',
    none: 'Angajamentul nu are documente de fundamentare.',
    pick: 'Alegeți o revizie din arbore ca să-i vedeți documentul.',
    pickList: 'Alegeți o revizie din listă.',
  },
  ordonantari: {
    grid: 'ang.ordonantari',
    listGrid: 'ang.ordonantari-lista',
    emptyLines: 'Nu există linii de ordonanțare.',
    emptyList: 'Nu există documente pentru acest angajament.',
    none: 'Nu există documente pentru acest angajament.',
    pick: 'Alegeți un document din arbore ca să îl vedeți.',
    pickList: 'Alegeți un document din listă.',
  },
};

/** A grid in a host element: its columns and saved layout come from the catalog by id. */
export function makeGrid(host, id, extra = {}) {
  return new DataGrid($(host), {
    columns: columnsOf(id), layoutId: id, rowHeight: gridRowHeight(), ...extra,
  });
}

