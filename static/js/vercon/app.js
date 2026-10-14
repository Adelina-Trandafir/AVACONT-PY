// slice VC02-01/02: VERCON working page. The context (DC -> subunit -> gestiune -> year) is resolved by the server on
// every change; the page keeps only the last ACCEPTED answer. A response is accepted only if it answers the latest
// request (sequence number) and carries the version the server computed for it. The menu comes from the server
// (type + rights + occupation); retired navigation entries are hidden locally. Cleanup: one AbortController tied to `pagehide`.
import { openMenu } from '../adechit/menu.js';
import { startSessionTimer } from '../adechit/session.js';
import { Combobox } from '../components/combobox/combobox.js';
import { confirmBox } from '../utils/confirm-box.js';
import { showMessage, messageHistory } from '../portal/messages.js';
import { installLayouts } from '../portal/layouts.js';
import { createApi, portalToken, toPortal } from './api.js';
import { createDraft } from './draft.js';
import { mountEditor } from './editor.js';
import { mountDocuments } from './docs.js';
import { mountLista } from './lista.js';
import { mountMf } from './mf.js';
import { mountStock } from './stock.js';
import { mountReports } from './reports.js';
import { mountNomenclator, mountSettings } from './nomenclatoare.js';
import { createLock } from './lock.js';
import { createSaver } from './save.js';
import { writePreference } from './documents.js';
import { el, preview, scope } from './ui.js';

const $ = (k_id) => document.getElementById(k_id);
const lifetime = new AbortController();   // everything below is bound to this signal
const IS_PREVIEW = document.documentElement.dataset.vcPreview === 'true';
const state = { unit: null, ctx: null, seq: 0, form: null, draft: null, editor: null, columns: null, revision: 1, saver: null };
let lock = null;   // created right after api; the headers below read it lazily
const api = createApi({
  getUnit: () => state.unit, signal: lifetime.signal,
  extraHeaders: () => ({ ...(lock?.token ? { 'X-Vercon-Lock': lock.token } : {}), ...(state.ctx ? { 'X-Vercon-Version': state.ctx.version } : {}) }),
});
lock = createLock({ api, signal: lifetime.signal, getUnit: () => state.unit,
  onLost: (k_message) => { showMessage($('vc-message'), `${k_message} Datele dvs. au rămas în formular, dar nu mai pot fi salvate fără o nouă ocupare.`, 'error'); } });
const combos = {};
const pageUi = scope();
let formSequence = 0, formLoading = false;
installLayouts();

const paint = (k_kind, k_text) => {
  const k_host = $('vc-state');
  k_host.hidden = !k_text; k_host.dataset.state = k_kind;
  k_host.replaceChildren(...(k_text ? [Object.assign(document.createElement('p'), { textContent: k_text })] : []));
};
const report = (k_error) => {
  if (k_error?.name === 'AbortError') return;
  console.error('[vercon]', k_error);
  showMessage($('vc-message'), k_error.requestId ? `${k_error.message} (cod ${k_error.requestId})` : (k_error.message || 'Eroare necunoscută.'), 'error');
};
const query = (k_sel) => new URLSearchParams(Object.entries(k_sel).filter(([, k_v]) => k_v != null && k_v !== '').map(([k_k, k_v]) => [k_k, String(k_v)]));

async function logoutNow() {
  try { await fetch('/api/portal/logout', { method: 'POST', headers: { 'X-Portal-Token': portalToken() } }); }
  catch (k_error) { console.error('[vercon] logout call failed', k_error); }
  toPortal(true);
}

// ---- context ----------------------------------------------------------------------------------------------------
async function loadContext(k_wanted) {
  const k_seq = ++state.seq;
  let k_ctx;
  try { k_ctx = await api(`/api/vercon/context?${query(k_wanted)}`); }
  catch (k_error) {
    if (k_seq !== state.seq) return false;                     // a newer request is already running
    if (k_error.reason === 'SELECTION_CHANGED') { showMessage($('vc-message'), k_error.message, 'info'); return loadContext({}); }
    throw k_error;
  }
  if (k_seq !== state.seq) return false;                       // an old answer never enters a newer context
  applyContext(k_ctx);
  return true;
}

function applyContext(k_ctx) {
  state.ctx = k_ctx;
  if (!k_ctx.ready) { paint('empty', 'VERCON este în pregătire pentru această unitate. Nu există încă gestiuni de lucru.'); $('vc-menu').hidden = true; return; }
  paint('', '');
  const k_sel = k_ctx.selection; const k_opt = k_ctx.options;
  const k_set = (k_name, k_items, k_current) => {
    const k_combo = combos[k_name];
    k_combo.options.staticData = k_items;
    const k_found = k_items.find((k_i) => String(k_i.value) === String(k_current));
    if (k_found) k_combo.setValue(k_found.value, k_found.label); else k_combo.clear();
  };
  k_set('subunit', k_opt.subunits.map((k_s) => ({ value: String(k_s.id), label: k_s.name })), k_sel.subunit);
  k_set('gestiune', k_opt.gestiuni.map((k_g) => ({ value: String(k_g.id), label: k_g.name })), k_sel.gestiune);
  k_set('year', k_opt.years.map((k_y) => ({ value: String(k_y), label: String(k_y) })), k_sel.year);
  paintMenu(k_ctx);
}

