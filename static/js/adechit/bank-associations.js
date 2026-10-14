import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { showMessage } from '../portal/messages.js';
import { confirmBox } from '../utils/confirm-box.js';
import { bindBankPreview } from './bank-preview.js';
import { bindBankPeriods } from './bank-periods.js';

const $ = (id) => document.getElementById(id);
const text = (key, title, width = 150) => ({ key, title, width, minWidth: 50, valueType: ValueType.Text });
const money = (value) => Number(value || 0).toLocaleString('ro-RO', { maximumFractionDigits: 2 });

export function bindBankAssociations({ api, context, refresh, monthId }) {
  const dialog = $('ade-bank-dialog'); const month = $('ade-bank-month');
  const explanation = $('ade-bank-explanation'); const search = $('ade-bank-search');
  let grids = []; let data = { extracts: [], groups: [], children: [], payers: [], months: [] };
  let extract = null; let group = null; let child = null; let payer = null;
  let busy = false; let automatic = false; let touched = false; let next = null; let chosenMonth = '';
  let savedRequest = null; let listSequence = 0; let searchTimer = null; let queuedSearch = false; let lastSearch = '';
  const canSave = () => ['collect', 'catalog'].every((right) => context()?.permissions.includes(right));
  const selectedMonth = () => data.months.find((row) => String(row.IDL) === month.value);
  const message = (value, kind = 'info') => showMessage($('ade-bank-message'), value, kind);
  const report = (error) => { console.error('[ADE bank]', error); message(error.message, 'error'); };
  const filters = () => new URLSearchParams({ period: periods.value(),
    search: search.value.trim().length >= 2 ? search.value.trim() : '',
    include_hidden: $('ade-bank-show-hidden').checked ? '1' : '0' });
  async function run(action) {
    if (busy) return false;
    busy = true; dialog.inert = true;
    try { return await action(); }
    catch (error) { report(error); return false; }
    finally {
      busy = false; dialog.inert = false; update();
      if (queuedSearch) { queuedSearch = false; requestSearch(); }
    }
  }
  const auto = bindBankPreview({ api, context, month: selectedMonth, filters, refresh, run, message });
  const periods = bindBankPeriods({ api, onChange: () => run(async () => {
    clearTimeout(searchTimer);
    return automatic ? auto.load() : loadExtracts();
  }) });

  function update() {
    $('ade-bank-save').disabled = busy || !canSave() || !extract || !child || !payer?.Activ || !selectedMonth();
    $('ade-bank-more').hidden = next == null;
  }
  async function suggestExplanation() {
    if (touched || !selectedMonth()) return;
    const reference = selectedMonth().IDL;
    const result = await api(`/api/adechit/bank-extracts/defaults?IDL=${reference}`);
    if (!touched && selectedMonth()?.IDL === reference) explanation.value = result.explanation;
  }
  function createGrid(host, columns, key, onSelect, fill) {
    const instance = new DataGrid($(host), { rows: [], columns, rowKey: key, onSelect, autoSize: false,
      headerHeight: 32, mobileRowScale: 1.2, footer: true, footerCaption: '{0} înregistrări',
      theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'modern', layout: { fill } });
    grids.push(instance); return instance;
  }
  function buildGrids() {
    grids.forEach((instance) => instance.destroy()); grids = [];
    createGrid('ade-bank-extract-list', [
      { ...text('DataDoc', 'Data document', 95), valueType: ValueType.DateTime },
      text('NrDoc', 'Nr. document', 95), text('platitor_nume', 'Plătitor bancă', 240),
      text('platitor_cui', 'Cod fiscal', 90), text('platitor_iban', 'Cont plătitor', 210),
      { ...text('suma_credit', 'Încasare', 80), valueType: ValueType.Number, align: 'right' },
      text('Explicatii', 'Detalii extras', 200)], 'IDFXE', (row) => {
      if (busy) return; extract = row; update();
    }, 'Explicatii');
    createGrid('ade-bank-groups', [text('Grupa', 'Grupa', 200)], 'IDG', (row) => run(async () => {
      group = row; child = null; payer = null; data.children = []; data.payers = [];
      grids[2].setRows([]); grids[3].setRows([]); update();
      data.children = (await api(`/api/adechit/rows/Platitori?IDG=${row.IDG}`)).rows;
      grids[2].setRows(data.children);
    }), 'Grupa');
    createGrid('ade-bank-children', [text('Nume', 'Copil', 210), text('CNP', 'CNP copil', 100)], 'IDP',
      (row) => run(() => selectChild(row)), 'Nume');
    createGrid('ade-bank-payers', [{ ...text('Nume', 'Plătitor', 242), minWidth: 242 }, text('CUI', 'CNP Plătitor', 135), text('Cont', 'Cont', 168),
      text('BancaCont', 'Banca', 152),
      { ...text('Closed', 'Închis', 60), valueType: ValueType.Boolean, display: 'checkbox', filter: false }], 'IDS',
      (row) => { if (!busy) { payer = row; update(); } }, 'Nume');
  }
  function renderTargets() {
    grids[1].setRows(data.groups); grids[2].setRows(data.children);
    grids[3].setRows(data.payers.map((item) => ({ ...item, Closed: !Boolean(item.Activ) })));
    if (group) grids[1].selectRow(group.IDG);
    if (child) grids[2].selectRow(child.IDP);
    if (payer) grids[3].selectRow(payer.IDS);
  }
  async function selectChild(row) {
    child = row; payer = null; data.payers = []; grids[3].setRows([]); update();
    data.payers = (await api(`/api/adechit/bank-extracts/payers?IDP=${row.IDP}`)).rows;
    payer = data.payers.find((item) => item.Activ) || null;
    renderTargets(); update();
  }
  async function loadCatalogs() {
    const [catalog, months] = await Promise.all([
      api(`/api/adechit/catalog-data?include_hidden=${$('ade-bank-show-hidden').checked ? '1' : '0'}`),
      api('/api/adechit/rows/LunaD'),
    ]);
    data.groups = catalog.groups.sort((a, b) => String(a.Grupa).localeCompare(String(b.Grupa), 'ro'));
    data.months = months.rows.sort((a, b) => b.Anul - a.Anul || b.Luna - a.Luna);
    const preferred = month.value || String(monthId() || '');
    month.replaceChildren(new Option('Alegeți luna', ''));
    data.months.forEach((item) => month.add(new Option(`${item.LunaT} ${item.Anul}${item.Inchisa ? ' · închisă' : ''}`, String(item.IDL))));
    month.value = data.months.some((item) => String(item.IDL) === preferred) ? preferred : String(data.months[0]?.IDL || '');
    chosenMonth = month.value;
    group = data.groups.find((item) => item.IDG === group?.IDG) || null;
    if (group) {
      data.children = (await api(`/api/adechit/rows/Platitori?IDG=${group.IDG}`)).rows;
      child = data.children.find((item) => item.IDP === child?.IDP) || null;
    } else { child = null; data.children = []; }
    if (child) await selectChild(child);
    else { payer = null; data.payers = []; }
    renderTargets(); await suggestExplanation();
  }
  async function loadExtracts(more = false, background = false) {
    const query = filters(); const own = ++listSequence;
    if (more && next != null) query.set('before', next);
    const result = await api(`/api/adechit/bank-extracts?${query}`);
    if (own !== listSequence || !dialog.open || automatic) return false;
    if (background && busy) { queuedSearch = true; return false; }
    if (!more) {
      const retained = background && extract ? result.rows.find((row) => row.IDFXE === extract.IDFXE) : null;
      extract = retained?.fingerprint === extract?.fingerprint ? retained : null;
    }
    data.extracts = more ? [...data.extracts, ...result.rows] : result.rows;
    next = result.next; grids[0].setRows(data.extracts);
    if (extract) grids[0].selectRow(extract.IDFXE);
    update(); return true;
  }
  function requestSearch() {
    if (!dialog.open) return;
    if (busy) { queuedSearch = true; return; }
    const action = automatic ? auto.load(null, false, { background: true }) : loadExtracts(false, true);
    action.catch(report);
  }
  async function setMode(value) {
    listSequence += 1;
    automatic = value; dialog.dataset.mode = value ? 'auto' : 'manual';
    dialog.querySelectorAll('.ade-bank-manual').forEach((item) => { item.hidden = value; });
    $('ade-bank-auto-panel').hidden = !value; $('ade-bank-auto-save').hidden = !value;
    $('ade-bank-manual-mode').hidden = !value;
    $('ade-bank-auto-mode').hidden = value;
    $('ade-bank-manual-mode').setAttribute('aria-pressed', String(!value));
    $('ade-bank-auto-mode').setAttribute('aria-pressed', String(value));
    message('');
    if (value) await auto.load();
    else { auto.cancelLoad(); await loadCatalogs(); await loadExtracts(); }
  }
  async function open() {
    if (dialog.open || !context()?.subunit) return;
    extract = null; group = null; child = null; payer = null; touched = false; savedRequest = null;
    data = { extracts: [], groups: [], children: [], payers: [], months: [] };
    auto.invalidate(); explanation.value = ''; search.value = ''; lastSearch = ''; queuedSearch = false;
    $('ade-bank-show-hidden').checked = false;
    month.replaceChildren(new Option('Alegeți luna', '')); message(''); dialog.showModal(); buildGrids();
    await run(async () => { await periods.open(); await setMode(false); });
  }
  $('ade-bank-save').addEventListener('click', () => run(async () => {
    const period = selectedMonth();
    if (!extract || !child || !payer?.Activ || !period || !canSave()) return;
    const body = JSON.stringify({ extract_id: extract.IDFXE, fingerprint: extract.fingerprint,
      payer_id: payer.IDS, payer_version: payer.Version, child_id: child.IDP, child_version: child.Version,
      month_id: period.IDL, month_version: period.Version, explanation: explanation.value.trim() });
    if (!savedRequest || savedRequest.body !== body) savedRequest = { body, key: crypto.randomUUID() };
    if (!await confirmBox(`${money(extract.suma_credit)} lei · ${extract.platitor_nume} → ${child.Nume}\n`
      + `${period.LunaT} ${period.Anul} · ${context().subunit.name}\nExplicație: ${explanation.value.trim()}`, {
      title: 'Asociere extras', yes: 'Asociază și înregistrează', no: 'Renunță' })) return;
    await api('/api/adechit/bank-extracts/associate', { method: 'POST', ...savedRequest });
    extract = null; savedRequest = null; touched = false;
    try { await refresh(); await loadCatalogs(); await loadExtracts(); }
    catch (error) { throw new Error(`Asocierea a fost salvată. Reîncărcați listele: ${error.message}`); }
    message('Asociere salvată.');
  }));
  $('ade-bank-manual-mode').addEventListener('click', () => run(() => setMode(false)));
  $('ade-bank-auto-mode').addEventListener('click', () => run(() => setMode(true)));
  $('ade-bank-reload').addEventListener('click', () => run(async () => {
    clearTimeout(searchTimer);
    if (automatic) await auto.load();
    else { await periods.open({ preserve: true }); await loadCatalogs(); await loadExtracts(); }
  }));
  $('ade-bank-more').addEventListener('click', () => run(() => loadExtracts(true)));
  search.addEventListener('input', () => {
    const term = search.value.trim().length >= 2 ? search.value.trim() : '';
    if (term === lastSearch) return;
    lastSearch = term; clearTimeout(searchTimer); searchTimer = setTimeout(requestSearch, 300);
  });
  search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(searchTimer); requestSearch(); } });
  month.addEventListener('change', () => run(async () => {
    if (!await auto.discardChanges()) { month.value = chosenMonth; return; }
    chosenMonth = month.value; auto.invalidate(); await suggestExplanation(); if (automatic) await auto.load();
  }));
  $('ade-bank-show-hidden').addEventListener('change', () => run(async () => {
    await loadCatalogs(); auto.invalidate(); if (automatic) await auto.load();
  }));
  explanation.addEventListener('input', () => { touched = true; });
  function cleanup() {
    clearTimeout(searchTimer); listSequence += 1; queuedSearch = false;
    grids.forEach((instance) => instance.destroy()); grids = []; auto.invalidate(); periods.destroy();
  }
  function close() { if (!busy) { dialog.close(); cleanup(); } }
  $('ade-bank-close').addEventListener('click', close);
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  addEventListener('pagehide', cleanup, { once: true });
  return { open };
}
