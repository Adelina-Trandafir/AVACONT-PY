// Existing preview protocol, with the same shared controls as the real document screens.
import { confirmBox } from '../utils/confirm-box.js';
import { format, parse } from './fields.js';
import { el, scope, field, column, grid, rowMenu, snapshotPreview, showMessage, statusBar } from './ui.js';
export function mountEditor(host, opts) {
  const { draft, columns, saver } = opts, headerUi = scope(), linesUi = scope(), dialogs = scope(), inputs = new Map();
  let disposed = false, linesGrid, baseline = snapshot();
  const status = el('span', { className: 'vc-save-status', role: 'status' }), header = el('div', { className: 'vc-grid vc-doc__header' }), linesHost = el('div', { className: 'vc-doc__lines' });
  const saveButton = el('button', { type: 'submit', textContent: 'Salvează', className: 'is-primary' });
  const reconcileButton = el('button', { type: 'button', textContent: 'Verifică salvarea', hidden: true }), cancelButton = el('button', { type: 'button', textContent: 'Renunță' }), previewButton = el('button', { type: 'button', textContent: 'Previzualizare' });
  const bar = statusBar(el('div', { className: 'vc-actions vc-doc__bar' }), { start: [cancelButton, previewButton], status, end: [reconcileButton, saveButton] });
  const form = el('form', { noValidate: true, className: 'vc-doc__main' }, el('h3', { textContent: 'Document • previzualizare' }), header, linesHost, bar); host.replaceChildren(form);
  const say = (text, bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const dirty = () => draft.dirty || headerUi.dirty() || linesUi.dirty();
  function snapshot() { return { header: draft.header(), rows: draft.rows() }; }
  async function commit() { return await headerUi.commit() && await linesUi.commit(); }
  function paintHeader() {
    headerUi.clear(); inputs.clear(); header.replaceChildren();
    for (const spec of columns.header) { const f = field(spec, draft.header()[spec.key], true, (raw) => draft.setHeader(spec.key, raw), headerUi); inputs.set(spec.key, f); header.append(f.node); }
  }
  function paintLines() {
    linesUi.clear(); linesHost.replaceChildren(); const body = el('div'); linesHost.append(body);
    const rows = draft.rows().map((row, index) => ({ key: row.key, ordinal: index + 1, ...row.cells }));
    linesGrid = grid(body, { layoutId: 'vercon.preview.' + opts.form, rowKey: 'key', rows, editable: !saver.hasUnknown,
      columns: [{ key: 'ordinal', title: '#', width: 42, editable: false }, ...columns.lines.map((spec) => column(spec, { editable: true, width: spec.type === 'text' ? 230 : 125 })), { key: 'actions', title: '⋮', width: 42, editable: false, display: 'button', actionText: '⋮' }],
      footer: true, footerCaption: '', footerAction: { label: '+ Adaugă rând', span: 2, disabled: saver.hasUnknown }, onFooterAction: async () => {
        if (saver.hasUnknown || !await commit()) return; const rowKey = draft.addRow(); paintLines(); const row = linesGrid._rows.find((item) => item.key === rowKey);
        linesGrid.beginNewRowEdit(row, columns.lines[0]?.key, () => { draft.removeRow(rowKey); paintLines(); });
      },
      onCellSave: ({ row, key, value }) => { draft.setCell(row.key, key, value); return { [key]: value }; }, onEditError: ({ error }) => report(error),
      onCellAction: ({ row }) => rowMenu(linesGrid, row, [{ label: 'Elimină rândul', disabled: saver.hasUnknown, run: async () => { if (!await commit()) return; draft.removeRow(row.key); paintLines(); } }]),
    }, linesUi);
  }
  function freezeUnknown() {
    header.inert = saver.hasUnknown; linesHost.inert = saver.hasUnknown; saveButton.disabled = saver.hasUnknown; cancelButton.disabled = saver.hasUnknown; reconcileButton.hidden = !saver.hasUnknown;
  }
  function saved(result) {
    draft.markSaved(); baseline = snapshot(); opts.onSaved(result.revision); freezeUnknown(); say(result.replayed ? 'Salvat (cererea fusese deja aplicată).' : 'Salvat.');
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); if (saver.hasUnknown || !await commit()) return;
    for (const input of inputs.values()) input.error.textContent = '';
    const data = draft.collect(columns, parse);
    if (data.errors) {
      for (const error of data.errors) if (error.where === 'header') inputs.get(error.field).error.textContent = error.message;
      say(data.errors.map((error) => (error.where === 'header' ? '' : 'Rând ' + (draft.rows().findIndex((row) => row.key === error.where) + 1) + ': ') + error.message).join(' '), true); return;
    }
    if (!opts.saveUrl) { say('Salvarea nu este disponibilă pentru acest ecran.', true); return; } form.inert = true; say('Se salvează…');
    try {
      const result = await saver.save({ form: opts.form, subunit: draft.context.subunit, gestiune: draft.context.gestiune, base_revision: opts.getRevision(), header: data.header, lines: data.lines }, opts.simulate ? { 'X-Preview-Simulate': opts.simulate() } : null);
      if (disposed) return;
      if (result.outcome === 'saved') saved(result);
      else if (result.outcome === 'conflict') say((result.message || 'Documentul a fost modificat.') + ' Datele introduse au rămas în ciornă.', true);
      else say(result.message || 'Salvarea nu a fost confirmată.', true);
      freezeUnknown();
    } catch (error) { report(error); } finally { if (!disposed) { form.inert = false; freezeUnknown(); } }
  });
  reconcileButton.addEventListener('click', async () => {
    form.inert = true; try {
      const result = await saver.reconcile(); if (disposed) return;
      if (result.outcome === 'saved') saved(result);
      else if (result.outcome === 'not-applied') say('Salvarea nu fusese aplicată. Puteți salva din nou.');
      else say(result.message || 'Salvarea nu a putut fi confirmată.', true); freezeUnknown();
    } catch (error) { report(error); } finally { if (!disposed) { form.inert = false; freezeUnknown(); } }
  });
  cancelButton.addEventListener('click', async () => {
    if (saver.hasUnknown || dirty() && !await confirmBox('Renunțați la modificările nesalvate?', { title: 'Renunțare', yes: 'Renunț', no: 'Rămân aici' }) || disposed) return;
    for (const row of draft.rows()) draft.removeRow(row.key);
    for (const spec of columns.header) draft.setHeader(spec.key, baseline.header[spec.key] ?? '');
    for (const row of baseline.rows) { const id = draft.addRow(); for (const [name, value] of Object.entries(row.cells)) draft.setCell(id, name, value); }
    draft.markSaved(); paintHeader(); paintLines(); say('');
  });
  previewButton.addEventListener('click', async () => {
    if (!await commit() || disposed) return;
    try { await snapshotPreview('Document • ciornă', [
      { title: 'Antet', columns: [{ key: 'label', title: 'Câmp', width: 240 }, { key: 'value', title: 'Valoare', width: 440 }], rows: columns.header.map((spec) => ({ label: spec.label, value: format(draft.header()[spec.key], spec) })) },
      { title: 'Produse', columns: columns.lines.map((spec) => column(spec, { editable: false })), rows: draft.rows().map((row) => ({ ...row.cells })) },
    ], dialogs, () => !disposed, { documentLabel: draft.header().Numar ? 'nr. ' + draft.header().Numar : '', date: draft.header().Data }); } catch (error) { report(error); }
  });
  paintHeader(); paintLines();
  return { isDirty: dirty, destroy: () => { disposed = true; headerUi.clear(); linesUi.clear(); dialogs.clear(); host.replaceChildren(); } };
}
