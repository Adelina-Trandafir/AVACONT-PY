// Public registration wizard (slice 0075-04, plan section 4).
//
// Six screens, one visible at a time. The registration token minted by /anaf is
// kept in memory only; every later call sends it back (api.js). The server re-checks
// everything at /cerere, so nothing here is trusted -- the checks below exist to spare
// the applicant a round trip, not to guard anything.

import { TreeView } from '../components/treeview/treeview.js';
import { Combobox } from '../components/combobox/combobox.js';
import { ApiError, createApi } from './api.js';
import { buildClassificationTree, countLeaves } from './tree-builder.js';

const TOKEN_REASONS = new Set(['TOKEN_ABSENT', 'TOKEN_UNKNOWN', 'TOKEN_EXPIRED']);
const EXPIRY_WARNING_S = 5 * 60;
const TREE_VISIBLE_ROWS = 14;
const MSG_EXPIRED = 'Sesiunea de înregistrare a expirat. Reluați de la codul fiscal.';

// What a unit name may contain: the server's `[\w\s,]` (nume.py). Python's \w is
// Unicode; a JS \w is ASCII-only and would strip every diacritic, hence \p{L}\p{N}.
const NAME_FORBIDDEN = /[^\p{L}\p{N}_\s,]/gu;

// A refusal from /cerere that the applicant fixes on an earlier screen.
const STEP_FOR_REASON = {
  DENUMIRE_ABSENTA: 1,
  DENUMIRE_CARACTERE_INTERZISE: 1,
  DENUMIRE_PREA_LUNGA: 1,
  EMAIL_NEVERIFICAT: 2,
  SS_ABSENT: 3,
  SS_NECUNOSCUT: 3,
  CLSF_F_ABSENT: 4,
  CLSF_E_ABSENT: 4,
  CLSF_F_NECUNOSCUT: 4,
  CLSF_E_NECUNOSCUT: 4,
  CLSF_F_NU_E_FRUNZA: 4,
  CLSF_E_NU_E_FRUNZA: 4,
  PREA_MULTE_RANDURI: 4,
};

const state = {
  step: 1,
  finished: false,
  token: null,
  expiresAt: 0, // ms; end of the 30-minute registration window
  cf: '',
  unit: null, // ANAF fields: cui, denumire, adresa, nr_reg_com
  email: '',
  emailMasked: '',
  verified: false,
  denumire: '',
  surse: null, // [{cod, sursa, sector, denumire}] once loaded; kept across registrations
  chosenSS: new Set(), // SursaSector codes; any number of them
  treesLoaded: false,
  totalF: 0,
  totalE: 0,
  checkedF: [],
  checkedE: [],
};

const api = createApi(() => state.token);
const $ = (id) => document.getElementById(id);

const panels = [...document.querySelectorAll('.step-panel')];
const stepperItems = [...document.querySelectorAll('#stepper li')];
const panelFor = (n) => panels.find((p) => Number(p.dataset.step) === n);

const el = {
  timer: $('session-timer'),
  cfInput: $('cf-input'),
  cfSearch: $('cf-search'),
  anafCard: $('anaf-card'),
  nameBlock: $('name-block'),
  denumireInput: $('denumire-input'),
  next1: $('next-1'),
  emailInput: $('email-input'),
  codeSend: $('code-send'),
  codeBox: $('code-box'),
  codeNote: $('code-note'),
  codeInput: $('code-input'),
  codeVerify: $('code-verify'),
  codeResend: $('code-resend'),
  emailOk: $('email-ok'),
  emailOkText: $('email-ok-text'),
  emailChange: $('email-change'),
  next2: $('next-2'),
  ssAdd: $('ss-add'),
  ssList: $('ss-list'),
  ssEmpty: $('ss-empty'),
  next3: $('next-3'),
  countF: $('count-f'),
  countE: $('count-e'),
  rowsNote: $('rows-note'),
  next4: $('next-4'),
  summary: $('summary'),
  submit: $('submit'),
  doneId: $('done-id'),
};

let treeF = null;
let treeE = null;
let yearCombo = null;
let sursaCombo = null;
let sectorCombo = null;
let timerHandle = null;
let nameCheckSeq = 0;

