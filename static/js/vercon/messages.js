// VERCON notifications use the existing modal messageBox, like confirmations.
// Keep the existing action-bar status and session journal; never open a toast.
import { messageBox } from '../utils/confirm-box.js';
import { showMessage as recordMessage } from '../portal/messages.js';
export { messageHistory } from '../portal/messages.js';

const active = new WeakMap();
const journals = new WeakMap();
const titles = { info: 'Informare', success: 'Operație finalizată', warning: 'Atenție', error: 'Eroare' };

function present(text, level, { source = null, title, ownerDocument = source?.ownerDocument || document } = {}) {
  active.get(ownerDocument)?.box.close();
  const box = messageBox(text, { title: title || titles[level] || titles.info,
    warning: level === 'error' || level === 'warning', ownerDocument });
  const entry = { box, source };
  active.set(ownerDocument, entry);
  // Closing a screen also closes its message; global notices have their own lifecycle.
  const observer = source ? new MutationObserver(() => { if (!source.isConnected) box.close(); }) : null;
  observer?.observe(ownerDocument.documentElement, { childList: true, subtree: true });
  const onPageHide = () => box.close();
  ownerDocument.defaultView.addEventListener('pagehide', onPageHide, { once: true });
  box.closed.then(() => {
    observer?.disconnect();
    ownerDocument.defaultView.removeEventListener('pagehide', onPageHide);
    if (active.get(ownerDocument) === entry) active.delete(ownerDocument);
  });
  return box;
}

export function showMessage(element, text, level = 'info', options = {}) {
  recordMessage(element, text, level, { ...options, tooltip: false });
  if (!text) {
    const previous = active.get(element.ownerDocument);
    if (previous?.source === element) previous.box.close();
    return null;
  }
  if (options.tooltip === false) return null;
  return present(text, level, { source: element, ...options });
}

// No inline host for application-wide notices. The detached node is journal-only.
export function notify(text, level = 'info', options = {}) {
  const doc = options.ownerDocument || document;
  let journal = journals.get(doc);
  if (!journal) { journal = doc.createElement('span'); journals.set(doc, journal); }
  recordMessage(journal, text, level, { inline: false, tooltip: false });
  return text ? present(text, level, options) : null;
}
