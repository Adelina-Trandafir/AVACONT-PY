// Duplicate suppliers (the same firm typed under two fiscal codes) and their merge into one. Shown when the Intrari forms open
// and from «Intrări > Parteneri dublați…». Server: routes/vercon/supplier_merge.py, supplier_lookup.py.
// Only suppliers that appear in VERCON documents or received e-invoices are listed (never the whole partner list).
import { el, statusBar } from './ui.js';
import { confirmBox, messageBox } from '../utils/confirm-box.js';

const newKey = () => (globalThis.crypto?.randomUUID?.().replaceAll('-', '') || `${Date.now()}${Math.random().toString(16).slice(2)}`).slice(0, 64);
const plain = text => String(text ?? '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
const anafText = item => item.found
  ? `ANAF: ${item.denumire}\n${item.adresa || '—'}\nNr. Reg. Com.: ${item.nr_reg_com || '—'}`
  : `ANAF: ${item.reason}`;

// Resolves true when at least one merge was made (the page behind must reload its lists), false otherwise.
export async function showSupplierDuplicates({ api, sel, auto = false }) {
  const query = new URLSearchParams(Object.entries(sel()).filter(([, value]) => value != null && value !== ''));
  const read = () => api(`/api/vercon/suppliers/duplicates?${query}`);
  let answer = await read();
  if (auto && (!answer.can_merge || !answer.groups.length)) return false;
  if (!answer.groups.length) {
    await messageBox(answer.can_merge ? 'Nu există parteneri dublați în documentele VERCON sau în e-Facturi.' : 'Nu aveți dreptul să combinați parteneri.', { title: 'Parteneri dublați' }).closed;
    return false;
  }
  const dialog = el('dialog', { className: 'vc-efi vc-dup', 'aria-label': 'Parteneri dublați' });
  const status = el('p', { className: 'vc-efi__status vc-save-status', role: 'status' });
  const list = el('div', { className: 'vc-dup__list' });
  const closeButton = el('button', { type: 'button', textContent: 'Închide' });
  dialog.append(el('header', {}, el('h2', { textContent: 'Parteneri dublați' })),
    el('div', { className: 'vc-efi__body' },
      el('p', { className: 'vc-efi__hint', textContent: 'Furnizori din NIR-uri, facturi și e-Facturi care apar cu același nume (fără SC, SRL, spații și puncte) dar cu coduri fiscale diferite. '
        + 'Apăsați «ANAF» ca să verificați codurile, apoi alegeți codul CORECT (Păstrează), bifați partenerii de combinat în el și apăsați «Combină». '
        + 'Toate NIR-urile și facturile lor trec pe codul păstrat; ceilalți parteneri dispar din liste.' }),
      list),
    statusBar(el('footer'), { start: [closeButton], status }));
  document.body.append(dialog); dialog.showModal();
  let busy = false, merged = false;
  const say = (text, bad = false) => { status.textContent = text; status.dataset.state = bad ? 'error' : 'info'; };

  function paintGroup(group) {
    const radioName = 'dup-' + group.key;
    const picks = new Map(), keeps = new Map(), rowOf = new Map();
    let checked = null;   // the ANAF answers of this group, null until asked
    let blocked = '';     // why ANAF forbids the merge
    const note = el('p', { className: 'vc-dup__note', role: 'status' });
    const anafButton = el('button', { type: 'button', textContent: 'ANAF' });
    const button = el('button', { type: 'button', className: 'is-primary', textContent: 'Combină' });
    const chosenCf = () => [...keeps].find(([, radio]) => radio.checked)?.[0];
    const sync = () => {
      const chosen = chosenCf(), target = group.members.find(member => member.cf === chosen);
      for (const [cf, box] of picks) { box.disabled = busy || cf === chosen; if (cf === chosen) box.checked = false; }
      const any = [...picks].some(([cf, box]) => cf !== chosen && box.checked);
      const reason = blocked || (target && !target.partner ? 'Partenerul păstrat nu există în Parteneri: adăugați-l întâi acolo sau păstrați alt cod fiscal.' : '');
      note.textContent = reason; note.dataset.state = reason ? 'error' : 'info';
      anafButton.disabled = busy;
      button.disabled = busy || !chosen || !any || !!reason;
    };
    // ANAF verdict: none found -> no merge; one found -> it is the one to keep; several found with different data -> different firms.
    function judge() {
      const found = group.members.filter(member => checked[member.cf]?.found);
      blocked = '';
      for (const member of group.members) keeps.get(member.cf).disabled = !checked[member.cf]?.found;   // a code ANAF does not know cannot be kept
      if (found.length && !found.some(member => keeps.get(member.cf).checked)) {
        keeps.get(found[0].cf).checked = true;
        for (const [cf, box] of picks) if (cf !== found[0].cf) box.checked = true;
      }
      if (!found.length) blocked = 'ANAF nu cunoaște niciunul dintre aceste coduri fiscale: nu le combinați.';
      else if (new Set(found.map(member => plain(checked[member.cf].denumire) + '|' + plain(checked[member.cf].adresa))).size > 1) {
        blocked = 'ANAF are date diferite pentru aceste coduri: sunt firme diferite cu același nume, nu le combinați.';
      }
    }
    const rows = group.members.map(member => {
      const radio = el('input', { type: 'radio', name: radioName, checked: member === group.members[0], 'aria-label': 'Păstrează ' + member.name });
      const box = el('input', { type: 'checkbox', checked: member !== group.members[0], 'aria-label': 'Combină ' + member.name });
      keeps.set(member.cf, radio); picks.set(member.cf, box);
      radio.addEventListener('change', () => { for (const [cf, other] of picks) if (cf !== member.cf) other.checked = true; sync(); });
      box.addEventListener('change', sync);
      const row = el('tr', {}, el('td', { className: 'is-center' }, radio), el('td', { className: 'is-center' }, box),
        el('td', { textContent: member.name + (member.partner ? '' : ' (fără partener)') }), el('td', { textContent: member.cf }),
        el('td', { className: 'is-number', textContent: String(member.receipts) }), el('td', { className: 'is-number', textContent: String(member.invoices) }),
        el('td', { className: 'is-number', textContent: String(member.einvoices) }));
      rowOf.set(member.cf, row);
      return row;
    });
    anafButton.addEventListener('click', async () => {
      if (busy) return;
      busy = true; sync(); say('Se întreabă ANAF…');
      try {
        checked = (await api('/api/vercon/suppliers/anaf', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...sel(), codes: group.members.map(member => member.cf) }) })).items;
        for (const [cf, row] of rowOf) row.title = checked[cf] ? anafText(checked[cf]) : '';
        judge(); busy = false; sync();
        say('Date ANAF preluate. Treceți cu mouse-ul peste un rând ca să le vedeți.');
      } catch (error) { busy = false; sync(); say(error.message || 'ANAF nu a putut fi interogat.', true); }
    });
    button.addEventListener('click', async () => {
      const chosen = chosenCf();
      const sources = [...picks].filter(([cf, box]) => cf !== chosen && box.checked).map(([cf]) => cf);
      if (busy || !chosen || !sources.length || blocked) return;
      const target = group.members.find(member => member.cf === chosen);
      if (!await confirmBox(`Combinați ${sources.length} ${sources.length === 1 ? 'partener' : 'parteneri'} în «${target.name}» (${chosen})? `
        + 'NIR-urile și facturile lor primesc codul fiscal ' + chosen + ', iar partenerii combinați dispar din liste. Operația nu se poate anula.',
        { title: 'Combinare parteneri', yes: 'Combină', no: 'Renunț', warning: true }) || !dialog.open) return;
      busy = true; sync(); say('Se combină partenerii…');
      try {
        const result = await api('/api/vercon/suppliers/merge', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...sel(), keep: chosen, merge: sources, request_key: newKey() }) });
        merged = true;
        say(`Combinat: ${result.receipts} NIR-uri și ${result.invoices} facturi au acum codul fiscal ${result.keep}.`
          + (result.invoice_twins ? ` Atenție: ${result.invoice_twins} facturi au acum același număr și aceeași dată; reuniți-le din NIR.` : ''));
        answer = await read(); busy = false; paint();
      } catch (error) { busy = false; sync(); say(error.message || 'Combinarea nu a putut fi făcută.', true); }
    });
    sync();
    return el('section', { className: 'vc-dup__group' }, el('h3', { textContent: group.label }),
      el('table', { className: 'vc-efi__table' },
        el('thead', {}, el('tr', {}, ...['Păstrează', 'Combină', 'Partener', 'Cod fiscal', 'NIR-uri', 'Facturi', 'e-Facturi'].map(text => el('th', { textContent: text })))),
        el('tbody', {}, ...rows)),
      el('div', { className: 'vc-dup__actions' }, note, anafButton, button));
  }
  function paint() {
    list.replaceChildren(...(answer.groups.length ? answer.groups.map(paintGroup)
      : [el('p', { className: 'vc-efi__hint', textContent: 'Nu mai există parteneri dublați.' })]));
  }
  paint();
  if (!merged) say(`${answer.groups.length} ${answer.groups.length === 1 ? 'grup' : 'grupuri'} de parteneri dublați.`);
  return new Promise(resolve => {
    const close = () => { if (busy) return; dialog.close(); dialog.remove(); resolve(merged); };
    closeButton.addEventListener('click', close);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  });
}
