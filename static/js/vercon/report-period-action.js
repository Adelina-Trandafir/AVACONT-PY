// VERCON-only period editor over a PDF; the report's other query parameters stay frozen.
import { el, field, modal, showMessage } from './ui.js';
import { reportPeriod } from './report-period.js';
import { generateReport, reportContext } from './report-request.js';
import { stockCardLinks } from './report-stock-card.js';
import { reportPdfSource } from './report-pdf-source.js';

export function reportPeriodAction({ api, name, params, catalog, dateOnly = false, isCurrent = () => true }) {
  let activeParams = new URLSearchParams(params), popup = null;
  return {
    icon: '📅', label: 'Schimbă perioada',
    title: 'Schimbă perioada — Regenerează raportul păstrând celelalte selecții.',
    async run(view) {
      if (!isCurrent() || view.closed) return;
      if (popup && !popup.closed) { popup.dialog.focus(); return; }
      const editor = popup = modal(dateOnly ? 'Schimbă data raportului' : 'Schimbă perioada raportului', view.scope);
      editor.dialog.classList.add('vc-report-period-popup');
      const panel = el('section', { className: 'vc-reports__period' },
        el('h4', { textContent: dateOnly ? 'Selectează data' : 'Selectează perioada' }));
      const status = el('p', { className: 'vc-hint', role: 'status' });
      const cancel = el('button', { type: 'button', textContent: 'Renunță' });
      const apply = el('button', { type: 'button', textContent: 'Schimbă perioada', disabled: true, className: 'is-primary' });
      let start = activeParams.get('start'), end = activeParams.get('end'), busy = false;
      const alive = () => !editor.closed && !view.closed && isCurrent();
      const periods = reportPeriod(editor.scope, (first, last) => {
        start = first; end = last;
        begin.input._picker.setValue(new Date(start + 'T12:00:00'), false);
        finish.input._picker.setValue(new Date(end + 'T12:00:00'), false);
      });
      const begin = field({ key: 'start', label: 'Dată început', type: 'date', required: true }, start, !dateOnly,
        value => { start = value; periods.sync(start, end); }, editor.scope);
      const finish = field({ key: 'end', label: dateOnly ? 'La data' : 'Dată sfârșit', type: 'date', required: true }, end, true,
        value => { end = value; periods.sync(start, end); }, editor.scope);
      periods.node.hidden = begin.node.hidden = dateOnly;
      panel.append(periods.node, begin.node, finish.node); editor.body.append(panel, status);
      editor.footer.append(cancel, apply);
      cancel.addEventListener('click', editor.close);
      editor.dialog.addEventListener('cancel', event => {
        if (busy) { event.preventDefault(); event.stopImmediatePropagation(); }
      }, { capture: true });
      apply.addEventListener('click', async () => {
        if (busy || !alive() || !await editor.scope.commit()) return;
        if (!end || !dateOnly && (!start || start > end)) {
          showMessage(status, 'Completați o perioadă validă, cu data de început înaintea datei de sfârșit.', 'error');
          return;
        }
        busy = true; apply.disabled = cancel.disabled = true; panel.inert = true;
        showMessage(status, 'Se regenerează raportul…', 'info');
        const changed = new URLSearchParams(activeParams);
        changed.set('start', dateOnly ? end : start); changed.set('end', end);
        try {
          const replacement = await regenerate(changed, alive);
          if (!replacement || !alive()) return;
          if (await view.replaceReport(replacement) && alive()) {
            activeParams = changed; editor.close();
          }
        } catch (error) {
          if (alive()) showMessage(status, error.message || 'Raportul nu a putut fi regenerat.', 'error');
        } finally {
          busy = false;
          if (alive()) { apply.disabled = cancel.disabled = false; panel.inert = false; }
        }
      });
      try {
        showMessage(status, 'Se încarcă perioadele…', 'info');
        const data = catalog || await api(`/api/vercon/reports/catalog?${reportContext(activeParams)}`);
        if (!alive()) return;
        const initialStart = start, initialEnd = end;
        periods.configure(data, begin, finish);
        start = initialStart; end = initialEnd;
        begin.input._picker.setValue(new Date(start + 'T12:00:00'), false);
        finish.input._picker.setValue(new Date(end + 'T12:00:00'), false);
        periods.sync(start, end);
        showMessage(status, '', 'info'); apply.disabled = false;
      } catch (error) {
        if (alive()) showMessage(status, error.message || 'Perioadele nu au putut fi încărcate.', 'error');
      }
    },
  };

  async function regenerate(changed, alive) {
    const generate = () => generateReport(api, name, changed);
    const context = reportContext(changed).toString();
    let model = await generate();
    if (!alive()) return null;
    let pdfSource;
    try {
      pdfSource = await reportPdfSource(api, model, context, isCurrent);
    } catch (error) {
      if (error.reason !== 'REPORT_EXPIRED' || !alive()) throw error;
      model = await generate();
      if (!alive()) return null;
      pdfSource = await reportPdfSource(api, model, context, isCurrent);
    }
    if (!alive()) return null;
    const snapshot = model.snapshot, filename = model.filename, selection = context;
    return { ...pdfSource, title: model.title, filename: filename + '.pdf',
      onLink: stockCardLinks(api, model, isCurrent),
      date: dateOnly ? changed.get('end') : null,
      period: { start: changed.get('start'), end: changed.get('end') },
      exportExcel: async (preview, progress) => {
        if (!isCurrent() || preview.closed || progress.closed) return null;
        const bytes = await api(`/api/vercon/reports/export/${snapshot}/xlsx?${selection}`, { responseType: 'arrayBuffer' });
        if (!isCurrent() || preview.closed || progress.closed) return null;
        return { data: bytes, filename: filename + '.xlsx', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' };
      },
    };
  }
}
