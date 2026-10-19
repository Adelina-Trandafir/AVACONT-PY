// One report operation per page, from the initial gesture through the visible result.
import { el, reportDialog } from './report-dialog.js';
import { messageBox } from '../utils/confirm-box.js';

let active = null;

export function beginReport(title, owner, details) {
  if (active) {
    if (!active.view.closed) active.view.dialog.focus();
    else if (!active.notice) active.notice = messageBox(
      'Raportul anterior este încă în pregătire. Așteptați finalizarea lui înainte să porniți alt raport.',
      { title: 'Se pregătește un raport' });
    return null;
  }
  const view = reportDialog(title, owner, details);
  const task = { view };
  active = task;
  const spinner = el('span', { className: 'report-loading-spinner', 'aria-hidden': 'true' });
  const heading = el('h3', { textContent: 'Se pregătește raportul…' });
  const text = el('p', { textContent: 'Vă rugăm să așteptați. Raportul va apărea aici imediat ce este gata.' });
  const close = el('button', { type: 'button', textContent: 'Închide' });
  close.addEventListener('click', view.close);
  const actions = el('div', { className: 'report-loading-actions' }, close);
  const panel = el('section', { className: 'report-loading', role: 'status', 'aria-live': 'polite' },
    el('div', { className: 'report-loading-card' }, spinner, heading, text, actions));
  view.body.append(panel);
  view.dialog.setAttribute('aria-busy', 'true');

  return {
    view,
    async paint() {
      // Allow the browser to paint before even a synchronous local PDF builder runs.
      await new Promise(resolve => {
        const timer = setTimeout(resolve, 100);
        requestAnimationFrame(() => { clearTimeout(timer); setTimeout(resolve, 0); });
      });
    },
    message(value) { heading.textContent = value; },
    ready() {
      const focused = panel.contains(document.activeElement) || document.activeElement === view.dialog;
      panel.remove(); view.dialog.setAttribute('aria-busy', 'false');
      if (focused) view.dialog.querySelector('.report-view-toolbar button:not(:disabled)')?.focus({ preventScroll: true });
    },
    fail(error, download) {
      if (view.closed) return;
      spinner.hidden = true;
      panel.classList.add('report-loading--error');
      panel.setAttribute('role', 'alert');
      heading.textContent = 'Raportul nu a putut fi pregătit';
      text.textContent = error.message || 'Închideți fereastra și încercați din nou.';
      if (download) {
        const button = el('button', { type: 'button', textContent: 'Descarcă PDF' });
        button.addEventListener('click', download); actions.prepend(button);
      }
      view.dialog.setAttribute('aria-busy', 'false');
    },
    finish() { task.notice?.close(); if (active === task) active = null; },
  };
}
