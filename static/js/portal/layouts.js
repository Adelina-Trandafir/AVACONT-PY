// Slice 0110-11 -- where the column layouts of the grids come from.
//
//   baked   grid-layouts.defaults.js: what every visitor gets (written from an exported file)
//   admin   the administrator's own changes, kept in this browser only (localStorage), made in
//           Administrare > Coloane and applied ONLY while the signed-in account is an administrator,
//           so they can be tried on the real grids before they are given to the developer
//
// A layout is {order: [key], hidden: [key], widths: {key: px}, fill: key | null}.

import { DataGrid } from '../dgv/datagrid.js';
import { DEFAULT_LAYOUTS } from './grid-layouts.defaults.js';

const STORE_KEY = 'kbot.portal.gridlayouts.v1';
export const FORMAT = 'kbot-grid-layouts';

let admin = false;
const lastRows = new Map(); // grid id -> the rows it last showed (administrator only; never stored)
const listeners = new Set();

/** A layout in the one shape the grid reads; anything else in the input is dropped. */
export function sanitize(raw) {
  const r = raw && typeof raw === 'object' ? raw : {};
  const keys = (list) => (Array.isArray(list) ? [...new Set(list.filter((k) => typeof k === 'string' && k))] : []);
  const widths = {};
  Object.entries(r.widths && typeof r.widths === 'object' ? r.widths : {}).forEach(([k, w]) => {
    const n = Math.round(Number(w));
    if (k && n >= 20 && n <= 2000) widths[k] = n;
  });
  return { order: keys(r.order), hidden: keys(r.hidden), widths, fill: typeof r.fill === 'string' && r.fill ? r.fill : null };
}

function readStore() {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const clean = {};
    Object.keys(parsed || {}).forEach((id) => { clean[id] = sanitize(parsed[id]); });
    return clean;
  } catch (err) {
    return {}; // private window, blocked storage or a damaged value: start empty, the page still works
  }
}

let overrides = readStore();

function writeStore() {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(overrides));
    return true;
  } catch (err) {
    return false; // the page keeps the changes for this visit; the caller says they were not saved
  }
}

/** The layout a grid gets now, or null (all columns, as in columns.js). */
export function layoutFor(id) {
  if (admin && overrides[id]) return overrides[id];
  return DEFAULT_LAYOUTS[id] ? sanitize(DEFAULT_LAYOUTS[id]) : null;
}

/** The administrator's own layout of one grid, or null. */
export const overrideOf = (id) => (overrides[id] ? sanitize(overrides[id]) : null);
export const overriddenIds = () => Object.keys(overrides);
export const defaultOf = (id) => (DEFAULT_LAYOUTS[id] ? sanitize(DEFAULT_LAYOUTS[id]) : null);

/** Keeps (or, with null, forgets) the administrator's layout of one grid. Returns false when the browser would not store it. */
export function setOverride(id, layout) {
  if (layout) overrides[id] = sanitize(layout);
  else delete overrides[id];
  const saved = writeStore();
  listeners.forEach((fn) => fn(id));
  return saved;
}

export function clearOverrides() {
  overrides = {};
  const saved = writeStore();
  listeners.forEach((fn) => fn(null));
  return saved;
}

/** fn(id | null) runs after every change; returns the way to stop. */
export function onLayoutChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const rowsOf = (id) => lastRows.get(id) || [];

/** Turns the administrator's changes on or off (signed in as administrator or not). */
export function setAdmin(on) {
  admin = on === true;
  DataGrid.rowSink = admin ? (id, rows) => lastRows.set(id, rows) : null;
}

export const isAdmin = () => admin;

/** Wires the grid to this store; once, at start. */
export function installLayouts() {
  DataGrid.layoutProvider = layoutFor;
}

const orderOf = (columns, order) => {
  const rank = new Map(order.map((k, i) => [k, i]));
  const place = (c) => (rank.has(c.key) ? rank.get(c.key) : 1e6 + columns.indexOf(c));
  return [...columns].sort((a, b) => place(a) - place(b));
};

/**
 * The file the administrator gives to the developer. For every grid he changed: the columns in the
 * chosen order with their titles, which show, the width of each (px; null = as wide as the content),
 * and the fill column; plus the size of the window the widths were chosen on (a fill column makes
 * sense only against that).
 * @param {{id: string, group: string, title: string, columns: object[]}[]} catalog
 * @param {(id: string) => {key: string, width: number}[] | null} measured the widths the preview drew, by grid id (optional)
 */
export function buildExport(catalog, measured) {
  const grids = {};
  catalog.forEach((g) => {
    const o = overrides[g.id];
    if (!o) return;
    const seen = measured ? measured(g.id) : null;
    const known = new Set(g.columns.map((c) => c.key));
    const ordered = orderOf(g.columns, o.order);
    grids[g.id] = {
      title: g.title,
      group: g.group,
      order: ordered.map((c) => c.key),
      hidden: o.hidden.filter((k) => known.has(k)),
      widths: Object.fromEntries(Object.entries(o.widths).filter(([k]) => known.has(k))),
      fill: o.fill && known.has(o.fill) ? o.fill : null,
      columns: ordered.map((c) => {
        const m = seen ? seen.find((s) => s.key === c.key) : null;
        return {
          key: c.key,
          title: c.title,
          shown: !o.hidden.includes(c.key),
          width: o.widths[c.key] || null,
          widthSeen: m ? m.width : null,
          fill: o.fill === c.key,
        };
      }),
    };
  });
  return {
    format: FORMAT,
    version: 1,
    savedAt: new Date().toISOString(),
    window: { width: window.innerWidth, height: window.innerHeight, pixelRatio: window.devicePixelRatio || 1 },
    note: 'order = left to right; hidden = columns that do not show; widths in px (a column with no width is as wide as its content); fill = the one column that takes the free width.',
    changedGrids: Object.keys(grids),
    grids,
  };
}

/** Reads an exported file back. Returns {ok, count, error}. */
export function importExport(text, catalog) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    return { ok: false, count: 0, error: 'Fișierul nu este un JSON valid.' };
  }
  if (!data || data.format !== FORMAT || typeof data.grids !== 'object') {
    return { ok: false, count: 0, error: 'Fișierul nu este un export de coloane K-BOT.' };
  }
  const ids = new Set(catalog.map((g) => g.id));
  let count = 0;
  Object.entries(data.grids).forEach(([id, g]) => {
    if (!ids.has(id)) return;
    overrides[id] = sanitize(g);
    count += 1;
  });
  const saved = writeStore();
  listeners.forEach((fn) => fn(null));
  return { ok: true, count, error: saved ? '' : 'Modificările nu au putut fi păstrate în acest browser.' };
}
