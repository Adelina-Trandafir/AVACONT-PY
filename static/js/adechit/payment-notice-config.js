// Per-subunit note editor. Only the server's allowlisted HTML is inserted into the DOM.
import { showMessage } from '../portal/messages.js';

export function bindNoticeConfig({ api, context, report }) {
  const $ = (id) => document.getElementById(id);
  const dialog = $('ade-notice-config');
  const form = $('ade-notice-config-form');
  const editor = $('ade-notice-editor');
  const source = $('ade-notice-source');
  const mode = $('ade-notice-source-mode');
  const toolbar = $('ade-notice-toolbar');
  const message = $('ade-notice-config-message');
  let version = 0;
  let visualChanged = false;
  let saving = false;
  let selection = null;
  let attempt = null;

  function error(text = '') { showMessage(message, text, 'error'); }
  function rememberSelection() {
    const selected = window.getSelection();
    if (selected.rangeCount && editor.contains(selected.anchorNode) && editor.contains(selected.focusNode)) {
      selection = selected.getRangeAt(0).cloneRange();
    }
  }
  document.addEventListener('selectionchange', () => { if (dialog.open) rememberSelection(); });
  function execute(command, value = null) {
    editor.focus();
    if (selection) {
      const selected = window.getSelection(); selected.removeAllRanges(); selected.addRange(selection);
    }
    document.execCommand('styleWithCSS', false, false);
    document.execCommand(command, false, value);
    visualChanged = true; attempt = null;
    rememberSelection();
  }
  toolbar.querySelectorAll('[data-notice-command]').forEach((button) => {
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', () => execute(button.dataset.noticeCommand));
  });
  for (const [id, command] of [['ade-notice-font', 'fontName'], ['ade-notice-size', 'fontSize'], ['ade-notice-color', 'foreColor']]) {
    $(id).addEventListener('change', (event) => execute(command, event.target.value));
  }
  editor.addEventListener('input', () => { visualChanged = true; attempt = null; });
  source.addEventListener('input', () => { attempt = null; });
  editor.addEventListener('paste', (event) => {
    event.preventDefault();
    execute('insertText', event.clipboardData.getData('text/plain'));
  });
  editor.addEventListener('drop', (event) => event.preventDefault());

  function syncSource() {
    if (visualChanged) { source.value = editor.innerHTML; visualChanged = false; }
  }
  mode.addEventListener('change', async () => {
    syncSource();
    mode.disabled = true;
    try {
      if (!mode.checked) {
        source.disabled = true;
        const result = await api('/api/adechit/payment-notice/preview', { method: 'POST', body: JSON.stringify({ html: source.value }) });
        editor.innerHTML = result.preview;
        selection = null;
      }
      editor.hidden = mode.checked; toolbar.hidden = mode.checked; source.hidden = !mode.checked;
      error();
    } catch (failure) { console.error('[ade] notice preview failed', failure); mode.checked = true; error(failure.message); }
    finally { mode.disabled = false; source.disabled = false; }
  });
  dialog.addEventListener('cancel', (event) => { if (saving) event.preventDefault(); });
  $('ade-notice-config-cancel').addEventListener('click', () => { if (!saving) dialog.close(); });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving || mode.disabled) return;
    syncSource();
    if (source.value.length > 30000) { error('Textul HTML trebuie să aibă cel mult 30.000 de caractere.'); return; }
    attempt ||= { key: crypto.randomUUID(), body: JSON.stringify({ html: source.value, version }) };
    saving = true;
    $('ade-notice-config-save').disabled = true; $('ade-notice-config-cancel').disabled = true;
    editor.contentEditable = 'false'; source.disabled = true; mode.disabled = true;
    toolbar.querySelectorAll('button, select').forEach((control) => { control.disabled = true; });
    error();
    try {
      await api('/api/adechit/payment-notice/config', { method: 'POST', ...attempt });
      dialog.close();
    } catch (failure) { console.error('[ade] notice configuration save failed', failure); error(failure.message); }
    finally {
      saving = false;
      $('ade-notice-config-save').disabled = false; $('ade-notice-config-cancel').disabled = false;
      editor.contentEditable = 'true'; source.disabled = false; mode.disabled = false;
      toolbar.querySelectorAll('button, select').forEach((control) => { control.disabled = false; });
    }
  });

  return { async open() {
    if (!context()?.permissions?.includes('catalog')) { report(new Error('Nu aveți dreptul de modificare a configurării.')); return; }
    try {
      const result = await api('/api/adechit/payment-notice/config');
      version = result.version; source.value = result.html; editor.innerHTML = result.preview;
      mode.checked = false; source.hidden = true; editor.hidden = false; toolbar.hidden = false;
      selection = null; visualChanged = false; attempt = null; error();
      dialog.showModal(); editor.focus();
    } catch (failure) { console.error('[ade] notice configuration load failed', failure); report(failure); }
  } };
}
