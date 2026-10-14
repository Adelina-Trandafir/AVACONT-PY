// Saved documents use the official report layout; an unsaved draft stays local.
import { pdfPreview } from './ui.js';
import { snapshotPdf } from './snapshot-pdf.js';

const names = { nir: 'NIR', storno: 'NIR_Storno', bc: 'Bon_Consum', br: 'BonRetur', lista: 'ListaAlimente2015' };

export async function documentReport({ api, selection, family, document, title, sections, owner, isCurrent = () => true }) {
  if (document && !document.annulled && ['ALIMENTE', 'MATERIALE'].includes(selection.type) && names[family]) {
    const year = Number(selection.year);
    const params = new URLSearchParams({ ...selection, start: `${year}-01-01`, end: `${year}-12-31`, document: document.id });
    try {
      const model = await api(`/api/vercon/reports/${encodeURIComponent(names[family])}/generate?${params}`);
      if (!isCurrent()) return;
      const data = await api(`/api/vercon/reports/export/${model.snapshot}/pdf?${new URLSearchParams(selection)}`, { responseType: 'arrayBuffer' });
      if (isCurrent()) return pdfPreview(title, data, owner, isCurrent);
      return;
    } catch (error) {
      // A reader without the separate report right may still print the document already loaded.
      if (error.status !== 403) throw error;
      if (!isCurrent()) return;
    }
  }
  const frozenSections = sections.map(section => ({ ...section, rows: section.rows.map(row => ({ ...row })) }));
  const data = await snapshotPdf(title, frozenSections);
  if (isCurrent()) return pdfPreview(title, data, owner, isCurrent);
}
