// Saved documents use the official report layout; an unsaved draft stays local.
import { pdfPreview } from './ui.js';
import { snapshotPdf } from './snapshot-pdf.js';
import { reportPeriodAction } from './report-period-action.js';
import { voucherHeader } from './voucher-header.js';
import { generateReport } from './report-request.js';

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

export async function documentReport({ api, selection, family, document, header = document?.header, title, date = document?.header?.Data, sections, owner, isCurrent = () => true }) {
  return pdfPreview(title, async view => {
    const current = () => isCurrent() && !view.closed;
    const mfVoucher = selection.type === 'MF' && ['bp', 'bt', 'bs'].includes(family);
    const name = mfVoucher ? `MF_Bon_${family.toUpperCase()}` : names[family];
    if (document && !document.annulled && (['ALIMENTE', 'MATERIALE', 'OI'].includes(selection.type) || mfVoucher) && name) {
      const year = ['OI', 'MF'].includes(selection.type) ? Number(document.header.Data.slice(0, 4)) : Number(selection.year);
      const params = new URLSearchParams({ ...selection, start: `${year}-01-01`, end: `${year}-12-31`, document: document.id });
      try {
        const model = await generateReport(api, name, params);
        if (!current()) return null;
        const data = await api(`/api/vercon/reports/export/${model.snapshot}/pdf?${new URLSearchParams(selection)}`, { responseType: 'arrayBuffer' });
        return current() ? { data, title: model.title, filename: model.filename + '.pdf',
          toolbarActions: [reportPeriodAction({ api, name, params, isCurrent: current })] } : null;
      } catch (error) {
        // A reader without the report right may still print the document already loaded.
        if (error.status !== 403) throw error;
        if (!current()) return null;
      }
    }
    const formHeader = voucherHeader(selection, family, header, sections[0]?.rows || [], title);
    const localSections = formHeader ? sections.flatMap(section => section.title !== 'Antet' ? [section]
      : [{ ...section, title: 'Date suplimentare', rows: section.rows.filter(row => row.value && /cont|categorie|explicații/i.test(row.label)) }]
        .filter(section => section.rows.length)) : sections;
    const frozenSections = localSections.map(section => ({ ...section,
      rows: section.rows.map(row => family === 'storno' ? stornoDisplay(row) : { ...row }) }));
    return snapshotPdf(title, frozenSections, { documentHeader: formHeader });
  }, owner, isCurrent, { date });
}
