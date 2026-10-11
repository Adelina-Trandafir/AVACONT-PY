// The application's one confirmation box (replaces window.confirm everywhere): the page behind it is blurred and
// the question sits in the middle. «No» / «Renunta» is on the left, «Yes» / «OK» on the right.
// `warning: true` paints the box as a serious warning (red header). Escape and the backdrop answer No. Usage: if (!await confirmBox('Intrebare?', { title, yes, no })) return;
let styled = false;
function ensureStyle() {
  if (styled) return; styled = true;
  const k_link = document.createElement('link'); k_link.rel = 'stylesheet'; k_link.href = '/static/css/confirm-box.css';
  document.head.append(k_link);
}

export function confirmBox(k_text, { title = 'Confirmare', yes = 'Da', no = 'Nu', warning = false } = {}) {
  ensureStyle();
  return new Promise((k_resolve) => {
    const k_dialog = document.createElement('dialog');
    k_dialog.className = warning ? 'confirm-box confirm-box--warning' : 'confirm-box';
    k_dialog.setAttribute('aria-label', title);
    const k_header = document.createElement('header');
    const k_title = document.createElement('h2'); k_title.textContent = title; k_header.append(k_title);
    const k_body = document.createElement('p'); k_body.className = 'confirm-box__text'; k_body.textContent = k_text;
    const k_footer = document.createElement('footer');
    const k_finish = (k_answer) => { k_dialog.close(); k_dialog.remove(); k_resolve(k_answer); };
    for (const [k_label, k_answer] of [[no, false], [yes, true]]) {
      const k_button = document.createElement('button'); k_button.type = 'button';
      k_button.textContent = k_label; k_button.addEventListener('click', () => k_finish(k_answer)); k_footer.append(k_button);
    }
    k_dialog.addEventListener('cancel', (k_event) => { k_event.preventDefault(); k_finish(false); });
    k_dialog.addEventListener('mousedown', (k_event) => { if (k_event.target === k_dialog) k_finish(false); });
    k_dialog.append(k_header, k_body, k_footer); document.body.append(k_dialog); k_dialog.showModal();
    k_footer.lastElementChild.focus();
  });
}

// A warning box that also asks for a reason: resolves with the trimmed reason, or null on No / Escape / outside click.
// The «Yes» button stays disabled while the reason is empty. Usage: const k_reason = await reasonBox('Intrebare?', { title, label, yes, no });
export function reasonBox(k_text, { title = 'Confirmare', label = 'Motiv', yes = 'Da', no = 'Nu', maxLength = 255 } = {}) {
  ensureStyle();
  return new Promise((k_resolve) => {
    const k_dialog = document.createElement('dialog');
    k_dialog.className = 'confirm-box confirm-box--warning';
    k_dialog.setAttribute('aria-label', title);
    const k_header = document.createElement('header');
    const k_title = document.createElement('h2'); k_title.textContent = title; k_header.append(k_title);
    const k_body = document.createElement('p'); k_body.className = 'confirm-box__text'; k_body.textContent = k_text;
    const k_field = document.createElement('label'); k_field.className = 'confirm-box__field'; k_field.textContent = label;
    const k_input = document.createElement('textarea'); k_input.rows = 3; k_input.maxLength = maxLength; k_input.required = true;
    k_field.append(k_input);
    const k_footer = document.createElement('footer');
    const k_finish = (k_answer) => { k_dialog.close(); k_dialog.remove(); k_resolve(k_answer); };
    const k_no = document.createElement('button'); k_no.type = 'button'; k_no.textContent = no;
    k_no.addEventListener('click', () => k_finish(null));
    const k_yes = document.createElement('button'); k_yes.type = 'button'; k_yes.textContent = yes; k_yes.disabled = true;
    k_yes.addEventListener('click', () => { const k_reason = k_input.value.trim(); if (k_reason) k_finish(k_reason); });
    k_input.addEventListener('input', () => { k_yes.disabled = !k_input.value.trim(); });
    k_footer.append(k_no, k_yes);
    k_dialog.addEventListener('cancel', (k_event) => { k_event.preventDefault(); k_finish(null); });
    k_dialog.addEventListener('mousedown', (k_event) => { if (k_event.target === k_dialog) k_finish(null); });
    k_dialog.append(k_header, k_body, k_field, k_footer); document.body.append(k_dialog); k_dialog.showModal();
    k_input.focus();
  });
}

// A box with a list of choices (the phone's group picker): resolves with the chosen value, or null on «Renunta» / Escape / outside click.
export function pickBox(title, items, { no = 'Renunță' } = {}) {
  ensureStyle();
  return new Promise((k_resolve) => {
    const k_dialog = document.createElement('dialog');
    k_dialog.className = 'confirm-box confirm-box--pick';
    k_dialog.setAttribute('aria-label', title);
    const k_header = document.createElement('header');
    const k_title = document.createElement('h2'); k_title.textContent = title; k_header.append(k_title);
    const k_list = document.createElement('div'); k_list.className = 'confirm-box__list';
    const k_footer = document.createElement('footer');
    const k_finish = (k_answer) => { k_dialog.close(); k_dialog.remove(); k_resolve(k_answer); };
    for (const k_item of items) {
      const k_button = document.createElement('button'); k_button.type = 'button'; k_button.textContent = k_item.label;
      k_button.addEventListener('click', () => k_finish(k_item.value)); k_list.append(k_button);
    }
    if (!items.length) { const k_empty = document.createElement('p'); k_empty.className = 'confirm-box__text'; k_empty.textContent = 'Nu există alte grupe.'; k_list.append(k_empty); }
    const k_cancel = document.createElement('button'); k_cancel.type = 'button'; k_cancel.textContent = no;
    k_cancel.addEventListener('click', () => k_finish(null)); k_footer.append(k_cancel);
    k_dialog.addEventListener('cancel', (k_event) => { k_event.preventDefault(); k_finish(null); });
    k_dialog.addEventListener('mousedown', (k_event) => { if (k_event.target === k_dialog) k_finish(null); });
    k_dialog.append(k_header, k_list, k_footer); document.body.append(k_dialog); k_dialog.showModal();
  });
}
