// Slice 0110-04 -- sign-in with a digital certificate, COMPUTERS ONLY.
//
// A phone or tablet has no certificate connected to it, so there the whole feature is taken out of
// the page (the «Intră cu certificat» button, the «Certificat» button of the header and the dialog
// are removed from the DOM, nothing is hidden-but-present). The decision is made once, here.
//
// The calls go to /api/portal/certificat/* on the host named by <meta name="portal-cert-origin">
// (empty = this host). nginx asks the browser for the certificate on that connection; the page only
// sees the server's answer: {error, reason} or, for the sign-in, {token}.

const CERT_PATH = '/api/portal/certificat/';

const $ = (id) => document.getElementById(id);

// True on a phone or a tablet. By the device, never by the window width: a narrow desktop window
// still has its token. Also covers «Request desktop site» on a phone (touch only, no mouse).
export function isPhoneOrTablet() {
  try {
    if (navigator.userAgentData && navigator.userAgentData.mobile) return true;
    if (/Android|iPhone|iPad|iPod|Mobile|Windows Phone/i.test(navigator.userAgent || '')) return true;
    // iPadOS answers as a Mac, but has a touch screen
    if (/Macintosh/.test(navigator.userAgent || '') && navigator.maxTouchPoints > 1) return true;
    const touchOnly = window.matchMedia('(pointer: coarse)').matches && !window.matchMedia('(any-pointer: fine)').matches;
    return touchOnly;
  } catch (err) {
    console.error('[portal] cannot tell the device, certificate sign-in stays off', err);
    return true;
  }
}

function certOrigin() {
  const meta = document.querySelector('meta[name="portal-cert-origin"]');
  const value = meta ? (meta.getAttribute('content') || '').trim().replace(/\/+$/, '') : '';
  return value.startsWith('https://') ? value : '';
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'Z');
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('ro-RO');
}

/**
 * @param {object}   opts
 * @param {Function} opts.call        portal.js call(method, path, body) -> {ok,status,data}
 * @param {Function} opts.say         portal.js say(id, text)
 * @param {Function} opts.onSignedIn  async (token) => the page opens the signed-in state
 */
export function createCertificate({ call, say, onSignedIn }) {
  const ids = ['cert-login', 'btn-cert', 'dlg-cert'];
  if (isPhoneOrTablet()) {
    ids.forEach((id) => { const el = $(id); if (el) el.remove(); });
    return { enabled: false };
  }

  const base = certOrigin();
  const url = (name) => base + CERT_PATH + name;

  $('cert-login').hidden = false;
  $('btn-cert').hidden = false;

  // ---- sign in with the certificate; any refusal leaves the password form right there
  $('btn-cert-login').addEventListener('click', async () => {
    say('msg-login', '');
    const button = $('btn-cert-login');
    button.disabled = true;
    const r = await call('POST', url('login'));
    button.disabled = false;
    if (r.ok && r.data.token) {
      await onSignedIn(r.data.token);
      return;
    }
    if (r.data.reason === 'NETWORK' || r.data.reason === 'CERT_ABSENT') {
      say('msg-login', 'Certificatul nu a putut fi folosit (nu a fost ales sau nu este conectat). Poți intra cu parola și codul.');
      return;
    }
    say('msg-login', (r.data.error || 'Autentificarea cu certificat nu a reușit.') + ' Poți intra cu parola și codul.');
  });

  // ---- the dialog: is this certificate enrolled, enrol it, switch one off
  const dlg = $('dlg-cert');
  let here = false;

  function paint(data) {
    here = !!data.certificat_prezent;
    const enrolled = !!data.acest_certificat_inrolat;
    let text;
    if (enrolled) {
      text = 'Certificatul acesta este înrolat: poți intra cu el, fără cod.';
    } else if (here) {
      text = 'Browserul a trimis un certificat care nu este înrolat pe contul tău.';
    } else if (data.motiv === 'CERT_EXPIRAT') {
      text = 'Certificatul a expirat. Înrolează unul nou.';
    } else if (data.motiv === 'CERT_INVALID') {
      text = 'Certificatul trimis nu este de la o autoritate acceptată.';
    } else {
      text = 'Browserul nu a trimis niciun certificat. Conectează tokenul sau cardul, apoi redeschide această fereastră. Dacă tocmai l-ai conectat, închide și redeschide browserul.';
    }
    $('cert-state').textContent = text;
    $('btn-cert-enrol').hidden = !(here && !enrolled);

    const list = $('cert-list');
    list.replaceChildren();
    (data.certificate || []).forEach((c) => {
      const li = document.createElement('li');
      li.className = c.activ ? '' : 'is-off';
      const label = document.createElement('span');
      const until = formatDate(c.valid_pana);
      label.textContent = (c.subiect || 'Certificat') + (until ? ' · valabil până la ' + until : '')
        + (c.aici ? ' · acesta' : '') + (c.activ ? '' : ' · dezactivat');
      li.appendChild(label);
      if (c.activ) {
        const off = document.createElement('button');
        off.type = 'button';
        off.className = 'btn btn--ghost btn--sm';
        off.textContent = 'Dezactivează';
        off.addEventListener('click', () => switchOff(c.id));
        li.appendChild(off);
      }
      list.appendChild(li);
    });
  }

  async function refresh() {
    say('msg-cert', '');
    $('cert-state').textContent = 'Se verifică...';
    $('btn-cert-enrol').hidden = true;
    const r = await call('GET', url('stare'));
    if (!r.ok) {
      $('cert-state').textContent = '';
      say('msg-cert', r.data.error || 'Starea certificatului nu a putut fi citită.');
      return;
    }
    paint(r.data);
  }

  async function switchOff(id) {
    say('msg-cert', '');
    const r = await call('POST', url('dezactiveaza'), { id });
    if (!r.ok) {
      say('msg-cert', r.data.error || 'Certificatul nu a putut fi dezactivat.');
      return;
    }
    await refresh();
  }

  $('btn-cert').addEventListener('click', async () => {
    dlg.showModal();
    await refresh();
  });
  $('btn-cert-close').addEventListener('click', () => dlg.close());
  $('btn-cert-enrol').addEventListener('click', async () => {
    say('msg-cert', '');
    const button = $('btn-cert-enrol');
    button.disabled = true;
    const r = await call('POST', url('inroleaza'), {});
    button.disabled = false;
    if (!r.ok) {
      say('msg-cert', r.data.error || 'Certificatul nu a putut fi înrolat.');
      return;
    }
    await refresh();
  });

  return { enabled: true };
}
