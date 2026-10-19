import { el, TextBox } from './ui.js';
import { subText, subHundredths } from './receipt-number.js';

// The optional fractional NIR/FCS number stays inside the number field.
export function documentSubnumber({ family, owner, state, check, report }) {
  let subBox = null, subGroup = null;
  function open(text = '') {
    if (!subGroup) return;
    subGroup.hidden = false;
    if (text) { subBox.setValue(text); state.draft.header.NumarSub = subHundredths(text); state.draft.autoNumber = false; state.dirty = true; }
    subBox.focus();
  }
  function close() {
    if (!subGroup) return;
    subGroup.hidden = true; subBox.setValue(''); state.draft.header.NumarSub = 0;
    state.draft.autoNumber = false; state.dirty = true;
  }
  return {
    controls: () => ({ subBox, subGroup }),
    leave(text, box, event) {
      if (subGroup && !subGroup.hidden && event?.relatedTarget === subBox.input) return;
      check().catch(report);
    },
    attach(item, header) {
      const main = item.input._box, input = item.input, row = el('div', { className: 'vc-number-row' });
      main.element.replaceWith(row); row.append(main.element);
      subGroup = el('div', { className: 'vc-number-sub' }, el('span', { textContent: ',' }));
      subBox = owner.own(new TextBox(subGroup, { type: 'text', maxLength: 2,
        ariaLabel: `Sub-număr ${family === 'storno' ? 'FCS' : 'NIR'}`, textAlign: 'right',
        onInput: (text, box) => {
          const digits = text.replace(/\D/g, ''); if (digits !== text) box.setValue(digits);
          state.draft.header.NumarSub = subHundredths(digits); state.draft.autoNumber = false; state.dirty = true;
        }, onLeave: text => { if (!text) close(); check().catch(report); } }));
      row.append(subGroup); subGroup.hidden = !(Number(header.NumarSub) > 0);
      if (!subGroup.hidden) subBox.setValue(subText(header.NumarSub));
      input.addEventListener('keydown', event => {
        if (event.key !== ',' && event.key !== '.') return;
        event.preventDefault(); if (/\d/.test(input.value)) open();
      });
      input.addEventListener('input', () => {
        const at = input.value.search(/[.,]/); if (at < 0) return;
        const base = input.value.slice(0, at).replace(/\D/g, ''), fraction = input.value.slice(at + 1).replace(/\D/g, '');
        main.setValue(base); state.draft.header.Numar = main.text;
        if (!base) return;
        open(fraction.slice(0, 2));
        if (fraction.length > 2) main.setError('Sub-numărul poate avea cel mult 2 cifre.');
      });
      subBox.input.addEventListener('keydown', event => {
        if (event.key === 'Backspace' && !subBox.text) { event.preventDefault(); close(); input.focus(); }
      });
    },
  };
}
