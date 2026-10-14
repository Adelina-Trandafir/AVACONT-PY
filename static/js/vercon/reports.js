// Period / report type / data selection, with the existing VERCON controls and theme.
import { el, scope, field, paintTree, pdfPreview, showMessage, CheckBox } from './ui.js';
import { mountSelection, selectionFilters, selectionError } from './report-selection.js';
import { paintReportBlock, reportNodes } from './report-result.js';
import { downloadFile } from './pdf-preview.js';

const months = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const endOfMonth = (year, month) => new Date(Date.UTC(year, month + 1, 0)).toISOString().slice(0, 10);

export function mountReports(host, { api, sel }) {
  const owner = scope(), selectionOwner = scope(), tablesOwner = scope();
  owner.own(() => selectionOwner.clear()); owner.own(() => tablesOwner.clear());
  const year = Number(sel().year);
  const state = { start: `${year}-01-01`, end: `${year}-12-31`, name: null, label: '', spec: null,
    checked: [], model: null, seq: 0, lookupSeq: 0, destroyed: false, loading: false, busy: false };
  let selectionTree;
  const root = el('section', { className: 'vc-reports' });
  const left = el('aside', { className: 'vc-reports__period' }, el('h4', { textContent: 'Selectează perioada' }));
  const periods = field({ key: 'period', label: 'Perioada', type: 'select', options: [
    { id: 'all', label: `Anul ${year}` }, ...months.map((label, index) => ({ id: String(index), label }))
  ] }, 'all', true, value => {
    const month = value === 'all' ? null : Number(value);
    state.start = month == null ? `${year}-01-01` : `${year}-${String(month + 1).padStart(2, '0')}-01`;
    state.end = month == null ? `${year}-12-31` : endOfMonth(year, month);
    begin.input._picker.setValue(new Date(state.start + 'T12:00:00'), false);
    end.input._picker.setValue(new Date(state.end + 'T12:00:00'), false);
    invalidate(); void lookups();
  }, owner);
  const begin = field({ key: 'start', label: 'Dată început', type: 'date' }, state.start, true,
    value => { state.start = value; invalidate(); void lookups(); }, owner);
  const end = field({ key: 'end', label: 'Dată sfârșit', type: 'date' }, state.end, true,
    value => { state.end = value; invalidate(); void lookups(); }, owner);
  const options = el('section', { className: 'vc-reports__options' }, el('h4', { textContent: 'Opțiuni afișare' }));
  const showOutput = owner.own(new CheckBox(options, { label: 'Afișează rezultatul în pagină', className: 'vc-reports__check' })).input;
  left.append(periods.node, begin.node, end.node, options);
  const center = el('section', { className: 'vc-reports__types' });
  const right = el('section', { className: 'vc-reports__selection' }, el('h4', { textContent: 'Selectare date' }),
    el('p', { className: 'vc-hint', textContent: 'Alegeți mai întâi tipul raportului.' }));
  const status = el('p', { role: 'status', className: 'vc-reports__status' });
  const excel = el('button', { type: 'button', textContent: 'Export Excel', disabled: true });
  const list = el('button', { type: 'button', textContent: 'Listare', disabled: true, className: 'is-primary' });
  const actions = el('div', { className: 'vc-actions vc-reports__actions' }, status, excel, list);
  const output = el('section', { className: 'vc-reports__output', hidden: true }), notes = el('div');
  root.append(left, center, right, actions, notes, output); host.replaceChildren(root);

  const query = (withFilters = true) => new URLSearchParams({ ...sel(), start: state.start, end: state.end,
    ...(withFilters ? selectionFilters(state.checked) : {}) });
  function available() {
    const error = selectionError(state.spec, state.checked);
    const disabled = !state.name || state.loading || state.busy || !!error;
    excel.disabled = list.disabled = disabled;
    if (state.spec && !state.loading && !state.busy && error) status.textContent = error;
  }
  function invalidate() {
    ++state.seq; state.model = null;
    tablesOwner.clear(); output.replaceChildren(); notes.replaceChildren();
    status.textContent = 'Alegeți datele, apoi Listare sau Export Excel.'; available();
  }
  function paintOutput() {
    tablesOwner.clear(); output.replaceChildren(); output.hidden = !showOutput.checked;
    if (showOutput.checked && state.model) state.model.blocks.forEach(block => paintReportBlock(block, output, tablesOwner));
  }
  showOutput.addEventListener('change', paintOutput);
  async function lookups() {
    if (!state.name || state.destroyed) return;
    const name = state.name, seq = ++state.lookupSeq;
    state.loading = true; state.spec = null; available(); status.textContent = 'Se încarcă datele de selecție…';
    // A failed lookup must not leave the previous report's selectable data on screen.
    selectionOwner.clear(); right.replaceChildren(el('h4', { textContent: 'Selectare date' })); selectionTree = null;
    try {
      const response = await api(`/api/vercon/reports/${encodeURIComponent(name)}/selection?${query(false)}`);
      if (state.destroyed || seq !== state.lookupSeq || name !== state.name) return;
      state.spec = response.spec;
      selectionTree = mountSelection(right, response, state.checked, ids => {
        state.checked = ids; invalidate();
      }, selectionOwner);
      state.checked = selectionTree.getCheckedLeaves();
      status.textContent = 'Alegeți datele, apoi Listare sau Export Excel.';
    } catch (error) {
      if (!state.destroyed && seq === state.lookupSeq) showMessage(status, error.message, 'error');
    } finally {
      if (!state.destroyed && seq === state.lookupSeq) { state.loading = false; available(); }
    }
  }
  async function generate(seq) {
    if (state.model) return state.model;
    const model = await api(`/api/vercon/reports/${encodeURIComponent(state.name)}/generate?${query()}`);
    if (state.destroyed || seq !== state.seq) return null;
    state.model = model; paintOutput();
    notes.replaceChildren(...model.notes.map(note => el('p', { className: 'vc-hint', textContent: note })));
    return model;
  }
  async function exportReport(format) {
    if (state.busy || state.loading || !state.name || selectionError(state.spec, state.checked) || !await owner.commit()) return;
    if (state.destroyed || state.busy || state.loading || selectionError(state.spec, state.checked)) return;
    const seq = state.seq;
    state.busy = true; available(); status.textContent = 'Se pregătește raportul…';
    try {
      let model = await generate(seq);
      if (!model) return;
      let data;
      try {
        data = await api(`/api/vercon/reports/export/${model.snapshot}/${format}?${new URLSearchParams(sel())}`, { responseType: 'arrayBuffer' });
      } catch (error) {
        if (error.reason !== 'REPORT_EXPIRED' || state.destroyed || seq !== state.seq) throw error;
        state.model = null; model = await generate(seq); if (!model) return;
        data = await api(`/api/vercon/reports/export/${model.snapshot}/${format}?${new URLSearchParams(sel())}`, { responseType: 'arrayBuffer' });
      }
      if (state.destroyed || seq !== state.seq) return;
      if (format === 'pdf') await pdfPreview(model.title, data, owner, () => !state.destroyed && seq === state.seq);
      else downloadFile(data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', model.filename + '.xlsx');
      if (!state.destroyed && seq === state.seq) status.textContent = `${state.label}: raport pregătit.`;
    } catch (error) {
      if (!state.destroyed && seq === state.seq) showMessage(status, error.message, 'error');
    } finally { if (!state.destroyed) { state.busy = false; available(); } }
  }
  list.addEventListener('click', () => void exportReport('pdf'));
  excel.addEventListener('click', () => void exportReport('xlsx'));
  const ready = (async () => {
    try {
      const data = await api(`/api/vercon/reports/catalog?${new URLSearchParams(sel())}`);
      if (state.destroyed) return;
      paintTree(center, reportNodes(data.reports), { title: 'Tipul raportului', onSelect: name => {
        ++state.lookupSeq; state.name = name; state.label = data.reports.find(item => item.id === name)?.label || name;
        state.checked = []; state.spec = null; invalidate(); void lookups();
      } }, owner);
      status.textContent = 'Alegeți raportul și perioada.';
    } catch (error) { if (!state.destroyed) showMessage(status, error.message, 'error'); }
  })();
  return { ready, isDirty: () => false,
    destroy() { state.destroyed = true; ++state.seq; ++state.lookupSeq; owner.clear(); host.replaceChildren(); } };
}
