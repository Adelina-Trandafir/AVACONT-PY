// Slice 0110-11 -- the «Coloane» tab of the Administrare page: the administrator decides, for EVERY grid of
// the web area, which columns show, in which order, how wide, and which one column takes the free width
// when the grid is wider than its columns.
//
//   - one grid at a time (the list above the editor), its columns in a table: shown / title / move /
//     width (px) / «se extinde»; a preview grid under it shows the result, and dragging a column border
//     in the preview sets that width
//   - every change is kept at once in THIS browser (layouts.js) and applies to the real grids while the
//     account is an administrator
//   - «Descarcă fișierul» / «Copiază textul» give the file the developer turns into the permanent layout
//     (grid-layouts.defaults.js): the chosen order, visibility and widths of every changed grid, and the
//     size of the window they were chosen on

import { DataGrid } from '../dgv/datagrid.js';
import { confirmBox } from '../utils/confirm-box.js';
import { Combobox } from '../components/combobox/combobox.js';
import { GRID_CATALOG, columnsOf } from './columns.js';
import {
  buildExport, clearOverrides, defaultOf, importExport, overriddenIds, overrideOf, rowsOf, setOverride,
} from './layouts.js';

const $ = (id) => document.getElementById(id);
const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);

const emptyLayout = () => ({ order: [], hidden: [], widths: {}, fill: null });

/**
 * @param {{say: Function, realRows: (id: string) => Promise<{rows: object[], why: string}>}} deps
 *   say(text) shows a sentence on the page; realRows(id) reads the rows of that grid from the open unit
 */
