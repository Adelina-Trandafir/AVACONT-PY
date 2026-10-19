// Invoice context is displayed alongside local rows, never added to the editable draft.
import { receiptTotal } from './receipt-reconciliation.js';

export const sharedReceipt = doc => !!doc?.invoice && !doc.invoice.local && !doc.annulled;
export function invoiceRows(doc, local) {
  if (!sharedReceipt(doc)) return local;
  const blank = row => !row.product && [row.Cant, row.Pret].every(value => value == null || String(value).trim() === '');
  // Keep the automatic new-row slot last, even when the invoice has external rows.
  return [...local.filter(row => !blank(row)), ...(doc.other_lines || []), ...local.filter(blank)];
}
export const receiptGridRows = (doc, local, editingMF) => invoiceRows(doc, local)
  .filter(row => !editingMF || !row.has_warehouse_exit);
export const invoiceValue = (doc, header) => sharedReceipt(doc)
  ? doc.invoice.total ?? doc.invoice.received ?? receiptTotal(invoiceRows(doc, doc.lines), 'ValoareCUTVA', 4)
  : header.Valoare;

export function receiptLineClass(row, selection, product) {
  const destination = row.destination ?? product(row.product)?.destination;
  const outside = row.external || destination && destination !== `${selection.subunit}:${selection.gestiune}`;
  return [outside ? 'vc-nir-line--external' : '', row.calc_mode === 'GROSS' ? 'vc-nir-line--corrected' : ''].filter(Boolean).join(' ');
}

export function linkedReceipt(doc, selection) {
  return doc?.invoice?.receipts?.find(row => !row.cancelled && String(row.subunit) === String(selection.subunit)
    && String(row.gestiune) === String(selection.gestiune)
    && (['OI', 'MF'].includes(selection.type) || !selection.year || String(row.day || '').startsWith(`${selection.year}-`)));
}
