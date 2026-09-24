// The operator's approval page (slice 0075-05). Sign-in (password, then mailed code),
// the list of requests, one request with its live plan, approve / reject / new link,
// and the progress of a running approval.
//
// Every value from the server reaches the page through textContent, never innerHTML:
// the unit name and the e-mail were typed by strangers on the public page.

import { createOperatorApi, ApiError } from './api.js';

const TOKEN_KEY = 'kbot.operator.token';
const EMAIL_KEY = 'kbot.operator.email';
const DB_NAME_SHAPE = /^1[1-9]{2}_[A-Z]{4}$/;
const COD_PROGRAM_SHAPE = /^[A-Za-z0-9]{1,32}$/;
const POLL_MS = 800;
const MSG_UNEXPECTED = 'A apărut o eroare neașteptată. Reîncărcați pagina.';

const STARI = [
  { key: 'InAsteptare', label: 'În așteptare' },
  { key: 'Esuata', label: 'Eșuate' },
  { key: 'Aprobata', label: 'Aprobate' },
  { key: 'Respinsa', label: 'Respinse' },
  { key: 'toate', label: 'Toate' },
];
const STARE_LABEL = {
  InAsteptare: 'În așteptare',
  Esuata: 'Eșuată',
  Aprobata: 'Aprobată',
  Respinsa: 'Respinsă',
};

const $ = (id) => document.getElementById(id);
const state = {
  token: readStored(TOKEN_KEY),
  email: readStored(EMAIL_KEY),
  pending: null,
  filter: 'InAsteptare',
  detail: null,
  jobId: null,
  jobFrom: 0,
  jobCerere: null,
};
const api = createOperatorApi(() => state.token);

// ---------------------------------------------------------------------------
// Storage: per tab, so a closed tab is a closed session. Blocked storage only
// means a reload asks for the sign-in again.
// ---------------------------------------------------------------------------
function readStored(key) {
  try {
    return sessionStorage.getItem(key) || null;
  } catch (err) {
    console.warn('[operator] sessionStorage unavailable', err);
    return null;
  }
}

function writeStored(key, value) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch (err) {
    console.warn('[operator] sessionStorage unavailable', err);
  }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const PANELS = ['panel-login', 'panel-code', 'panel-list', 'panel-detail', 'panel-job'];

function show(panelId, focusId) {
  for (const id of PANELS) $(id).hidden = id !== panelId;
  $('op-user').hidden = !state.token;
  $('op-user-email').textContent = state.email || '';
  if (focusId) $(focusId).focus();
}

function message(boxId, text, kind = 'error') {
  const box = $(boxId);
  box.textContent = text || '';
  box.className = `msg msg-${kind}`;
  box.hidden = !text;
}

function messageOf(err) {
  if (err instanceof ApiError) return err.message;
  console.error('[operator] unexpected failure', err);
  return MSG_UNEXPECTED;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === 'className') node.className = value;
    else node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

async function busy(buttonId, work) {
  const button = $(buttonId);
  button.disabled = true;
  button.classList.add('is-busy');
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.classList.remove('is-busy');
  }
}

// A 401/403 on a signed-in call means the session is gone: back to the sign-in.
function sessionLost(err) {
  if (err instanceof ApiError && (err.status === 401 || err.reason === 'NU_ESTE_OPERATOR')) {
    signOutLocally();
    message('login-msg', err.message, 'info');
    show('panel-login', 'login-email');
    return true;
  }
  return false;
}

function signOutLocally() {
  state.token = null;
  state.jobId = null;
  writeStored(TOKEN_KEY, null);
}

// ---------------------------------------------------------------------------
// Sign-in
// ---------------------------------------------------------------------------
async function onLogin(event) {
  event.preventDefault();
  const email = $('login-email').value.trim().toLowerCase();
  const parola = $('login-pwd').value;
  if (!email || !parola) {
    message('login-msg', 'Introduceți adresa de e-mail și parola.');
    return;
  }
  message('login-msg', '');
  try {
    const answer = await busy('login-submit', () => api.login(email, parola));
    $('login-pwd').value = '';
    state.pending = answer.pending;
    state.email = email;
    $('code-email').textContent = answer.email_masked || '';
    $('code-input').value = '';
    message('code-msg', '');
    show('panel-code', 'code-input');
  } catch (err) {
    message('login-msg', messageOf(err));
  }
}