// ---------------------------------------------------------------------------
// Navigation, messages, busy state
// ---------------------------------------------------------------------------

const enterStep = {
  3: enterSurse,
  4: enterTrees,
  5: enterSummary,
};

function showStep(n) {
  state.step = n;
  for (const panel of panels) panel.hidden = Number(panel.dataset.step) !== n;
  for (const li of stepperItems) {
    const s = Number(li.dataset.step);
    li.classList.toggle('current', s === n);
    li.classList.toggle('done', s < n);
    if (s === n) li.setAttribute('aria-current', 'step');
    else li.removeAttribute('aria-current');
  }
  clearMessage(n);
  syncStep(n);
  if (enterStep[n]) enterStep[n]();
  window.scrollTo({ top: 0 });
  panelFor(n).querySelector('h2').focus({ preventScroll: true });
}

// The enabled/disabled state every screen derives from `state`.
function syncStep(n) {
  if (n === 1) {
    el.next1.disabled = !(state.token && state.unit);
  } else if (n === 2) {
    el.next2.disabled = !state.verified;
    el.codeSend.disabled = state.verified;
    el.emailInput.readOnly = state.verified;
    el.emailOk.hidden = !state.verified;
    if (state.verified) el.codeBox.hidden = true;
  }
}

// The last screen carries no message box: there is nothing left to go wrong on it.
function messageBox(step) {
  return panelFor(step).querySelector('.msg');
}

function showMessage(step, text, kind = 'error', retry = null) {
  const box = messageBox(step);
  if (!box) return;
  box.className = `msg msg-${kind}`;
  box.textContent = text;
  if (retry) {
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'btn-link';
    again.textContent = 'Reîncearcă';
    again.addEventListener('click', () => {
      clearMessage(step);
      retry();
    });
    box.append(' ', again);
  }
  box.hidden = false;
}

function clearMessage(step) {
  const box = messageBox(step);
  if (!box) return;
  box.hidden = true;
  box.textContent = '';
}

// Every failure lands here: an expired registration starts over, anything else is
// the server's own sentence on the screen that asked.
function fail(step, err, retry = null) {
  if (!(err instanceof ApiError)) {
    console.error('[inregistrare] unexpected error', err);
    showMessage(step, 'A apărut o eroare neașteptată. Reîncărcați pagina și reluați.');
    return;
  }
  if (TOKEN_REASONS.has(err.reason)) {
    restart(err.message);
    return;
  }
  showMessage(step, err.message, 'error', retry);
}

// Locks the screen while a request runs, so a double click cannot send it twice.
async function busy(button, work) {
  const panel = button.closest('.step-panel');
  const step = Number(panel.dataset.step);
  const controls = [...panel.querySelectorAll('button, input')];
  for (const c of controls) c.disabled = true;
  button.classList.add('is-busy');
  panel.setAttribute('aria-busy', 'true');
  try {
    return await work();
  } finally {
    for (const c of controls) c.disabled = false;
    button.classList.remove('is-busy');
    panel.removeAttribute('aria-busy');
    syncStep(step);
  }
}

function restart(message) {
  resetRegistration();
  showStep(1);
  showMessage(1, message || MSG_EXPIRED);
}

// Everything tied to one token. The dictionaries stay: they are the same for everyone.
function resetRegistration() {
  Object.assign(state, {
    token: null,
    expiresAt: 0,
    cf: '',
    unit: null,
    email: '',
    emailMasked: '',
    verified: false,
    denumire: '',
    checkedF: [],
    checkedE: [],
    finished: false,
  });
  state.chosenSS = new Set();
  stopTimer();
  nameCheckSeq++;
  el.anafCard.hidden = true;
  el.nameBlock.hidden = true;
  el.codeBox.hidden = true;
  el.codeInput.value = '';
  el.denumireInput.value = '';
  if (state.surse) renderSurse();
  if (treeF) treeF.clearChecks();
  if (treeE) treeE.clearChecks();
  syncStep(1);
  syncStep(2);
}

// ---------------------------------------------------------------------------
// The registration window
// ---------------------------------------------------------------------------

function startTimer() {
  stopTimer();
  tick();
  timerHandle = setInterval(tick, 1000);
}

