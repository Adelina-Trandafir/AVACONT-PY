// VERCON v2: shared document controls. Cell edits update a draft; Save writes the entire document.
import { confirmBox, reasonBox } from '../utils/confirm-box.js';
import { format, parse } from './fields.js';
import { documentReport } from './document-report.js';
import { el, scope, field, column, grid, paintTree, refreshTree, markSelected, groupedNodes, CheckBox, TextBox, rowMenu, openMenu, showMessage, preview as openPreview } from './ui.js';
import { receiptTree } from './receipt-tree.js';
import { ModalFrame } from '../components/modal-frame/modal-frame.js';
import { subText, subHundredths, numberText } from './receipt-number.js';
import { receiptContext } from './receipt-context.js';
import { ActionAddButton } from '../components/action-add-button/action-add-button.js';

// Keep the modal implementation available; receipt editing temporarily stays inline.
const RECEIPT_MODAL_ENABLED = false;

const families = {
  nir: { title: 'Recepție NIR', number: 'Număr NIR', lines: 'Produse recepționate', groups: ['party', 'date'] },
  bc: { title: 'Bon de consum', number: 'Număr BC', lines: 'Produse pe bon', groups: ['receiver', 'date'] },
  storno: { title: 'Factură storno', number: 'Număr FCS', lines: 'Produse stornate', groups: ['party', 'date'], source: 'Recepția stornată' },
  br: { title: 'Retur bon de consum', number: 'Număr retur', lines: 'Produse returnate', groups: ['receiver', 'date'], source: 'Bonul returnat' },
  bp: { title: 'Bon de predare', number: 'Număr BP', lines: 'Produse predate', groups: ['holder', 'date'] },
  bt: { title: 'Bon de transfer', number: 'Număr BT', lines: 'Produse transferate', groups: ['old', 'date'], holder: 'IDC_OLD' },
  bs: { title: 'Bon de casare', number: 'Număr BS', lines: 'Produse casate', groups: ['holder', 'date'], holder: 'IDC_BC' },
};
const key = () => crypto.randomUUID();
const number = (value) => value == null || value === '' ? null : Number(String(value).replace(',', '.'));
const fixed = (value, decimals) => value == null || !Number.isFinite(value) ? '' : value.toFixed(decimals);
const dateText = (value) => format(value, { type: 'date' });

