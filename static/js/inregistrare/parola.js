// The page the approval mail links to (slice 0075-03): the new user chooses a password.
// The token comes in the link's fragment (#...), which the browser never sends to the
// server; it is read once, wiped from the address bar, and posted in request bodies only.

import { createApi, ApiError } from './api.js';

const MIN_LENGTH = 8;
const MSG_UNEXPECTED = 'A apărut o eroare neașteptată. Reîncărcați pagina și reluați.';

const api = createApi(() => null);
const $ = (id) => document.getElementById(id);

const token = readToken();

function readToken() {
  const value = decodeURIComponent((window.location.hash || '').replace(/^#/, '')).trim();
  // Out of the address bar and the history: a link that works once should not linger
  // where the next person at this computer can find it.
  if (window.location.hash) {
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  return value;
}

function show(panelId) {
  for (const id of ['panel-check', 'panel-dead', 'panel-form', 'panel-done']) {
    $(id).hidden = id !== panelId;
  }
}

function dead(message) {
  $('dead-msg').textContent = message;
  show('panel-dead');
}

function formMessage(text) {
  const box = $('form-msg');
  box.textContent = text || '';
  box.className = 'msg msg-error';
  box.hidden = !text;
}

function messageOf(err) {
  if (err instanceof ApiError) return err.message;
  console.error('[parola] unexpected failure', err);
  return MSG_UNEXPECTED;
}

async function start() {
  if (!token) {
    dead('Linkul este incomplet. Deschideți-l exact așa cum a venit în e-mail.');
    return;
  }
  try {
    const state = await api.linkState(token);
    $('form-unit').textContent = state.denumire || '';
    $('form-user').textContent = state.email_masked || '';
    show('panel-form');
    $('pwd-1').focus();
  } catch (err) {
    dead(messageOf(err));
  }
}

async function submit(event) {
  event.preventDefault();
  const first = $('pwd-1').value;
  const second = $('pwd-2').value;

  if (first.length < MIN_LENGTH) {
    formMessage(`Parola trebuie să aibă cel puțin ${MIN_LENGTH} caractere.`);
    $('pwd-1').focus();
    return;
  }
  if (first !== second) {
    formMessage('Cele două parole nu coincid.');
    $('pwd-2').focus();
    return;
  }
  formMessage('');

  const button = $('pwd-submit');
  button.disabled = true;
  button.classList.add('is-busy');
  try {
    const answer = await api.setPassword(token, first);
    $('done-user').textContent = answer.utilizator || '';
    show('panel-done');
    $('h-done').focus();
  } catch (err) {
    if (err instanceof ApiError && err.reason === 'LINK_INVALID') {
      dead(err.message);
    } else {
      formMessage(messageOf(err));
    }
  } finally {
    button.disabled = false;
    button.classList.remove('is-busy');
  }
}

$('pwd-form').addEventListener('submit', submit);
start();