function stopTimer() {
  clearInterval(timerHandle);
  timerHandle = null;
  el.timer.hidden = true;
}

function tick() {
  const left = Math.max(0, Math.round((state.expiresAt - Date.now()) / 1000));
  if (left === 0) {
    restart(MSG_EXPIRED);
    return;
  }
  const mm = String(Math.floor(left / 60)).padStart(2, '0');
  const ss = String(left % 60).padStart(2, '0');
  el.timer.textContent = `Timp rămas: ${mm}:${ss}`;
  el.timer.title = 'Cererea trebuie trimisă în 30 de minute de la căutarea codului fiscal.';
  el.timer.classList.toggle('warn', left <= EXPIRY_WARNING_S);
  el.timer.hidden = false;
}

// ---------------------------------------------------------------------------
// 1. Fiscal code and unit name
// ---------------------------------------------------------------------------

async function searchCf() {
  clearMessage(1);
  const cf = el.cfInput.value.trim();
  if (!cf) {
    showMessage(1, 'Introduceți codul fiscal.');
    el.cfInput.focus();
    return;
  }
  await busy(el.cfSearch, async () => {
    try {
      const data = await api.anaf(cf);
      resetRegistration();
      state.token = data.token;
      state.expiresAt = Date.now() + data.expires_in * 1000;
      state.cf = data.cf;
      state.unit = data.unitate || {};
      // ANAF's spelling goes through the same filter as typing: the applicant never
      // sees a character the name could not be saved with.
      state.unit.denumire = cleanName(state.unit.denumire);
      state.denumire = state.unit.denumire;
      $('anaf-cui').textContent = state.unit.cui || state.cf;
      $('anaf-adresa').textContent = state.unit.adresa || '—';
      $('anaf-regcom').textContent = state.unit.nr_reg_com || '—';
      el.anafCard.hidden = false;
      el.denumireInput.value = state.denumire;
      el.nameBlock.hidden = false;
      startTimer();
    } catch (err) {
      resetRegistration();
      fail(1, err);
    }
  });
}

// The name travels to /cerere exactly as typed. /nume is asked here only for its verdict:
// it refuses a name it cannot turn into a database name (the four-letter rule /cerere does
// not repeat). The name it proposes is internal -- the operator settles it at approval.
async function next1() {
  clearMessage(1);
  const text = cleanName(el.denumireInput.value);
  el.denumireInput.value = text;
  state.denumire = text;
  if (!text) {
    showMessage(1, 'Introduceți denumirea unității.');
    el.denumireInput.focus();
    return;
  }
  const seq = ++nameCheckSeq;
  const ok = await busy(el.next1, async () => {
    try {
      await api.dbName(text);
      return true;
    } catch (err) {
      if (seq === nameCheckSeq) fail(1, err);
      return false;
    }
  });
  if (ok && seq === nameCheckSeq) showStep(2);
}

// Forbidden characters leave the field as they are typed or pasted, and a run of spaces
// becomes one. The caret stays where it was relative to the text that remains.
function nameEdited(e) {
  if (e && e.isComposing) return;
  const input = el.denumireInput;
  const before = input.value;
  const tidy = tidyName(before);
  if (tidy !== before) {
    const at = input.selectionStart ?? before.length;
    const caret = Math.min(tidyName(before.slice(0, at)).length, tidy.length);
    input.value = tidy;
    input.setSelectionRange(caret, caret);
  }
  state.denumire = cleanName(input.value);
}

// Editing the code after a lookup means a different unit: the card no longer applies.
function cfEdited() {
  if (!state.token) return;
  const digits = el.cfInput.value.replace(/\D/g, '');
  if (digits !== state.cf) {
    resetRegistration();
    clearMessage(1);
  }
}

// ---------------------------------------------------------------------------
// 2. E-mail
// ---------------------------------------------------------------------------

