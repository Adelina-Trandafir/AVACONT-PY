// Shared report preview for every application, using the Portal PDF.js viewer.
import { createPdfView } from '../portal/pdfview.js';
import { el } from './report-dialog.js';
import { beginReport } from './report-progress.js';
import { showMessage } from '../portal/messages.js';
import { openMenu } from '../components/menu/menu.js';

export function downloadFile(data, type, filename) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = el('a', { href: url, download: filename, className: 'report-file-download' });
  document.body.append(link); link.click(); link.remove();
  // Keep the URL alive until the browser has consumed the download, even if the dialog closes.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// A lazy source runs only after the dialog is visible and the shared gate is acquired.
// It returns bytes or { data, title, filename, exportPdf, exportExcel, downloadOnly, mime }.
export async function openPdfReport(title, source, owner, details = {}) {
  const task = beginReport(title, owner, details);
  if (!task) return null;
  const view = task.view;
  const current = () => !view.closed && (details.isCurrent?.() ?? true);
  let data, filename, downloadPdf, canDownloadPdf = false;
  const status = el('span', { role: 'status', className: 'report-view-status' });
  try {
    await task.paint();
    if (!current()) { view.close(); return view; }
    const result = typeof source === 'function' ? await source(view) : source;
    if (!current() || result == null) { view.close(); return view; }
    const output = result instanceof ArrayBuffer || ArrayBuffer.isView(result) ? { data: result } : result;
    data = output.data;
    const options = { ...details, ...output };
    const exportData = () => typeof options.exportPdf === 'function' ? options.exportPdf(view) : data;
    downloadPdf = async () => {
      const source = data, name = filename;
      try {
        const bytes = await exportData();
        if (bytes && current() && data === source) downloadFile(bytes, 'application/pdf', name);
      } catch (error) {
        if (current()) showMessage(status, error.message || 'PDF-ul nu a putut fi descărcat.', 'error');
      }
    };
    filename = options.filename || title.replace(/[<>:"/\\|?*]/g, '_') + '.pdf';
    if (options.title) view.setTitle(options.title, options);
    if (options.downloadOnly) {
      downloadFile(data, options.mime || 'application/pdf', filename);
      view.close(); return view;
    }
    canDownloadPdf = true;
    task.message('Se afișează raportul…');
    const toolbar = el('nav', { className: 'report-view-toolbar', 'aria-label': 'Acțiuni raport' });
    const host = el('div', { className: 'report-view-pdf' });
    const configureLinks = () => {
      const linked = typeof options.onLink === 'function';
      host.classList.toggle('report-view-pdf--product-links', linked);
      if (linked) host.setAttribute('data-kbot-tooltip', 'off');
      else host.removeAttribute('data-kbot-tooltip');
    };
    configureLinks();
    const onLink = event => {
      const anchor = event.target.closest?.('a[href]');
      if (!anchor || !host.contains(anchor) || !current() || typeof options.onLink !== 'function') return;
      if (options.onLink(anchor.href, view) === true) {
        event.preventDefault(); event.stopImmediatePropagation();
      }
    };
    host.addEventListener('click', onLink, { capture: true });
    host.addEventListener('auxclick', onLink, { capture: true });
    const pageLabel = el('span', { className: 'report-view-page', textContent: 'Pagina —' });
    const zoomLabel = el('span', { className: 'report-view-zoom', textContent: '— %' });
    let blobUrl = null;
    view.scope.own(() => { if (blobUrl) URL.revokeObjectURL(blobUrl); });
    let currentPage = 1, printing = false, rendered = false;
    const controls = [];
    const button = (icon, run, accessible, tooltip) => {
      const node = el('button', { type: 'button', title: tooltip, 'aria-label': accessible, disabled: true },
        el('span', { textContent: icon, 'aria-hidden': 'true' }));
      node.addEventListener('click', run); controls.push(node); return node;
    };
    let resolveRendered;
    const firstPage = new Promise(resolve => { resolveRendered = resolve; });
    view.scope.own(() => resolveRendered(false));
    const pdf = createPdfView(host, { initialScale: 'page-actual',
      docBaseUrl: options.resolveRelativeLinks || typeof options.onLink === 'function' ? window.location.href : undefined, onState: state => {
      if (!current()) return;
      currentPage = state.page; pageLabel.textContent = `Pagina ${state.page} / ${state.pages}`;
      zoomLabel.textContent = `${Math.round(state.scale * 100)} %`;
    }, onRendered: error => {
      if (rendered) return;
      rendered = true;
      resolveRendered(error ? { error } : { ready: true });
    } });
    view.scope.own(() => { void pdf.clear(); });
    let replacing = false;
    // Optional application actions can redraw this viewer with a new report.
    // The previous bytes and exports are retained if loading the replacement fails.
    view.replaceReport = async replacement => {
      if (!current() || replacing) return false;
      replacing = true;
      host.inert = true;
      controls.forEach(node => { node.disabled = true; });
      closeExportMenu?.();
      view.dialog.setAttribute('aria-busy', 'true');
      try {
        rendered = false;
        const nextPage = new Promise(resolve => { resolveRendered = resolve; });
        await pdf.open(replacement.data);
        const page = await nextPage;
        if (page?.error) throw page.error;
        if (!page?.ready || !current()) return false;
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        blobUrl = null;
        data = replacement.data;
        Object.assign(options, replacement);
        configureLinks();
        filename = options.filename || filename;
        view.setTitle(options.title || title, options);
        showMessage(status, '', 'info');
        return true;
      } catch (error) {
        if (current()) {
          rendered = true;
          await pdf.open(data);
        }
        throw error;
      } finally {
        replacing = false;
        host.inert = false;
        if (current()) {
          controls.forEach(node => { node.disabled = false; });
          view.dialog.setAttribute('aria-busy', 'false');
        }
      }
    };
    async function print() {
      if (printing) return;
      printing = true;
      const source = data;
      try {
        const bytes = await exportData();
        if (!bytes || !current() || data !== source) { printing = false; return; }
        if (!blobUrl) blobUrl = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
        const frame = el('iframe', { className: 'report-print-frame', title: 'Tipărire raport' });
        frame.addEventListener('load', () => {
          try { frame.contentWindow.focus(); frame.contentWindow.print(); }
          catch (error) { console.error('[report-preview] printing failed', error); showMessage(status, 'Deschideți PDF-ul descărcat pentru tipărire.', 'error'); }
          finally { printing = false; }
        }, { once: true });
        view.scope.own(() => frame.remove()); frame.src = blobUrl; document.body.append(frame);
      } catch (error) {
        printing = false;
        if (current()) showMessage(status, error.message || 'PDF-ul nu a putut fi pregătit pentru tipărire.', 'error');
      }
    }
    let closeExportMenu = null;
    view.scope.own(() => closeExportMenu?.());
    const exportButton = button('📤', () => {
      if (closeExportMenu) { closeExportMenu(); return; }
      closeExportMenu = openMenu(exportButton, [
        { icon: '📕', label: 'Descarcă PDF', run: downloadPdf },
        { icon: '📗', label: 'Descarcă Excel', disabled: !options.exportExcel,
          reason: 'Acest raport nu oferă export Excel.',
          run: () => {
            void openPdfReport('Export Excel', progress => options.exportExcel(view, progress), view.scope,
              { downloadOnly: true, isCurrent: current });
          } },
      ], { label: 'Export Raport', align: 'left', onClose: () => { closeExportMenu = null; } });
    }, 'Export Raport', 'Export Raport — Deschide opțiunile de descărcare în format PDF sau Excel.');
    exportButton.setAttribute('aria-haspopup', 'menu');
    exportButton.setAttribute('aria-expanded', 'false');
    toolbar.append(
      button('🖨️', print, 'Tipărește', 'Tipărește raportul — Deschide dialogul de imprimare pentru raportul afișat.'),
      button('⚙️', print, 'Alege imprimanta', 'Alege imprimanta — Deschide dialogul de imprimare pentru alegerea imprimantei, paginilor și numărului de copii.'),
      exportButton,
      ...(options.toolbarActions || []).map(action => button(action.icon, () => {
        void Promise.resolve().then(() => action.run(view)).catch(error => {
          if (current()) showMessage(status, error.message || 'Operația nu a putut fi finalizată.', 'error');
        });
      }, action.label, action.title || action.label)),
      button('➖', () => pdf.zoom(-.1), 'Micșorează', 'Micșorează — Reduce zoomul raportului cu 10 puncte procentuale.'), zoomLabel,
      button('➕', () => pdf.zoom(.1), 'Mărește', 'Mărește — Crește zoomul raportului cu 10 puncte procentuale.'),
      button('↔️', () => pdf.fitWidth(), 'Potrivește lățimea', 'Potrivește lățimea — Ajustează zoomul pentru a încadra lățimea paginii în fereastră.'),
      button('🎯', () => pdf.actualSize(), 'Dimensiune reală', 'Dimensiune reală — Revine la zoom 100%.'),
      button('⬅️', () => pdf.goToPage(currentPage - 1), 'Pagina precedentă', 'Pagina precedentă — Afișează pagina anterioară a raportului.'), pageLabel,
      button('➡️', () => pdf.goToPage(currentPage + 1), 'Pagina următoare', 'Pagina următoare — Afișează pagina următoare a raportului.'));
    const close = button('❌', view.close, 'Închide raportul', 'Închide raportul — Revine la ecranul din care a fost deschis raportul.');
    close.disabled = false; toolbar.append(close);
    view.header.append(toolbar); view.body.prepend(host); view.footer.append(status);
    // Loading remains visible until a page has actually been drawn, not merely fetched.
    await pdf.open(data);
    const page = await firstPage;
    if (page?.error) throw page.error;
    if (page?.ready && current()) {
      controls.forEach(node => { node.disabled = false; });
      task.ready();
    } else view.close();
  } catch (error) {
    if (error.name !== 'AbortError') console.error('[report-preview] report failed', error);
    if (!current() || error.name === 'AbortError') view.close();
    else task.fail(error, canDownloadPdf ? downloadPdf : null);
  } finally { task.finish(); }
  return view;
}
