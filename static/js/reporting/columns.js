import { el } from './report-dialog.js';

export function chooseColumns(options, owner) {
  return new Promise(resolve => {
    const dialog = el('dialog', { className: 'confirm-box report-columns', 'aria-label': 'Export Excel' });
    const content = el('div', { className: 'report-columns-content' });
    const all = el('input', { type: 'checkbox', checked: true });
    content.append(el('label', {}, all, 'Toate coloanele'));
    const list = el('div', { className: 'report-columns-list' });
    const choices = options.map(([key, label]) => {
      const input = el('input', { type: 'checkbox', value: key, checked: true });
      list.append(el('label', {}, input, el('span', { textContent: label })));
      return input;
    });
    const save = el('button', { type: 'button', textContent: 'Exportă Excel' });
    const cancel = el('button', { type: 'button', textContent: 'Renunță' });
    let closed = false;
    const close = result => { if (closed) return; closed = true; dialog.close(); dialog.remove(); resolve(result); };
    const sync = () => {
      const count = choices.filter(input => input.checked).length;
      all.checked = count === choices.length;
      all.indeterminate = count > 0 && count < choices.length;
      save.disabled = choices.length > 0 && count === 0;
    };
    all.addEventListener('change', () => { choices.forEach(input => { input.checked = all.checked; }); sync(); });
    choices.forEach(input => input.addEventListener('change', sync));
    save.addEventListener('click', () => close(choices.filter(input => input.checked).map(input => input.value)));
    cancel.addEventListener('click', () => close(null));
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(null); });
    owner?.own(() => close(null));
    content.append(list);
    dialog.append(el('header', {}, el('h2', { textContent: 'Export Excel' })), content, el('footer', {}, cancel, save));
    document.body.append(dialog); dialog.showModal();
  });
}