export function mountDocuments(host, opts) {
  const family = opts.family, config = families[family], abort = new AbortController();
  const state = { docs: [], lookups: null, doc: null, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null, canWrite: false, closed: false, zec: 4, eligible: [], sources: [], available: {}, busy: false };
  const headerUi = scope(), linesUi = scope(), treeUi = scope(), dialogs = scope();
  const inputs = new Map(), errors = new Map();
  let savedTree = null, treeAddButton = null, treeDirty = true, linesGrid = null, availabilityTimer = null, numberTimer = null, numberSequence = 0, availabilitySequence = 0, openSequence = 0, eligibleSequence = 0;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const say = (text, bad = false) => showMessage(status, text, bad ? 'error' : 'info');
  const report = (error) => { if (!abort.signal.aborted && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const url = (path, extra = '') => `/api/vercon/doc/${family}${path}?${query()}${extra}`;
  const json = (method, path, body) => opts.api(`/api/vercon/doc/${family}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...opts.sel(), ...body }) });
  // NIR: the saved receipts are read level by level, when the operator opens a supplier / a month (receipt-tree.js).
  const lazy = family === 'nir', receipts = lazy ? receiptTree((params) => opts.api(url('/tree', `&${new URLSearchParams(params)}`))) : null;
  const mf = () => family === 'nir' && opts.sel().type === 'MF', editing = () => state.mode !== 'view';
  const accountKey = () => ({ nir: 'IdMasina', bc: 'IdMasina', bp: 'IdMasinaP' }[family]);
  const accountId = () => Number(inputs.get(accountKey())?.value ?? state.draft?.header[accountKey()] ?? state.doc?.header[accountKey()]) || null;
  const inAccount = (item) => !accountKey() || item?.category === accountId();
  const bon = () => ['bc', 'bp'].includes(family), variants = () => state.lookups?.variants || [];
  const products = () => Array.isArray(state.lookups?.products) ? state.lookups.products : [];
  const variant = (id) => variants().find((item) => String(item.id) === String(id)), product = (id) => products().find((item) => String(item.id) === String(id));
  const options = (items) => (items || []).map((item) => ({ value: item.id, label: item.label || item.name }));
  const newLine = (values = {}) => ({ key: key(), id: null, ...values });
  const title = el('h3'), pill = el('span', { className: 'vc-pill' });
  const headerHost = el('div', { className: 'vc-grid vc-doc__header' }), linesHost = el('div', { className: 'vc-doc__lines' }), bar = el('div', { className: 'vc-actions vc-doc__bar' });
  const main = el('div', { className: 'vc-doc__main' }, el('div', { className: 'vc-doc__titlebar' }, title, pill), headerHost, linesHost, bar);
  const savedHost = el('aside', { className: 'vc-doc__tree' });
  const root = el('section', { className: `vc-doc vc-doc--${family}` }, main, savedHost); host.replaceChildren(root);
  const treeHost = family === 'nir' ? el('div', { className: 'vc-receipt-tree' }) : savedHost;
  if (treeHost !== savedHost) savedHost.append(treeHost);
  const side = family === 'nir' ? receiptContext(savedHost, { root, main, headerHost, linesHost, treeHost,
    api: opts.api, selection: opts.sel, context: () => ({ draft: state.draft }),
    validLine: (line) => !isBlankLine(line) && !lineProblems(line).length,
    blankLine: (line) => isBlankLine(line) }) : null;

  function headerSpecs() {
    const lookup = state.lookups || {}, specs = [], ref = (name, label, items, required = true) => ({ key: name, label, type: 'ref', required, showArrow: family === 'nir', options: options(items) });
    if (family === 'nir') specs.push({ ...{ ...ref('IdPartener', 'Furnizor', lookup.partners, state.draft?.tip !== 'STOC_INITIAL'), dropDownOnEnter: true }, fallback: state.mode === 'add' ? '' : state.doc?.header.party || '' }, ref('IdMasina', 'Categorie / cont', lookup.categories), { ...ref('IdComisie', 'Comisie', lookup.commissions, (lookup.commissions || []).length > 0), dropDownStyle: 'simple' }, { key: 'NrFact', label: 'Număr factură', type: 'text', required: state.draft?.tip !== 'STOC_INITIAL' }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, { key: 'Valoare', label: 'Valoare factură (lei)', type: 'decimal', min: 0, hint: 'Necompletat: suma produselor.' });
    if (family === 'storno') specs.push({ key: 'NrFact', label: 'Număr factură storno', type: 'text', required: true }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, ref('IdComisie', 'Comisie', lookup.commissions, false));
    if (family === 'bc') specs.push(ref('IDC', 'Primitor / gestionar', lookup.receivers), ref('IdMasina', 'Categorie / cont', lookup.categories));
    if (family === 'bp') specs.push(ref('IdMasinaP', 'Cont de intrare', lookup.categories), ref('IDC_BP', 'Primitor', lookup.holders), ref('IdMasina', 'Cont primitor', lookup.categories));
    if (family === 'bt') specs.push(ref('IDC_OLD', 'Predător', lookup.holders), ref('IDC_BT', 'Primitor', lookup.holders));
    if (family === 'bs') specs.push(ref('IDC_BC', 'Predător', lookup.holders));
    specs.push({ key: 'Numar', label: config.number, type: 'int', min: 1 }, { key: 'Data', label: family === 'nir' ? 'Data NIR' : 'Data', type: 'date', required: true });
    if (!['nir', 'storno'].includes(family)) specs.push({ key: 'Expl', label: 'Explicații', type: 'text', max: 255 }); return specs;
  }
  function lineSpecs() {
    if (config.source || config.holder) return [
      { key: config.holder && editing() ? '_choice' : 'name', label: 'Produs', type: 'ref', product: true, editable: !!config.holder }, { key: 'um', label: 'UM' },
      ...(editing() ? [{ key: 'qty', label: config.holder ? 'Disponibil la predător' : 'Cantitate sursă', type: 'decimal' }, ...(!config.holder ? [{ key: 'done', label: 'Deja', type: 'decimal' }, { key: 'left', label: 'Rămas', type: 'decimal' }] : [])] : []),
      { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, min: 0 }, { key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' },
    ];
    const specs = [{ key: family === 'nir' ? 'product' : 'variant', label: 'Produs', type: 'ref', product: true, editable: true, required: true }, { key: 'um', label: 'UM' }, { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, required: true, min: 0 }];
    if (family === 'nir') {
      specs.push({ key: 'Pret', label: 'Preț', type: 'decimal', editable: true, required: true, min: 0 }, { key: 'IDTVA', label: 'TVA %', type: 'ref', editable: true, required: true }, { key: 'Valoare', label: 'Valoare', type: 'decimal' }, { key: 'TVA', label: 'TVA', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'TOTAL', type: 'decimal' });
      if (mf()) specs.push({ key: 'code', label: 'Nr. inventar', editable: true, required: true }, { key: 'data_intrarii', label: 'Data intrării', type: 'date', editable: true, required: true }, { key: 'DNF', label: 'Durată normală (luni)', type: 'int', editable: true, required: true, min: 1 }, { key: 'DF', label: 'Durată folosită (luni)', type: 'int', editable: true, min: 0 }, { key: 'ValoareLuna', label: 'Valoare lunară', type: 'decimal' });
    } else specs.push({ key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' }, { key: 'avail', label: 'Disponibil la data bonului', type: 'decimal' }); return specs;
  }
  const docLabel = (doc) => `${['nir', 'storno'].includes(family) ? doc.invoice || doc.number : `${family.toUpperCase()} ${doc.number}`} • ${format(doc.total, { type: 'decimal' })} lei${doc.annulled ? ' • anulat' : ''}`;
  const sourceLabel = (doc) => `${doc.invoice || doc.number} • ${dateText(doc.date)} • ${doc.party || doc.receiver || ''} • ${format(doc.total, { type: 'decimal' })} lei`;
  async function load() {
    const [list, lookups] = await Promise.all([lazy ? receipts.load() : opts.api(url('/list')), opts.api(url('/lookups'))]); if (abort.signal.aborted) return;
    if (family === 'nir' && !Array.isArray(lookups?.products)) {
      console.error('[VERCON] Receipt product catalog missing from response', { path: url('/lookups'), fields: Object.keys(lookups || {}) });
      throw new Error('Serverul nu a trimis lista produselor pentru recepție. Documentul nu poate fi încărcat.');
    }
    treeDirty = true; Object.assign(state, { docs: list.documents || [], canWrite: list.can_write && !list.closed, closed: list.closed, zec: list.zec, lookups }); if (config.source) state.sources = (await opts.api(url('/sources'))).documents;
  }
  // The open nodes of the saved tree: of the live tree, or kept for the next one while it is hidden.
  const openNodes = () => savedTree ? savedTree.expandedNodes : (treeHost._vcExpanded ||= new Set());
  async function refresh() {
    if (lazy) { const shown = await receipts.refresh(state.doc ? { party: state.doc.header.party || '', month: String(state.doc.header.Data || '').slice(0, 7) } : null); if (!abort.signal.aborted && shown.length) { const open = openNodes(), root = shown[0]; for (const id of [...open]) if (id.startsWith('party:') ? id !== root : id.startsWith('month:') && !id.startsWith(`month:${root.slice(6)}|`)) open.delete(id); shown.forEach((id) => open.add(id)); } treeDirty = true; return; }
    const answer = await opts.api(url('/list')); if (!abort.signal.aborted) { state.docs = answer.documents; treeDirty = true; }
  }
  // A branch of the NIR tree was opened: its placeholder child stays until the server answers, then the real children replace it.
  async function opened(id, isOpen) {
    if (!isOpen || !receipts.needs(id)) return;
    try { await receipts.expand(id); } catch (error) { openNodes().delete(id); report(error); }
    if (!abort.signal.aborted && savedTree?.treeContainer?.isConnected) refreshTree(savedTree, receipts.nodes(), state.doc?.id);
  }
  async function allowDiscard() { return !state.dirty && !headerUi.dirty() && !linesUi.dirty() || await confirmBox('Renunțați la modificările nesalvate?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true }); }
  async function open(id) {
    if (state.busy || !await allowDiscard()) return; const sequence = ++openSequence, doc = await opts.api(url(`/${id}`)); if (sequence !== openSequence || abort.signal.aborted) return;
    Object.assign(state, { doc, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null }); say(''); paint();
  }
  function draftFromDoc(doc) { return { source: doc.header.source ?? null, tip: doc.doc_type || 'NIR', header: { ...doc.header, Numar: String(doc.number ?? '').replace(/\D/g, ''), NumarSub: Number(doc.sub_number) || 0 }, lines: doc.lines.map((line) => newLine({ ...line })) }; }
  async function add() { if (!await allowDiscard()) return; const today = state.lookups.today; Object.assign(state, { mode: 'add', dirty: false, requestKey: null, pendingBody: null, draft: { source: null, tip: 'NIR', vat: null, header: { Data: today, DataFc: today, NumarSub: 0 }, lines: config.source || config.holder ? [] : [newLine()] } }); say(''); paint(); if (family === 'nir') await checkNumber(true); }
  async function edit() { if (!state.doc || !state.canWrite || state.doc.annulled) return; state.mode = 'edit'; state.draft = draftFromDoc(state.doc); state.dirty = false; say(''); if (config.source || config.holder) await eligible(); paint(); }
  async function discard() { if (!await allowDiscard()) return; state.mode = 'view'; state.draft = null; state.dirty = false; state.requestKey = null; state.pendingBody = null; say(''); paint(); }
  async function eligible() {
    const draft = state.draft, sequence = ++eligibleSequence, source = config.holder ? Number(draft.header[config.holder]) : draft.source;
    if (!source) { state.eligible = []; draft.lines = []; return; }
    const answer = await opts.api(url('/eligible', `&${config.holder ? 'holder' : 'source'}=${source}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`));
    if (abort.signal.aborted || draft !== state.draft || sequence !== eligibleSequence) return; state.eligible = answer.lines;
    if (!config.holder) draft.lines = answer.lines.map((item) => { const old = draft.lines.find((line) => line.variant === item.variant && (line.origin ?? line.batch) === item.origin); return newLine({ ...item, id: old?.id ?? null, Cant: old?.Cant ?? '' }); });
    else draft.lines.forEach((line) => { line._choice = answer.lines.findIndex((item) => item.variant === line.variant && item.origin === (line.origin ?? line.batch)); const item = answer.lines[line._choice]; if (item) { line.qty = item.qty; line.left = item.left; } });
  }
  function calculate(line) {
    if (!editing()) return line;
    const originalPrice = state.mode === 'edit' ? state.doc.lines.find((item) => String(item.variant) === String(line.variant))?.PUTVA : null;
    const qty = number(line.Cant), price = family === 'nir' ? number(line.Pret) : bon() ? number(originalPrice ?? variant(line.variant)?.putva) : number(line.PUTVA), vat = state.lookups.vat?.find((item) => String(item.id) === String(line.IDTVA));
    if (family === 'nir') {
      const net = qty != null && price != null ? qty * price : null, grossUnit = price != null && vat ? Number(fixed(price * (1 + Number(vat.percent) / 100), state.zec)) : null, total = qty != null && grossUnit != null ? Number(fixed(qty * grossUnit, state.zec)) : null;
      line.Valoare = fixed(net, state.zec); line.TVA = fixed(total != null && net != null ? total - net : null, state.zec); line.ValoareCUTVA = fixed(total, state.zec); if (mf()) line.ValoareLuna = fixed(number(line.DNF) > 0 && total != null ? total / number(line.DNF) : null, state.zec);
    } else { line.ValoareCUTVA = fixed(qty != null && price != null ? qty * price : null, state.zec); if (bon()) line.PUTVA = fixed(price, state.zec); } return line;
  }
  // The modal route is retained, but disabled for NIR while its context cards are in use.
  let frame = null;
  function syncModal() {
    const modal = editing() && (family !== 'nir' || RECEIPT_MODAL_ENABLED);
    if (modal && !frame) { frame = new ModalFrame(main, { width: 1240, height: 800, caption: title.parentElement, onRequestClose: () => discard().catch(report) }); frame.open(); root.classList.add('is-modal'); }
    else if (!modal && frame) { frame.close(); frame = null; root.classList.remove('is-modal'); }
  }
  function paint() {
    if (abort.signal.aborted) return; inputs.clear(); errors.clear(); syncModal();
    title.textContent = `${config.title}${editing() ? state.mode === 'add' ? ' • document nou' : ' • modificare' : state.doc ? ` nr. ${state.doc.number}` : ''}`;
    pill.textContent = editing() ? 'Ciornă' : state.doc ? state.doc.annulled ? 'Anulat' : 'Salvat' : ''; pill.hidden = !pill.textContent; pill.dataset.state = editing() ? 'draft' : state.doc?.annulled ? 'annulled' : 'saved';
    paintHeader(); paintLines(); paintBar(); paintSaved(); freezePending();
  }
  // A starting-stock NIR has no supplier and no invoice number: those two fields are invisible but keep their place (the supplier cell keeps only the checkbox).
  function stockHidden() {
    if (family !== 'nir') return;
    const on = (editing() ? state.draft.tip : state.doc?.doc_type) === 'STOC_INITIAL';
    headerHost.querySelector('[data-field="IdPartener"]')?.classList.toggle('is-stock', on);
    const invoice = headerHost.querySelector('[data-field="NrFact"]'); invoice?.classList.toggle('is-stock', on);
  }
  function paintHeader() {
    vatInput = null;
    headerUi.clear(); headerHost.replaceChildren(); if (!editing() && !state.doc) { headerHost.append(el('p', { textContent: 'Alegeți un document din arbore sau adăugați unul nou.' })); return; }
    const header = editing() ? state.draft.header : { ...state.doc.header, Numar: numberText(state.doc) };
    headerHost.classList.toggle('vc-doc__header--nir', family === 'nir'); headerHost.classList.toggle('vc-doc__header--oi', ['bp', 'bt', 'bs'].includes(family));
    if (config.source) {
      const item = field({ key: 'source', label: config.source, type: 'ref', required: true, options: state.sources.map((doc) => ({ value: doc.id, label: sourceLabel(doc) })) }, editing() ? state.draft.source : header.source, editing() && state.mode === 'add', async (value) => { state.draft.source = Number(value) || null; state.dirty = true; try { await eligible(); paintLines(); } catch (error) { report(error); } }, headerUi); headerHost.append(item.node); errors.set('source', item.error);
    }
    const groups = family === 'nir' ? ['Furnizor și cont', 'Factură', 'NIR', 'Valoare și TVA'].map((label) => { const node = el('div', { className: 'vc-header-group', 'aria-label': label }); headerHost.append(node); return node; }) : null;
    for (const spec of headerSpecs()) {
      // a single account in the gestiune (e.g. food) is chosen for the operator and cannot be changed
      const only = ['IdMasina', 'IdMasinaP'].includes(spec.key) && spec.options.length === 1 ? spec.options[0] : null;
      if (only && editing()) header[spec.key] = only.value;
      const item = field(family === 'nir' && spec.key === 'Numar' ? { ...spec, onLeave: leaveNumber } : spec, header[spec.key], editing() && !only, async (value) => {
        const previous = state.draft.header[spec.key]; state.draft.header[spec.key] = value; state.dirty = true;
        if (status.textContent.startsWith('Completați mai întâi antetul') && !headerLock()) say('');
        if (spec.key === config.holder && String(previous) !== String(value)) {
          if (state.draft.lines.length && !await confirmBox('Schimbarea predătorului elimină produsele selectate din ciornă.', { yes: 'Schimbă predătorul', no: 'Păstrează' })) { state.draft.header[spec.key] = previous; paintHeader(); return; }
          state.draft.lines = []; try { await eligible(); paintLines(); } catch (error) { report(error); }
        }
        if (spec.key === accountKey()) { const incompatible = state.draft.lines.filter((line) => (line.variant || line.product) && !inAccount(family === 'nir' ? product(line.product) : variant(line.variant))); if (incompatible.length) { state.draft.lines = state.draft.lines.filter((line) => !incompatible.includes(line)); say('Produsele incompatibile cu noul cont au fost scoase din ciornă.', true); } paintLines(); }
        if (spec.key === 'Data') availability();
        side?.refresh();
      }, headerUi);
      inputs.set(spec.key, item.input); errors.set(`h.${spec.key}`, item.error); if (spec.hint && editing()) item.input.title = spec.hint;
      if (family === 'nir' && spec.key === 'Numar' && editing()) attachSub(item, header);
      const index = ['IdPartener', 'IdMasina', 'IdComisie'].includes(spec.key) ? 0 : ['NrFact', 'DataFc'].includes(spec.key) ? 1 : spec.key === 'Valoare' ? 3 : 2; (groups ? groups[index] : headerHost).append(item.node);
      if (family === 'nir' && spec.key === 'IdPartener' && state.mode === 'add') {
        const stock = headerUi.own(new CheckBox(item.node, { label: 'Stoc inițial (fără furnizor)', labelSide: 'left', className: 'vc-nir-stock', checked: state.draft.tip === 'STOC_INITIAL',
          onChange: (checked) => {
            state.draft.tip = checked ? 'STOC_INITIAL' : 'NIR'; state.dirty = true;
            for (const name of ['IdPartener', 'NrFact']) { const input = inputs.get(name); input?._setRequired(!checked); if (checked) { input?._clearValue(); state.draft.header[name] = null; } }
            stockHidden();
            side?.refresh();
          } }));
      }
    }
    stockHidden();
    if (family === 'nir' && editing()) {
      const vat = field({ key: '_vat', label: 'Cotă TVA', type: 'ref', dropDownStyle: 'simple', options: (state.lookups.vat || []).map(item => ({ value: item.id, label: `${item.percent}%` })) }, state.draft.vat, true, async value => {
        if (!await linesUi.commit()) { paintHeader(); return; }
        state.draft.vat = Number(value) || null; state.draft.lines.forEach(line => { line.IDTVA = state.draft.vat; calculate(line); }); state.dirty = true; paintLines();
      }, headerUi); groups[3].append(vat.node); vatInput = vat.input; syncVatLock();
    }
    if (state.doc?.annulled && !editing()) headerHost.append(el('p', { textContent: `Motiv anulare: ${state.doc.reason || ''}` }));
  }
  // An error message for a place that is not a field (the lines grid) is a tooltip, never text on the screen: assigning '' clears it.
  const tooltipSink = (node) => ({ get textContent() { return node.title; }, set textContent(text) { if (text) node.title = text; else node.removeAttribute('title'); } });
  // Keyboard walk through the header, like an Access form: Tab / Shift+Tab and Enter go from one editable control to the next. Checkboxes,
  // buttons (list arrows, calendar), hidden, disabled and invisible controls are skipped. After the last one the lines grid starts editing.
  function headerStops() {
    return [...headerHost.querySelectorAll('input, select, textarea')].filter((node) => !node.disabled && node.type !== 'hidden' && node.type !== 'checkbox'
      && node.getClientRects().length > 0 && getComputedStyle(node).visibility !== 'hidden');
  }
  function onHeaderKey(event) {
    if ((event.key !== 'Tab' && event.key !== 'Enter') || event.altKey || event.ctrlKey || event.metaKey || !editing()) return;
    const stops = headerStops(), at = stops.indexOf(event.target);
    if (at < 0) return;
    const back = event.key === 'Tab' && event.shiftKey, next = stops[at + (back ? -1 : 1)];
    if (next) { event.preventDefault(); next.focus(); if (next.type === 'text' && !next.readOnly) next.select?.(); return; }
    if (back) return;   // Shift+Tab on the first control: the browser decides
    const row = state.draft?.lines[0], column = lineSpecs().find((spec) => spec.editable);
    if (linesGrid && row && column) { event.preventDefault(); Promise.resolve(headerUi.commit()).then((ok) => { if (ok && linesGrid) linesGrid.beginEdit(row, column.key); }).catch(report); }
  }
  // The NIR number box with its optional sub-number box (707,1). A "," or "." typed in the number opens the narrow sub box beside it,
  // INSIDE the same cell (the number box just gets narrower, nothing around it moves). The number is checked once it is complete: when the
  // number box is left, or, when the sub box is open, when that one is left. Backspace in an empty sub box closes it.
  let subBox = null, subGroup = null;
  function attachSub(item, header) {
    const main = item.input._box, input = item.input, row = el('div', { className: 'vc-number-row' });
    main.element.replaceWith(row); row.append(main.element);
    subGroup = el('div', { className: 'vc-number-sub' }, el('span', { textContent: ',' }));
    subBox = headerUi.own(new TextBox(subGroup, { type: 'text', maxLength: 2, ariaLabel: 'Sub-număr NIR', textAlign: 'left',
      onInput: (text, box) => { const digits = text.replace(/\D/g, ''); if (digits !== text) box.setValue(digits); state.draft.header.NumarSub = subHundredths(digits); state.dirty = true; },
      onLeave: (text) => { if (!text) closeSub(); checkNumber().catch(report); } }));
    row.append(subGroup);
    subGroup.hidden = !(Number(header.NumarSub) > 0);
    if (!subGroup.hidden) subBox.setValue(subText(header.NumarSub));
    input.addEventListener('keydown', (event) => {
      if (event.key !== ',' && event.key !== '.') return;
      event.preventDefault();
      if (/\d/.test(input.value)) openSub();
    });
    input.addEventListener('input', () => {   // a pasted "707,1"
      const at = input.value.search(/[.,]/); if (at < 0) return;
      const base = input.value.slice(0, at).replace(/\D/g, ''), fraction = input.value.slice(at + 1).replace(/\D/g, '');
      main.setValue(base); state.draft.header.Numar = main.text;
      if (!base) return;   // a sub-number needs digits in front of it: nothing opens
      openSub(fraction.slice(0, 2));
      if (fraction.length > 2) main.setError('Sub-numărul poate avea cel mult 2 cifre.');
    });
    subBox.input.addEventListener('keydown', (event) => { if (event.key === 'Backspace' && !subBox.text) { event.preventDefault(); closeSub(); input.focus(); } });
  }
  function openSub(text = '') {
    if (!subGroup) return;
    subGroup.hidden = false;
    if (text) { subBox.setValue(text); state.draft.header.NumarSub = subHundredths(text); state.dirty = true; }
    subBox.focus();
  }
  function closeSub() { if (!subGroup) return; subGroup.hidden = true; subBox.setValue(''); state.draft.header.NumarSub = 0; state.dirty = true; }
  function leaveNumber(text, box, event) {
    if (subGroup && !subGroup.hidden && event?.relatedTarget === subBox.input) return;   // going to the sub box: the number is not complete yet
    checkNumber().catch(report);
  }
  async function checkNumber(offer = false) {
    if (family !== 'nir' || !editing()) return;
    const draft = state.draft, raw = draft.header.Numar || '', sub = Number(draft.header.NumarSub) || 0, seq = ++numberSequence;
    const answer = await opts.api(url('/number', `&number=${encodeURIComponent(raw)}&sub=${sub}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`));
    if (abort.signal.aborted || draft !== state.draft || seq !== numberSequence || String(draft.header.Numar || '') !== String(raw) || (Number(draft.header.NumarSub) || 0) !== sub) return;
    const input = inputs.get('Numar');
    if (offer && !raw) { draft.header.Numar = String(answer.suggested); if (input?._box) input._box.setValue(draft.header.Numar); else if (input) input.value = draft.header.Numar; }
    // the verdict is the box's error colour and tooltip, never text on the screen
    const message = !answer.valid ? 'Introduceți un număr întreg pozitiv (și un sub-număr de cel mult 2 cifre).' : answer.duplicate ? 'Numărul este deja folosit. Alegeți alt număr.' : '';
    const error = errors.get('h.Numar'); if (error) error.textContent = message;
    if (input && !message) input.title = offer && !raw ? 'Propus automat. Verificat din nou la salvare.' : !raw ? `Următorul număr disponibil: ${answer.suggested}.` : answer.valid && !answer.duplicate ? 'Număr disponibil. Verificat din nou la salvare.' : `Număr disponibil propus: ${answer.suggested}.`;
  }
  function productOptions() { return (family === 'nir' ? products() : variants()).filter(inAccount).map((item) => ({ value: item.id, label: family === 'nir' ? item.name : `${item.name} • ${format(item.putva, { type: 'decimal' })} lei` })); }
  // The NIR grid always ends with one empty row (the new row, as in an Access datasheet); an empty line is never saved.
  const isBlankLine = (line) => !line.product && [line.Cant, line.Pret].every((value) => value == null || String(value).trim() === '');
  const draftRows = () => { const rows = state.draft.lines.map(calculate); rows.forEach((row, index) => { row._index = index + 1; }); return rows; };
  function dropLine(line) { state.draft.lines = state.draft.lines.filter((other) => other.key !== line.key); linesGrid.setRows(draftRows(), { keepWidths: true }); totals(); availability(); side?.refresh(); }
  // A line that was edited is judged when the operator leaves it: what is missing or wrong (the same rules as the save) and the question.
  function lineProblems(line) {
    return lineSpecs().filter((spec) => spec.editable).filter((spec) => spec.type === 'ref' ? line[spec.key] == null || line[spec.key] === '' : !parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }).ok).map((spec) => spec.label);
  }
  const rowIssue = (row) => { if (config.source || config.holder) return ''; const missing = lineProblems(row); return missing.length ? `Rândul ${row._index} nu este complet. Lipsesc sau sunt greșite: ${missing.join(', ')}.` : ''; };
  const confirmRowIssue = async ({ message }) => await confirmBox(`${message} Continuați editarea rândului?`, { title: 'Rând incomplet', yes: 'Continuă editarea', no: 'Anulează rândul' }) ? 'continue' : 'cancel';
  function paintLines() {
    linesUi.clear(); linesGrid = null; linesHost.replaceChildren(); if (!editing() && !state.doc) return;
    const rows = (editing() ? state.draft.lines : state.doc.lines.map((line) => ({ ...line, key: `saved:${line.id}` }))).map(calculate); rows.forEach((row, index) => { row._index = index + 1; });
    const specs = lineSpecs(), columns = [{ key: '_index', title: '#', width: 46, fixedWidth: true, filter: false, sortable: false, align: 'center' }];
    for (const spec of specs) {
      const choices = () => spec.product ? config.holder ? state.eligible.map((item, index) => ({ value: index, label: `${item.name} • ${item.um} • disponibil ${format(item.left ?? item.qty, { type: 'decimal' })}` })) : productOptions() : (state.lookups.vat || []).map((item) => ({ value: item.id, label: `${item.percent}%` }));
      columns.push(column({ ...spec, type: spec.type || 'text' }, { width: spec.product || spec.key === 'name' ? 234 : family === 'nir' && spec.key === 'Cant' ? 90 : spec.key === 'um' ? 64 : family === 'nir' && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? 100 : 128, showArrow: family === 'nir', editable: editing() && !!spec.editable, filter: !editing(), sortable: !editing(),
        ...(spec.key === 'um' ? { align: 'center' } : family === 'nir' && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? { align: 'right' } : {}),
        ...(spec.type === 'ref' ? { editor: 'list', searchable: true, options: choices, formatter: (value) => spec.product && (!editing() || spec.key === 'name') ? rows.find((row) => row[spec.key] === value)?.name || String(value ?? '') : choices().find((item) => String(item.value) === String(value))?.label || '', validate: (value) => value == null || value === '' ? 'Alegeți o valoare din listă.' : null } : {}) }));
    }
    columns.push({ key: '_action', title: '⋮', width: 46, fixedWidth: true, filter: false, sortable: false, display: 'button', actionText: '⋮', actionLabel: 'Opțiuni linie' });
    const body = el('div', { className: 'vc-doc__grid' }); errors.set('lines', tooltipSink(body)); linesHost.append(el('h4', { textContent: config.lines }), body);
    linesGrid = grid(body, { columns, rows, rowKey: 'key',
      rowIssue, confirmRowIssue, newRowAlwaysVisible: family === 'nir' && editing(), isBlankRow: isBlankLine, onCancelNewRow: dropLine,
      onNewRow: () => { const line = newLine({ IDTVA: state.draft.vat }); state.draft.lines.push(line); line._index = state.draft.lines.length; return line; }, proportionalWidths: true, editable: editing(), allowIncompleteEdits: family === 'nir', singleClickEdit: true, editGuard: headerLock, onEditBlocked: lockedLines, footer: true, footerCaption: '', layoutId: `vercon.${opts.sel().type}.${family}.lines.${family === 'nir' ? 'receipt-v5' : 'compact-v1'}`, footerAction: { label: '➕ Adaugă rând', span: 2, disabled: !editing() || !!config.source }, onFooterAction: () => addLine().catch(report),
      onCellSave: ({ row, key: name, value }) => {
        const line = state.draft.lines.find((item) => item.key === row.key); if (!line) throw new Error('Linia nu mai este în document.');
        if (name === '_choice') { const item = state.eligible[Number(value)]; if (!item) throw new Error('Alegeți un produs al predătorului.'); if (state.draft.lines.some((other) => other !== line && other.variant === item.variant && (other.origin ?? other.batch) === item.origin)) throw new Error('Produsul din acest lot este deja în document.'); const id = line.variant === item.variant && (line.origin ?? line.batch) === item.origin ? line.id : null; Object.assign(line, { ...item, _choice: Number(value), id }); }
        else { line[name] = ['variant', 'product', 'IDTVA'].includes(name) ? value == null || value === '' ? null : Number(value) : value; if (['variant', 'product'].includes(name)) { const item = family === 'nir' ? product(value) : variant(value); line.name = item?.name || ''; line.um = item?.um || ''; if (name === 'product') line.variant = null; } }
        state.dirty = true; return { ...calculate(line) };
      }, onCellSaved: ({ row }) => { totals(); availability(); side?.refresh(row); }, onEditError: ({ error }) => say(error.message, true),
      onSelect: (row) => side?.refresh(row),
      onEditState: ({ state: mode }) => { if (mode === 'editing') side?.refresh(linesGrid?.getSelectedRows()[0]); },
      onCellAction: ({ row }) => rowMenu(linesGrid, row, [{ icon: '×', label: 'Scoate rândul', disabled: !editing() || !!config.source, run: async () => { if (!await linesUi.commit()) return; state.draft.lines = state.draft.lines.filter((item) => item.key !== row.key); state.dirty = true; paintLines(); } }]),
    }, linesUi); totals(); availability(); side?.refresh();
  }
  // The lines grid stays locked until every required header field is filled; the reason is the message shown on a click in it.
  function headerLock() {
    if (!editing()) return '';
    const blank = (value) => value == null || String(value).trim() === '';
    const missing = headerSpecs().filter((spec) => spec.required && blank(inputs.get(spec.key)?.value ?? state.draft.header[spec.key])).map((spec) => spec.label);
    if (config.source && !state.draft.source) missing.unshift(config.source);
    return missing.length ? `Completați mai întâi antetul documentului: ${missing.join(', ')}.` : '';
  }
  function lockedLines({ message }) { say(message, true); headerUi.validate(); }
  async function addLine(item) {
    if (!editing() || !await linesUi.commit()) return; const lock = headerLock(); if (lock) { lockedLines({ message: lock }); inputs.get(headerSpecs().find((spec) => spec.required && !(inputs.get(spec.key)?.value))?.key)?.closest('.vc-field')?.querySelector('input:not([type=hidden])')?.focus(); return; } if (family === 'nir' && linesGrid) { linesGrid.beginEditNewRow('product'); return; } if (config.holder && !state.eligible.length) { say('Alegeți un predător cu produse disponibile.', true); return; }
    const line = newLine({ ...(family === 'nir' ? { IDTVA: state.draft.vat } : {}), ...(item ? { [family === 'nir' ? 'product' : 'variant']: item.id, name: item.name, um: item.um } : {}) }); state.draft.lines.push(line); state.dirty = true; paintLines(); linesGrid.beginNewRowEdit(line, config.holder ? '_choice' : family === 'nir' ? 'product' : 'variant', () => { state.draft.lines = state.draft.lines.filter((other) => other.key !== line.key); paintLines(); });
  }
  // The header VAT rate is locked as soon as a line has been filled in (the empty new row does not count).
  let vatInput = null;
  function syncVatLock() {
    if (!vatInput?._combo) return;
    const locked = !!state.draft?.lines.some((line) => !isBlankLine(line));
    vatInput._combo.setEnabled(!locked);
    vatInput._combo.input.title = locked ? 'Cota TVA nu se mai schimbă după ce există produse în document.' : '';
  }
  function totals() {
    syncVatLock();
    if (!linesGrid) return;
    const rows = editing() ? state.draft.lines : state.doc.lines;
    const sum = (name) => fixed(rows.reduce((total, row) => total + (number(row[name]) || 0), 0), state.zec);
    const lineTotal = sum('ValoareCUTVA');
    const values = { PUTVA: 'TOTAL', ValoareCUTVA: format(lineTotal, { type: 'decimal' }) };
    if (family === 'nir') { values.Valoare = format(sum('Valoare'), { type: 'decimal' }); values.TVA = format(sum('TVA'), { type: 'decimal' }); }
    linesGrid.setFooter({ values });
    let note = linesHost.querySelector('.vc-doc__reconciliation');
    if (!note) { note = el('p', { className: 'vc-hint vc-doc__reconciliation' }); linesHost.append(note); }
    const declared = number(state.doc?.header.Valoare), difference = declared == null ? 0 : Number(fixed(declared - number(lineTotal), state.zec));
    note.hidden = editing() || !difference;
    note.textContent = note.hidden ? '' : `Valoare în antet: ${format(state.doc.header.Valoare, { type: 'decimal' })} lei. Diferență față de liniile documentului: ${format(fixed(difference, state.zec), { type: 'decimal' })} lei.`;
  }
  function availability() {
    clearTimeout(availabilityTimer); const sequence = ++availabilitySequence; if (!bon() || !editing()) return; const draft = state.draft; state.available = {};
    availabilityTimer = setTimeout(async () => {
      const date = parse(inputs.get('Data')?.value, { type: 'date', required: true }), ids = [...new Set([...draft.lines.map((line) => line.variant)].filter(Boolean))]; if (!date.ok || !ids.length) return;
      try { const exclude = state.mode === 'edit' && (family === 'bc' || state.doc.header.IdBonConsum) ? `&exclude_kind=bc&exclude_id=${family === 'bc' ? state.doc.id : state.doc.header.IdBonConsum}` : ''; const result = await opts.api(`/api/vercon/stock/available?${query()}&date=${date.value}&products=${ids.join(',')}${exclude}`); if (abort.signal.aborted || sequence !== availabilitySequence || state.draft !== draft) return; state.available = result.available; draft.lines.forEach((line) => { line.avail = result.available[String(line.variant)] ?? ''; }); if (linesGrid && !linesGrid.hasEdit) linesGrid.setRows(draft.lines, { keepWidths: true }); } catch (error) { report(error); }
    }, 300);
  }
  const treeNodes = () => lazy ? receipts.nodes() : groupedNodes(state.docs, config.groups.map((name) => (doc) => name === 'date' ? dateText(doc.date) : doc[name]), docLabel);
  function paintSaved() {
    savedHost.hidden = false;
    side?.setVisible(editing() && !RECEIPT_MODAL_ENABLED);
    if (side && editing() && !RECEIPT_MODAL_ENABLED) return;
    if (savedTree?.treeContainer?.isConnected) {   // the tree is kept alive: opening a document only moves the mark; new data is drawn in place
      if (savedHost.hidden) return;
      if (treeDirty) refreshTree(savedTree, treeNodes(), state.doc?.id); else markSelected(savedTree, state.doc?.id);
      paintTreeAdd();
      treeDirty = false; return;
    }
    treeUi.clear(); savedTree = null; if (savedHost.hidden) return; treeDirty = false;
    savedTree = paintTree(treeHost, treeNodes(), { title: family === 'bc' ? 'Bonuri salvate' : 'Documente salvate', showSearchBox: false, collapsed: true, selected: state.doc?.id, onToggle: lazy ? opened : undefined, exclusiveRoots: lazy, onSelect: (id) => open(Number(id)).catch(report), onAction: (id, anchor) => {
      const doc = lazy ? receipts.find(id) : state.docs.find((item) => String(item.id) === id); if (!doc) return; const run = async (action) => { if (state.doc?.id !== doc.id) await open(doc.id); if (state.doc?.id === doc.id) await action(); };
      openMenu(anchor, [{ icon: '✎', label: 'Modifică', disabled: !state.canWrite || doc.annulled, run: () => run(edit).catch(report) }, { icon: '◉', label: 'Previzualizează', run: () => run(preview).catch(report) }, { icon: '◷', label: 'Istoric', run: () => run(history).catch(report) }, { icon: '×', label: 'Anulează', rule: true, disabled: !state.canWrite || doc.annulled, run: () => run(cancel).catch(report) }], { align: 'right' });
    } }, treeUi);
    paintTreeAdd();
  }
  function paintTreeAdd() {
    if (family !== 'nir') return;
    const heading = treeHost.querySelector(':scope > h4');
    if (!heading) return;
    if (!treeAddButton || treeAddButton.element.parentElement !== heading) {
      treeAddButton?.destroy();
      treeAddButton = new ActionAddButton(heading, { className: 'vc-receipt-tree__add',
        title: 'Adaugă NIR', onClick: () => add().catch(report) });
      treeUi.own(treeAddButton);
    }
    treeAddButton.setEnabled(state.canWrite);
  }
  function paintBar() {
    bar.replaceChildren(); const button = (text, action, attrs = {}) => { const node = el('button', { type: 'button', textContent: text, ...attrs }); node.addEventListener('click', () => Promise.resolve(action()).catch(report), { signal: abort.signal }); return node; };
    if (editing()) bar.append(button('Renunță', discard), button('Previzualizează', preview), button(`Salvează ${family === 'nir' ? 'NIR' : 'bonul'}`, save, { className: 'is-primary' })); else { if (family !== 'nir') bar.append(button('+ Adaugă', add, { disabled: !state.canWrite })); if (state.doc) bar.append(button('Modifică', edit, { disabled: !state.canWrite || state.doc.annulled }), button('Previzualizează', preview)); } bar.append(status); if (state.closed) bar.append(el('span', { className: 'vc-hint', textContent: 'Perioadă închisă: numai consultare.' }));
  }
  function collect() {
    const out = { header: {}, lines: [], source: state.draft.source }, invalid = {};
    for (const spec of headerSpecs()) { const raw = inputs.get(spec.key)?.value ?? state.draft.header[spec.key]; if (spec.type === 'ref') { out.header[spec.key] = raw === '' || raw == null ? null : Number(raw); if (spec.required && out.header[spec.key] == null && !(family === 'nir' && state.draft.tip === 'STOC_INITIAL' && spec.key === 'IdPartener')) invalid[`h.${spec.key}`] = 'Alegeți o valoare.'; } else { const result = parse(raw, spec); if (!result.ok) invalid[`h.${spec.key}`] = result.message; else out.header[spec.key] = result.value; } }
    if (family === 'nir') { out.header.TipDoc = state.draft.tip; out.header.NumarSub = Number(state.draft.header.NumarSub) || 0; }
    for (const line of state.draft.lines) {
      if (family === 'nir' && isBlankLine(line)) continue;   // the always-visible new row
      if (config.source || config.holder) { if (line.Cant == null || line.Cant === '') continue; const result = parse(String(line.Cant), { type: 'decimal', required: true, min: 0 }); if (!result.ok || !line.variant) invalid[`l|${line.key}|Cant`] = result.message || 'Alegeți un produs.'; else out.lines.push({ key: line.key, id: line.id, variant: line.variant, batch: line.origin ?? line.batch ?? null, Cant: result.value }); continue; }
      const result = { key: line.key, id: line.id }; for (const spec of lineSpecs().filter((item) => item.editable)) { if (spec.type === 'ref') { result[spec.key] = line[spec.key] ?? null; if (result[spec.key] == null) invalid[`l|${line.key}|${spec.key}`] = 'Alegeți o valoare.'; } else { const parsed = parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }); if (!parsed.ok) invalid[`l|${line.key}|${spec.key}`] = parsed.message; else result[spec.key] = parsed.value; } } out.lines.push(result);
    }
    if (!out.lines.length) invalid.lines = 'Adăugați cel puțin un produs și cantitatea.'; if (config.source && !out.source) invalid.source = 'Alegeți documentul sursă.'; return { body: out, errors: invalid };
  }
  function showErrors(invalid) {
    for (const node of errors.values()) node.textContent = '';
    headerUi.validate(); linesUi.validate();   // the validation event: every control of the screen answers (colour; the grid marks its empty required cells)
    const blank = (value) => value == null || String(value).trim() === '';
    const messages = [];
    let detailed = false;
    for (const [name, text] of Object.entries(invalid || {})) {
      if (name.startsWith('h.')) {
        const fieldName = name.slice(2), spec = headerSpecs().find((item) => item.key === fieldName), box = inputs.get(fieldName)?._box;
        if (spec?.required && blank(inputs.get(fieldName)?.value)) continue;   // empty required: colour and tooltip came from validate(), no message under it
        box?.setError(text);
      }
      if (name.startsWith('l|')) {
        const [, rowKey, fieldName] = name.split('|'), index = state.draft.lines.findIndex((item) => item.key === rowKey);
        const spec = lineSpecs().find((item) => item.key === fieldName);
        if (spec?.required && blank(state.draft.lines[index]?.[fieldName])) continue;
        messages.push(`Rând ${index + 1}: ${text}`);
      } else if (errors.has(name)) errors.get(name).textContent = text;
      detailed = true;
    }
    if (messages.length) errors.get('lines').textContent = messages.join(' ');
    return detailed;
  }
  function freezePending() { headerHost.inert = linesHost.inert = !!state.pendingBody; }
  async function save() {
    if (!state.canWrite || !editing() || state.busy || !await headerUi.commit() || !await linesUi.commit()) return; const data = collect(); if (Object.keys(data.errors).length) { say(showErrors(data.errors) ? 'Corectați câmpurile marcate.' : '', true); return; } state.busy = true; root.inert = true; say('Se salvează…');
    try { let result; if (state.mode === 'add') { state.requestKey ||= key(); state.pendingBody ||= structuredClone(data.body); result = await json('POST', '', { ...state.pendingBody, request_key: state.requestKey }); } else result = await json('PUT', `/${state.doc.id}`, { ...data.body, version: state.doc.version }); if (abort.signal.aborted) return; Object.assign(state, { doc: result.document, mode: 'view', draft: null, dirty: false, requestKey: null, pendingBody: null }); paint(); say(result.replayed ? 'Salvat (cererea fusese deja aplicată).' : 'Salvat.'); try { await refresh(); if (!abort.signal.aborted) paintSaved(); } catch (error) { report(error); } }
    catch (error) { if (error.fields) showErrors(error.fields); if (error.status && error.status < 500) { state.requestKey = null; state.pendingBody = null; } report(error); if (!error.status || error.status >= 500) say('Răspunsul salvării nu este confirmat. Reîncercați salvarea aceleiași ciorne; cererea păstrează aceeași cheie.', true); }
    finally { state.busy = false; root.inert = false; freezePending(); }
  }
  async function cancel() { const doc = state.doc; if (!doc || !state.canWrite || doc.annulled) return; const reason = await reasonBox(`Anulați ${config.title.toLocaleLowerCase('ro')} nr. ${doc.number}?`, { title: 'Anulare document', yes: 'Anulează', no: 'Renunță' }); if (reason == null) return; try { const result = await json('POST', `/${doc.id}/cancel`, { reason, version: doc.version }); if (abort.signal.aborted) return; state.doc = result.document; await refresh(); paint(); say('Anulat.'); } catch (error) { report(error); } }
  async function history() { const doc = state.doc; if (!doc) return; const result = await opts.api(url(`/${doc.id}/history`)); if (!abort.signal.aborted) openPreview(`Istoric • ${config.title} nr. ${doc.number}`, [{ title: 'Operații', columns: [{ key: 'at', title: 'Data', width: 190 }, { key: 'user', title: 'Utilizator', width: 180 }, { key: 'operation', title: 'Operație', width: 160 }, { key: 'after', title: 'Valori', width: 420, formatter: (value) => value && typeof value === 'object' ? JSON.stringify(value) : value ?? '' }], rows: result.history }], dialogs); }
  async function preview() {
    if (editing() && (!await headerUi.commit() || !await linesUi.commit())) return; const data = editing() ? collect() : null; if (data && Object.keys(data.errors).length) { say(showErrors(data.errors) ? 'Corectați câmpurile marcate.' : '', true); return; } const header = editing() ? data.body.header : state.doc ? { ...state.doc.header, Numar: numberText(state.doc) } : null; if (!header) return;
    const token = openSequence, doc = editing() ? null : state.doc, draft = state.draft;
    const sections = [
      { title: 'Antet', columns: [{ key: 'label', title: 'Câmp', width: 240 }, { key: 'value', title: 'Valoare', width: 580 }], rows: headerSpecs().map((spec) => ({ label: spec.label, value: spec.type === 'ref' ? spec.options.find((item) => String(item.value) === String(header[spec.key]))?.label || '' : format(header[spec.key], spec) })) },
      { title: config.lines, columns: lineSpecs().map((spec) => ({ key: spec.product ? 'name' : spec.key, title: spec.label, width: spec.product ? 250 : 120, formatter: (value) => spec.key === 'IDTVA' ? `${state.lookups.vat.find(item => String(item.id) === String(value))?.percent ?? ''}%` : format(value, { type: spec.type === 'ref' ? 'text' : spec.type || 'text' }) })), rows: editing() ? state.draft.lines.filter((line) => family !== 'nir' || !isBlankLine(line)) : state.doc.lines },
    ];
    await documentReport({ api: opts.api, selection: opts.sel(), family, document: doc,
      title: `${config.title}${editing() ? ' • Ciornă nesalvată' : ` nr. ${state.doc.number}`}`, sections, owner: dialogs,
      isCurrent: () => !abort.signal.aborted && token === openSequence && draft === state.draft && (!doc || doc === state.doc) });
  }
  // opts.startAdd: the page opens straight on a new document (nothing of the consultation screen is drawn meanwhile).
  headerHost.addEventListener('keydown', onHeaderKey, { signal: abort.signal });
  const ready = load().then(() => {
    if (abort.signal.aborted) return;
    if (opts.startAdd && state.canWrite) return add();
    paint(); if (opts.startAdd) say('Nu aveți dreptul să adăugați documente în această gestiune.', true);
  }).catch(report);
  if (!opts.startAdd) paint();
  return { ready, startAdd: async () => { await ready; if (abort.signal.aborted) return; if (state.canWrite) await add(); else say('Nu aveți dreptul să adăugați documente în această gestiune.', true); }, isDirty: () => state.dirty || headerUi.dirty() || linesUi.dirty(), search: (text) => { const input = savedHost.querySelector('.treeview-search-input'); if (input) { input.value = text; input.dispatchEvent(new Event('input')); } }, destroy: () => { frame?.destroy(); side?.destroy(); abort.abort(); numberSequence++; openSequence++; availabilitySequence++; eligibleSequence++; clearTimeout(numberTimer); clearTimeout(availabilityTimer); [headerUi, linesUi, treeUi, dialogs].forEach((owner) => owner.clear()); host.replaceChildren(); } };
}
