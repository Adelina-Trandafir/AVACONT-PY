// ADE report filters use the common PDF preview and frozen report exports.
import { DatePicker } from '../components/datepicker/datepicker.js';
import { openPdfReport } from '../reporting/pdf-preview.js';
import { chooseColumns } from '../reporting/columns.js';

const $ = (id) => document.getElementById(id);
const views = new Set();
let generation = 0;

export function closeReportPreviews() {
  ++generation;
  for (const close of views) close();
  views.clear();
}
window.addEventListener('app:session-expired', closeReportPreviews);

export function outputReport({ fetchOutput, path, pdf, title }) {
  const sequence = generation;
  const current = () => sequence === generation;
  const owner = { own(close) { views.add(close); } };
  return openPdfReport(title, async view => {
    const valid = () => current() && !view.closed;
    const url = new URL(path, location.origin);
    const reportPath = /^\/api\/adechit\/reports\/[^/]+\/(print|pdf)$/.test(url.pathname);
    let model;
    if (reportPath) {
      url.pathname = url.pathname.replace(/\/(print|pdf)$/, '/generate');
      model = await (await fetchOutput(url.pathname + url.search, 'application/json')).json();
      if (!valid()) return null;
      url.pathname = `/api/adechit/reports/export/${model.snapshot}/pdf`;
      url.search = '';
    } else url.pathname = url.pathname.replace(/\/print$/, '/pdf');
    const response = await fetchOutput(url.pathname + url.search, 'application/pdf');
    const data = await response.arrayBuffer();
    if (!valid()) return null;
    const filename = model ? model.filename + '.pdf' : title.replace(/[<>:"/\\|?*]/g, '_') + '.pdf';
    const exportExcel = model ? async (preview, progress) => {
      const columns = await chooseColumns(model.columns, progress.scope);
      if (columns === null || preview.closed || progress.closed || !current()) return null;
      const query = new URLSearchParams({ columns: columns.join(',') });
      const response = await fetchOutput(`/api/adechit/reports/export/${model.snapshot}/xlsx?${query}`,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      const data = await response.arrayBuffer();
      if (preview.closed || progress.closed || !current()) return null;
      return { data, filename: model.filename + '.xlsx',
        mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' };
    } : null;
    return { data, filename, title: model?.title || title, exportExcel };
  }, owner, { downloadOnly: pdf, isCurrent: current, onClose: close => views.delete(close) });
}

// snapshot() -> { month, monthId, groupId, selected }; the panel reads it on every click, never keeps a copy.
export function bindReports({ fetchOutput, snapshot, report }) {
  const k_start = new DatePicker($('ade-rep-start'));
  const k_end = new DatePicker($('ade-rep-end'));
  const k_all = $('ade-rep-all'); const k_split = $('ade-rep-split');
  k_all.addEventListener('change', () => { k_split.disabled = !k_all.checked; if (!k_all.checked) k_split.checked = false; });

  // The cash and bank reports start on the whole month that is open on the screen.
  function syncDates() {
    const k_month = snapshot().month;
    if (!k_month) return;
    k_start.setValue(new Date(k_month.Anul, k_month.Luna - 1, 1), false);
    k_end.setValue(new Date(k_month.Anul, k_month.Luna, 0), false);
  }

  function run(k_kind, k_params, k_pdf, k_title) {
    const k_query = new URLSearchParams(k_params).toString();
    return outputReport({ fetchOutput, path: `/api/adechit/reports/${k_kind}/${k_pdf ? 'pdf' : 'print'}?${k_query}`, pdf: k_pdf, title: k_title });
  }
  const k_month = () => {
    const k_now = snapshot();
    if (!k_now.monthId) throw new Error('Alegeți luna.');
    return k_now;
  };
  const k_click = (k_id, k_action) => $(k_id).addEventListener('click', () => {
    // a synchronous throw (missing choice) must reach the notice strip the same way as a failed request
    try { Promise.resolve(k_action()).catch(report); } catch (k_error) { report(k_error); }
  });

  k_click('ade-rep-situation', () => {
    const k_now = k_month();
    if (!k_all.checked && k_now.groupId == null) throw new Error('Alegeți grupa sau bifați «Toate grupele».');
    const k_params = { month: k_now.monthId };
    if (k_all.checked) { k_params.all = '1'; if (k_split.checked) k_params.split = '1'; } else k_params.group = k_now.groupId;
    return run('situation', k_params, false, 'Situație lunară');
  });
  k_click('ade-rep-payment-notice', () => {
    const k_now = k_month();
    if (!k_all.checked && k_now.groupId == null) throw new Error('Alegeți grupa sau bifați «Toate grupele».');
    const k_params = { month: k_now.monthId };
    if (k_all.checked) k_params.all = '1'; else k_params.group = k_now.groupId;
    return run('payment-notice', k_params, false, 'Situație de plată');
  });
  const k_range = () => {
    if (!k_start.iso || !k_end.iso) throw new Error('Completați data de început și data de sfârșit.');
    return { start: k_start.iso, end: k_end.iso };
  };
  k_click('ade-rep-cash', () => run('cash', k_range(), false, 'Registru de casă'));
  k_click('ade-rep-bank', () => run('bank', k_range(), false, 'Raport bancă'));
  k_click('ade-rep-cancelled', () => run('cancelled', { month: k_month().monthId }, false, 'Documente anulate'));
  // Account sheet and debtor sheet: the selected child, or every child of the group.
  const k_children = (k_kind, k_title) => () => {
    const k_now = k_month();
    const k_params = { month: k_now.monthId };
    if ($('ade-rep-account-all').checked) {
      if (k_now.groupId == null) throw new Error('Alegeți grupa.');
      Object.assign(k_params, { all: '1', group: k_now.groupId });
    } else {
      if (!k_now.selected) throw new Error('Selectați un copil din tabel.');
      k_params.child = k_now.selected.IDP;
    }
    return run(k_kind, k_params, false, k_title);
  };
  k_click('ade-rep-account', k_children('account', 'Fișă cont'));
  // the debtor sheet and financial state buttons are hidden in adechit.html (reports.py HIDDEN); the wiring is kept
  $('ade-rep-debtor')?.addEventListener('click', () => { try { Promise.resolve(k_children('debtor', 'Fișă debitor')()).catch(report); } catch (k_error) { report(k_error); } });
  return { syncDates };
}
