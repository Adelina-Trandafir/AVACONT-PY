// Period / report type / data selection, with the existing VERCON controls and theme.
import { el, scope, field, paintTree, pdfPreview, CheckBox } from './ui.js';
import { messageBox } from '../utils/confirm-box.js';
import { mountSelection, selectionHeader, selectionFilters, selectionError } from './report-selection.js';
import { reportNodes } from './report-result.js';
import { format as formatField } from './fields.js';
import { reportPeriod } from './report-period.js';
import { reportPeriodAction } from './report-period-action.js';

export function mountReports(host, { api, sel }) {
  const owner = scope(), selectionOwner = scope();
  owner.own(() => selectionOwner.clear());
  const year = Number(sel().year);
  let dateOnly = false;
  const state = { start: `${year}-01-01`, end: `${year}-12-31`, name: null, label: '', spec: null, report: null, category: null,
    checked: [], documents: [], documentMode: false, selectionKind: '', model: null, seq: 0, lookupSeq: 0, destroyed: false, loading: false, selectionLoading: false, busy: false };
  let selectionTree, errorBox, periodCatalog;
  owner.own(() => errorBox?.close());
  const root = el('section', { className: 'vc-reports' });
  const periodTitle = el('h4', { textContent: 'Selectează perioada' });
  const left = el('aside', { className: 'vc-reports__period' }, periodTitle);
  const periods = reportPeriod(owner, (start, finish) => {
    state.start = start; state.end = finish;
    begin.input._picker.setValue(new Date(state.start + 'T12:00:00'), false);
    end.input._picker.setValue(new Date(state.end + 'T12:00:00'), false);
    invalidate(); void lookups();
  });
  const begin = field({ key: 'start', label: 'Dată început', type: 'date', required: true }, state.start, true,
    value => { state.start = value; periods.sync(state.start, state.end); invalidate(); void lookups(); }, owner);
  const end = field({ key: 'end', label: 'Dată sfârșit', type: 'date', required: true }, state.end, true,
    value => {
      state.end = value;
      periods.sync(state.start, state.end); invalidate(); void lookups();
    }, owner);
  const options = el('section', { className: 'vc-reports__options', hidden: true }, el('h4', { textContent: 'Opțiuni afișare' }));
  const balanceOptions = el('div', { hidden: true }); options.append(balanceOptions);
  const pageBreakAccounts = owner.own(new CheckBox(balanceOptions, { label: 'Grupează pe cont, cu pagină nouă',
    checked: false, className: 'vc-reports__check', onChange: invalidate })).input;
  const showWithoutMovements = owner.own(new CheckBox(balanceOptions, { label: 'Afiseaza FARA miscari',
    checked: false, className: 'vc-reports__check', onChange: invalidate })).input;
  const documentOptions = el('div', { hidden: true }); options.append(documentOptions);
  const showDocuments = owner.own(new CheckBox(documentOptions, { label: 'Afișează Documente',
    checked: false, className: 'vc-reports__check', onChange: () => {
      if (!state.report?.document_options) return;
      state.checked = []; state.documents = []; invalidate(); void lookups();
    } })).input;
  const receiverOptions = el('div', { hidden: true }); options.append(receiverOptions);
  const showReceivers = owner.own(new CheckBox(receiverOptions, { label: 'Afișează Primitori',
    checked: false, className: 'vc-reports__check', onChange: () => {
      if (!state.report?.receiver_options) return;
      state.checked = []; state.documents = []; invalidate(); void lookups();
    } })).input;
  const depreciationOptions = el('div', { hidden: true }); options.append(depreciationOptions);
  const groupByReceivers = owner.own(new CheckBox(depreciationOptions, { label: 'Grupează după Primitori',
    checked: false, className: 'vc-reports__check', onChange: () => {
      if (state.report?.mf_selection !== 'depreciation') return;
      state.checked = []; state.documents = []; invalidate(); void lookups();
    } })).input;
  left.append(periods.node, begin.node, end.node, options);
  const center = el('section', { className: 'vc-reports__types' });
  const right = el('section', { className: 'vc-reports__selection' }, selectionHeader(selectionOwner).node,
    el('p', { className: 'vc-hint', textContent: 'Alegeți mai întâi tipul raportului.' }));
  const excel = el('button', { type: 'button', textContent: 'Export Excel', disabled: true });
  const list = el('button', { type: 'button', textContent: 'Listare', disabled: true, className: 'is-primary' });
  const actions = el('div', { className: 'vc-actions vc-reports__actions' }, excel, list);
  root.append(left, center, right, actions); host.replaceChildren(root);

  const selectionKind = report => {
    if (report.mf_selection) return 'mf-' + report.mf_selection;
    if (report.outgoing_selection) {
      if (report.receiver_options) return showReceivers.checked ? 'outgoing-receivers' : 'outgoing-accounts-documents';
      return showDocuments.checked ? 'outgoing-documents-products' : 'outgoing-accounts-documents-products';
    }
    if (report.document_options && showDocuments.checked) return 'documents';
    if (report.id === 'NIR') return 'incoming-documents';
    return report.incoming_selection ? 'incoming-products' : 'products';
  };
  const query = (withFilters = true) => new URLSearchParams({ ...sel(), start: dateOnly ? state.end : state.start, end: state.end,
    ...(state.report?.mf_selection === 'depreciation' ? { group_by_receivers: groupByReceivers.checked ? '1' : '0' } : {}),
    ...(withFilters && state.spec?.kind === 'balance' ? {
      page_break_accounts: pageBreakAccounts.checked ? '1' : '0',
      show_without_movements: showWithoutMovements.checked ? '1' : '0',
    } : {}),
    ...(withFilters ? selectionTree?.getFilters(state.spec) ?? selectionFilters(state.checked) : {}) });
  function available() {
    const error = periodError() || selectionError(state.spec, state.checked);
    const disabled = !state.name || state.loading || state.selectionLoading || state.busy || !!error;
    excel.disabled = list.disabled = disabled;
    excel.title = list.title = error || '';
    actions.setAttribute('aria-busy', String(state.loading || state.selectionLoading || state.busy));
  }
  function reportError(error) {
    if (state.destroyed) return;
    errorBox?.close();
    errorBox = messageBox(error.message || 'Raportul nu a putut fi pregătit.', { title: 'Rapoarte', warning: true });
  }
  function periodError() {
    if (dateOnly) return state.end ? '' : 'Completați data raportului.';
    if (!state.start || !state.end) return 'Completați data de început și data de sfârșit.';
    return state.start > state.end ? 'Data de început trebuie să fie înaintea datei de sfârșit.' : '';
  }
  function invalidate() {
    ++state.seq; state.model = null;
    available();
  }
  async function lookups() {
    if (!state.name || state.destroyed) return;
    const name = state.name, seq = ++state.lookupSeq;
    state.documentMode = !!state.report.document_options && showDocuments.checked;
    state.selectionKind = selectionKind(state.report);
    state.loading = true; state.selectionLoading = false; state.spec = null; state.documents = []; available();
    // A failed lookup must not leave the previous report's selectable data on screen.
    selectionOwner.clear(); right.replaceChildren(selectionHeader(selectionOwner).node); selectionTree = null;
    if (periodError()) { state.loading = false; available(); return; }
    try {
      const endpoint = `/api/vercon/reports/${encodeURIComponent(name)}/selection`;
      const base = query(false);
      if (state.documentMode) base.set('selection_mode', 'documents');
      else if (state.selectionKind === 'outgoing-receivers') base.set('selection_mode', 'receivers');
      const initial = new URLSearchParams(base); initial.set('branch', 'root');
      const response = await api(`${endpoint}?${initial}`);
      if (state.destroyed || seq !== state.lookupSeq) return;
      state.spec = state.report;
      state.documents = response.documents || [];
      selectionTree = mountSelection(right, response, state.checked, ids => {
        state.checked = ids; invalidate();
      }, selectionOwner, {
        loadBranch: (branch, recursive, signal) => {
          const params = new URLSearchParams(base); params.set('branch', branch);
          if (recursive) params.set('recursive', '1');
          return api(`${endpoint}?${params}`, { signal });
        },
        onDocuments: documents => {
          if (!state.destroyed && seq === state.lookupSeq) state.documents = documents;
        },
        onBusy: value => {
          if (!state.destroyed && seq === state.lookupSeq) { state.selectionLoading = value; available(); }
        },
        onError: reportError,
      });
      await selectionTree.ready;
      if (state.destroyed || seq !== state.lookupSeq) return;
      state.checked = selectionTree.getCheckedLeaves();
    } catch (error) {
      if (!state.destroyed && seq === state.lookupSeq) { state.spec = null; reportError(error); }
    } finally {
      if (!state.destroyed && seq === state.lookupSeq) { state.loading = false; available(); }
    }
  }
  async function generate(seq) {
    if (state.model) return state.model;
    const model = await api(`/api/vercon/reports/${encodeURIComponent(state.name)}/generate?${query()}`);
    if (state.destroyed || seq !== state.seq) return null;
    state.model = model;
    return model;
  }
  function previewDetails() {
    const period = { start: state.spec?.kind === 'depreciation' ? state.end : state.start, end: state.end };
    if (!state.spec.filters.includes('document')) return { period };
    const selected = [...new Set(state.checked.flatMap(id => id.startsWith('document:') ? [id.slice('document:'.length)]
      : id.startsWith('outgoing-item:') ? [id.split(':')[3]] : id.startsWith('mf-item:bons:') ? [id.split(':')[4]] : []))];
    const documents = state.documents.filter(item => selected.includes(String(item.id)));
    if (documents.length === 1) return { documentLabel: documents[0].label.replace(/\b\d{4}-\d{2}-\d{2}\b/g, date => formatField(date, { type: 'date' })) };
    return { documentLabel: selected.length ? `${selected.length} documente` : '', period };
  }
  async function exportReport(format) {
    if (state.destroyed || state.busy || state.loading || state.selectionLoading || !state.name || periodError() || selectionError(state.spec, state.checked)) return;
    const seq = state.seq;
    const current = () => !state.destroyed && seq === state.seq;
    const details = previewDetails();
    state.busy = true; available();
    try {
      await pdfPreview(state.label, async view => {
        if (!await owner.commit() || !current() || view.closed) return null;
        let model = await generate(seq);
        if (!model || view.closed || !current()) return null;
        let data;
        try {
          data = await api(`/api/vercon/reports/export/${model.snapshot}/${format}?${new URLSearchParams(sel())}`, { responseType: 'arrayBuffer' });
        } catch (error) {
          if (error.reason !== 'REPORT_EXPIRED' || !current() || view.closed) throw error;
          state.model = null; model = await generate(seq);
          if (!model || view.closed || !current()) return null;
          data = await api(`/api/vercon/reports/export/${model.snapshot}/${format}?${new URLSearchParams(sel())}`, { responseType: 'arrayBuffer' });
        }
        if (!current() || view.closed) return null;
        const snapshot = model.snapshot, filename = model.filename, selection = new URLSearchParams(sel()).toString();
        const exportExcel = async (preview, progress) => {
          if (!current() || preview.closed || progress.closed) return null;
          const data = await api(`/api/vercon/reports/export/${snapshot}/xlsx?${selection}`, { responseType: 'arrayBuffer' });
          if (!current() || preview.closed || progress.closed) return null;
          return { data, filename: filename + '.xlsx', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' };
        };
        return { data, title: model.title, filename: filename + '.' + format, exportExcel,
          toolbarActions: [reportPeriodAction({ api, name: state.name, params: query(), catalog: periodCatalog, dateOnly, isCurrent: current })],
          downloadOnly: format === 'xlsx', mime: format === 'xlsx'
            ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'application/pdf' };
      }, owner, current, details);
    } catch (error) {
      if (current()) reportError(error);
    } finally { if (!state.destroyed) { state.busy = false; available(); } }
  }
  list.addEventListener('click', () => void exportReport('pdf'));
  excel.addEventListener('click', () => void exportReport('xlsx'));
  const ready = (async () => {
    try {
      const data = await api(`/api/vercon/reports/catalog?${new URLSearchParams(sel())}`);
      if (state.destroyed) return;
      periodCatalog = data;
      periods.configure(data, begin, end);
      const reports = data.reports;
      const nodes = reportNodes(reports), categories = new Map();
      const indexCategories = (items, category = null, depth = 0) => {
        for (const node of items) {
          if (node.id.startsWith('doc:')) categories.set(node.id.slice(4), category);
          else indexCategories(node.children || [], depth <= 1 ? node.id : category, depth + 1);
        }
      };
      indexCategories(nodes);
      const selectReport = name => {
        const report = reports.find(item => item.id === name);
        if (!report || (name === state.name && (selectionTree || state.loading))) return;
        const category = categories.get(name);
        const sameCategory = category != null && category === state.category
          && !report.mf_selection && !state.report?.mf_selection;
        balanceOptions.hidden = report?.kind !== 'balance';
        documentOptions.hidden = !report.document_options;
        receiverOptions.hidden = !report.receiver_options;
        depreciationOptions.hidden = report.mf_selection !== 'depreciation';
        options.hidden = balanceOptions.hidden && documentOptions.hidden && receiverOptions.hidden && depreciationOptions.hidden;
        dateOnly = report?.kind === 'depreciation';
        periodTitle.textContent = dateOnly ? 'Selectează data' : 'Selectează perioada';
        periods.node.hidden = begin.node.hidden = dateOnly;
        begin.input._picker.field.disabled = begin.input._picker.button.disabled = dateOnly;
        end.node.firstElementChild.textContent = (dateOnly ? 'La data' : 'Dată sfârșit') + ' *';
        end.input.setAttribute('aria-label', dateOnly ? 'La data' : 'Dată sfârșit');
        end.input._picker.field.setAttribute('aria-label', dateOnly ? 'La data' : 'Dată sfârșit');
        state.name = name; state.label = report.label; state.report = report; state.category = category;
        // MF reports have distinct trees and reload on report changes. The other
        // reports retain the existing category-based selection reuse.
        if (sameCategory && (selectionTree || state.loading)) {
          state.spec = state.loading ? null : report; invalidate();
          return;
        }
        ++state.lookupSeq; state.checked = []; state.spec = null; invalidate(); return lookups();
      };
      center._vcExpanded = new Set(nodes.length ? [nodes[0].id] : []);
      const reportsTree = paintTree(center, nodes, { title: 'Tipul raportului', collapsed: true, exclusiveRoots: true, autoCollapse: true,
        onSelect: name => { void selectReport(name); } }, owner);
      // Report navigation always keeps one sibling branch open, including Shift+click.
      const toggleReportNode = reportsTree.toggleNode.bind(reportsTree);
      reportsTree.toggleNode = id => toggleReportNode(id);
    } catch (error) { if (!state.destroyed) reportError(error); }
  })();
  return { ready, isDirty: () => false,
    destroy() { state.destroyed = true; ++state.seq; ++state.lookupSeq; owner.clear(); host.replaceChildren(); } };
}
