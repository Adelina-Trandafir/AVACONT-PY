// The «Administrare» page of the portal (slice 0110-09). Shown ONLY to the accounts the server
// names as administrators (the menu entry appears when /api/portal/me says is_admin, and every
// route behind it answers 404 to anybody else). VIEW ONLY.
//
//   tab Baze de date   one row per database: size, tables, activity, errors, schema state
//   tab Jurnale        the server's log files, all users; filters above the grid: the FILE and the
//                      DATABASE (including «fara baza de date»); the full text of the chosen line below
//   tab Vizitatori     who looked at the presentation page: address, flag, device, time on the page,
//                      and which sections were read for how long
//   tab Coloane        (slice 0110-11) which columns of EVERY grid of the web area show, in which order, how wide

import { DataGrid } from '../dgv/datagrid.js';
import { Combobox } from '../components/combobox/combobox.js';
import { columnsOf } from './columns.js';
import { createColumnsEditor } from './colprefs.js';

const $ = (id) => document.getElementById(id);
const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);

const NO_DB = '-';
const NO_DB_LABEL = '(fără bază de date)';
// The sections of the presentation page (the ids of its [data-screen] blocks) as the operator reads them.
// An id not listed here (a section added to the page later) is shown from the id itself.
const SECTION_NAMES = {
  acasa: 'Acasă',
  'pentru-toti': 'Pentru toți',
  tutoriale: 'Tutoriale',
  'pentru-contabil': 'Pentru contabil',
  extrase: 'Extrase',
  'pentru-director': 'Pentru director',
  'descarcari-multiple': 'Descărcări multiple',
  siguranta: 'Siguranță',
  castig: 'Câștig',
  'nu-contabilitate': 'Nu e contabilitate',
  integrare: 'Integrare',
  'integrare-api': 'Integrare API',
  'ce-urmeaza': 'Ce urmează',
  'despre-noi': 'Despre noi',
  'despre-filosofie': 'Despre filosofie',
  testimoniale: 'Testimoniale',
  incepe: 'Începe',
};
const prettySection = (id) => {
  if (SECTION_NAMES[id]) return SECTION_NAMES[id];
  const text = String(id || '').replace(/-/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
};

// Two letters -> the flag emoji (two regional indicators). Windows draws these as the letters, so the
// country name always follows.
const flagOf = (code) => (/^[A-Z]{2}$/.test(code)
  ? String.fromCodePoint(...[...code].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)) : '');

let regionNames = null;
function countryLabel(code) {
  if (!code) return '—';
  if (regionNames === null) {
    try { regionNames = new Intl.DisplayNames(['ro'], { type: 'region' }); } catch (err) { regionNames = false; }
  }
  let name = code;
  if (regionNames) {
    try { name = regionNames.of(code) || code; } catch (err) { name = code; }
  }
  return `${flagOf(code)} ${name} (${code})`.trim();
}

// The rows of the grids of this page from the answers of the admin routes. Also used by the «Coloane»
// editor, so its preview shows the same rows the tabs do.
export const databaseRows = (data) => data.databases.map((d) => ({
  ...d,
  exists_label: d.exists ? 'Da' : 'LIPSĂ',
  years: d.an_min ? (d.an_min === d.an_max ? String(d.an_min) : `${d.an_min}–${d.an_max}`) : '',
}));

export const logRows = (data) => data.rows.map((e) => ({ ...e, db_label: e.db || NO_DB_LABEL }));

export const visitListRows = (data) => data.rows.map((v) => ({
  ...v,
  country_label: countryLabel(v.country),
  device: v.mobile ? 'Mobil' : 'Calculator',
  robot: v.bot ? 'Da' : '',
  top_label: prettySection(v.top_section),
  sections_text: v.sections.map((s) => `${prettySection(s.id)} ${s.seconds} s`).join(' · '),
}));

