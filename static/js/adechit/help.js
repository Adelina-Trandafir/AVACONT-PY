// ADECHIT entry point for the shared help renderer.
import { bindHelp as bindSharedHelp } from '../help/help.js';

export function bindHelp(options) {
  return bindSharedHelp({ ...options, prefix: 'ade', contentUrl: '/static/help/adechit.json',
    pdfPath: '/api/adechit/help/pdf', pdfTitle: 'Ajutor ADECHIT' });
}