async function sendCode(button) {
  clearMessage(2);
  const email = el.emailInput.value.trim();
  if (!email) {
    showMessage(2, 'Introduceți adresa de e-mail.');
    el.emailInput.focus();
    return;
  }
  const sent = await busy(button, async () => {
    try {
      const data = await api.sendCode(email);
      state.email = email.toLowerCase();
      state.emailMasked = data.email_masked;
      state.verified = false;
      const minutes = Math.max(1, Math.round(data.expires_in / 60));
      el.codeNote.textContent =
        `Am trimis un cod de 6 cifre la ${data.email_masked}. ` +
        `Codul este valabil ${countLabel(minutes, 'minut', 'minute')}.`;
      el.codeInput.value = '';
      el.codeBox.hidden = false;
      return true;
    } catch (err) {
      fail(2, err);
      return false;
    }
  });
  if (sent) el.codeInput.focus();
}

async function verifyCode() {
  clearMessage(2);
  const code = el.codeInput.value.replace(/\s+/g, '');
  // Checked here so a typo does not spend one of the five attempts.
  if (!/^\d{6}$/.test(code)) {
    showMessage(2, 'Codul are 6 cifre.');
    el.codeInput.focus();
    return;
  }
  const ok = await busy(el.codeVerify, async () => {
    try {
      const data = await api.verifyCode(code);
      state.verified = true;
      state.emailMasked = data.email_masked;
      state.expiresAt = Date.now() + data.expires_in * 1000;
      el.emailOkText.textContent = `✓ Adresa ${state.email} a fost confirmată.`;
      return true;
    } catch (err) {
      fail(2, err);
      return false;
    }
  });
  if (ok) showStep(3);
}

function changeEmail() {
  state.verified = false;
  el.codeBox.hidden = true;
  syncStep(2);
  el.emailInput.focus();
  el.emailInput.select();
}

// A code already sent belongs to the address it went to.
function emailEdited() {
  if (!state.verified && !el.codeBox.hidden && el.emailInput.value.trim().toLowerCase() !== state.email) {
    el.codeBox.hidden = true;
  }
}

// ---------------------------------------------------------------------------
// 3. Sector-source
// ---------------------------------------------------------------------------

function enterSurse() {
  if (state.surse) renderSurse();
  else loadSurse();
}

async function loadSurse() {
  el.ssEmpty.textContent = 'Se încarcă…';
  try {
    const data = await api.sursaSector();
    state.surse = data.surse || [];
    renderSurse();
  } catch (err) {
    el.ssEmpty.textContent = '';
    fail(3, err, loadSurse);
  }
}

// Two comboboxes over DefaSursaSector -- `Sursa` first, then the `Sectorul` values that
// exist for it -- and the Add button puts the pair (one `SursaSector` code) on the list below.
// A unit can keep accounts for any number of pairs.
function renderSurse() {
  const known = new Set(state.surse.map((s) => s.cod));
  state.chosenSS = new Set([...state.chosenSS].filter((c) => known.has(c)));
  ensureSurseCombos();

  const sources = [...new Set(state.surse.map((s) => s.sursa))].sort();
  sursaCombo.options.staticData = sources.map((s) => ({ value: s, label: s }));
  sursaCombo.clear();
  fillSectors(null);
  renderSSList();
}

function ensureSurseCombos() {
  if (sursaCombo) return;
  sursaCombo = new Combobox($('sursa-combo'), {
    readonly: true,
    placeholder: 'Alegeți sursa',
    onSelect: (value) => pickSursa(value),
  });
  sectorCombo = new Combobox($('sector-combo'), {
    readonly: true,
    placeholder: 'Alegeți întâi sursa',
    onSelect: (value) => pickSector(value),
  });
  sursaCombo.input.setAttribute('aria-labelledby', 'label-sursa');
  sectorCombo.input.setAttribute('aria-labelledby', 'label-sector');
}

// Loads the sectors of one source into the second combobox; nothing chosen yet.
function fillSectors(sursa) {
  const rows = sursa ? state.surse.filter((s) => s.sursa === sursa) : [];
  sectorCombo.options.staticData = rows.map((s) => ({ value: s.sector, label: sectorLabel(s) }));
  sectorCombo.clear();
  sectorCombo.input.placeholder = sursa ? 'Alegeți sectorul' : 'Alegeți întâi sursa';
  sectorCombo.setEnabled(Boolean(sursa));
  return rows;
}

function pickSursa(sursa) {
  const rows = fillSectors(sursa);
  // One sector only (03, 04, 05, 08): there is nothing to choose.
  if (rows.length === 1) pickSector(rows[0].sector);
  clearMessage(3);
}

