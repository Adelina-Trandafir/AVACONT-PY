// VERCON, VC06-38: the small window shown when «FOLOSEȘTE DENUMIREA DIN EFACTURA» is chosen on a NIR line that came from an e-Factura.
// The Administrator sees what the supplier wrote (name and UM editable, the name over several rows because some are very long), the CPV of the
// invoice line (read-only when the invoice has one; otherwise typed or picked from the CPV nomenclator, as in «Produse») and the accountant's note.
// «Salvează» answers { name, um, cpv }; «Renunță», Esc and the close button answer null (the line stays as it was).
import { ModalFrame } from '../components/modal-frame/modal-frame.js';
import { confirmBox } from '../utils/confirm-box.js';
import { mountCpvPicker } from './cpv-picker.js';
import { el, scope, statusBar } from './ui.js';

const NAME_MAX = 255, UM_MAX = 50;

export function efacturaLineDialog({ host, api, sel, note = () => '' }) {
  let open = null;

  function edit(line) {
    if (open) return open.done;
    const owner = scope(), fixedCpv = String(line.ef_cpv ?? '').trim();
    let busy = false, closed = false, picker = null, resolve;
    const done = new Promise(answer => { resolve = answer; });
    const area = (rows, attrs) => el('textarea', { rows, ...attrs });
    const nameBox = area(5, { value: String(line.ef_name ?? ''), maxLength: NAME_MAX, 'aria-label': 'Denumire', className: 'vc-efl__name' });
    const umBox = el('input', { type: 'text', value: String(line.ef_um ?? ''), maxLength: UM_MAX, 'aria-label': 'UM', autocomplete: 'off' });
    const cpvBox = el('input', { type: 'text', value: fixedCpv, readOnly: !!fixedCpv, maxLength: 20, 'aria-label': 'CPV', autocomplete: 'off',
      placeholder: fixedCpv ? '' : 'Tastați codul sau alegeți-l din nomenclator' });
    const pickButton = fixedCpv ? null : el('button', { type: 'button', className: 'vc-efl__pick', textContent: 'Alege…', title: 'Alege codul din nomenclatorul CPV' });
    const noteBox = area(4, { readOnly: true, value: note() || '', placeholder: 'Contabilul nu a scris nicio notă.', 'aria-label': 'Notă contabil' });
    const status = el('p', { className: 'vc-efi__status vc-save-status', role: 'status' });
    const cancelButton = el('button', { type: 'button', textContent: 'Renunță' });
    const saveButton = el('button', { type: 'button', className: 'is-primary', textContent: 'Salvează' });
    const label = (text, ...controls) => el('label', { className: 'vc-field' }, el('span', { textContent: text }), ...controls);
    const section = el('section', { className: 'vc-efi vc-efl', 'aria-label': 'Produs din eFactura' },
      el('div', { className: 'vc-efi__body vc-efl__body' },
        label('Denumire *', nameBox),
        el('div', { className: 'vc-efl__row' }, label('UM *', umBox),
          label('CPV', fixedCpv ? cpvBox : el('span', { className: 'vc-efl__cpv' }, cpvBox, pickButton))),
        label('Notă contabil', noteBox)),
      statusBar(el('footer'), { start: [cancelButton], status, end: [saveButton] }));
    host.append(section);
    const frame = new ModalFrame(section, { title: 'Produs din eFactura', width: 640, height: 560, onRequestClose: () => close(null) });
    const say = (text = '', bad = false) => { status.textContent = text; status.dataset.state = bad ? 'error' : 'info'; };
    const controls = () => { cancelButton.disabled = saveButton.disabled = busy; if (pickButton) pickButton.disabled = busy; };
    function close(value) {
      if (closed || busy) return;
      closed = true; picker?.destroy(); picker = null; owner.clear(); frame.destroy(); section.remove(); open = null; resolve(value);
    }

    pickButton?.addEventListener('click', () => {
      if (busy || picker) return;
      section.inert = true;
      picker = mountCpvPicker(host, { api, sel, name: nameBox.value.trim() || 'Produs nou', current: cpvBox.value.trim() || null, note: note() || null,
        onPick: code => { cpvBox.value = code; },
        onClose: () => { picker = null; if (!closed) { section.inert = false; cpvBox.focus(); } } });
    });
    cancelButton.addEventListener('click', () => close(null));
    async function save() {
      if (busy || closed) return;
      const name = nameBox.value.trim(), um = umBox.value.trim(), cpv = cpvBox.value.trim();
      if (!name) { say('Introduceți denumirea produsului.', true); nameBox.focus(); return; }
      if (!um) { say('Introduceți unitatea de măsură (UM).', true); umBox.focus(); return; }
      busy = true; controls();
      try {
        if (cpv && !fixedCpv) {
          say('Se verifică codul CPV…');
          const query = new URLSearchParams(Object.entries(sel()).filter(([, value]) => value != null && value !== ''));
          try { await api(`/api/vercon/cpv/${encodeURIComponent(cpv)}?${query}`); }
          catch (error) {
            if (error.name === 'AbortError') throw error;
            say(error.message || 'Codul CPV nu a putut fi verificat.', true); cpvBox.focus(); return;
          }
        }
        if (!cpv) {
          say();
          busy = false; controls();
          if (!await confirmBox('Produsul NU va avea CPV. Continuați fără CPV?', { title: 'Produs fără CPV', yes: 'Continuă fără CPV', no: 'Alege CPV', warning: true })) { if (!closed) cpvBox.focus(); return; }
          busy = true; controls();
        }
        busy = false; close({ name, um, cpv: cpv || null });
      } catch (error) {
        if (error.name !== 'AbortError') say(error.message || 'Datele nu au putut fi salvate.', true);
      } finally { if (!closed) { busy = false; controls(); } }
    }
    saveButton.addEventListener('click', () => void save());
    section.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement && !event.target.readOnly) { event.preventDefault(); void save(); }
    });
    frame.open(); nameBox.focus(); nameBox.select();
    open = { done, destroy: () => { busy = false; close(null); } };
    return done;
  }

  return { edit, destroy() { open?.destroy(); } };
}