async function onCode(event) {
  event.preventDefault();
  const cod = $('code-input').value.trim();
  if (!/^\d{6}$/.test(cod)) {
    message('code-msg', 'Codul are 6 cifre.');
    return;
  }
  try {
    const answer = await busy('code-submit', () => api.verify(state.pending, cod));
    state.pending = null;
    state.token = answer.token;
    state.email = answer.email;
    writeStored(TOKEN_KEY, state.token);
    writeStored(EMAIL_KEY, state.email);
    await openList(state.filter);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      // The code expired or was spent: the password step starts over.
      state.pending = null;
      message('login-msg', err.message, 'info');
      show('panel-login', 'login-pwd');
      return;
    }
    message('code-msg', messageOf(err));
  }
}

async function onLogout() {
  try {
    await api.logout();
  } catch (err) {
    console.warn('[operator] logout call failed; the session is dropped here anyway', err);
  }
  signOutLocally();
  message('login-msg', 'Ați ieșit din pagina de cereri.', 'info');
  show('panel-login', 'login-email');
}

// ---------------------------------------------------------------------------
// The list
// ---------------------------------------------------------------------------
async function openList(filter) {
  state.filter = filter;
  state.detail = null;
  message('list-msg', '');
  show('panel-list');
  try {
    const answer = await api.list(filter);
    renderTabs(answer.numar || {});
    renderList(answer.cereri || [], answer.limita);
  } catch (err) {
    if (sessionLost(err)) return;
    renderTabs({});
    renderList([]);
    message('list-msg', messageOf(err));
  }
}

function renderTabs(counts) {
  const tabs = $('list-tabs');
  tabs.replaceChildren();
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  for (const { key, label } of STARI) {
    const count = key === 'toate' ? total : counts[key] || 0;
    const tab = el('button', {
      type: 'button', className: 'op-tab', role: 'tab',
      'aria-selected': String(key === state.filter),
    }, label, el('span', { className: 'op-count' }, count));
    tab.addEventListener('click', () => openList(key));
    tabs.append(tab);
  }
}

function renderList(rows, limit) {
  const body = $('list-body');
  body.replaceChildren();
  for (const r of rows) {
    const tr = el('tr', { className: 'op-row', tabindex: '0' },
      el('td', { className: 'num' }, r.IdCerere),
      el('td', { className: 'nowrap' }, r.DataCerere || ''),
      el('td', {}, r.Denumire || ''),
      el('td', { className: 'nowrap' }, r.CF || ''),
      el('td', {}, r.Email || ''),
      el('td', { className: 'num' }, r.randuri),
      el('td', {}, badge(r.Stare)),
      el('td', { className: 'nowrap op-code' }, r.DbName || ''),
    );
    const open = () => openDetail(r.IdCerere);
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
    body.append(tr);
  }
  $('list-table').hidden = rows.length === 0;
  $('list-empty').hidden = rows.length !== 0;
  if (limit && rows.length >= limit) {
    message('list-msg', `Se arată doar ultimele ${limit} cereri.`, 'info');
  }
}

function badge(stare) {
  return el('span', { className: `op-badge op-badge-${stare}` }, STARE_LABEL[stare] || stare);
}

// ---------------------------------------------------------------------------
// One request
// ---------------------------------------------------------------------------
async function openDetail(idCerere) {
  message('detail-msg', '');
  $('h-detail').textContent = `Cererea ${idCerere}`;
  $('detail-kv').replaceChildren();
  $('detail-ss').replaceChildren();
  $('detail-approve').hidden = true;
  $('detail-link').hidden = true;
  $('detail-anaf').hidden = true;
  $('detail-motiv').hidden = true;
  show('panel-detail', 'h-detail');
  try {
    const d = await api.detail(idCerere);
    state.detail = d;
    renderDetail(d);
    if (d.job) watchJob(d.job, d.IdCerere, 0);
  } catch (err) {
    if (sessionLost(err)) return;
    message('detail-msg', messageOf(err));
  }
}

