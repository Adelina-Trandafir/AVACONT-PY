// VERCON, VC06-07 / VC06-08 (EV04, EV05, plan docs/EV_PLAN.md): the invoices the accountant SENT from K-BOT, offered in «Adaugare NIR».
// pick() lists the sendings that wait, lets the Administrator choose one (or return it to the accountant with a reason) and answers its detail;
// apply() puts that detail in the draft of the NIR: the supplier, the number, the date and the declared total of the invoice, the accounts the accountant
// proposed (ticked in «Categorie / cont») and the lines WITHOUT a product (the Administrator picks the product of each). Nothing is saved here: the NIR
// is saved by the editor, and its body carries `efactura` so the server closes the sending in the same transaction.
import { reasonBox } from '../utils/confirm-box.js';
import { el } from './ui.js';

const money = (value) => value == null || value === '' ? '' : new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
const day = (value) => value ? value.slice(0, 10).split('-').reverse().join('.') : '';

export function efacturaIntake({ api, sel }) {
  const query = () => new URLSearchParams(Object.entries(sel()).filter(([, value]) => value != null && value !== '')).toString();
  const read = (path) => api(`/api/vercon/doc/nir/efactura${path}?${query()}`);
  const post = (path, body) => api(`/api/vercon/doc/nir/efactura${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...sel(), ...body }) });

  // The dialog: a table of the waiting invoices, «Închide» / «Returnează…» / «Folosește factura». Resolves to the detail of the invoice chosen, or null.
  async function pick() {
    const dialog = el('dialog', { className: 'vc-efi', 'aria-label': 'Facturi din e-Factura' });
    const status = el('p', { className: 'vc-efi__status', role: 'status' });
    const tbody = el('tbody');
    const table = el('table', { className: 'vc-efi__table' },
      el('thead', {}, el('tr', {}, ...['Furnizor', 'Factura', 'Data', 'Total', 'Linii', 'Trimisă de', 'Nota contabilului'].map((text) => el('th', { textContent: text })))), tbody);
    const useButton = el('button', { type: 'button', className: 'is-primary', textContent: 'Folosește factura', disabled: true });
    const returnButton = el('button', { type: 'button', textContent: 'Returnează…', disabled: true });
    const closeButton = el('button', { type: 'button', textContent: 'Închide' });
    dialog.append(
      el('header', {}, el('h2', { textContent: 'Facturi trimise de contabil (e-Factura)' })),
      el('div', { className: 'vc-efi__body' },
        el('p', { className: 'vc-efi__hint', textContent: 'Alegeți factura pe care o recepționați. Se completează furnizorul, numărul, data, valoarea, conturile propuse de contabil și liniile; produsul fiecărei linii îl alegeți dumneavoastră. Dacă factura nu trebuie recepționată în Vercon, o puteți returna contabilului cu un motiv.' }),
        el('div', { className: 'vc-efi__scroll' }, table), status),
      el('footer', {}, closeButton, el('span', { className: 'vc-efi__spacer' }), returnButton, useButton));
    document.body.append(dialog);
    let items = [], chosen = null, busy = false;
    const say = (text, bad = false) => { status.textContent = text; status.dataset.state = bad ? 'error' : 'info'; };
    const choose = (item) => { chosen = item; for (const row of tbody.children) row.classList.toggle('is-selected', row.dataset.id === String(item?.id)); useButton.disabled = returnButton.disabled = !item || busy; };
    const paintRows = () => {
      tbody.replaceChildren(...items.map((item) => {
        const row = el('tr', { tabIndex: 0, title: item.again ? 'Factura fusese preluată, iar NIR-ul ei a fost anulat: așteaptă din nou.' : '' }, ...[
          item.supplier || item.cui || '', item.number || '', day(item.date), money(item.total), String(item.lines), item.sent_by || '', item.note || ''].map((text, index) =>
          el('td', { textContent: text, className: index === 3 || index === 4 ? 'is-number' : '' })));
        row.dataset.id = String(item.id);
        row.addEventListener('click', () => choose(item));
        row.addEventListener('dblclick', () => { choose(item); void use(); });
        row.addEventListener('keydown', (event) => { if (event.key === 'Enter') { choose(item); void use(); } else if (event.key === ' ') { event.preventDefault(); choose(item); } });
        return row;
      }));
      choose(items.find((item) => item.id === chosen?.id) || null);
    };
    async function load() {
      busy = true; say('Se încarcă…');
      try {
        const answer = await read('');
        items = answer.items || [];
        paintRows();
        say(!answer.available ? 'Facturile din e-Factura nu sunt disponibile în această bază de date.' : items.length ? `${items.length} ${items.length === 1 ? 'factură așteaptă' : 'facturi așteaptă'} recepția.` : 'Nu este nicio factură trimisă de contabil care să aștepte recepția.');
      } catch (error) { if (error.name !== 'AbortError') say(error.message || 'Lista nu a putut fi încărcată.', true); }
      finally { busy = false; choose(chosen); }
    }
    let finish; const done = new Promise((resolve) => { finish = resolve; });
    const close = (value) => { if (dialog.open) dialog.close(); dialog.remove(); finish(value); };
    async function use() {
      if (!chosen || busy) return;
      busy = true; choose(chosen); say('Se citește factura…');
      try { close(await read(`/${chosen.id}`)); }
      catch (error) { busy = false; choose(chosen); say(error.message || 'Factura nu a putut fi citită.', true); if (error.status === 409) await load(); }
    }
    async function giveBack() {
      if (!chosen || busy) return;
      const reason = await reasonBox(`Returnați contabilului factura ${chosen.number} (${chosen.supplier || chosen.cui || ''})? Nu va mai apărea în această listă; contabilul vede motivul și o poate trimite din nou.`,
        { title: 'Returnare factură', label: 'Motiv', yes: 'Returnează', no: 'Renunță', maxLength: 500 });
      if (!reason) return;
      busy = true; choose(chosen); say('Se returnează…');
      try { await post(`/${chosen.id}/return`, { reason }); chosen = null; busy = false; await load(); }
      catch (error) { busy = false; choose(chosen); say(error.message || 'Returnarea nu a putut fi făcută.', true); if (error.status === 409) await load(); }
    }
    useButton.addEventListener('click', () => void use());
    returnButton.addEventListener('click', () => void giveBack());
    closeButton.addEventListener('click', () => close(null));
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(null); });
    dialog.showModal();
    void load();
    return done;
  }

  // Puts the invoice in the draft. `zec` = the decimals of the gestiune is not needed: prices and quantities are copied as the invoice wrote them.
  // Returns the accounts that could NOT be ticked (an account of a gestiune this context does not offer): the page says so.
  function apply(detail, { draft, allocation, newLine }) {
    const header = draft.header;
    header.IdPartener = detail.partner ? detail.partner.id : null;
    header.NrFact = detail.number || '';
    header.DataFc = detail.date || header.DataFc;
    header.Valoare = detail.total;
    const wanted = detail.accounts || [];
    const usable = wanted.filter((id) => allocation.account(id));
    if (usable.length) draft.accounts = [...usable]; else delete draft.accounts;
    draft.efactura = detail.id;
    draft.lines = detail.lines.map((line) => newLine({
      ef_name: line.name, Cant: line.qty, Pret: line.price,
      IDTVA: allocation.taxOptions({}).find((item) => Number(item.percent) === Number(line.rate))?.id ?? null,
    }));
    return wanted.filter((id) => !usable.includes(id));
  }

  return { pick, apply };
}