/** The sections read, summed over the given visits (rows of visitListRows). */
export function sectionRows(pool) {
  const total = new Map();
  pool.forEach((v) => v.sections.forEach((s) => {
    const slot = total.get(s.id) || { label: prettySection(s.id), visits: 0, seconds: 0 };
    slot.visits += 1;
    slot.seconds += s.seconds;
    total.set(s.id, slot);
  }));
  return [...total.values()]
    .map((s) => ({ ...s, average: s.visits ? s.seconds / s.visits : 0 }))
    .sort((a, b) => b.seconds - a.seconds);
}

/** Rows of one Administrare grid for the editor preview: {rows, why}. */
export async function adminRows(call, id) {
  const read = async (path) => {
    const r = await call('GET', path);
    return r.ok ? r.data : null;
  };
  if (id === 'admin.baze') {
    const d = await read('/api/portal/admin/databases');
    return d ? { rows: databaseRows(d), why: '' } : { rows: [], why: 'Bazele nu au putut fi citite.' };
  }
  if (id === 'admin.jurnale') {
    const d = await read('/api/portal/admin/logs?file=&db=&limit=500&gens=1');
    return d ? { rows: logRows(d), why: '' } : { rows: [], why: 'Jurnalele nu au putut fi citite.' };
  }
  const d = await read('/api/portal/admin/visits?days=30&bots=0');
  if (!d || d.table_missing) return { rows: [], why: 'Vizitele nu au putut fi citite (tabelul Vizite_Site lipsește sau serverul nu răspunde).' };
  const visits = visitListRows(d);
  return id === 'admin.vizite' ? { rows: visits, why: '' } : { rows: sectionRows(visits), why: '' };
}

/**
 * @param {{call: Function, fail: Function, say: Function}} deps
 *   call(method, path) -> {ok, status, data}; fail(r, fallback) shows the server's sentence.
 */
