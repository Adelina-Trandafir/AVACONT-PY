// Saved documents use the official report layout; an unsaved draft stays local.
import { pdfPreview } from './ui.js';
import { snapshotPdf } from './snapshot-pdf.js';

const names = { nir: 'NIR', storno: 'NIR_Storno', bc: 'Bon_Consum', br: 'BonRetur', lista: 'ListaAlimente2015',
  bp: 'OI_Bon_BP', bt: 'OI_Bon_BT', bs: 'OI_Bon_BS' };

function stornoDisplay(row) {
  const displayed = { ...row };
  for (const key of ['Cant', 'Valoare', 'ValoareCUTVA', 'TVA']) {
    const value = row[key];
    if (value == null || value === '') continue;
    const magnitude = String(value).trim().replace(/^[+-]/, '');
    displayed[key] = /^0+(?:\.0+)?$/.test(magnitude) ? magnitude : `-${magnitude}`;
  }
  return displayed;
}

export async function documentReport({ api, selection, family, document, title, date = document?.header?.Data, sections, owner, isCurrent = () => true }) {
  const details = { date };
  if (document && !document.annulled && ['ALIMENTE', 'MATERIALE', 'OI'].includes(selection.type) && names[family]) {
    const year = selection.type === 'OI' ? Number(document.header.Data.slice(0, 4)) : Number(selection.year);
    const params = new URLSearchParams({ ...selection, start: `${year}-01-01`, end: `${year}-12-31`, document: document.id });
    try {
      const model = await api(`/api/vercon/reports/${encodeURIComponent(names[family])}/generate?${params}`);
      if (!isCurrent()) return;
      const data = await api(`/api/vercon/reports/export/${model.snapshot}/pdf?${new URLSearchParams(selection)}`, { responseType: 'arrayBuffer' });
      if (isCurrent()) return pdfPreview(title, data, owner, isCurrent, details);
      return;
    } catch (error) {
      // A reader without the separate report right may still print the document already loaded.
      if (error.status !== 403) throw error;
      if (!isCurrent()) return;
    }
  }
  const frozenSections = sections.map(section => ({ ...section,
    rows: section.rows.map(row => family === 'storno' ? stornoDisplay(row) : { ...row }) }));
  const data = await snapshotPdf(title, frozenSections);
  if (isCurrent()) return pdfPreview(title, data, owner, isCurrent, details);
}
