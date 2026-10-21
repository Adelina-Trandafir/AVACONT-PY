// Manual repair of the OI/MF register: receipt positions that arrived from the old program without a product. Only the product is written.
import { el, scope, modal, grid, column, field, showMessage } from './ui.js';

const STATUS = { sure: 'Sigur', order: 'De verificat', choose: 'De ales', none: 'Fără linie potrivită' };

export async function repairRegister({ api, url, json, owner, changed, report }) {
  const view = modal('Reglare registru obiecte de inventar', owner);
  view.dialog.classList.add('vc-register-repair');
  const controls = scope(); view.scope.own(() => controls.clear());
  const message = el('p', { role: 'status' }), table = el('div'), actions = el('div', { className: 'vc-grid' });
  const applySure = el('button', { type: 'button', textContent: 'Aplică propunerile sigure', disabled: true });
  const applyOne = el('button', { type: 'button', textContent: 'Aplică produsul ales', disabled: true });
  const reload = el('button', { type: 'button', textContent: 'Reîncarcă lista' });
  const close = el('button', { type: 'button', textContent: 'Închide' });
  view.body.append(el('p', { textContent: 'Unele poziții din registru au venit din programul vechi fără produs. Produsul se alege dintre liniile bonului cu aceeași cantitate și același preț. «Sigur»: există o singură variantă. «De verificat»: mai multe produse la fel de potrivite, propuse în ordinea liniilor; verificați-le înainte de aplicare. «De ales»: alegeți voi produsul.' }), table, actions, message);
  view.footer.append(applySure, applyOne, reload, close);
  let rows = [], products = {}, selected = null, choice = null, canWrite = false, busy = false, pending = null, tableGrid = null, problem = null;
  const say = (text, bad = false) => showMessage(message, text, bad ? 'error' : 'info');
  const name = variant => products[variant] ? `${products[variant].name}${products[variant].um ? ` (${products[variant].um})` : ''}` : `Varianta ${variant}`;
  const sure = () => rows.filter(row => row.status === 'sure');
  const active = () => canWrite && !busy && !pending;
  function buttons() {
    applySure.disabled = pending ? busy || pending.kind !== 'sure' : !(active() && sure().length);
    applyOne.disabled = pending ? busy || pending.kind !== 'one' : !(active() && selected && choice);
    applySure.textContent = pending?.kind === 'sure' ? 'Reia aceeași aplicare' : `Aplică propunerile sigure (${sure().length})`;
    applyOne.textContent = pending?.kind === 'one' ? 'Reia aceeași aplicare' : 'Aplică produsul ales';
    reload.disabled = close.disabled = busy || !!pending;
    table.inert = actions.inert = busy || !!pending;
  }
  function paintChoice() {
    controls.clear(); actions.replaceChildren();
    if (selected) {
      actions.append(el('p', { textContent: `Poziția ${selected.id} • ${selected.family.toUpperCase()} ${selected.number} • cantitate ${selected.Cant} • preț ${selected.PUTVA}` }));
      if (!selected.choices.length) actions.append(el('p', { textContent: 'Bonul nu are nicio linie liberă cu această cantitate și acest preț, deci produsul nu se poate alege de aici. Verificați bonul.' }));
      else actions.append(field({ key: 'variant', label: 'Produs', type: 'ref', required: true, showArrow: true,
        options: selected.choices.map(variant => ({ value: variant, label: name(variant) })) },
      choice, !!active(), value => { choice = value ? Number(value) : null; buttons(); }, controls).node);
    }
    buttons();
  }
  async function load() {
    busy = true; buttons(); say('Se încarcă pozițiile…');
    try {
      const answer = await api(url('/register')); if (view.closed) return;
      products = answer.products; problem = answer.problem; canWrite = answer.can_write; selected = null; choice = null;
      rows = answer.rows.map(row => ({ ...row, status: row.choices.length ? row.status : 'none' }));
      if (!tableGrid) tableGrid = grid(table, { rowKey: 'id', columns: [
        column({ key: 'id', label: 'Poziție', type: 'int' }, { width: 80 }), column({ key: 'bon', label: 'Bon' }, { width: 110 }),
        column({ key: 'Cant', label: 'Cantitate', type: 'decimal' }), column({ key: 'PUTVA', label: 'Preț cu TVA', type: 'decimal' }),
        column({ key: 'product', label: 'Produs propus' }, { width: 300 }), column({ key: 'state', label: 'Stare' }, { width: 150 }),
      ].map(spec => ({ ...spec, editable: false })), rows: [], onSelect: shown => {
        if (busy || pending) return;
        selected = rows.find(row => row.id === shown.id) || null; choice = selected?.proposal ?? null; paintChoice();
      } }, view.scope);
      tableGrid.setRows(rows.map(row => ({ id: row.id, bon: `${row.family.toUpperCase()} ${row.number}`, Cant: row.Cant, PUTVA: row.PUTVA,
        product: row.proposal ? name(row.proposal) : '', state: STATUS[row.status] })));
      const tail = problem ? ` Registrul mai are o problemă: ${problem}` : '';
      say(!rows.length ? (problem ? `Nu mai sunt poziții fără produs.${tail}` : 'Nu există poziții fără produs. Nu este nimic de reglat.')
        : `${rows.length} poziții fără produs (${sure().length} sigure).${canWrite ? '' : ' Numai consultare: perioada este închisă.'}${tail}`, !!problem);
    } finally { busy = false; if (!view.closed) paintChoice(); }
  }
  async function write(kind) {
    if (busy || !pending && !active()) return;
    if (!pending) {
      const picked = kind === 'sure' ? sure().map(row => ({ id: row.id, variant: row.proposal, version: row.version }))
        : [{ id: selected.id, variant: choice, version: selected.version }];
      pending = { kind, body: { assignments: picked, request_key: crypto.randomUUID() } };
    }
    busy = true; buttons(); say('Se salvează…');
    let applied = false;
    try {
      const answer = await json('POST', '/register/product', pending.body);
      if (view.closed) return;
      pending = null; applied = true;
      await changed({ document_id: null });
      await load();
      say(`${answer.result.applied} poziții reglate.${answer.result.problem ? ` Registrul mai are o problemă: ${answer.result.problem}` : ' Registrul este în regulă.'}`, !!answer.result.problem);
    } catch (error) {
      if (view.closed) return;
      if (error.status && error.status < 500) pending = null;
      say(applied ? `Operația a fost salvată; reîncărcarea a eșuat: ${error.message}` : pending
        ? 'Rezultatul nu este confirmat. Reluați aceeași operație; cererea păstrează aceeași cheie.' : error.message, true);
      report(error);
    } finally { busy = false; if (!view.closed) { if (!pending) paintChoice(); buttons(); } }
  }
  applySure.addEventListener('click', () => write('sure').catch(report));
  applyOne.addEventListener('click', () => write('one').catch(report));
  reload.addEventListener('click', () => load().catch(error => { say(error.message, true); report(error); }));
  close.addEventListener('click', () => { if (!busy && !pending) view.close(); });
  view.dialog.addEventListener('cancel', event => { if (busy || pending) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  const closed = new Promise(resolve => view.dialog.addEventListener('close', resolve, { once: true }));
  try { await load(); } catch (error) { if (!view.closed) say(error.message, true); report(error); }
  await closed;
}
