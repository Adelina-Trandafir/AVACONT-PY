import { bindHelp as bindSharedHelp } from '../help/help.js';
import { openPdfReport } from './pdf-preview.js';

export function bindHelp({ api, report, signal }) {
  const help = bindSharedHelp({ prefix: 'vc', contentUrl: '/static/help/vercon.json',
    bindTrigger: false, report,
    exportPdf: () => openPdfReport('Ajutor VERCON', async () => ({
      data: await api('/api/vercon/help/pdf', { responseType: 'arrayBuffer' }),
      filename: 'Ajutor_VERCON.pdf', title: 'Ajutor VERCON',
    }), null, { downloadOnly: true, isCurrent: () => !signal?.aborted }),
  });
  return () => help.open().catch(report);
}