function paintMenu(k_ctx) {
  const k_nav = $('vc-menu');
  k_nav.hidden = !k_ctx.menu.length;
  k_nav.replaceChildren();
  const k_groups = new Map();
  for (const k_item of k_ctx.menu) {
    if (k_item.key === 'stocuri') continue;
    if (!k_groups.has(k_item.group)) k_groups.set(k_item.group, []);
    k_groups.get(k_item.group).push(k_item);
  }
  for (const [k_group, k_items] of k_groups) {
    const k_div = document.createElement('div'); k_div.className = 'vc-menu__group';
    const k_config = k_group === 'Configurari';
    if (k_config) {
      const button = el('button', { type: 'button', textContent: '▤ Nomenclatoare', 'aria-haspopup': 'menu' });
      button.addEventListener('click', () => openMenu(button, k_items.map((item) => ({ icon: '▤', label: item.label, disabled: !item.enabled, run: () => openForm(item.key).catch(report) })), { label: 'Nomenclatoare și setări' }), { signal: lifetime.signal });
      if (k_items.some((item) => item.key === state.form)) button.setAttribute('aria-current', 'true');
      k_div.append(button);
    }
    for (const k_item of k_config ? [] : k_items) {
      const k_btn = Object.assign(document.createElement('button'), { type: 'button', textContent: k_item.label, disabled: !k_item.enabled, title: k_item.reason || '' });
      if (state.form === k_item.key) k_btn.setAttribute('aria-current', 'true');
      if (k_item.key === 'nir' && !IS_PREVIEW) {   // purchase invoice: the same popup menu as in ADECHIT
        k_btn.setAttribute('aria-haspopup', 'menu');
        k_btn.addEventListener('click', () => openMenu(k_btn, [
          { icon: '➕', label: 'Adăugare', run: () => openReceipt(true).catch(report) },
          { icon: '✎', label: 'Editare / Modificare', run: () => openReceipt(false).catch(report) },
          { icon: '🖨', label: 'Listare', rule: true, run: () => {} },
        ], { label: k_item.label }), { signal: lifetime.signal });
        k_div.append(k_btn); continue;
      }
      k_btn.addEventListener('click', () => openForm(k_item.key).catch(report), { signal: lifetime.signal });
      k_div.append(k_btn);
    }
    k_nav.append(k_div);
  }
}

// Purchase invoice menu: "Adaugare" opens the page straight on a new document, "Editare" just opens the page.
let startReceiptInAdd = false;   // read by openForm at the moment it mounts the page, so no consultation screen flashes first
async function openReceipt(k_add) {
  if (state.form === 'nir') { if (k_add) await state.editor?.startAdd?.(); return; }   // already open and loaded
  startReceiptInAdd = k_add;
  try { await openForm('nir'); } finally { startReceiptInAdd = false; }
}

// A change of context with unsaved work asks first: keep it (stay where you are) or discard it (and move).
async function allowLeave() {
  if (!state.draft?.dirty && !state.editor?.isDirty?.()) return true;
  return confirmBox('Aveți modificări nesalvate. Dacă schimbați contextul, ele se pierd.', { title: 'Modificări nesalvate', yes: 'Renunț la modificări', no: 'Rămân aici', warning: true });
}

function closeForm() {
  ++formSequence;
  pageUi.clear();
  state.editor?.destroy(); state.editor = null; state.draft = null; state.form = null; state.columns = null; state.saver = null;
  $('vc-work').hidden = true; $('vc-work').replaceChildren();
}

async function change(k_wanted) {
  if (!(await allowLeave())) { applyContext(state.ctx); return; }   // paint the accepted context back
  closeForm();
  await lock.release();                                              // the old gestiune is free for others
  await loadContext(k_wanted);
}

