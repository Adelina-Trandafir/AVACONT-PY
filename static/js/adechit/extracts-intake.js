import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { showMessage } from '../portal/messages.js';

// Bringing bank statements in: PDF files / a folder, and the unit's mailbox (SLICE-AD13-01).
// The statements land in the same tables the association window reads; nothing here associates.
const $ = (id) => document.getElementById(id);
const text = (key, title, width = 120) => ({ key, title, width, minWidth: 50, valueType: ValueType.Text });
const STATES = { importat: 'Importat', duplicat: 'Deja existent', fara_xml: 'Fără XML', invalid: 'XML invalid' };
const SOURCES = { manual: 'Fișier', 'e-mail': 'E-mail' };
const BATCH_FILES = 30;
const BATCH_BYTES = 10 * 1024 * 1024;     // the server refuses a request above 17 MB
const FILE_BYTES = 12 * 1024 * 1024;
const when = (iso) => (iso ? new Date(iso).toLocaleString('ro-RO', { dateStyle: 'short', timeStyle: 'short' }) : '');

export function bindExtractsIntake({ api, context, onAssociate }) {
  const dialog = $('ade-intake-dialog'); const form = $('ade-intake-settings');
  let grid = null; let busy = false; let mail = { configured: false };
  const message = (value, kind = 'info') => showMessage($('ade-intake-message'), value, kind);
  const report = (error) => { console.error('[ADE intake]', error); message(error.message, 'error'); };
  const allowed = () => ['collect', 'catalog'].every((right) => context()?.permissions.includes(right));

  async function run(action) {
    if (busy) return false;
    busy = true; dialog.inert = true;
    try { return await action(); }
    catch (error) { report(error); return false; }
    finally { busy = false; dialog.inert = false; }
  }

  function buildGrid() {
    grid?.destroy();
    grid = new DataGrid($('ade-intake-grid'), { rows: [], columns: [
      text('Cand', 'Încărcat', 110), text('Sursa', 'Sursa', 70), text('FISIER', 'Fișier', 330), text('Stare', 'Stare', 110),
      { ...text('RANDURI', 'Operațiuni noi', 100), valueType: ValueType.Number, align: 'right' },
      text('MESAJ', 'Detalii', 300), text('EMAIL_DE', 'Expeditor e-mail', 180), text('INCARCAT_DE', 'Utilizator', 170)],
    rowKey: 'ID', autoSize: false, headerHeight: 32, mobileRowScale: 1.2, footer: true, footerCaption: '{0} înregistrări',
    theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'modern', layout: { fill: 'MESAJ' } });
  }
  async function loadHistory() {
    const result = await api('/api/adechit/extracts/history');
    grid.setRows(result.rows.map((row) => ({ ...row, Cand: when(row.INCARCAT_LA), Sursa: SOURCES[row.SURSA] || row.SURSA,
      Stare: STATES[row.STARE] || row.STARE })));
  }

  function renderMailState() {
    $('ade-intake-mail-state').textContent = mail.configured
      ? `${mail.user} · ${mail.host}` + (mail.last_check ? `\nUltima verificare: ${when(mail.last_check)} — ${mail.last_result}` : '\nNu a fost verificată încă.')
      : 'Căsuța de e-mail nu este configurată. Folosiți «Setări e-mail…».';
    $('ade-intake-fetch').disabled = !mail.configured || !mail.has_password;
  }
  async function loadMail() { mail = await api('/api/adechit/extracts/mail'); renderMailState(); }
  function fillForm() {
    $('ade-intake-host').value = mail.host || ''; $('ade-intake-user').value = mail.user || '';
    $('ade-intake-password').value = ''; $('ade-intake-password').placeholder = mail.has_password ? 'Neschimbată' : '';
    $('ade-intake-folder-name').value = mail.folder || 'INBOX'; $('ade-intake-sender').value = mail.sender || '';
    $('ade-intake-name-part').value = mail.name_part || 'ExtrasEP'; $('ade-intake-since').value = mail.since || '';
    const preset = $('ade-intake-preset'); preset.value = [...preset.options].some((item) => item.value === mail.host) ? mail.host : '';
  }
  function showSettings(visible) {
    form.hidden = !visible; $('ade-intake-mail-settings').setAttribute('aria-expanded', String(visible));
    if (visible) { fillForm(); $('ade-intake-host').focus(); }
  }
  const formBody = () => ({ host: $('ade-intake-host').value.trim(), user: $('ade-intake-user').value.trim(),
    password: $('ade-intake-password').value, folder: $('ade-intake-folder-name').value.trim(),
    sender: $('ade-intake-sender').value.trim(), name_part: $('ade-intake-name-part').value.trim(),
    since: $('ade-intake-since').value });
  async function saveSettings() {
    mail = await api('/api/adechit/extracts/mail', { method: 'POST', body: JSON.stringify(formBody()) });
    renderMailState(); showSettings(false); message('Setările e-mailului au fost salvate.');
    return true;
  }

  const countText = (summary) => `${summary.imported} importate (${summary.rows} operațiuni noi), ${summary.duplicates} deja existente`
    + (summary.unreadable ? `, ${summary.unreadable} fără XML citibil` : '');
  async function upload(list) {
    const pdfs = [...list].filter((file) => /\.pdf$/i.test(file.name));
    const tooBig = pdfs.filter((file) => file.size > FILE_BYTES); const files = pdfs.filter((file) => file.size <= FILE_BYTES);
    if (!files.length) { message(list.length ? 'Nu am găsit fișiere PDF de încărcat.' : '', list.length ? 'error' : 'info'); return false; }
    const total = { imported: 0, rows: 0, duplicates: 0, unreadable: 0 };
    let done = 0; let batch = []; let size = 0;
    const send = async () => {
      if (!batch.length) return;
      const body = new FormData(); batch.forEach((file) => body.append('files', file, file.name));
      const result = await api('/api/adechit/extracts/upload', { method: 'POST', body });
      for (const key of Object.keys(total)) total[key] += result.summary[key];
      done += batch.length; batch = []; size = 0;
      message(`Se încarcă… ${done} din ${files.length}`);
    };
    try {
      for (const file of files) {
        if (batch.length >= BATCH_FILES || (batch.length && size + file.size > BATCH_BYTES)) await send();
        batch.push(file); size += file.size;
      }
      await send();
    } catch (error) {
      await loadHistory().catch(report);
      throw new Error(`${error.message} Au fost procesate ${done} din ${files.length} fișiere.`);
    }
    await loadHistory();
    const skipped = (list.length - pdfs.length) + tooBig.length;
    message(`${files.length} fișiere citite: ${countText(total)}.` + (skipped ? ` ${skipped} fișiere ignorate (nu sunt PDF sau depășesc 12 MB).` : ''),
      total.unreadable ? 'error' : 'info');
    return true;
  }
  async function fetchMail() {
    message('Se verifică e-mailul…');
    const result = await api('/api/adechit/extracts/mail/fetch', { method: 'POST', body: JSON.stringify({}) });
    await Promise.all([loadMail(), loadHistory()]);
    const found = result.files.length;
    message(`${result.scanned} mesaje noi verificate. ` + (found ? `${found} extrase găsite: ${countText(result.summary)}.` : 'Niciun extras nou.')
      + (result.more ? ' Mai sunt mesaje de verificat: apăsați din nou.' : ''), result.summary.unreadable ? 'error' : 'info');
    return true;
  }
  const pick = (input) => { input.value = ''; input.click(); };
  for (const id of ['ade-intake-files', 'ade-intake-folder']) {
    $(id).addEventListener('change', (event) => { const list = [...event.target.files]; event.target.value = ''; run(() => upload(list)); });
  }
  $('ade-intake-pick-files').addEventListener('click', () => pick($('ade-intake-files')));
  $('ade-intake-pick-folder').addEventListener('click', () => pick($('ade-intake-folder')));
  $('ade-intake-fetch').addEventListener('click', () => run(fetchMail));
  $('ade-intake-mail-settings').addEventListener('click', () => showSettings(form.hidden));
  $('ade-intake-settings-cancel').addEventListener('click', () => showSettings(false));
  $('ade-intake-preset').addEventListener('change', (event) => { if (event.target.value) $('ade-intake-host').value = event.target.value; });
  form.addEventListener('submit', (event) => { event.preventDefault(); run(saveSettings); });
  $('ade-intake-settings-test').addEventListener('click', () => run(async () => {
    if (!mail.configured || $('ade-intake-password').value || $('ade-intake-host').value.trim() !== mail.host
      || $('ade-intake-user').value.trim() !== mail.user) await saveSettings();
    message('Se testează conexiunea…');
    message((await api('/api/adechit/extracts/mail/test', { method: 'POST', body: JSON.stringify({}) })).message);
    return true;
  }));
  $('ade-intake-associate').addEventListener('click', () => { if (busy) return; close(); onAssociate(); });

  async function open() {
    if (dialog.open) return;
    message(allowed() ? '' : 'Încărcarea extraselor necesită drepturile de încasare și de modificare a plătitorilor.', allowed() ? 'info' : 'error');
    mail = { configured: false }; renderMailState(); form.hidden = true; dialog.showModal(); buildGrid();
    const usable = allowed();
    ['ade-intake-pick-files', 'ade-intake-pick-folder', 'ade-intake-mail-settings'].forEach((id) => { $(id).disabled = !usable; });
    if (!usable) { $('ade-intake-fetch').disabled = true; return; }
    await run(async () => { await Promise.all([loadMail(), loadHistory()]); });
  }
  function cleanup() { grid?.destroy(); grid = null; }
  function close() { if (!busy && dialog.open) { dialog.close(); cleanup(); } }
  $('ade-intake-close').addEventListener('click', close);
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  addEventListener('pagehide', cleanup, { once: true });
  return { open };
}
