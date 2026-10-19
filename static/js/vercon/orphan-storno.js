// Manual recovery of migrated FCS lines. Saved quantities and prices stay intact.
import { el, scope, modal, grid, column, field, showMessage } from './ui.js';
import { reasonBox } from '../utils/confirm-box.js';
import { numberText } from './receipt-number.js';
import { format } from './fields.js';

export async function orphanStorno({ api, url, json, owner, changed, report }) {
  const view = modal('Linii de storno fără recepție', owner);
  view.dialog.classList.add('vc-orphan-storno');
  const controls = scope(); view.scope.own(() => controls.clear());
  const message = el('p', { role: 'status' }), table = el('div'), actions = el('div', { className: 'vc-grid' });
  const link = el('button', { type: 'button', textContent: 'Leagă de o factură storno', disabled: true });
  const cancel = el('button', { type: 'button', textContent: 'Anulează linia', disabled: true });
  const reload = el('button', { type: 'button', textContent: 'Reîncarcă lista' });
  const close = el('button', { type: 'button', textContent: 'Închide' });
  view.body.append(el('p', { textContent: 'Selectați o linie și factura storno de care o legați sau anulați linia cu motiv. Liniile neasociate nu intră în stoc.' }), table, actions, message);
  view.footer.append(link, cancel, reload, close);
  let rows = [], documents = [], selected = null, target = null, sourceField = null, canWrite = false;
  let pending = null, busy = false, tableGrid = null, fieldError = '';
  const say = (text, bad = false) => showMessage(message, text, bad ? 'error' : 'info');
  const active = () => canWrite && selected && !selected.annulled && !busy && !pending;
  function buttons() {
    link.disabled = pending ? busy || pending.action !== 'link' : !active();
    cancel.disabled = pending ? busy || pending.action !== 'cancel' : !active();
    link.textContent = pending?.action === 'link' ? 'Reia aceeași asociere' : 'Leagă de o factură storno';
    cancel.textContent = pending?.action === 'cancel' ? 'Reia aceeași anulare' : 'Anulează linia';
    reload.disabled = close.disabled = busy || !!pending;
    table.inert = actions.inert = busy || !!pending;
  }
  function paintChoice() {
    controls.clear(); actions.replaceChildren(); sourceField = null;
    if (selected) actions.append(el('p', { textContent: `${selected.name} • ${format(selected.Cant, { type: 'decimal' })} ${selected.um || ''}${selected.annulled ? ` • anulat: ${selected.reason || ''}` : ''}` }));
    sourceField = field({ key: 'source', label: 'Factura storno', type: 'ref', required: true, showArrow: true,
      options: documents.map(doc => ({ value: doc.id, label: `FCS ${numberText(doc)} • ${doc.invoice || ''} • ${format(doc.date, { type: 'date' })} • ${doc.party || ''}` })) },
    target?.id, !!active(), value => { target = documents.find(doc => String(doc.id) === String(value)) || null; fieldError = ''; }, controls);
    sourceField.error.textContent = fieldError;
    actions.append(sourceField.node); buttons();
  }
  async function load() {
    busy = true; buttons(); say('Se încarcă liniile…');
    try {
      const answer = await api(url('/orphans')); if (view.closed) return;
      rows = answer.lines; documents = answer.documents; canWrite = answer.can_write;
      selected = null; target = null; fieldError = '';
      if (!tableGrid) tableGrid = grid(table, { rowKey: 'id', columns: [
        column({ key: 'id', label: 'Linie', type: 'int' }, { width: 70 }),
        column({ key: 'name', label: 'Produs' }, { width: 260 }), column({ key: 'um', label: 'UM' }, { width: 65 }),
        column({ key: 'Cant', label: 'Cantitate', type: 'decimal' }), column({ key: 'PUTVA', label: 'Preț cu TVA', type: 'decimal' }),
        column({ key: 'saved_at', label: 'Data salvării' }, { width: 180 }), column({ key: 'status', label: 'Stare' }),
      ].map(spec => ({ ...spec, editable: false })), rows: [], onSelect: row => {
        if (busy || pending) return;
        selected = row; target = null; fieldError = ''; paintChoice();
      } }, view.scope);
      tableGrid.setRows(rows.map(row => ({ ...row, status: row.annulled ? 'Anulat' : 'Fără recepție' })));
      say(!rows.length ? 'Nu există linii de storno fără recepție în contextul curent.' : canWrite ? 'Alegeți o linie din listă.' : 'Numai consultare: perioada este închisă sau lipsește dreptul de scriere.');
    } finally { busy = false; if (!view.closed) paintChoice(); }
  }
  async function write(action) {
    if (busy || !pending && !active()) return;
    if (!pending) {
      if (action === 'link') {
        // Typing in a reference field clears its hidden id until a choice is made.
        target = documents.find(doc => String(doc.id) === sourceField.input.value) || null;
        if (!target) { sourceField.error.textContent = 'Alegeți factura storno.'; say('Alegeți factura storno de care legați linia.', true); return; }
      }
      const reason = action === 'cancel' ? await reasonBox('Anulați linia de storno fără recepție?', { title: 'Anulare linie', yes: 'Anulează', no: 'Renunță' }) : null;
      if (action === 'cancel' && reason == null || view.closed) return;
      pending = { action, id: selected.id, body: { version: selected.version, source: target?.id,
        target_version: target?.version, reason, request_key: crypto.randomUUID() } };
    }
    busy = true; buttons(); say('Se salvează…');
    let applied = false;
    try {
      const answer = await json('POST', `/orphans/${pending.id}/${pending.action}`, pending.body);
      if (view.closed) return;
      pending = null; applied = true;
      await changed(answer.result);
      await load();
      say(action === 'link' ? 'Linia a fost asociată facturii storno.' : 'Linia a fost anulată.');
    } catch (error) {
      if (view.closed) return;
      fieldError = error.fields?.source || '';
      if (fieldError) sourceField.error.textContent = fieldError;
      if (error.status && error.status < 500) pending = null;
      say(applied ? `Operația a fost salvată; reîncărcarea a eșuat: ${error.message}` : pending
        ? 'Rezultatul nu este confirmat. Reluați aceeași operație; cererea păstrează aceeași cheie.' : error.message, true);
      report(error);
    } finally { busy = false; if (!view.closed) { if (!pending) paintChoice(); buttons(); } }
  }
  link.addEventListener('click', () => write('link').catch(report));
  cancel.addEventListener('click', () => write('cancel').catch(report));
  reload.addEventListener('click', () => load().catch(error => { say(error.message, true); report(error); }));
  close.addEventListener('click', () => { if (!busy && !pending) view.close(); });
  view.dialog.addEventListener('cancel', event => { if (busy || pending) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  const closed = new Promise(resolve => view.dialog.addEventListener('close', resolve, { once: true }));
  try { await load(); } catch (error) { if (!view.closed) say(error.message, true); report(error); }
  await closed;
}
