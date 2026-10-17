// SLICE-ADE9-01: the Rapoarte panel. Every button asks the server for one report (print page or PDF); the server reads,
// the page never calculates. The panel works on what is chosen on the screen: month, group, child, and the two dates.
import { DatePicker } from '../components/datepicker/datepicker.js';
import { messageBox } from '../utils/confirm-box.js';
import { trackSessionWindow } from '../utils/session-exit.js';

const $ = (id) => document.getElementById(id);

function fileNameOf(k_response, k_fallback) {
  const k_match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(k_response.headers.get('Content-Disposition') || '');
  return k_match ? decodeURIComponent(k_match[1]) : k_fallback;
}

async function downloadOutput(k_response, k_fallback) {
  const k_url = URL.createObjectURL(await k_response.blob());
  const k_link = document.createElement('a'); k_link.href = k_url; k_link.download = fileNameOf(k_response, k_fallback);
  document.body.append(k_link); k_link.click(); k_link.remove();
  setTimeout(() => URL.revokeObjectURL(k_url), 60000);
}

// Print: the window is opened first, from the click, so the browser accepts it; the report is written into it afterwards.
export async function outputReport({ fetchOutput, path, pdf, title }) {
  const k_window = pdf ? null : trackSessionWindow(window.open('', '_blank'));
  try {
    if (!pdf && !k_window) throw new Error('Browserul a blocat fereastra de listare. Permiteți ferestrele popup pentru această pagină.');
    if (k_window) { k_window.document.title = title; k_window.document.body.textContent = 'Se pregătește raportul…'; }
    const k_response = await fetchOutput(path, pdf ? 'application/pdf' : 'text/html');
    if (!pdf) {
      const k_html = await k_response.text();
      if (k_window.closed) return;
      k_window.document.open(); k_window.document.write(k_html); k_window.document.close();
      // on screen the report follows the theme of this page; printing and the PDF stay on the Access palette
      if (document.documentElement.dataset.theme === 'dark') k_window.document.documentElement.dataset.theme = 'dark';
      // the page keeps its opener: the PDF button asks it to download (it holds the sign-in the request needs)
      k_window.adePdf = () => outputReport({ fetchOutput, path: path.replace('/print?', '/pdf?'), pdf: true, title })
        .catch((k_error) => { if (!k_window.closed) messageBox(k_error.message, { title: 'Raport', warning: true, ownerDocument: k_window.document }); });
      k_window.adeExcel = async (k_columns) => {
        const k_url = new URL(path, window.location.origin);
        k_url.pathname = k_url.pathname.replace(/\/print$/, '/xlsx');
        k_url.searchParams.set('columns', k_columns.join(','));
        const k_excel = await fetchOutput(k_url.pathname + k_url.search,
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
        await downloadOutput(k_excel, 'Raport.xlsx');
      };
      return;
    }
    await downloadOutput(k_response, 'Raport.pdf');
  } catch (k_error) { k_window?.close(); throw k_error; }
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
