import { receiptTotal } from './receipt-reconciliation.js';

const priceKey = value => receiptTotal([{ amount: value }], 'amount', 4);
const batchKey = row => `${row.origin ?? row.batch}|${row.variant}|${row.IdMasina}|${priceKey(row.PUTVA)}`;
const remaining = (quantity, used) => receiptTotal([
  { quantity: quantity ?? '0' },
  ...used.map(row => ({ quantity: '-' + String(row.Cant ?? '0').replace(',', '.') })),
], 'quantity', 4);

export async function warehouseStock(api, selection, date, excludedBC = null) {
  const query = new URLSearchParams(Object.entries(selection).filter(([, value]) => value != null && value !== ''));
  query.set('date', date);
  if (excludedBC) { query.set('exclude_kind', 'bc'); query.set('exclude_id', excludedBC); }
  return (await api('/api/vercon/stock/available?' + query)).available;
}

export function warehouseChoices(items, available, lines, current) {
  return items.filter(item => Number(remaining(available[String(item.id)], lines.filter(row =>
    row.key !== current?.key && String(row.variant) === String(item.id)))) > 0);
}

export function holderChoices(items, lines, current) {
  return items.map((item, choiceIndex) => ({ ...item, choiceIndex }))
    .filter(item => Number(remaining(item.left ?? item.qty, lines.filter(row =>
      row.key !== current?.key && batchKey(row) === batchKey(item)))) > 0);
}
