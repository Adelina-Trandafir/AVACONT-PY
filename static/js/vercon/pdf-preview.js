// Report toolbar follows ADECHIT; the page display uses the shared PDF.js viewer.
import { createPdfView } from '../portal/pdfview.js';
import { el, modal, showMessage } from './ui.js';

export function downloadFile(data, type, filename) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = el('a', { href: url, download: filename, className: 'vc-file-download' });
  document.body.append(link); link.click(); link.remove();
  // Keep the URL alive until the browser has consumed the download, even if the dialog closes.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export async function openPdfReport(title, data, owner) {
  const view = modal(title, owner);
  view.dialog.classList.add('vc-modal--report');
  const toolbar = el('nav', { className: 'vc-report-toolbar', 'aria-label': 'Acțiuni raport' });
  const status = el('span', { role: 'status', className: 'vc-report-status', textContent: 'Se încarcă raportul…' });
  const host = el('div', { className: 'vc-pdf' });
  const pageLabel = el('span', { className: 'vc-report-page', textContent: 'Pagina —' });
  const zoomLabel = el('span', { className: 'vc-report-zoom', textContent: '— %' });
  const filename = title.replace(/[<>:"/\\|?*]/g, '_') + '.pdf';
  // Blob is created before passing the bytes to PDF.js.
  const blobUrl = URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
  view.scope.own(() => URL.revokeObjectURL(blobUrl));
  let currentPage = 1, printing = false;
  const controls = [];
  const button = (text, run, accessible = text) => {
    const node = el('button', { type: 'button', textContent: text, 'aria-label': accessible, disabled: true });
    node.addEventListener('click', run); controls.push(node); return node;
  };
  const pdf = createPdfView(host, { initialScale: 'page-actual', onState: state => {
    if (view.closed) return;
    currentPage = state.page; pageLabel.textContent = `Pagina ${state.page} / ${state.pages}`;
    zoomLabel.textContent = `${Math.round(state.scale * 100)} %`;
    controls.forEach(node => { node.disabled = false; }); status.textContent = '';
  } });
  view.scope.own(() => { void pdf.clear(); });
  function print() {
    if (printing) return;
    printing = true;
    const frame = el('iframe', { className: 'vc-print-frame', title: 'Tipărire raport' });
    frame.addEventListener('load', () => {
      try { frame.contentWindow.focus(); frame.contentWindow.print(); }
      catch (error) { showMessage(status, 'Deschideți PDF-ul descărcat pentru tipărire.', 'error'); }
      finally { printing = false; }
    }, { once: true });
    view.scope.own(() => frame.remove()); frame.src = blobUrl; document.body.append(frame);
  }
  toolbar.append(
    button('Tipărește', print), button('Alege imprimanta', print),
    button('Descarcă PDF', () => downloadFile(data, 'application/pdf', filename)),
    button('−', () => pdf.zoom(-.1), 'Micșorează'), zoomLabel,
    button('+', () => pdf.zoom(.1), 'Mărește'), button('Potrivește lățimea', () => pdf.fitWidth()),
    button('100 %', () => pdf.actualSize()),
    button('‹', () => pdf.goToPage(currentPage - 1), 'Pagina precedentă'), pageLabel,
    button('›', () => pdf.goToPage(currentPage + 1), 'Pagina următoare'));
  const close = el('button', { type: 'button', textContent: 'Închide' }); close.addEventListener('click', view.close); toolbar.append(close);
  view.body.append(toolbar, host); view.footer.append(status);
  try { await pdf.open(data); }
  catch (error) {
    if (!view.closed) { controls[2].disabled = false; showMessage(status, error.message || 'Raportul nu a putut fi afișat.', 'error'); }
  }
  return view;
}