// ---- forms ------------------------------------------------------------------------------------------------------
async function openForm(k_key) {
  if (formLoading) return;
  if (state.form === k_key) return;
  if (!(await allowLeave())) return;
  if (formLoading) return;
  formLoading = true; $('vc-menu').inert = true;
  try {
  const opening = ++formSequence;
  const k_version = state.ctx.version; const k_sel = state.ctx.selection;
  const k_open = await api(`/api/vercon/forms/${encodeURIComponent(k_key)}/open?${query(k_sel)}`);
  if (opening !== formSequence || k_open.version !== state.ctx.version || k_version !== state.ctx.version) return;
  if (k_open.write && !lock.holds(k_sel)) {
    try { await lock.acquire(k_sel); }
    catch (k_error) {
      if (k_error.reason !== 'GESTIUNE_OCCUPIED') throw k_error;
      // another tab of the same user may be the holder: the server allows a takeover only for the same user
      if (!(await confirmBox(`${k_error.message} Dacă este o altă filă a dvs., o puteți prelua (cealaltă filă pierde accesul).`, { title: 'Gestiune ocupată', yes: 'Preiau', no: 'Renunț' }))) return;
      await lock.acquire(k_sel, true);
    }
    if (opening !== formSequence || state.ctx.version !== k_version) { await lock.release(); return; }
  }
  closeForm();
  state.form = k_key; state.columns = k_open.columns; state.revision = 1;
  writePreference(`form.${k_sel.type}`, k_key);               // per type: an MF choice is never applied to Alimente
  paintMenu(state.ctx);
  const k_work = $('vc-work'); k_work.hidden = false;
  if (k_key === 'rapoarte') {
    state.editor = mountReports(k_work, { api, sel: () => state.ctx.selection });
    return;
  }
  if (k_key === 'stocuri') {
    state.editor = mountStock(k_work, { api, sel: () => state.ctx.selection, signal: lifetime.signal,
      openForm: (key) => openForm(key).catch(report), canOpen: (key) => state.ctx.menu.some((item) => item.key === key && item.enabled) });
    return;
  }
  if (k_open.nom?.length || k_key === 'setari') {   // nomenclatures and gestiune settings (VC06-01)
    const k_args = { api, sel: () => state.ctx.selection, signal: lifetime.signal };
    state.editor = k_open.nom?.length ? mountNomenclator(k_work, { ...k_args, kinds: k_open.nom }) : mountSettings(k_work, k_args);
    await state.editor.ready;
    return;
  }
  if (!IS_PREVIEW && k_key === 'lista_alimente') {   // the food list (VC07-01)
    state.editor = mountLista(k_work, { api, sel: () => state.ctx.selection, signal: lifetime.signal });
    return;
  }
  if (!IS_PREVIEW && k_key === 'mf_registru') {   // the fixed-asset register (VC07-04)
    state.editor = mountMf(k_work, { api, sel: () => state.ctx.selection, signal: lifetime.signal });
    return;
  }
  if (!IS_PREVIEW && ['nir', 'storno', 'bc', 'br', 'bp', 'bt', 'bs'].includes(k_key)) {   // documents (VC06-02..05); the preview keeps the stand-in editor
    state.editor = mountDocuments(k_work, { api, sel: () => state.ctx.selection, family: k_key, signal: lifetime.signal, startAdd: k_key === 'nir' && startReceiptInAdd });
    return;
  }
  if (!k_open.columns.lines.length) {
      k_work.replaceChildren(el('section', { className: 'vc-empty-page' }, el('h3', { textContent: state.ctx.menu.find((item) => item.key === k_key)?.label || 'VERCON' }), el('p', { textContent: 'Această secțiune este în pregătire.' })));
    return;
  }
  state.draft = createDraft({ unit: state.unit, subunit: k_sel.subunit, gestiune: k_sel.gestiune, year: k_sel.year, form: k_key });
  state.saver = createSaver({ endpoint: '/api/vercon/preview/save', getUnit: () => state.unit, getVersion: () => state.ctx.version, getLock: () => lock.token, signal: lifetime.signal });
  state.editor = mountEditor(k_work, {
    draft: state.draft, columns: k_open.columns, saver: state.saver, form: k_key,
    saveUrl: IS_PREVIEW ? '/api/vercon/preview/save' : null,   // real save endpoints arrive with VC06
    getRevision: () => state.revision,
    onSaved: (k_revision) => { if (k_revision != null) state.revision = k_revision; },
    simulate: () => new URLSearchParams(location.search).get('simulate') || '',
  });
  } finally { formLoading = false; $('vc-menu').inert = false; }
}

// ---- start ------------------------------------------------------------------------------------------------------
function makeCombo(k_name, k_holder, k_placeholder, k_onPick) {
  const k_combo = new Combobox($(k_holder), { readonly: true, placeholder: k_placeholder, staticData: [], onSelect: k_onPick });
  k_combo.input.id = `vc-${k_name}-combo-input`;
  k_combo.input.setAttribute('aria-label', k_placeholder);
  combos[k_name] = k_combo;
}