function renderDetail(d) {
  $('h-detail').textContent = `Cererea ${d.IdCerere} · ${d.Denumire || ''}`;

  const kv = $('detail-kv');
  kv.replaceChildren();
  const pairs = [
    ['Stare', badge(d.Stare)],
    ['Unitate', d.Denumire],
    ['Cod fiscal', d.CF],
    ['E-mail (contul)', d.Email],
    ['An', d.An],
    ['Rânduri Clasificatii', d.randuri],
    ['Depusă', `${d.DataCerere || ''}${d.IpAddress ? ` · IP ${d.IpAddress}` : ''}`],
  ];
  if (d.DbName) pairs.push(['Baza', el('span', { className: 'op-code' }, d.DbName)]);
  if (d.DataDecizie) pairs.push(['Decizie', `${d.DataDecizie} · ${d.Decis || ''}`]);
  for (const [label, value] of pairs) kv.append(el('dt', {}, label), el('dd', {}, value));

  // Plan 7: the applicant may edit the name ANAF gave; the operator compares the two.
  if (d.DenumireAnaf && d.DenumireAnaf !== d.Denumire) {
    message('detail-anaf', `Denumirea la ANAF: «${d.DenumireAnaf}». Solicitantul a scris: «${d.Denumire}».`, 'info');
  }
  if (d.Motiv) {
    const label = d.Stare === 'Respinsa' ? 'Motivul respingerii' : 'Motiv';
    message('detail-motiv', `${label}: ${d.Motiv}`, d.Stare === 'Respinsa' ? 'info' : 'error');
  }

  renderCodes('detail-f', 'detail-f-sum', 'Coduri F (funcționale)', d.f || []);
  renderCodes('detail-e', 'detail-e-sum', 'Coduri E (economice)', d.e || []);

  const plan = d.plan && d.plan.ok ? d.plan.rezultat : null;
  renderSs(d, plan);

  $('detail-approve').hidden = !d.aprobabila;
  if (d.aprobabila) {
    $('db-name').value = plan ? plan.db_name : '';
    $('db-name').dataset.proposed = plan ? plan.db_name_propus : '';
    $('db-name').classList.remove('is-invalid');
    $('reject-motiv').value = '';
    renderPlan(d.plan);
  }

  $('detail-link').hidden = d.Stare !== 'Aprobata';
  if (d.Stare === 'Aprobata') {
    $('link-manual').hidden = true;
    let text;
    if (!d.LinkActiv) text = 'Parola a fost aleasă (sau linkul a fost anulat).';
    else if (d.LinkValabil) text = `Link trimis, valabil până la ${d.ParolaExpira}; parola nu a fost aleasă încă.`;
    else text = `Linkul a expirat la ${d.ParolaExpira}; parola nu a fost aleasă.`;
    $('link-state').textContent = text;
  }
}

function renderCodes(listId, summaryId, label, codes) {
  $(summaryId).textContent = `${label}: ${codes.length}`;
  const list = $(listId);
  list.replaceChildren();
  for (const c of codes) {
    list.append(el('li', {}, el('code', {}, c.cod), ' ', c.denumire || ''));
  }
}

function renderSs(d, plan) {
  const body = $('detail-ss');
  body.replaceChildren();
  for (const s of d.ss || []) {
    const idUnitate = plan && plan.id_unitate ? plan.id_unitate[s.cod] : null;
    let cell;
    if (d.aprobabila) {
      const input = el('input', {
        className: 'text-input op-code', maxlength: '32', autocomplete: 'off',
        spellcheck: 'false', 'data-ss': s.cod, 'aria-label': `CodProgram pentru ${s.cod}`,
      });
      input.value = plan && plan.cod_program ? plan.cod_program[s.cod] || '' : '';
      input.addEventListener('input', markPlanStale);
      cell = input;
    } else {
      cell = '—';
    }
    body.append(el('tr', {},
      el('td', { className: 'op-code' }, s.cod),
      el('td', {}, s.denumire || ''),
      el('td', { className: 'num' }, idUnitate ? `≈ ${idUnitate}` : '—'),
      el('td', {}, cell),
    ));
  }
}

function renderPlan(planAnswer) {
  const box = $('plan-box');
  box.replaceChildren();
  delete box.dataset.stale;
  if (!planAnswer) return;
  if (!planAnswer.ok) {
    box.append(el('div', { className: 'msg msg-error', role: 'alert' },
      'Nu se poate aproba așa: ', planAnswer.problema || ''));
    $('approve-btn').disabled = true;
    return;
  }
  const r = planAnswer.rezultat;
  const lines = [
    `Baza: ${r.db_name}${r.db_name !== r.db_name_propus ? ` (propunerea calculată: ${r.db_name_propus})` : ''}`,
    `Șablon: ${r.tabele} tabele, ${r.viewuri} view-uri`,
    `Clasificatii: ${r.randuri} rânduri`,
    `Contul: ${r.email}, rolul Contabil, drepturi numai pe ${r.db_name}`,
  ];
  box.append(el('div', { className: 'ok-note' }, 'Toate verificările au trecut.'),
    el('ul', {}, ...lines.map((t) => el('li', {}, t))));
  $('approve-btn').disabled = false;
}

