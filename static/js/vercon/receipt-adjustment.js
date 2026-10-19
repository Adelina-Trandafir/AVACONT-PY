import { receiptDifference, receiptTotal } from './receipt-reconciliation.js';
import { messageBox } from '../utils/confirm-box.js';

const quantity = value => {
  const parsed = Number(String(value ?? 0).replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : 0;
};
const amount = value => String(value ?? '').replace('.', ',');

export function adjustmentAvailable(row, mode, savedLines) {
  const old = row.id && savedLines.find(line => line.id === row.id);
  return quantity(row.adjustable_quantity ?? old?.adjustable_quantity ?? (mode === 'add' || !row.id ? row.Cant : 0));
}

export function adjustableReceiptLine(row, mode, savedLines) {
  return !row.external && !row.has_warehouse_exit && row.calc_mode !== 'GROSS' && row.product
    && quantity(row.Cant) >= 1 && adjustmentAvailable(row, mode, savedLines) >= 1;
}

export function receiptAdjustmentState({ declared, rows, local, decimals, mode, savedLines,
  canWrite, closed, busy, pending, blank, problems, name }) {
  const difference = receiptDifference(declared, rows, decimals);
  const summary = declared == null ? 'Valoarea reală a facturii nu este completată.'
    : `Valoare factură: ${amount(declared)} lei. Total produse: ${amount(receiptTotal(rows, 'ValoareCUTVA', decimals))} lei. Diferență de ajustat: ${amount(difference.amount)} lei.`;
  const reasons = [], details = [];
  if (closed) reasons.push('Perioada este închisă; NIR-ul nu poate fi modificat.');
  else if (!canWrite) reasons.push('Nu aveți drept de modificare în contextul curent.');
  if (busy || pending) reasons.push('Există o operație în curs. Finalizați-o înainte de ajustare.');
  if (difference.different && !difference.canCorrect) reasons.push('Diferența depășește limita de 1 leu. Corectați produsele sau valoarea/repartizarea facturii.');
  const lines = local.filter(row => !blank(row));
  if (lines.some(row => row.calc_mode === 'GROSS')) reasons.push('Există deja o corecție automată. Ștergeți-o cu revenire în grup înainte de o nouă ajustare.');
  if (!lines.some(row => adjustableReceiptLine(row, mode, savedLines))) {
    reasons.push('Niciun produs nu are cel puțin 1 unitate disponibilă pentru ajustare, fără consum sau predare.');
    if (!lines.length) details.push('NIR-ul curent nu conține produse.');
    lines.forEach((row, index) => {
      const available = Math.max(0, Math.min(quantity(row.Cant), adjustmentAvailable(row, mode, savedLines)));
      let reason;
      if (row.has_warehouse_exit) reason = 'produsul are ieșire/predare și este protejat';
      else if (row.calc_mode === 'GROSS') reason = 'rândul are deja o corecție automată';
      else if (quantity(row.Cant) < 1) reason = 'cantitatea rândului este mai mică de 1';
      else if (available < 1) reason = 'stocul rămas după ieșiri/consumuri/predări este sub 1 pe parcursul mișcărilor';
      else reason = 'produsul nu este completat';
      details.push(`• ${name(row) || `Rând ${index + 1}`}: cantitate ${amount(row.Cant)}, disponibilă pentru ajustare ${row.has_warehouse_exit ? '0' : amount(available)}; ${reason}.`);
    });
  }
  lines.forEach((row, index) => {
    const missing = problems(row);
    if (missing.length) details.push(`• ${name(row) || `Rând ${index + 1}`}: completați/corectați ${missing.join(', ')}.`);
  });
  if (lines.some(row => problems(row).length)) reasons.push('Există produse cu date incomplete sau invalide.');
  const text = [summary, ...reasons, ...details].join('\n');
  return { ...difference, reasons, summary, text,
    tooltip: !difference.different ? 'Nu există diferențe de ajustat.' : reasons.length ? text
      : `${summary}\nAjustează 1 unitate disponibilă, păstrând cota reală de TVA.` };
}

export function adjustmentError(error, status, owner) {
  const fields = Object.values(error.fields || {}).filter(value => typeof value === 'string');
  const text = [...new Set([error.message || 'Ajustarea facturii a eșuat.', ...fields, status?.text].filter(Boolean))].join('\n\n');
  const box = messageBox(text, { title: 'Factura nu poate fi ajustată', warning: true, className: 'vc-receipt-adjustment-error' });
  const close = () => box.close();
  owner.own(close);
  return box;
}