async function start() {
  paint('loading', 'Se încarcă…');
  try {
    const k_me = await fetch('/api/portal/me', { headers: { 'X-Portal-Token': portalToken() }, signal: lifetime.signal, cache: 'no-store' });
    if (k_me.status === 401) { toPortal(true); return; }
    const k_who = await k_me.json().catch(() => ({}));
    if (!k_who.db_name) { toPortal(false); return; }
    state.unit = k_who.db_name;
    $('vc-subunit-combo').title = `Unitate: ${k_who.db_name}`;
    $('vc-context').textContent = k_who.email || '';
    await loadContext({});
  } catch (k_error) {
    paint('error', k_error.message || 'Eroare necunoscută.');
    report(k_error);
  }
}

function bind() {
  const k_sel = () => state.ctx.selection;
  // an upstream change clears everything below it; the server then auto-selects a single remaining option
  makeCombo('subunit', 'vc-subunit-combo', 'Subunitate', (k_v) => { if (String(k_v) !== String(k_sel().subunit)) change({ subunit: k_v }).catch(report); });
  makeCombo('gestiune', 'vc-gestiune-combo', 'Gestiune', (k_v) => { if (String(k_v) !== String(k_sel().gestiune)) change({ subunit: k_sel().subunit, gestiune: k_v }).catch(report); });
  makeCombo('year', 'vc-year-combo', 'An', (k_v) => { if (String(k_v) !== String(k_sel().year)) change({ subunit: k_sel().subunit, gestiune: k_sel().gestiune, year: k_v }).catch(report); });
  $('vc-theme').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('kbot-portal-theme', theme); } catch (_) { /* The theme still applies to this visit. */ }
    document.querySelectorAll('.vc-dgv > .dgv').forEach((node) => { node.dataset.theme = theme === 'dark' ? 'dark' : 'modern'; });
  }, { signal: lifetime.signal });
  const help = () => preview('Ajutor VERCON', [{ title: 'Lucrul cu documente', columns: [{ key: 'step', title: 'Acțiune', width: 200 }, { key: 'text', title: 'Descriere', width: 580 }], rows: [
    { step: 'Context', text: 'Alegeți subunitatea, gestiunea și exercițiul înainte de a deschide o operație.' },
    { step: 'Consultare', text: 'Alegeți documentul din arborele din dreapta. ⋮ deschide opțiunile documentului.' },
    { step: 'Adaugă / Modifică', text: 'Arborele se ascunde. Click sau Enter editează o celulă; Tab confirmă și trece la următoarea.' },
    { step: 'Salvează', text: 'Salvează întregul document. Valorile și disponibilul se validează pe server.' },
    { step: 'Renunță', text: 'Abandonează modificările nesalvate și revine la consultare.' },
    { step: 'Lista de alimente', text: 'Adaugă rând în secțiunea BC asociază un bon existent, fără să repete consumul.' },
  ] }], pageUi);
  $('vc-help').addEventListener('click', () => openMenu($('vc-help'), [
    { icon: 'ⓘ', label: 'Cum lucrez cu VERCON', run: help },
    { icon: '◷', label: 'Mesaje din sesiune', run: () => preview('Mesaje VERCON', [{ title: 'Mesaje', columns: [
      { key: 'at', title: 'Data', width: 210 }, { key: 'level', title: 'Tip', width: 90 }, { key: 'message', title: 'Mesaj', width: 600 },
    ], rows: messageHistory() }], pageUi) },
  ]), { signal: lifetime.signal });
  $('vc-apps').addEventListener('click', () => {
    openMenu($('vc-apps'), [
      { icon: '🏢', label: $('vc-context').textContent || 'Contul curent', disabled: true },
      { icon: '🤖', label: 'K-BOT', rule: true, run: async () => { if (await allowLeave()) { await lock.release(); window.location.href = '/portal'; } } },
      { icon: '📋', label: 'ADECHIT', run: async () => { if (await allowLeave()) { await lock.release(); window.location.href = '/adechit'; } } },
      { icon: '📒', label: 'Avacont · în curând', disabled: true },
    ], { label: 'Aplicații', below: $('vc-apps').closest('header') });
  }, { signal: lifetime.signal });
  const stopTimer = portalToken() ? startSessionTimer({ token: portalToken, host: $('vc-session-timer'), onExpired: logoutNow }) : null;
  // leaving the page with unsaved work: the browser asks; then every request and listener stops
  addEventListener('beforeunload', (k_event) => { if (state.draft?.dirty || state.editor?.isDirty?.()) { k_event.preventDefault(); k_event.returnValue = ''; } }, { signal: lifetime.signal });
  addEventListener('pagehide', () => { stopTimer?.(); pageUi.clear(); lock.release(true); state.editor?.destroy(); Object.values(combos).forEach((k_c) => k_c.destroy?.()); lifetime.abort(); }, { once: true });
}

bind();
start();