function markPlanStale() {
  const box = $('plan-box');
  // The approval checks everything again on the server, so an edit may fix a refusal.
  $('approve-btn').disabled = false;
  if (box.dataset.stale === '1') return;
  box.dataset.stale = '1';
  box.prepend(el('div', { className: 'msg msg-info', id: 'plan-stale' },
    'Ați modificat valorile. Apăsați «Verifică din nou» (sau «Aprobă», care verifică la fel).'));
}

function clearPlanStale() {
  const box = $('plan-box');
  delete box.dataset.stale;
  const note = $('plan-stale');
  if (note) note.remove();
}

// What the operator typed: the name and the CodProgram per sector-source. Answers
// null (and says why) when something is not the right shape.
function readEdits() {
  const nameInput = $('db-name');
  const dbName = nameInput.value.trim().toUpperCase();
  nameInput.value = dbName;
  if (!DB_NAME_SHAPE.test(dbName)) {
    nameInput.classList.add('is-invalid');
    message('detail-msg', 'Numele bazei trebuie să aibă forma 1nn_SSSS (ex. 111_VTRS): cifra 1, două cifre de la 1 la 9, liniuță jos, patru litere.');
    nameInput.focus();
    return null;
  }
  nameInput.classList.remove('is-invalid');

  const codProgram = {};
  for (const input of document.querySelectorAll('#detail-ss input[data-ss]')) {
    const value = input.value.trim();
    if (!COD_PROGRAM_SHAPE.test(value)) {
      message('detail-msg', `CodProgram pentru ${input.dataset.ss} trebuie să aibă 1-32 litere sau cifre.`);
      input.focus();
      return null;
    }
    codProgram[input.dataset.ss] = value;
  }
  message('detail-msg', '');
  return { dbName, codProgram };
}

async function onPlanCheck() {
  const d = state.detail;
  const edits = readEdits();
  if (!d || !edits) return;
  try {
    const answer = await busy('plan-check', () => api.plan(d.IdCerere, edits.codProgram, edits.dbName));
    clearPlanStale();
    renderPlan(answer);
    if (answer.ok) renderSs(d, answer.rezultat);
  } catch (err) {
    if (sessionLost(err)) return;
    message('detail-msg', messageOf(err));
  }
}

async function onApprove() {
  const d = state.detail;
  const edits = readEdits();
  if (!d || !edits) return;
  const question =
    `Se creează baza ${edits.dbName} pentru «${d.Denumire}» și contul ${d.Email}.\n\n` +
    'Continuați?';
  if (!window.confirm(question)) return;
  try {
    const answer = await busy('approve-btn', () => api.approve(d.IdCerere, edits.codProgram, edits.dbName));
    watchJob(answer.job, d.IdCerere, 0);
  } catch (err) {
    if (sessionLost(err)) return;
    message('detail-msg', messageOf(err));
  }
}

async function onReject() {
  const d = state.detail;
  if (!d) return;
  const motiv = $('reject-motiv').value.trim();
  if (motiv.length < 5) {
    message('detail-msg', 'Scrieți motivul respingerii (îl primește solicitantul).');
    $('reject-motiv').focus();
    return;
  }
  if (!window.confirm(`Respingeți cererea ${d.IdCerere} (${d.Denumire})?\nSolicitantul primește motivul pe e-mail.`)) return;
  try {
    const answer = await busy('reject-btn', () => api.reject(d.IdCerere, motiv));
    await openDetail(d.IdCerere);
    message('detail-msg', answer.anuntat
      ? `Cererea a fost respinsă; motivul a fost trimis la ${answer.email}.`
      : `Cererea a fost respinsă, dar e-mailul către ${answer.email} NU a plecat. Anunțați solicitantul altfel.`,
    answer.anuntat ? 'info' : 'error');
  } catch (err) {
    if (sessionLost(err)) return;
    message('detail-msg', messageOf(err));
  }
}

