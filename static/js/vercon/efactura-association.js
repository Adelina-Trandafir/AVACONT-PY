import { el, Combobox, CheckBox, statusBar } from './ui.js';
import { compareTyped } from '../utils/typed-order.js';
import { proposals, units, quantity } from './efactura-match.js';
import { confirmBox, pickBox } from '../utils/confirm-box.js';
import { associationGrid } from './efactura-association-grid.js';
import { sourceGrids } from './efactura-source-grids.js';
import { tryAutoAssociate } from './efactura-auto-association.js';

export async function associateEfactura({ api, sel, doc }) {
  const saved = await tryAutoAssociate({ api, sel, doc });   // certain first association: saved without the window
  if (saved) return saved;
  const query = extra => new URLSearchParams({ ...sel(), ...extra });
  const read = (path, extra = {}) => api(`/api/vercon/doc/nir/efactura${path}?${query(extra)}`);
  const dialog = el('dialog', { className: 'vc-efi vc-efi--association', 'aria-label': 'Asociere NIR cu eFactura' });
  const status = el('p', { className: 'vc-efi__status vc-save-status', role: 'status' });
  const invoiceHost = el('div', { className: 'vc-efi__invoice' });
  const filterButton = el('button', { type: 'button', textContent: 'Afișare: Active', disabled: true });
  const inactiveHost = el('div', { className: 'vc-efi__visibility' });
  const inactive = new CheckBox(inactiveHost, { label: 'Inactiv', disabled: true,
    onChange: value => { void changeInactive(value); } });
  const panels = el('div', { className: 'vc-efi__panels' }), matches = el('div', { className: 'vc-efi__matches' });
  const otherReceipts = el('div', { className: 'vc-efi__other-receipts' });
  const saveButton = el('button', { type: 'button', className: 'is-primary', textContent: 'Salvează asocierile', disabled: true });
  const pairButton = el('button', { type: 'button', textContent: '🔗 Asociaza', title: 'Asociază rândurile selectate', disabled: true });
  const proposeButton = el('button', { type: 'button', textContent: '💡 Propune', title: 'Propune potriviri', disabled: true });
  const closeButton = el('button', { type: 'button', textContent: 'Închide' });
  const work = el('fieldset', { className: 'vc-efi__work', disabled: true }, otherReceipts, panels, matches);
  dialog.append(el('header', {}, el('h2', { textContent: `Asociere NIR ${doc.number} cu eFactura` })),
    el('div', { className: 'vc-efi__body' },
      el('div', { className: 'vc-efi__actions' }, invoiceHost, pairButton, proposeButton, filterButton, inactiveHost), work),
    statusBar(el('footer'), { start: [closeButton], status, end: [saveButton] }));
  document.body.append(dialog); dialog.showModal();
  let data = null, links = [], busy = false, pending = null, dirty = false, closed = false, selectedNir = null, selectedEf = null;
  let selectedInvoice = null, invoiceOptions = [], visibility = 'active', visibilityAvailable = false;
  let matchesGrid = null, products = null;
  const invoice = new Combobox(invoiceHost, { placeholder: 'Alegeți factura eFactura…', dropDownStyle: 'simple',
    allowEmpty: false, maxResults: 500, dropDownMinWidth: 480, staticData: [],
    onSelect: id => { void load(Number(id)); } });
  invoice.input.setAttribute('aria-label', 'Factura eFactura');
  const selectInvoice = id => invoice.setValue(id ?? '', invoiceOptions.find(item => item.value === id)?.label || '');
  let resolve; const done = new Promise(r => { resolve = r; });
  const say = (text, bad = false) => { status.textContent = text; status.dataset.state = bad ? 'error' : 'info'; };
  const controls = () => {
    if (closed) return;
    work.disabled = busy || !!pending || !data; invoice.setEnabled(!busy && !pending && invoiceOptions.length > 0);
    filterButton.disabled = busy || !!pending;
    inactive.checked = !!invoiceOptions.find(item => item.value === selectedInvoice)?.inactive;
    inactive.setEnabled(!busy && !pending && visibilityAvailable && selectedInvoice != null);
    inactiveHost.title = visibilityAvailable ? 'Ascunde factura pentru toți gestionarii unității. Debifați pentru restaurare.'
      : 'Marcajul Inactiv necesită instalarea manuală a sql/EV_03_vercon_facturi_inactive.sql.';
    saveButton.disabled = busy || !data; closeButton.disabled = busy || !!pending;
    proposeButton.disabled = busy || !!pending || !data;
    pairButton.disabled = busy || !!pending || !selectedNir || !selectedEf;
    saveButton.textContent = pending ? 'Reia aceeași salvare' : 'Salvează asocierile';
    matchesGrid?.setBusy(busy || !!pending || !data);
    products?.setBusy(busy || !!pending || !data);
  };
  const close = value => { if (busy || pending) return; closed = true; invoice.destroy(); inactive.destroy(); matchesGrid?.destroy(); products?.destroy(); matchesGrid = products = null; dialog.close(); dialog.remove(); resolve(value); };
  const available = (line, side) => (units(line.left) ?? 0n) - links.filter(l => l[side] === line.id)
    .reduce((sum, l) => sum + (units(l[side === 'line' ? 'qty' : 'nir_qty']) ?? 0n), 0n);
  matchesGrid = associationGrid(matches, {
    data: () => data, links: () => links, canEdit: () => !!data && !busy && !pending,
    changed: () => { dirty = true; products?.refresh(); controls(); },
    remove: (nid, eid) => { links = links.filter(link => link.nir_line !== nid || link.line !== eid); dirty = true; paint(); },
    error: message => say(message, true), editState: controls,
  });

  products = sourceGrids(panels, {
    data: () => data, available, canMove: () => !!data && !busy && !pending,
    commit: () => matchesGrid.commit(), pair,
    selected: (side, id) => { if (side === 'line') selectedEf = id; else selectedNir = id; controls(); },
    error: message => say(message, true),
  });

  function paint() {
    products.refresh(); matchesGrid.refresh();
    otherReceipts.replaceChildren();
    if (data.other_receipts.length) otherReceipts.append(el('strong', { textContent: 'Alte NIR-uri asociate acestei facturi: ' }),
      el('span', { textContent: [...data.other_receipts].sort((a, b) => compareTyped(a.subunit, b.subunit, 'text') || compareTyped(a.warehouse, b.warehouse, 'text')
        || compareTyped(a.date, b.date, 'date') || compareTyped(a.number, b.number, 'number') || compareTyped(a.sub_number, b.sub_number, 'number'))
        .map(r => `${r.subunit} / ${r.warehouse}: NIR ${r.number}${r.sub_number ? '/' + r.sub_number : ''} din ${r.date?.slice(0, 10) || ''}`).join('; ') }));
    controls();
  }

  async function pair(nid, eid, learn = false) {
    if (busy || pending || !data || !await matchesGrid.commit()) return;
    if (links.some(l => l.line === eid && l.nir_line === nid)) { say('Această pereche există deja. Modificați cantitățile din tabel.'); return; }
    const n = data.nir_lines.find(l => l.id === nid), e = data.lines.find(l => l.id === eid);
    if (!n || !e) return;
    const nq = available(n, 'nir_line'), eq = available(e, 'line');
    if (nq <= 0n || eq <= 0n) { say('Un rând nu mai are cantitate disponibilă.', true); return; }
    const qty = quantity(nq < eq ? nq : eq);
    links.push({ line: eid, nir_line: nid, qty, nir_qty: qty, learn, reason: learn ? 'Drag and drop' : 'Asociere din buton' });
    dirty = true; paint(); say('Cantități completate automat. Le puteți modifica în grilă, apoi salvați asocierile.');
  }

  async function load(id) {
    if (closed || busy || pending || !invoiceOptions.some(item => item.value === id)) return;
    if (!await matchesGrid.commit()) { selectInvoice(selectedInvoice); return; }
    if (closed || id === selectedInvoice && data?.id === id) return;
    if (dirty && !await confirmBox('Renunțați la asocierile nesalvate și încărcați factura aleasă?',
      { title: 'Schimbare factură eFactura', yes: 'Schimbă factura', no: 'Păstrează asocierile' })) { selectInvoice(selectedInvoice); return; }
    if (closed) return;
    busy = true; dirty = false; selectedInvoice = id; selectInvoice(id);
    data = null; links = []; products.resetSelection(); products.refresh(); matchesGrid.refresh(); otherReceipts.replaceChildren();
    controls(); say('Se citesc pozițiile și asocierile existente…');
    try {
      data = await read(`/${id}/association`, { nir: doc.id });
      selectedEf = selectedNir = null;
      const valid = data.allocations.filter(l => l.valid && data.lines.some(e => e.id === l.line) && data.nir_lines.some(n => n.id === l.nir_line));
      const stale = data.allocations.length - valid.length;
      links = valid.map(l => ({ ...l }));
      const proposed = proposals(data.lines, data.nir_lines, links); links.push(...proposed); dirty = !!proposed.length;
      paint(); say(`${proposed.length} potriviri propuse; ${data.other_receipts.length} alte NIR-uri asociate.${stale ? ` ${stale} asocieri vechi nu mai sunt valabile după modificarea documentului; refaceți-le.` : ''} Confirmați prin salvare.`);
    } catch (error) { say(error.message || 'Asocierea nu poate fi deschisă.', true); }
    finally { busy = false; controls(); }
  }

  async function save() {
    if (busy || !data || !await matchesGrid.commit()) return;
    if (!pending) {
      if (links.some(l => !(units(l.qty) > 0n) || !(units(l.nir_qty) > 0n))
          || data.lines.some(l => available(l, 'line') < 0n) || data.nir_lines.some(l => available(l, 'nir_line') < 0n)) {
        say('Completați cantități pozitive, cu cel mult 4 zecimale, în limita restului fiecărui rând.', true); return;
      }
      pending = { ...sel(), nir: doc.id, version: data.version, request_key: crypto.randomUUID(),
        allocations: links.map(({ line, nir_line, qty, nir_qty, learn }) => ({ line, nir_line, qty, nir_qty, learn: learn === true })) };
    }
    busy = true; controls(); say('Se salvează asocierile…');
    try {
      const answer = await api(`/api/vercon/doc/nir/efactura/${data.id}/association`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pending) });
      pending = null; busy = false; close(answer);
    } catch (error) {
      if (error.status >= 400 && error.status < 500) pending = null;
      say(`${error.message || 'Salvarea nu este confirmată.'}${pending ? ' Reluați aceeași salvare.' : ' Redeschideți asocierea dacă resturile s-au modificat.'}`, true);
    } finally { busy = false; controls(); }
  }

  async function discardForVisibility() {
    if (closed || busy || pending || !await matchesGrid.commit()) return false;
    return !dirty || await confirmBox('Renunțați la asocierile nesalvate înainte de schimbarea afișării facturilor?',
      { title: 'Facturi eFactura', yes: 'Renunț la modificări', no: 'Păstrează asocierile' });
  }

  async function reloadInvoices(preferred = selectedInvoice) {
    busy = true; dirty = false; data = null; links = []; selectedInvoice = null;
    selectedEf = selectedNir = null; invoiceOptions = []; invoice.options.staticData = []; selectInvoice(null);
    products.resetSelection(); products.refresh(); matchesGrid.refresh(); otherReceipts.replaceChildren();
    controls(); say('Se încarcă facturile trimise de contabil…');
    try {
      const result = await read('', { include_completed: 1, visibility, nir: doc.id });
      visibilityAvailable = !!result.visibility_available;
      const candidates = (result.items || []).filter(i => i.state !== 'PRELUATA' || i.manual_links)
        .sort((a, b) => compareTyped(a.supplier, b.supplier, 'text') || compareTyped(a.date, b.date, 'date') || compareTyped(a.number, b.number, 'text'));
      invoiceOptions = candidates.map(i => ({ value: i.id, inactive: i.inactive,
        label: `${i.supplier} / ${i.number} / ${i.date} — ${i.state === 'PARTIALA' ? 'asociată parțial' : i.state === 'PRELUATA' ? 'asociată integral' : 'neasociată'}${i.inactive ? ' · Inactiv' : ''}`,
        sortType: 'text', sortValue: i.supplier, sortThen: [{ type: 'date', value: i.date }, { type: 'text', value: i.number }] }));
      invoice.options.staticData = invoiceOptions;
      say(candidates.length ? '' : 'Nu există facturi în filtrul ales cu data și furnizorul facturii din NIR.');
    } catch (error) { say(error.message || 'Facturile nu au putut fi citite.', true); }
    finally { busy = false; controls(); }
    const target = invoiceOptions.find(item => item.value === preferred) || invoiceOptions[0];
    if (target && !closed) await load(target.value);
  }

  async function changeInactive(value) {
    const id = selectedInvoice;
    if (!await discardForVisibility() || closed) { controls(); return; }
    busy = true; controls(); say('Se salvează marcajul Inactiv pentru întreaga unitate…');
    try {
      await api(`/api/vercon/doc/nir/efactura/${id}/visibility`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...sel(), inactive: value }) });
      busy = false; await reloadInvoices(id);
    } catch (error) { say(error.message || 'Marcajul nu a putut fi salvat. Reluați bifarea.', true); }
    finally { busy = false; controls(); }
  }

  filterButton.addEventListener('click', async () => {
    if (closed || busy || pending) return;
    const mode = await pickBox('Afișează facturile', [
      { value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'all', label: 'Toate' }]);
    if (!mode || mode === visibility || !await discardForVisibility() || closed) return;
    visibility = mode; filterButton.textContent = 'Afișare: ' + { active: 'Active', inactive: 'Inactive', all: 'Toate' }[mode];
    await reloadInvoices();
  });

  pairButton.addEventListener('click', () => void pair(selectedNir, selectedEf));
  proposeButton.addEventListener('click', async () => { if (!await matchesGrid.commit()) return; const suggested = proposals(data.lines, data.nir_lines, links); links.push(...suggested); dirty ||= !!suggested.length; paint(); say(`${suggested.length} potriviri noi propuse. Confirmați prin salvare.`); });
  const discard = async () => { if (busy || pending) return; if (!dirty && !matchesGrid.hasEdit || await confirmBox('Renunțați la asocierile nesalvate?', { title: 'Asociere eFactura', yes: 'Renunț', no: 'Continui asocierea' })) close(null); };
  closeButton.addEventListener('click', () => void discard());
  dialog.addEventListener('cancel', e => { e.preventDefault(); void discard(); });
  saveButton.addEventListener('click', () => void save());
  await reloadInvoices();
  return done;
}