function pickSector(sector) {
  const row = findSS(sursaCombo.getSelectedValue(), sector);
  if (row) sectorCombo.setValue(row.sector, sectorLabel(row));
  clearMessage(3);
}

// The one row of DefaSursaSector with this pair, or nothing.
function findSS(sursa, sector) {
  if (!sursa || !sector || !state.surse) return undefined;
  return state.surse.find((s) => s.sursa === sursa && s.sector === sector);
}

// The chosen pairs in nomenclator order (the server sends DefaSursaSector sorted).
function chosenSursaSector() {
  return state.surse ? state.surse.filter((s) => state.chosenSS.has(s.cod)) : [];
}

function sectorLabel(row) {
  return row.denumire && row.denumire !== row.cod ? `${row.sector} · ${row.denumire}` : row.sector;
}

function sursaSectorText(row) {
  return row.denumire && row.denumire !== row.cod ? `${row.cod} · ${row.denumire}` : row.cod;
}

function renderSSList() {
  const rows = chosenSursaSector();
  el.ssList.replaceChildren();
  for (const row of rows) {
    const li = document.createElement('li');
    const text = document.createElement('span');
    text.className = 'ss-text';
    text.textContent = sursaSectorText(row);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn-link';
    remove.textContent = 'Elimină';
    remove.setAttribute('aria-label', `Elimină ${row.cod}`);
    remove.addEventListener('click', () => removeSS(row.cod));
    li.append(text, remove);
    el.ssList.append(li);
  }
  el.ssList.hidden = rows.length === 0;
  el.ssEmpty.hidden = rows.length > 0;
  el.ssEmpty.textContent = 'Nicio sursă-sector adăugată încă.';
}

function removeSS(cod) {
  state.chosenSS.delete(cod);
  clearMessage(3);
  renderSSList();
  el.ssAdd.focus();
}

// Puts the pair in the two comboboxes on the list. The comboboxes are filled from
// DefaSursaSector, so a pair with no row should not be reachable -- but the pair, not
// either half, is what the request carries, so it is checked here: a pair that is not
// in the table is refused. Answers true when the pair is on the list afterwards.
function addPendingSS({ quietIfListed = false } = {}) {
  const sursa = sursaCombo && sursaCombo.getSelectedValue();
  const sector = sectorCombo && sectorCombo.getSelectedValue();
  if (!sursa) {
    showMessage(3, 'Alegeți sursa.');
    return false;
  }
  if (!sector) {
    showMessage(3, 'Alegeți sectorul.');
    return false;
  }
  const row = findSS(sursa, sector);
  if (!row) {
    showMessage(3, `Combinația sursă ${sursa} – sector ${sector} nu există în nomenclator. Alegeți alta.`);
    return false;
  }
  if (state.chosenSS.has(row.cod) && !quietIfListed) {
    showMessage(3, `Sursa-sector ${row.cod} este deja în listă.`);
    return false;
  }
  state.chosenSS.add(row.cod);
  sursaCombo.clear();
  fillSectors(null);
  renderSSList();
  return true;
}

function addSS() {
  clearMessage(3);
  addPendingSS();
}

// A pair picked but not yet added is added now -- the applicant clearly meant it --
// and a pair with no row in DefaSursaSector stops the screen here.
function next3() {
  clearMessage(3);
  const pending = sursaCombo && sursaCombo.getSelectedValue();
  if (pending && !addPendingSS({ quietIfListed: true })) return;
  if (state.chosenSS.size === 0) {
    showMessage(3, 'Adăugați cel puțin o sursă-sector.');
    return;
  }
  showStep(4);
}

// ---------------------------------------------------------------------------
// 4. Classification trees
// ---------------------------------------------------------------------------

function enterTrees() {
  if (!state.treesLoaded) loadTrees();
  else updateCounts();
}