export function createAdminPage({ call, fail, say, realRows }) {
  let bound = false;
  let tab = 'db';
  const grids = { db: null, logs: null, visits: null, sections: null };
  const seen = { db: false, logs: false, visits: false };
  const seq = { db: 0, logs: 0, visits: 0 };
  let fileCombo = null;
  let dbCombo = null;
  let periodCombo = null;
  let visitRows = [];
  let colsEditor = null;

  const text = (id, value) => { $(id).textContent = value == null ? '' : String(value); };

  function makeCombo(id, label, items, value, onSelect) {
    const combo = new Combobox($(id), { readonly: true, placeholder: label, staticData: items, onSelect });
    combo.input.id = `${id}-input`;
    combo.input.setAttribute('aria-label', label);
    const hit = items.find((i) => i.value === value);
    if (hit) combo.setValue(hit.value, hit.label);
    return combo;
  }

  function makeGrid(host, id, extra = {}) {
    return new DataGrid($(host), { columns: columnsOf(id), layoutId: id, rows: [], rowHeight: rowHeight(), footer: true, ...extra });
  }

  // ------------------------------------------------------------ Baze de date
  async function loadDatabases() {
    const mine = (seq.db += 1);
    text('adm-db-note', 'Se încarcă...');
    const r = await call('GET', '/api/portal/admin/databases');
    if (mine !== seq.db) return;
    if (!r.ok) {
      text('adm-db-note', '');
      fail(r, 'Informațiile despre baze nu au putut fi citite.');
      return;
    }
    seen.db = true;
    const rows = databaseRows(r.data);
    if (!grids.db) grids.db = makeGrid('adm-grid-db', 'admin.baze', { footerCaption: '{0} baze', frozen: 1 });
    grids.db.setRows(rows);
    const missing = rows.filter((d) => !d.exists).length;
    text('adm-db-note', `${rows.length} baze${missing ? `, ${missing} pe care serverul nu le găsește` : ''}.`
      + ' Erorile și avertismentele se numără din fișierele de jurnal (ultimele 7 zile).'
      + (r.data.schema_ready ? '' : ' Tabelul schema_diff_log nu există: starea schemei nu e disponibilă.'));
  }

  // ------------------------------------------------------------ Jurnale
  async function loadLogMeta() {
    const r = await call('GET', '/api/portal/admin/logs/meta');
    if (!r.ok) {
      fail(r, 'Lista fișierelor de jurnal nu a putut fi citită.');
      return false;
    }
    const files = [{ value: '', label: 'Toate fișierele' }].concat(r.data.files.map((f) => ({
      value: f.key,
      label: f.exists ? `${f.label} (${(f.size / 1048576).toFixed(1)} MB${f.rotated ? `, +${f.rotated} vechi` : ''})` : `${f.label} (nu există)`,
    })));
    const dbs = [{ value: '', label: 'Toate bazele' }, { value: NO_DB, label: NO_DB_LABEL }].concat(
      r.data.databases.map((d) => ({ value: d.dc, label: `${d.dc} — ${d.name}` })));
    fileCombo = makeCombo('adm-file', 'Fișier', files, '', () => loadLogs());
    dbCombo = makeCombo('adm-db', 'Baza de date', dbs, '', () => loadLogs());
    return true;
  }

  async function loadLogs() {
    const mine = (seq.logs += 1);
    text('adm-log-note', 'Se încarcă...');
    const query = new URLSearchParams({
      file: fileCombo.getSelectedValue() || '',
      db: dbCombo.getSelectedValue() || '',
      limit: $('adm-limit').value,
      gens: $('chk-adm-old').checked ? '5' : '1',
    });
    const r = await call('GET', `/api/portal/admin/logs?${query}`);
    if (mine !== seq.logs) return;
    if (!r.ok) {
      text('adm-log-note', '');
      fail(r, 'Jurnalele nu au putut fi citite.');
      return;
    }
    seen.logs = true;
    const rows = logRows(r.data);
    if (!grids.logs) {
      grids.logs = makeGrid('adm-grid-logs', 'admin.jurnale', {
        footerCaption: '{0} intrări',
        emptyText: 'Nicio intrare pentru filtrele alese.',
        onSelect: (row) => showLogDetail(row),
      });
    }
    grids.logs.setRows(rows);
    showLogDetail(null);
    text('adm-log-note', (r.data.truncated
      ? `Se arată cele mai noi ${rows.length} din ${r.data.total} intrări. Restrângeți filtrele sau măriți limita.`
      : `${rows.length} intrări.`)
      + (r.data.unreadable.length ? ` Nu s-au putut citi: ${r.data.unreadable.join(', ')}.` : ''));
  }

  function showLogDetail(row) {
    $('adm-log-detail').textContent = row ? (row.detail || row.msg) : 'Alegeți o linie ca să-i vedeți tot textul.';
  }

  // ------------------------------------------------------------ Vizitatori
  async function loadVisits() {
    const mine = (seq.visits += 1);
    text('adm-visit-note', 'Se încarcă...');
    const query = new URLSearchParams({
      days: periodCombo.getSelectedValue() || '30',
      bots: $('chk-adm-bots').checked ? '1' : '0',
    });
    const r = await call('GET', `/api/portal/admin/visits?${query}`);
    if (mine !== seq.visits) return;
    if (!r.ok) {
      text('adm-visit-note', '');
      fail(r, 'Vizitele nu au putut fi citite.');
      return;
    }
    seen.visits = true;
    if (r.data.table_missing) {
      text('adm-visit-note', 'Tabelul Vizite_Site nu există pe server: rulați mai întâi sql/0110_09_vizite_site.sql.');
      visitRows = [];
      paintVisits();
      return;
    }
    visitRows = visitListRows(r.data);
    paintVisits();
    const mobile = visitRows.filter((v) => v.mobile).length;
    const ips = new Set(visitRows.map((v) => v.ip)).size;
    text('adm-visit-note', `${visitRows.length} vizite de la ${ips} adrese, ${mobile} de pe mobil.`
      + (r.data.truncated ? ' Lista e tăiată la cele mai noi vizite.' : '')
      + ` Vizitele se păstrează ${r.data.retention_days} de zile.`
      + (r.data.geo.ready
        ? ' Țările: DB-IP.com (CC-BY 4.0).'
        : ` Steagurile lipsesc: ${r.data.geo.reason}.`));
  }

  function paintVisits() {
    if (!grids.visits) {
      grids.visits = makeGrid('adm-grid-visits', 'admin.vizite', {
        footerCaption: '{0} vizite',
        emptyText: 'Nicio vizită în perioada aleasă.',
        onSelect: () => paintSections(),
      });
      grids.sections = makeGrid('adm-grid-sections', 'admin.sectiuni', {
        footerCaption: '{0} secțiuni',
        emptyText: 'Nicio secțiune citită.',
      });
    }
    grids.visits.setRows(visitRows);
    applyVisitGrouping();
    paintSections();
  }

  function applyVisitGrouping() {
    if ($('chk-adm-byip').checked) {
      grids.visits.setGroups([{ key: 'ip', dir: 'asc', headerCaption: 'IP {1} ({2} vizite)', showFooter: false, headerAggregates: true }]);
    } else {
      grids.visits.setGroups([]);
    }
  }

  // The sections of the chosen visit, or of every visit listed when none is chosen.
  function paintSections() {
    const chosen = grids.visits ? grids.visits.getSelectedRow() : null;
    const pool = chosen && chosen.sections ? [chosen] : visitRows;
    grids.sections.setRows(sectionRows(pool));
    text('adm-sections-title', chosen && chosen.sections
      ? `Secțiunile citite de ${chosen.ip} la ${String(chosen.first).replace('T', ' ')}`
      : 'Secțiunile citite, pe toate vizitele din listă (alegeți o vizită ca să o vedeți singură)');
  }

  // ------------------------------------------------------------ tabs
  async function showTab(name) {
    tab = name;
    document.querySelectorAll('#adm-tabs button').forEach((b) => b.classList.toggle('is-active', b.dataset.adm === name));
    ['db', 'logs', 'visits', 'cols'].forEach((key) => { $(`adm-panel-${key}`).hidden = key !== name; });
    say('');
    if (name === 'cols') {
      if (!colsEditor) colsEditor = createColumnsEditor({ say, realRows });
      colsEditor.show();
      return;
    }
    if (seen[name]) return;
    if (name === 'db') await loadDatabases();
    else if (name === 'logs') {
      if (!fileCombo && !(await loadLogMeta())) return;
      await loadLogs();
    } else {
      if (!periodCombo) {
        periodCombo = makeCombo('adm-period', 'Perioada', [
          { value: '7', label: 'Ultimele 7 zile' }, { value: '30', label: 'Ultimele 30 de zile' },
          { value: '90', label: 'Ultimele 90 de zile' }, { value: '180', label: 'Ultimele 180 de zile' },
        ], '30', () => loadVisits());
      }
      await loadVisits();
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.querySelectorAll('#adm-tabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.adm)));
    $('btn-adm-db').addEventListener('click', loadDatabases);
    $('btn-adm-log').addEventListener('click', () => { if (fileCombo) loadLogs(); });
    $('adm-limit').addEventListener('change', () => { if (fileCombo) loadLogs(); });
    $('chk-adm-old').addEventListener('change', () => { if (fileCombo) loadLogs(); });
    $('btn-adm-visit').addEventListener('click', () => { if (periodCombo) loadVisits(); });
    $('chk-adm-bots').addEventListener('change', () => { if (periodCombo) loadVisits(); });
    $('chk-adm-byip').addEventListener('change', () => { if (grids.visits) applyVisitGrouping(); });
  }

  async function show() {
    bind();
    say('');
    $('card-app').classList.add('is-adm');
    $('adm-view').hidden = false;
    await showTab(tab);
  }

  async function reload() {
    seen.db = seen.logs = seen.visits = false;
    if ($('adm-view').hidden) return;
    await showTab(tab);
  }

  function hide() {
    $('card-app').classList.remove('is-adm');
    $('adm-view').hidden = true;
  }

  return { show, reload, hide };
}
