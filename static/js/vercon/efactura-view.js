import { pdfPreview } from './ui.js';
import { pickBox } from '../utils/confirm-box.js';
import { compareTyped } from '../utils/typed-order.js';

export async function showReceiptEfactura({ api, selection, doc, owner }) {
  const invoices = [...(doc.efactura || [])].sort((a, b) => compareTyped(a.supplier, b.supplier, 'text')
    || compareTyped(a.date, b.date, 'date') || compareTyped(a.number, b.number, 'text'));
  if (!invoices.length) return;
  const id = invoices.length === 1 ? invoices[0].id : await pickBox('Afișează eFactura', invoices.map(item => ({
    value: item.id, label: `${item.supplier} / ${item.number} / ${item.date.slice(0, 10)}`,
  })));
  const chosen = invoices.find(item => item.id === id); if (!chosen) return;
  const query = new URLSearchParams(Object.entries(selection).filter(([, value]) => value != null && value !== ''));
  await pdfPreview(`eFactura ${chosen.number} · ${chosen.supplier}`, async () => ({
    data: await api(`/api/vercon/doc/nir/${doc.id}/efactura/${chosen.id}/pdf?${query}`, { responseType: 'arrayBuffer' }),
    filename: `eFactura_${chosen.number}.pdf`.replace(/[<>:"/\\|?*]/g, '_'),
  }), owner);
}