async function loadTrees() {
  el.countF.textContent = 'Se încarcă…';
  el.countE.textContent = 'Se încarcă…';
  try {
    const [f, e] = await Promise.all([api.clasificatii('F'), api.clasificatii('E')]);
    const dataF = buildClassificationTree(f.coduri, f.grupuri);
    const dataE = buildClassificationTree(e.coduri, e.grupuri);
    state.totalF = countLeaves(dataF);
    state.totalE = countLeaves(dataE);
    ensureTrees();
    // setData keeps the boxes already ticked, minus codes that are gone.
    treeF.setData(dataF);
    treeE.setData(dataE);
    state.treesLoaded = true;
    updateCounts();
  } catch (err) {
    el.countF.textContent = '';
    el.countE.textContent = '';
    fail(4, err, loadTrees);
  }
}

function ensureTrees() {
  if (treeF) return;
  // ClsfF: only the last level can be ticked. ClsfE: an article ticks its whole branch
  // at once; a title only opens (operator, 22.09.2026). Either way what is sent is the
  // ticked leaves.
  const options = (branchChecks) => ({
    checkable: true,
    branchChecks,
    placeholder: 'Alegeți pozițiile…',
    searchPlaceholder: 'Căutați (minimum 3 caractere)…',
    formatCheckSummary: (n) =>
      n === 0 ? 'Alegeți pozițiile…' : countLabel(n, 'poziție aleasă', 'poziții alese'),
    onCheckChange: updateCounts,
  });
  treeF = new TreeView($('tree-f'), options(false));
  treeE = new TreeView($('tree-e'), options(true));
  treeF.maxVisibleRows = TREE_VISIBLE_ROWS;
  treeE.maxVisibleRows = TREE_VISIBLE_ROWS;
  treeF.input.setAttribute('aria-labelledby', 'label-f');
  treeE.input.setAttribute('aria-labelledby', 'label-e');
}

function updateCounts() {
  if (!treeF) return;
  state.checkedF = treeF.getCheckedLeaves();
  state.checkedE = treeE.getCheckedLeaves();
  el.countF.textContent = `Alese: ${fmt(state.checkedF.length)} din ${fmt(state.totalF)}`;
  el.countE.textContent = `Alese: ${fmt(state.checkedE.length)} din ${fmt(state.totalE)}`;
  const rows = rowCount();
  el.rowsNote.hidden = rows === 0;
  el.rowsNote.textContent =
    `Cererea va crea ${countLabel(rows, 'clasificație', 'clasificații')} ` +
    '(funcționale × economice × surse-sector).';
}

function rowCount() {
  return state.checkedF.length * state.checkedE.length * state.chosenSS.size;
}

function next4() {
  clearMessage(4);
  if (state.checkedF.length === 0) {
    showMessage(4, 'Alegeți cel puțin o poziție din clasificația funcțională.');
    return;
  }
  if (state.checkedE.length === 0) {
    showMessage(4, 'Alegeți cel puțin o poziție din clasificația economică.');
    return;
  }
  showStep(5);
}

// ---------------------------------------------------------------------------
// 5. Year, summary, submit
// ---------------------------------------------------------------------------

function enterSummary() {
  ensureYearCombo();
  renderSummary();
}

function ensureYearCombo() {
  if (yearCombo) return;
  // D6: this year and the one before; the server checks again against its own clock.
  const now = new Date().getFullYear();
  yearCombo = new Combobox($('an-combo'), {
    readonly: true,
    placeholder: 'Alegeți anul',
    staticData: [now, now - 1].map((y) => ({ value: String(y), label: String(y) })),
  });
  yearCombo.setValue(String(now), String(now));
  yearCombo.input.setAttribute('aria-labelledby', 'label-an');
}

function renderSummary() {
  const rows = [
    ['Cod fiscal', state.cf],
    ['Denumire ANAF', state.unit ? state.unit.denumire || '—' : '—'],
    ['Denumire', state.denumire],
    ['E-mail', state.email],
    ['Surse-sector', chosenSursaSector().map((r) => r.cod).join(', ') || '—'],
    ['Clasificații funcționale', fmt(state.checkedF.length)],
    ['Clasificații economice', fmt(state.checkedE.length)],
    ['Clasificații create', fmt(rowCount())],
  ];
  if (state.unit && state.unit.denumire === state.denumire) rows.splice(1, 1);

  el.summary.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    el.summary.append(dt, dd);
  }
}

