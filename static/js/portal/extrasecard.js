// Slice 0110-12 -- the Extrase card of an angajament (computers): the web counterpart of the desktop
// ExtraseView / ExtrasePanel in «Angajament» mode, with the display menu of the tree header:
//
//   antet + operatii   root / month: the headers on top, the operations of the chosen header (or of the node)
//                      below; a day: that day's operations and, under them, the chosen operation in full
//   operatii + detalii every node: the operations of its period on top, the chosen one in full below
//
// The tree itself is made by the page (a TreeView in the card's tree host); this module owns the grids and
// the detail under them.

import { DataGrid } from '../dgv/datagrid.js';
import { columnsOf } from './columns.js';
import { buildExtraseModel, detailPairs } from './extrase.js';

const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);

export const MODE_HEADERS = 'antet';
export const MODE_DETAILS = 'detalii';

/**
 * @param {{top: string, ops: string, detail: string}} ids the ids of the three host elements
 */
export function createExtraseCard({ top, ops, detail }) {
  const $ = (id) => document.getElementById(id);
  let model = null;
  let mode = MODE_HEADERS;
  let current = '';
  let gTop = null;
  let gOps = null;

  function destroyGrids() {
    [gTop, gOps].forEach((g) => g && g.destroy());
    gTop = null;
    gOps = null;
  }

  const withClsf = (list) => list.map((o) => ({ ...o, clsf: (model.antetById.get(o.idfxh) || {}).clsf || '' }));

  function paintDetail(o) {
    const host = $(detail);
    host.textContent = '';
    detailPairs(o).forEach(([label, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = value == null ? '' : String(value);
      host.append(dt, dd);
    });
  }

  function show(id) {
    const node = model && model.nodes.get(id);
    if (!node) return;
    current = id;
    destroyGrids();
    const operations = withClsf(node.operatii);
    const opsGrid = (host, extra) => new DataGrid($(host), {
      columns: columnsOf('extrase.operatii'), layoutId: 'extrase.operatii', rowHeight: rowHeight(),
      footer: true, footerCaption: '{0} operațiuni', ...extra,
    });
    // a day, or the «operations + detail» mode: operations on top, the chosen one in full below
    if (node.isDay || mode === MODE_DETAILS) {
      $(ops).hidden = true;
      $(detail).hidden = false;
      paintDetail(null);
      gTop = opsGrid(top, { rows: operations, emptyText: 'Nu există operațiuni.', onSelect: (row) => paintDetail(row) });
      return;
    }
    $(ops).hidden = false;
    $(detail).hidden = true;
    gOps = opsGrid(ops, { rows: operations, emptyText: 'Nu există operațiuni.' });
    gTop = new DataGrid($(top), {
      columns: columnsOf('extrase.antete'), layoutId: 'extrase.antete', rowHeight: rowHeight(),
      rows: node.antete, footer: true, footerCaption: '{0} extrase', emptyText: 'Nu există extrase de cont.',
      onSelect: (row) => gOps.setRows(withClsf(row ? (model.opsByAntet.get(row.idexh) || []) : node.operatii)),
    });
  }

  return {
    /** Loads the two lists of GET /date/extrase; returns the TreeView nodes. */
    load(raw) {
      model = buildExtraseModel(raw.antete || [], raw.operatiuni || []);
      return model.tree;
    },
    show,
    get mode() { return mode; },
    /** Changes the display mode; the chosen node is shown again. */
    setMode(value) {
      if (value !== MODE_HEADERS && value !== MODE_DETAILS) throw new Error(`Unknown display mode: ${value}`);
      if (value === mode) return;
      mode = value;
      if (current) show(current);
    },
    destroy() {
      destroyGrids();
      model = null;
      current = '';
    },
  };
}
