// VERCON, VC06-07 / VC06-08 (EV04, EV05, plan docs/EV_PLAN.md): the invoices the accountant SENT from K-BOT, offered in «Adaugare NIR».
// pick() lists the sendings that wait, lets the Administrator choose one (or return it to the accountant with a reason) and answers its detail;
// apply() puts that detail in the draft of the NIR: the supplier, the number, the date and the declared total of the invoice, the accounts the accountant
// proposed (ticked in «Categorie / cont») and the lines WITHOUT a product (the Administrator picks the product of each). Nothing is saved here: the NIR
// is saved by the editor, and its body carries `efactura` so the server closes the sending in the same transaction.
import { pickEfactura } from './efactura-queue-dialog.js';

export function efacturaIntake({ api, sel }) {
  const read = (path, extra = {}) => api(`/api/vercon/doc/nir/efactura${path}?${new URLSearchParams(Object.entries({ ...sel(), ...extra }).filter(([, value]) => value != null && value !== ''))}`);
  const post = (path, body) => api(`/api/vercon/doc/nir/efactura${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...sel(), ...body }) });

  const pick = () => pickEfactura({ read, post });

  // Puts the invoice in the draft. `zec` = the decimals of the gestiune is not needed: prices and quantities are copied as the invoice wrote them.
  // Returns the accounts that could NOT be ticked (an account of a gestiune this context does not offer): the page says so.
  function apply(detail, { draft, allocation, newLine }) {
    const header = draft.header;
    header.CodFiscal = detail.partner ? detail.partner.cf : null;
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