async function submit() {
  clearMessage(5);
  const payload = {
    denumire: state.denumire,
    an: Number(yearCombo.getSelectedValue()),
    sursasector: [...state.chosenSS].sort(),
    clsf_f: state.checkedF,
    clsf_e: state.checkedE,
  };
  await busy(el.submit, async () => {
    try {
      const data = await api.submit(payload);
      finish(data);
    } catch (err) {
      submitRefused(err);
    }
  });
}

function submitRefused(err) {
  const target = err instanceof ApiError ? STEP_FOR_REASON[err.reason] : undefined;
  if (!target) {
    fail(5, err);
    return;
  }
  let message = err.message;
  if (err.reason === 'SS_NECUNOSCUT') {
    state.surse = null;
    message =
      'Lista surselor-sector s-a schimbat între timp. Am reîncărcat-o — ' +
      'verificați alegerea și continuați.';
  } else if (err.reason === 'CLSF_F_NECUNOSCUT' || err.reason === 'CLSF_E_NECUNOSCUT') {
    state.treesLoaded = false;
    message =
      'Nomenclatorul s-a schimbat între timp. L-am reîncărcat, iar pozițiile care nu mai ' +
      'există au fost debifate. Verificați selecția și continuați.';
  } else if (err.reason === 'EMAIL_NEVERIFICAT') {
    state.verified = false;
  }
  showStep(target);
  showMessage(target, message);
}

function finish(data) {
  el.doneId.textContent = String(data.id_cerere);
  stopTimer();
  state.token = null; // the server has discarded the registration
  state.finished = true;
  showStep(6);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// While typing: forbidden characters out, whitespace runs to one space. The ends are
// left alone, so a space typed after a word survives until the next word arrives.
function tidyName(text) {
  return String(text ?? '')
    .normalize('NFC')
    .replace(NAME_FORBIDDEN, '')
    .replace(/\s+/g, ' ');
}

// The name as it is shown, checked and sent.
function cleanName(text) {
  return tidyName(text).trim();
}

function fmt(n) {
  return n.toLocaleString('ro-RO');
}

// Romanian counts: 1 pozitie, 2-19 pozitii, 20 DE pozitii, 101 pozitii, 120 DE pozitii.
function countLabel(n, singular, plural) {
  if (n === 1) return `1 ${singular}`;
  const rest = n % 100;
  const withDe = n !== 0 && (rest === 0 || rest >= 20);
  return withDe ? `${fmt(n)} de ${plural}` : `${fmt(n)} ${plural}`;
}

function onEnter(input, action) {
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      action();
    }
  });
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

el.cfSearch.addEventListener('click', searchCf);
onEnter(el.cfInput, searchCf);
el.cfInput.addEventListener('input', cfEdited);
el.denumireInput.addEventListener('input', nameEdited);
el.denumireInput.addEventListener('compositionend', () => nameEdited());
onEnter(el.denumireInput, next1);
el.next1.addEventListener('click', next1);

el.codeSend.addEventListener('click', () => sendCode(el.codeSend));
el.codeResend.addEventListener('click', () => sendCode(el.codeResend));
onEnter(el.emailInput, () => {
  if (!state.verified) sendCode(el.codeSend);
});
el.emailInput.addEventListener('input', emailEdited);
el.codeVerify.addEventListener('click', verifyCode);
onEnter(el.codeInput, verifyCode);
el.emailChange.addEventListener('click', changeEmail);
el.next2.addEventListener('click', () => showStep(3));

el.ssAdd.addEventListener('click', addSS);
el.next3.addEventListener('click', next3);
el.next4.addEventListener('click', next4);
el.submit.addEventListener('click', submit);

for (const back of document.querySelectorAll('[data-back]')) {
  back.addEventListener('click', () => showStep(state.step - 1));
}

$('stepper').addEventListener('click', (e) => {
  const li = e.target.closest('li[data-step]');
  if (!li || state.finished) return;
  const s = Number(li.dataset.step);
  if (s < state.step) showStep(s);
});

// Everything lives in memory: leaving mid-way loses the registration.
window.addEventListener('beforeunload', (e) => {
  if (state.token && !state.finished) {
    e.preventDefault();
    e.returnValue = '';
  }
});

showStep(1);