export function createColumnsEditor({ say, realRows }) {
  let combo = null;
  let gridId = '';
  let work = emptyLayout(); // the layout of the open grid, as edited
  let columns = []; // the open grid's columns, in the order of `work`
  let preview = null;
  let bound = false;
  let previewSeq = 0;
  const read = new Map(); // grid id -> the rows read from the open unit (kept while the page is open)
  const seen = new Map(); // grid id -> the widths the preview drew (kept for the export)

  const note = (text) => { $('adm-cols-state').textContent = text || ''; };

  const label = (g) => `${g.group} › ${g.title}${overriddenIds().includes(g.id) ? '  •' : ''}`;

  function paintCombo() {
    const items = GRID_CATALOG.map((g) => ({ value: g.id, label: label(g) }));
    combo.options.staticData = items;
    const hit = items.find((i) => i.value === gridId);
    if (hit) combo.setValue(hit.value, hit.label);
  }

  /** The open grid's columns in the order of `work`; the ones the layout does not name follow in their own order. */
  function ordered(base, order) {
    const rank = new Map(order.map((k, i) => [k, i]));
    const place = (c) => (rank.has(c.key) ? rank.get(c.key) : 1e6 + base.indexOf(c));
    return [...base].sort((a, b) => place(a) - place(b));
  }

  function open(id) {
    gridId = id;
    const start = overrideOf(id) || defaultOf(id) || emptyLayout();
    const base = columnsOf(id);
    columns = ordered(base, start.order);
    work = { ...start, order: columns.map((c) => c.key) };
    paintList();
    paintPreview();
    paintState();
  }

  function commit() {
    work.order = columns.map((c) => c.key);
    const saved = setOverride(gridId, work);
    paintCombo();
    paintState(saved);
  }

  function paintState(saved = true) {
    const modified = overriddenIds().length;
    note(`${saved ? 'Modificările se păstrează în acest browser.' : 'Browserul nu a lăsat păstrarea modificărilor: ele rămân doar cât timp pagina e deschisă.'}`
      + ` Grile modificate: ${modified}.`
      + ' Cardurile deja deschise preiau schimbarea când le deschideți din nou.');
  }

  // ------------------------------------------------------------ the table of columns
  function paintList() {
    const host = $('adm-cols-list');
    host.textContent = '';
    const head = document.createElement('div');
    head.className = 'adm__crow adm__crow--head';
    ['Se vede', 'Coloana', 'Poziția', 'Lățime (px)', 'Se extinde'].forEach((t) => {
      const cell = document.createElement('span');
      cell.textContent = t;
      head.appendChild(cell);
    });
    host.appendChild(head);

    columns.forEach((col, i) => {
      const row = document.createElement('div');
      row.className = 'adm__crow';
      const shown = !work.hidden.includes(col.key);
      if (!shown) row.classList.add('is-off');

      const vis = document.createElement('input');
      vis.type = 'checkbox';
      vis.checked = shown;
      vis.setAttribute('aria-label', `Arată coloana ${col.title || col.key}`);
      vis.addEventListener('change', () => {
        work.hidden = vis.checked ? work.hidden.filter((k) => k !== col.key) : [...work.hidden, col.key];
        if (!vis.checked && work.fill === col.key) work.fill = null;
        commit();
        paintList();
        paintPreview();
      });

      const title = document.createElement('span');
      title.className = 'adm__ctitle';
      title.textContent = col.title || `(${col.key})`;
      title.title = col.key;

      const moves = document.createElement('span');
      moves.className = 'adm__cmoves';
      const move = (delta, text, aria) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn--ghost btn--sm';
        b.textContent = text;
        b.setAttribute('aria-label', aria);
        b.disabled = i + delta < 0 || i + delta >= columns.length;
        b.addEventListener('click', () => {
          [columns[i], columns[i + delta]] = [columns[i + delta], columns[i]];
          commit();
          paintList();
          paintPreview();
        });
        return b;
      };
      moves.append(move(-1, '▲', `Mută ${col.title || col.key} mai la stânga`), move(1, '▼', `Mută ${col.title || col.key} mai la dreapta`));

      const width = document.createElement('input');
      width.type = 'number';
      width.min = '20';
      width.max = '2000';
      width.step = '5';
      width.placeholder = 'automat';
      width.value = work.widths[col.key] ? String(work.widths[col.key]) : '';
      width.dataset.key = col.key;
      width.setAttribute('aria-label', `Lățimea coloanei ${col.title || col.key}, în pixeli`);
      width.addEventListener('change', () => {
        const n = Math.round(Number(width.value));
        if (width.value === '' || !(n >= 20)) delete work.widths[col.key];
        else work.widths[col.key] = Math.min(n, 2000);
        commit();
        paintList();
        paintPreview();
      });

      const fill = document.createElement('input');
      fill.type = 'radio';
      fill.name = 'adm-cols-fill';
      fill.checked = work.fill === col.key;
      fill.disabled = !shown;
      fill.setAttribute('aria-label', `${col.title || col.key} se extinde când e loc`);
      // a radio cannot be cleared by clicking it again: the second click on the chosen one does that here
      fill.addEventListener('click', () => {
        work.fill = work.fill === col.key ? null : col.key;
        commit();
        paintList();
        paintPreview();
      });

      row.append(vis, title, moves, width, fill);
      host.appendChild(row);
    });
  }

  // ------------------------------------------------------------ the preview
  /** The rows of the grid from the OPEN unit (read once per visit of the tab); {rows, why}. */
  async function rowsFor(id) {
    if (read.has(id)) return read.get(id);
    let got = { rows: [], why: '' };
    try {
      got = await realRows(id);
    } catch (err) {
      got = { rows: [], why: 'Datele unității nu au putut fi citite.' };
    }
    // a grid the reader could not fill: what the grid itself showed this session, if anything
    if (!got.rows.length && rowsOf(id).length) got = { rows: rowsOf(id), why: '' };
    if (got.rows.length) read.set(id, got);
    return got;
  }

  function drawPreview(rows) {
    if (preview) preview.destroy();
    preview = new DataGrid($('adm-cols-preview'), {
      columns: columnsOf(gridId),
      layout: { ...work, order: columns.map((c) => c.key) },
      rows,
      rowHeight: rowHeight(),
      emptyText: 'Nu sunt date pentru această grilă în unitatea deschisă',
      onColumnResize: ({ key, width }) => {
        work.widths[key] = Math.max(20, Math.round(width));
        commit();
        const input = $('adm-cols-list').querySelector('input[type="number"][data-key="' + CSS.escape(key) + '"]');
        if (input) input.value = String(work.widths[key]);
      },
    });
    seen.set(gridId, preview.getColumnState());
  }

  async function paintPreview() {
    const mine = (previewSeq += 1);
    const id = gridId;
    if (!read.has(id)) {
      drawPreview([]);
      $('adm-cols-sample').textContent = 'Se citesc datele unității deschise...';
    }
    const got = await rowsFor(id);
    if (mine !== previewSeq || id !== gridId) return; // another grid was chosen meanwhile
    drawPreview(got.rows);
    $('adm-cols-sample').textContent = got.rows.length
      ? 'Previzualizare cu ' + got.rows.length + ' rânduri reale din unitatea deschisă (lățimile «automat» se iau din ele). Trageți de marginea unui titlu ca să-i setați lățimea.'
      : (got.why || 'Nu sunt date pentru această grilă în unitatea deschisă.') + ' Previzualizarea arată doar antetul.';
  }

  // ------------------------------------------------------------ the file
  const stamp = () => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  };

  function exportText() {
    if (!overriddenIds().length) return '';
    return JSON.stringify(buildExport(GRID_CATALOG, (id) => seen.get(id) || null), null, 2);
  }

  function download() {
    const text = exportText();
    if (!text) {
      note('Nu ați schimbat nicio grilă: nu e nimic de descărcat.');
      return;
    }
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `kbot-coloane-${stamp()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    note(`Fișierul kbot-coloane-${stamp()}.json (${overriddenIds().length} grile) a fost descărcat în dosarul de descărcări.`);
  }

  async function copy() {
    const text = exportText();
    if (!text) {
      note('Nu ați schimbat nicio grilă: nu e nimic de copiat.');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      note(`Textul (${overriddenIds().length} grile) este copiat: lipiți-l în conversație.`);
    } catch (err) {
      note('Browserul nu a permis copierea. Folosiți «Descarcă fișierul».');
    }
  }

  async function importFile(file) {
    if (!file) return;
    const result = importExport(await file.text(), GRID_CATALOG);
    if (!result.ok) {
      note(result.error);
      return;
    }
    paintCombo();
    open(gridId);
    note(`S-au încărcat ${result.count} grile din fișier.${result.error ? ` ${result.error}` : ''}`);
  }

  function resetOne() {
    columns = ordered(columnsOf(gridId), []);
    work = emptyLayout();
    setOverride(gridId, null);
    paintCombo();
    paintList();
    paintPreview();
    note('Grila a revenit la valorile din cod (toate coloanele, în ordinea lor, cu lățimea după conținut).');
  }

  async function resetAll() {
    if (!overriddenIds().length) return;
    if (!await confirmBox('Ștergeți modificările de coloane ale TUTUROR grilelor, din acest browser?', { title: 'Coloane grile', yes: 'Șterge', no: 'Renunță' })) return;
    clearOverrides();
    paintCombo();
    open(gridId);
    note('Toate modificările au fost șterse din acest browser.');
  }

  // ------------------------------------------------------------ page
  function bind() {
    if (bound) return;
    bound = true;
    combo = new Combobox($('adm-cols-grid'), {
      readonly: true,
      placeholder: 'Alegeți grila',
      staticData: [],
      onSelect: (value) => { if (value && value !== gridId) open(value); },
    });
    combo.input.id = 'adm-cols-grid-input';
    combo.input.setAttribute('aria-label', 'Grila');
    $('btn-cols-export').addEventListener('click', download);
    $('btn-cols-copy').addEventListener('click', copy);
    $('btn-cols-reset').addEventListener('click', resetOne);
    $('btn-cols-clearall').addEventListener('click', resetAll);
    $('file-cols-import').addEventListener('change', (e) => {
      importFile(e.target.files[0]);
      e.target.value = '';
    });
  }

  /** Called when the tab is shown. */
  function show() {
    bind();
    read.clear(); // the unit may have changed since the last visit
    say('');
    if (!gridId) gridId = GRID_CATALOG[0].id;
    paintCombo();
    open(gridId);
  }

  return { show };
}