async function onNewLink() {
  const d = state.detail;
  if (!d) return;
  if (!window.confirm(`Trimiteți un link nou de parolă la ${d.Email}? Linkul vechi nu va mai funcționa.`)) return;
  try {
    const answer = await busy('link-btn', () => api.newLink(d.IdCerere));
    await openDetail(d.IdCerere);
    if (answer.link_trimis) {
      message('detail-msg', `Linkul nou a fost trimis la ${d.Email}.`, 'info');
    } else {
      message('detail-msg', 'E-mailul NU a plecat. Transmiteți linkul de mai jos solicitantului, pe altă cale.');
      message('link-manual', answer.link || '', 'info');
    }
  } catch (err) {
    if (sessionLost(err)) return;
    message('detail-msg', messageOf(err));
  }
}

function onNameReset() {
  const input = $('db-name');
  input.value = input.dataset.proposed || '';
  input.classList.remove('is-invalid');
  markPlanStale();
}

// ---------------------------------------------------------------------------
// A running approval
// ---------------------------------------------------------------------------
function watchJob(jobId, idCerere, from) {
  state.jobId = jobId;
  state.jobFrom = from;
  state.jobCerere = idCerere;
  $('h-job').textContent = `Aprobarea cererii ${idCerere}`;
  $('job-state').textContent = 'Rulează… Nu închideți pagina și nu reporniți serverul.';
  $('job-log').replaceChildren();
  message('job-msg', '');
  $('job-list').disabled = true;
  $('job-open').disabled = true;
  show('panel-job', 'h-job');
  pollJob(jobId);
}

async function pollJob(jobId) {
  if (state.jobId !== jobId) return;
  let answer;
  try {
    answer = await api.job(jobId, state.jobFrom);
  } catch (err) {
    if (sessionLost(err)) return;
    finishJob(false, messageOf(err));
    return;
  }
  const log = $('job-log');
  for (const line of answer.linii || []) {
    const cls = /^(EROARE|EȘUAT|REFUZAT|NU s-a putut)/.test(line) ? 'is-error'
      : /^Anulat/.test(line) ? 'is-undo' : '';
    log.append(el('li', cls ? { className: cls } : {}, line));
  }
  log.scrollTop = log.scrollHeight;
  state.jobFrom = answer.total;

  if (!answer.gata) {
    setTimeout(() => pollJob(jobId), POLL_MS);
    return;
  }
  if (answer.ok) {
    const r = answer.rezultat || {};
    let text = `Gata: baza ${r.db_name}, ${r.randuri} clasificații, contul ${r.email}.`;
    if (r.link_trimis === false) text += ` E-mailul cu linkul NU a plecat; linkul, de transmis de mână: ${r.link}`;
    finishJob(true, text, r.link_trimis === false ? 'error' : 'info');
  } else {
    let text = answer.eroare || 'Aprobarea nu a reușit.';
    if (answer.ramas && answer.ramas.length) text += ` De curățat de mână: ${answer.ramas.join(' ; ')}`;
    finishJob(false, text);
  }
}

function finishJob(ok, text, kind) {
  state.jobId = null;
  $('job-state').textContent = ok ? 'Încheiată.' : 'Oprită.';
  message('job-msg', text, kind || (ok ? 'info' : 'error'));
  $('job-list').disabled = false;
  $('job-open').disabled = false;
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
$('login-form').addEventListener('submit', onLogin);
$('code-form').addEventListener('submit', onCode);
$('code-back').addEventListener('click', () => {
  state.pending = null;
  show('panel-login', 'login-pwd');
});
$('btn-logout').addEventListener('click', onLogout);
$('list-refresh').addEventListener('click', () => openList(state.filter));
$('detail-back').addEventListener('click', () => openList(state.filter));
$('plan-check').addEventListener('click', onPlanCheck);
$('approve-btn').addEventListener('click', onApprove);
$('reject-btn').addEventListener('click', onReject);
$('link-btn').addEventListener('click', onNewLink);
$('db-name').addEventListener('input', markPlanStale);
$('db-name-reset').addEventListener('click', onNameReset);
$('job-list').addEventListener('click', () => openList(state.filter));
$('job-open').addEventListener('click', () => openDetail(state.jobCerere));

if (state.token) openList(state.filter);
else show('panel-login', 'login-email');
