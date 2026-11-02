// SLICE-WB02-01: personal certificate management, separate from the K-BOT data screens.
import { createCertificateManager, isPhoneOrTablet } from './certificat.js';
import { showMessage } from './messages.js';

const $ = (id) => document.getElementById(id);
const PATH = '/api/portal/certificat/administrator/';
const wanted = new URLSearchParams(window.location.search).get('next');
const back = ['/adechit', '/vercon', '/portal'].includes(wanted) ? wanted : '/portal';
$('certificate-back').href = back;

function say(id, message) {
  showMessage($(id), message, 'error', { tooltip: false });
}

let pending = false;
async function call(method, path, body) {
  if (pending) return { ok: false, status: 409, data: { error: 'Așteaptă finalizarea operației curente.' } };
  pending = true;
  const buttons = [...document.querySelectorAll('#btn-cert, #btn-cert-enrol, #cert-list button')]
    .map((button) => ({ button, disabled: button.disabled }));
  buttons.forEach(({ button }) => { button.disabled = true; });
  try {
    const token = window.sessionStorage.getItem('kbot-portal-token') || '';
    const headers = { Accept: 'application/json', 'X-Portal-Token': token };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {
      method, headers, cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (response.status === 401 && data.reason === 'SESIUNE_EXPIRATA') {
      $('btn-cert').hidden = true;
      $('btn-cert-enrol').hidden = true;
      $('cert-list').replaceChildren();
      data.error = 'Sesiunea a expirat. Revino în aplicație și autentifică-te din nou, apoi redeschide înrolarea.';
    }
    if (response.status === 403) {
      $('btn-cert').hidden = true;
      $('btn-cert-enrol').hidden = true;
      $('cert-list').replaceChildren();
    }
    // Sort displayed certificates by their actual subject, alphabetically in Romanian.
    if (Array.isArray(data.certificate)) data.certificate.sort((a, b) =>
      String(a.subiect || 'Certificat').localeCompare(String(b.subiect || 'Certificat'), 'ro-RO', { sensitivity: 'base' }));
    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    console.error('[certificate] Request failed', error);
    return { ok: false, status: 0, data: { reason: 'NETWORK',
      error: 'Nu s-a putut comunica cu serviciul de certificate. Verifică adresa paginii și conexiunea, apoi reîncearcă.' } };
  } finally {
    pending = false;
    buttons.forEach(({ button, disabled }) => { button.disabled = disabled; });
  }
}

async function start() {
  if (isPhoneOrTablet()) {
    $('certificate-account').textContent = '';
    say('msg-page', 'Înrolarea certificatului digital este disponibilă pe calculatorul la care este conectat tokenul sau cardul.');
    return;
  }
  const access = await call('GET', PATH + 'acces');
  if (!access.ok) {
    $('certificate-account').textContent = '';
    say('msg-page', access.data.error || 'Accesul nu a putut fi verificat.');
    return;
  }
  $('certificate-account').textContent = 'Cont: ' + access.data.email;
  $('btn-cert').hidden = false;
  // Like the portal: the request to the certificate host leaves only when the user presses the button.
  createCertificateManager({ call, say, path: PATH });
}

start().catch((error) => {
  console.error('[certificate] Page could not be opened', error);
  say('msg-page', error.message || 'Pagina nu a putut fi deschisă.');
});
