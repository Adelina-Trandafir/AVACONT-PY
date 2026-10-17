// Terminal session exit: no questions, no draft protection, no remaining popups.
let exiting = false;
const windows = new Set();

export function trackSessionWindow(target) {
  for (const item of windows) if (item.closed) windows.delete(item);
  if (target) {
    if (exiting) target.close();
    else windows.add(target);
  }
  return target;
}

export function beginSessionExit() {
  if (exiting) return;
  exiting = true;
  if (document.body) document.body.inert = true;
  window.dispatchEvent(new Event('app:session-expired'));
  for (const target of windows) { try { target.close(); } catch { /* Already closed. */ } }
  windows.clear();
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
}
