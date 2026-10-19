import { subText } from './receipt-number.js';

// Suggestions are read-only. Saving the document rechecks its family's number under the context lock.
export function documentNumbers({ family, state, allocation, selection, api, aborted, controls, error, track }) {
  let sequence = 0;
  return {
    invalidate() { ++sequence; },
    async check(offer = false) {
      if (!['nir', 'storno', 'bp', 'bt', 'bs'].includes(family) || state.mode === 'view' || allocation?.multi()) return;
      const draft = state.draft, automatic = family === 'storno' && offer;
      const currentNumber = draft.header.Numar || '', currentSub = Number(draft.header.NumarSub) || 0;
      const raw = automatic ? '' : currentNumber, sub = automatic ? 0 : currentSub, token = ++sequence;
      const day = draft.header.Data || '';
      const context = state.mode === 'add' && allocation ? allocation.numberSelection() : selection();
      const answer = await track(api(`/api/vercon/doc/${family}/number?${new URLSearchParams(context)}&number=${encodeURIComponent(raw)}&sub=${sub}&date=${encodeURIComponent(day)}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`, { headers: { 'X-Vercon-Version': '' } }));
      if (aborted() || draft !== state.draft || token !== sequence || draft.header.Data !== day || String(draft.header.Numar || '') !== String(currentNumber) || (Number(draft.header.NumarSub) || 0) !== currentSub) return;
      const { input, subGroup, subBox } = controls();
      if (offer && !raw) {
        draft.header.Numar = String(answer.suggested);
        if (input?._box) input._box.setValue(draft.header.Numar); else if (input) input.value = draft.header.Numar;
      }
      if (automatic) {
        draft.header.NumarSub = Number(answer.suggested_sub) || 0;
        if (subGroup) subGroup.hidden = !draft.header.NumarSub;
        subBox?.setValue(subText(draft.header.NumarSub));
      }
      const custody = ['bp', 'bt', 'bs'].includes(family);
      const message = !answer.valid ? custody ? 'Introduceți un număr întreg pozitiv.' : 'Introduceți un număr întreg pozitiv (și un sub-număr de cel mult 2 cifre).' : answer.duplicate ? 'Numărul este deja folosit. Alegeți alt număr.' : '';
      const sink = error(); if (sink) sink.textContent = message;
      if (input && !message) input.title = offer && !raw ? 'Propus automat. Verificat din nou la salvare.' : !raw ? `Următorul număr disponibil: ${answer.suggested}.` : answer.valid && !answer.duplicate ? 'Număr disponibil. Verificat din nou la salvare.' : `Număr disponibil propus: ${answer.suggested}.`;
    },
  };
}
