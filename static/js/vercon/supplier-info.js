// «Afișează info furnizor»: the card of a supplier branch of the saved-documents tree (menu ⋮ on the branch).
import { messageBox } from '../utils/confirm-box.js';

export async function showSupplierInfo({ api, sel, cf }) {
  const query = new URLSearchParams(Object.entries({ ...sel(), cf }).filter(([, value]) => value != null && value !== ''));
  const info = await api(`/api/vercon/suppliers/info?${query}`);
  const lines = [info.name || 'Furnizor fără nume', `Cod fiscal: ${info.cf}`,
    info.registered ? (info.hidden ? 'Partener ascuns în nomenclatorul Parteneri' : 'Partener înregistrat în nomenclatorul Parteneri') : 'Nu există în nomenclatorul Parteneri',
    ...(info.address ? [`Adresă: ${info.address}`] : []), ...(info.iban ? [`IBAN: ${info.iban}${info.bank ? ' (' + info.bank + ')' : ''}`] : []),
    `NIR-uri: ${info.receipts} · Facturi comune: ${info.invoices} · e-Facturi: ${info.einvoices}`];
  await messageBox(lines.join('\n'), { title: 'Info furnizor' }).closed;
}
