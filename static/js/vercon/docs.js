// VERCON v2: shared document controls. Cell edits update a draft; Save writes the entire document.
import { confirmBox, reasonBox } from '../utils/confirm-box.js';
import { format, parse } from './fields.js';
import { documentReport } from './document-report.js';
import { editLocked, editMessage } from './document-permissions.js';
import { showDocumentHistory } from './document-history.js';
import { documentNumbers } from './document-number.js';
import { el, scope, field, column, grid, paintTree, refreshTree, markSelected, CheckBox, rowMenu, openMenu, showMessage, statusBar, preview as openPreview } from './ui.js';
import { receiptTree } from './receipt-tree.js';
import { showSupplierInfo } from './supplier-info.js';
import { ModalFrame } from '../components/modal-frame/modal-frame.js';
import { numberText } from './receipt-number.js';
import { documentSubnumber } from './document-subnumber.js';
import { receiptContext } from './receipt-context.js';
import { receiptMfInfo } from './receipt-mf-info.js';
import { ActionAddButton } from '../components/action-add-button/action-add-button.js';
import { receiptAllocation } from './receipt-allocation.js';
import { receiptTotal } from './receipt-reconciliation.js';
import { adjustableReceiptLine, receiptAdjustmentState, adjustmentError } from './receipt-adjustment.js';
import { sharedReceipt, invoiceRows, receiptGridRows, invoiceValue, receiptLineClass, linkedReceipt } from './receipt-invoice-view.js';
import { stornoSourceField, stornoProductNodes, stornoProductKey, calculateStorno } from './storno-editor.js';
import { consumptionCategory } from './consumption-ui.js';
import { consumptionEditor } from './consumption-editor.js';
import { oiAccount, oiProductNodes } from './oi-ui.js';
import { oiTreeOrder } from './oi-tree-order.js';
import { documentTreeOrder, monthKey } from './document-tree-order.js';
import { warehouseChoices, holderChoices, warehouseStock } from './oi-product-choices.js';
import { oiLineValue } from './oi-values.js';
import { oiContext } from './oi-context.js';
import { orphanStorno } from './orphan-storno.js'; import { repairRegister } from './oi-register-repair.js';
import { receiptProductEntry, mfColumns, mfMonthly } from './receipt-product-entry.js';
import { efacturaIntake } from './efactura-intake.js';
import { associateEfactura } from './efactura-association.js';
import { showReceiptEfactura } from './efactura-view.js';
import { headerFit } from './header-fit.js';

// Keep the modal implementation available; receipt editing temporarily stays inline.
const RECEIPT_MODAL_ENABLED = false;

const families = {
  nir: { title: 'Recepție NIR', number: 'Număr NIR', lines: 'Produse recepționate', groups: ['party', 'date'] },
  bc: { title: 'Bon de consum', number: 'Număr BC', lines: 'Produse pe bon', groups: ['receiver', 'date'] },
  storno: { title: 'Factură storno', number: 'Număr FCS', lines: 'Produse stornate', groups: ['party', 'date'], source: 'Recepția stornată' },
  br: { title: 'Retur bon de consum', number: 'Număr BX', lines: 'Produse returnate', groups: ['receiver', 'date'], source: 'Bonul returnat' },
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
  const receipt = ['nir', 'storno'].includes(family);
  const custody = ['bp', 'bt', 'bs'].includes(family);
  // Consumption and custody bons are grouped by month by default only in a food gestiune; the others start with the box off.
  const defaultByDate = () => ['bc', 'bp', 'bt', 'bs'].includes(family) ? opts.sel().type === 'ALIMENTE' : family !== 'nir';
  let orderType = opts.sel().type;
  // The consumption bons offer a popup of three groupings (SLICE-VC06-19); the other families keep the box.
  const bcGroupings = [{ key: 'date', icon: '📅', label: 'Grupare Data' }, { key: 'receiver', icon: '👤', label: 'Grupare Primitor' }, { key: 'category', icon: '🧾', label: 'Grupare Cont' }];
  const bonOrder = custody ? oiTreeOrder(defaultByDate())
    : documentTreeOrder({ party: receipt ? 'party' : 'receiver', initialByDate: defaultByDate(), groupings: family === 'bc' ? bcGroupings : null });
  const inline = receipt || family === 'bc' || custody;
  const sourceOptions = opts;
  let currentContext = { ...opts.sel() }, contextAbort = new AbortController(), loadGeneration = 0;
  opts = { ...opts, sel: () => currentContext,
    api: (path, options = {}) => sourceOptions.api(path, { ...options, signal: AbortSignal.any([abort.signal, contextAbort.signal]) }) };
  const state = { docs: [], lookups: null, doc: null, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null, canWrite: false, closed: false, zec: 4, eligible: [], sources: [], available: {}, busy: false, returnSource: null };
  let pendingLink = null;
  const orphanReturn = () => family === 'br' && state.doc && state.doc.header.source == null;
  const headerUi = scope(), linesUi = scope(), treeUi = scope(), dialogs = scope();
  const stornoPending = new Set();
  function trackStorno(promise) {
    if (family !== 'storno' && !custody) return promise;
    stornoPending.add(promise);
    promise.finally(() => stornoPending.delete(promise)).catch(() => {});
    return promise;
  }
  const inputs = new Map(), errors = new Map();
  let adjustmentPending = false;
  let treeOrderSequence = 0;
  let savedTree = null, treeAddButton = null, treeDirty = true, linesGrid = null, invoiceCorrectionButton = null, availabilityTimer = null, numberTimer = null, availabilitySequence = 0, openSequence = 0, eligibleSequence = 0;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const say = (text, bad = false) => showMessage(status, text, bad ? 'error' : 'info');
  const clearRowError = () => { if (status.getAttribute('role') === 'alert') say(''); };
  const report = (error) => { if (!abort.signal.aborted && error.name !== 'AbortError') say(error.requestId ? `${error.message} (cod ${error.requestId})` : (error.message || 'Operația a eșuat.'), true); };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const url = (path, extra = '') => `/api/vercon/doc/${family}${path}?${query()}${extra}`;
  const json = (method, path, body) => opts.api(`/api/vercon/doc/${family}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...opts.sel(), ...body }) });
  // NIR reads only month/supplier roots, then the documents of each opened root.
  const lazy = family === 'nir';
  const createReceipts = () => receiptTree(params => opts.api(url('/tree', `&${new URLSearchParams(params)}`)), () => bonOrder.byDate);
  let receipts = lazy ? createReceipts() : null;
  const allocation = family === 'nir' ? receiptAllocation({ api: opts.api, selection: opts.sel, draft: () => state.draft, mode: () => state.mode }) : null;
  const intake = family === 'nir' ? efacturaIntake({ api: opts.api, sel: opts.sel }) : null;   // VC06-07: invoices the accountant sent from K-BOT
  const numbering = documentNumbers({ family, state, allocation, selection: opts.sel, api: opts.api,
    aborted: () => abort.signal.aborted, controls: () => ({ input: inputs.get('Numar'), ...subnumber.controls() }),
    error: () => errors.get('h.Numar'), track: trackStorno });
  const checkNumber = numbering.check;
  const subnumber = documentSubnumber({ family, owner: headerUi, state, check: checkNumber, report });
  const mf = () => family === 'nir' && (opts.sel().type === 'MF' || allocation.hasMF()), editing = () => state.mode !== 'view';
  const invoiceDoc = () => family === 'nir' && state.mode !== 'add' ? state.doc : null;
  const displayRows = local => invoiceRows(invoiceDoc(), local);
  const gridRows = local => receiptGridRows(invoiceDoc(), local, family === 'nir' && state.mode === 'edit' && opts.sel().type === 'MF');
  const accountKey = () => ({ nir: 'IdMasina', bc: 'IdMasina', bp: 'IdMasinaP' }[family]);
  const accountId = () => Number(inputs.get(accountKey())?.value ?? state.draft?.header[accountKey()] ?? state.doc?.header[accountKey()]) || null;
  const inAccount = (item) => family === 'nir' ? !!state.draft?.accounts?.includes(item?.category) : !accountKey() || item?.category === accountId();
  const bon = () => ['bc', 'bp'].includes(family), variants = () => state.lookups?.variants || [];
  const products = () => Array.isArray(state.lookups?.products) ? state.lookups.products : [];
  const variant = (id) => variants().find((item) => String(item.id) === String(id)), product = (id) => products().find((item) => String(item.id) === String(id));
  const options = (items) => (items || []).map((item) => ({ value: item.id, label: item.label || item.name }));
  const newLine = (values = {}) => ({ key: key(), id: null, ...values });
  const title = el('h3'), pill = el('span', { className: 'vc-pill' });
  const stockHost = el('div', { className: 'vc-doc__stock', hidden: true });
  const headerHost = el('div', { className: 'vc-grid vc-doc__header' }), linesHost = el('div', { className: 'vc-doc__lines' }), bar = el('div', { className: 'vc-actions vc-doc__bar' });
  const main = el('div', { className: 'vc-doc__main' }, el('div', { className: 'vc-doc__titlebar' }, title, stockHost, pill), headerHost, linesHost, bar);
  const savedHost = el('aside', { className: 'vc-doc__tree' });
  const root = el('section', { className: `vc-doc vc-doc--${family}` }, main, savedHost); host.replaceChildren(root);
  if (['nir', 'bc'].includes(family)) headerFit({ box: main, header: headerHost, lines: linesHost, minRows: 10, signal: abort.signal });
  const productEntry = family === 'nir' ? receiptProductEntry({ host, root, allocation, draft: () => state.draft,
    api: opts.api, selection: opts.sel, changed: () => { state.dirty = true; } }) : null;
  const treeHost = inline ? el('div', { className: 'vc-receipt-tree' }) : savedHost;
  if (treeHost !== savedHost) savedHost.append(treeHost);
  const mfInfo = family === 'nir' ? receiptMfInfo(savedHost) : null;
  const side = inline && !custody ? receiptContext(savedHost, { root, main, headerHost, linesHost, treeHost, family,
    api: opts.api, selection: opts.sel, lineSelection: line => allocation ? allocation.selectionForProduct(line.product) : opts.sel(), context: () => ({ draft: state.draft, excludeBC: family === 'bc' && state.mode === 'edit' ? state.doc?.id : null }),
    validLine: (line) => !isBlankLine(line) && (['storno', 'bc'].includes(family) ? !!line.variant : !lineProblems(line).length),
    blankLine: (line) => isBlankLine(line) }) : null;
  const custodySide = config.holder ? oiContext(root, { family, api: opts.api, selection: opts.sel, draft: () => state.draft }) : null;
  const bc = family === 'bc' ? consumptionEditor({ state, inputs, api: opts.api, selection: opts.sel, changed: () => {
    if (linesGrid && !linesGrid.hasEdit) linesGrid.setRows(editing() ? draftRows() : state.doc.lines.map((row, index) => ({ ...row, key: `saved:${row.id}`, _index: index + 1 })), { keepWidths: true });
    side?.refresh(linesGrid?.getSelectedRows()[0]);
  } }) : null;

  function headerSpecs() {
    const lookup = state.lookups || {}, specs = [], ref = (name, label, items, required = true) => ({ key: name, label, type: 'ref', required, showArrow: inline, options: options(items), ...(custody && state.mode !== 'add' ? { fallback: state.doc?.header.labels?.[name] || '' } : {}) });
    if (family === 'nir') specs.push({ ...{ ...ref('CodFiscal', 'Furnizor', lookup.partners, state.draft?.tip !== 'STOC_INITIAL'), dropDownOnEnter: true }, fallback: state.mode === 'add' ? '' : state.doc?.header.party || '' }, { key: 'Accounts', label: 'Categorie / cont', type: 'tree', required: true }, { ...ref('IdComisie', 'Comisie', lookup.commissions, (lookup.commissions || []).length > 0), dropDownStyle: 'simple' }, { key: 'NrFact', label: 'Număr factură', type: 'text', required: state.draft?.tip !== 'STOC_INITIAL' }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, { key: 'Valoare', label: 'Valoare Reală Factură (lei)', type: 'decimal', min: 0, hint: 'Necompletat: suma produselor. Valoarea declarată trebuie egalată exact.' });
    if (family === 'storno') specs.push({ key: 'NrFact', label: 'Număr factură storno', type: 'text', required: true }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, ref('IdComisie', 'Comisie', lookup.commissions, false));
    if (family === 'bc') specs.push({ ...ref('IDC', 'Primitor / gestionar', lookup.receivers), dropDownOnEnter: true, fallback: state.mode === 'add' ? '' : state.doc?.header.receiver || '' }, ref('IdMasina', 'Cont', lookup.categories));
    if (family === 'bp') specs.push(ref('IdMasinaP', 'Cont OI în magazie', lookup.warehouse_accounts), ref('IDC_BP', 'Primitor', lookup.holders), ref('IdMasina', 'Cont OI în folosință', lookup.use_accounts));
    if (family === 'bt') {
      const holderRef = (name, label, opposite) => ({ ...ref(name, label, lookup.holders),
        options: () => options((lookup.holders || []).filter(item => !editing()
          || String(item.id) !== String(state.draft.header[opposite] ?? ''))) });
      specs.push(holderRef('IDC_OLD', 'Predător', 'IDC_BT'), holderRef('IDC_BT', 'Primitor', 'IDC_OLD'));
    }
    if (family === 'bs') specs.push(ref('IDC_BC', 'Predător', lookup.holders));
    specs.push({ key: 'Numar', label: config.number, type: 'int', min: 1 }, { key: 'Data', label: family === 'nir' ? 'Data NIR' : 'Data', type: 'date', required: true });
    if (!['nir', 'storno'].includes(family)) specs.push({ key: 'Expl', label: 'Explicații', type: 'text', max: 255 }); return specs;
  }
  function lineSpecs() {
    if (family === 'storno') return [
      { key: 'variant', label: 'Produs', type: 'ref', product: true, editable: true, required: true },
      { key: 'um', label: 'UM' }, { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, required: true, min: 0 },
      { key: 'Pret', label: 'Preț', type: 'decimal' }, { key: 'IDTVA', label: 'TVA %', type: 'ref' },
      { key: 'Valoare', label: 'Valoare', type: 'decimal' }, { key: 'TVA', label: 'TVA', type: 'decimal' },
      { key: 'ValoareCUTVA', label: 'TOTAL', type: 'decimal' },
    ];
    if (config.source || config.holder) return [
      { key: config.holder && editing() ? '_choice' : 'name', label: 'Produs', type: 'ref', product: true, editable: !!config.holder }, { key: 'um', label: 'UM' },
      ...(editing() ? [{ key: 'qty', label: config.holder ? 'Disponibil' : 'Cantitate sursă', type: 'decimal' }, ...(!config.holder ? [{ key: 'done', label: 'Deja', type: 'decimal' }, { key: 'left', label: 'Rămas', type: 'decimal' }] : [])] : []),
      { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, required: !!config.holder, min: 0 }, { key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' },
    ];
    const specs = [{ key: family === 'nir' ? 'product' : 'variant', label: 'Produs', type: 'ref', product: true, editable: true, required: true }, { key: 'um', label: 'UM', ...(family === 'nir' ? { editable: true, required: true, max: 50 } : {}) },
      ...(['bc', 'bp'].includes(family) ? [{ key: 'avail', label: 'Disponibil', type: 'decimal' }] : []),
      { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, required: true, min: 0 }];
    if (family === 'nir' && editing() && state.draft?.efactura) specs.unshift({ key: 'ef_name', label: 'Din factura primită' });   // VC06-07: what the supplier wrote on the line
    if (family === 'nir') {
      specs.push({ key: 'Pret', label: 'Preț', type: 'decimal', editable: true, required: true, min: 0 }, { key: 'IDTVA', label: 'TVA %', type: 'ref', editable: true, required: true }, { key: 'Valoare', label: 'Valoare', type: 'decimal' }, { key: 'TVA', label: 'TVA', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'TOTAL', type: 'decimal' });

      if (mf() && editing()) specs.push(...mfColumns);
    } else specs.push({ key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' }); return specs;
  }
  const sourceLabel = (doc) => `${doc.invoice || doc.number} • ${dateText(doc.date)} • ${doc.party || doc.receiver || ''} • ${format(doc.total, { type: 'decimal' })} lei`;
  async function load() {
    const generation = ++loadGeneration;
    const [list, lookups, allocated] = await Promise.all([lazy ? receipts.load() : opts.api(url('/list')), opts.api(url('/lookups')), allocation?.load()]); if (abort.signal.aborted || generation !== loadGeneration) return;
    if (allocated) Object.assign(lookups, { products: allocated.products, vat: allocated.vat });
    if (family === 'nir' && !Array.isArray(lookups?.products)) {
      console.error('[VERCON] Receipt product catalog missing from response', { path: url('/lookups'), fields: Object.keys(lookups || {}) });
      throw new Error('Serverul nu a trimis lista produselor pentru recepție. Documentul nu poate fi încărcat.');
    }
    treeDirty = true; Object.assign(state, { docs: list.documents || [], canWrite: list.can_write && !list.closed, closed: list.closed, zec: list.zec, lookups });
    side?.configureCache(lookups.warehouse_statement_cache);
    if (config.source) state.sources = (await opts.api(url('/sources'))).documents;
  }
  // The open nodes of the saved tree: of the live tree, or kept for the next one while it is hidden.
  const openNodes = () => savedTree ? savedTree.expandedNodes : (treeHost._vcExpanded ||= new Set());
  async function refresh() {
    if (lazy) {
      const source = receipts, shown = await source.refresh(receiptTarget());
      if (source !== receipts || abort.signal.aborted) return;
      shown.forEach(id => openNodes().add(id)); treeDirty = true; return;
    }
    const answer = await opts.api(url('/list')); if (!abort.signal.aborted) {
      state.docs = answer.documents; treeDirty = true;
      const selected = state.docs.find(doc => doc.id === state.doc?.id);
      if (selected) openNodes().add(bonOrder.branch(selected));
    }
  }
  const receiptTarget = () => state.doc ? { cf: state.doc.header.CodFiscal || '', month: monthKey(state.doc.header.Data) } : null;
  async function changeTreeOrder() {
    const sequence = ++treeOrderSequence, source = receipts;
    const tree = savedTree;
    if (tree) tree.treeElement.inert = true;
    try {
      if (lazy) await source.load();
      if (source !== receipts || sequence !== treeOrderSequence || abort.signal.aborted) return;
      const selected = state.docs.find(doc => doc.id === state.doc?.id);
      const shown = lazy ? await source.reveal(receiptTarget()) : selected ? [bonOrder.branch(selected)] : [];
      if (source !== receipts || sequence !== treeOrderSequence || abort.signal.aborted) return;
      openNodes().clear(); shown.forEach(id => openNodes().add(id));
      treeDirty = true; paintSaved();
    } finally { if (tree) tree.treeElement.inert = false; }
  }
  // A branch of the NIR tree was opened: its placeholder child stays until the server answers, then the real children replace it.
  async function opened(id, isOpen) {
    if (!isOpen || !receipts.needs(id)) return;
    const source = receipts;
    try { await source.expand(id); } catch (error) { if (source === receipts) { openNodes().delete(id); report(error); } }
    if (source !== receipts) return;
    if (!abort.signal.aborted && savedTree?.treeContainer?.isConnected) refreshTree(savedTree, treeNodes(), state.doc?.id);
  }
  async function allowDiscard() { if (state.pendingBody || pendingCancel || pendingLink) { say('Rezultatul operației nu este confirmat. Reluați aceeași operație înainte de a schimba documentul.', true); return false; } return !state.dirty && !headerUi.dirty() && !linesUi.dirty() || await confirmBox('Renunțați la modificările nesalvate?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true }); }
  async function open(id) {
    if (state.busy || !await allowDiscard()) return; const sequence = ++openSequence, doc = await opts.api(url(`/${id}`)); if (sequence !== openSequence || abort.signal.aborted) return;
    Object.assign(state, { doc, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null, returnSource: null }); say(''); paint();
  }
  function draftFromDoc(doc) { return { source: doc.header.source ?? null, tip: doc.doc_type || 'NIR', invoice: doc.invoice, header: { ...doc.header, Numar: String(doc.number ?? '').replace(/\D/g, ''), NumarSub: Number(doc.sub_number) || 0 }, lines: doc.lines.map((line) => newLine({ ...line, _savedCode: line.code })) }; }
  async function add() { if (!await allowDiscard()) return; allocation?.clearDraftProducts(); const today = state.lookups.today; Object.assign(state, { mode: 'add', dirty: false, requestKey: null, pendingBody: null, draft: { source: null, tip: 'NIR', vat: null, autoNumber: true, header: { Data: today, DataFc: today, NumarSub: 0 }, lines: config.source && family !== 'storno' || config.holder ? [] : [newLine()] } }); say(''); if (family === 'bp') await loadWarehouseStock(); paint(); if (family === 'storno') await refreshStornoSources(); if (receipt || custody || bc) await checkNumber(true); }
  async function edit() { if (!state.doc || !state.canWrite || state.doc.annulled || state.busy || state.pendingBody || pendingCancel || pendingLink) return; if (editLocked(state.doc)) { say(editMessage(state.doc)); return; } if (orphanReturn()) { say('Returul nu are bon sursă. Alegeți Bonul returnat și folosiți Asociază cu un bon de consum, apoi puteți modifica returul.', true); inputs.get('source')?._combo?.input.focus(); return; } allocation?.clearDraftProducts(); state.mode = 'edit'; state.draft = draftFromDoc(state.doc); state.dirty = false; say(family === 'bc' ? 'La salvare se verifică stocul pe toate datele ulterioare bonului și se recalculează lista asociată.' : ''); if (family === 'storno') await refreshStornoSources(); if (config.source || config.holder) await eligible(); if (family === 'bp') await loadWarehouseStock(); paint(); }
  async function discard() { if (!await allowDiscard()) return; allocation?.clearDraftProducts(); state.mode = 'view'; state.draft = null; state.dirty = false; state.requestKey = null; state.pendingBody = null; say(''); paint(); }
  // VC06-07: the NIR starts from an invoice the accountant sent from K-BOT (efactura-intake.js fills the draft; the Administrator picks the product of each line).
  async function fromEfactura() {
    if (!intake || !state.canWrite || state.busy) return;
    const detail = await intake.pick(); if (!detail) return;
    if (state.mode === 'add') { if (state.dirty && !await confirmBox('Ciorna începută se înlocuiește cu factura aleasă. Continuați?', { title: 'Factură din e-Factura', yes: 'Înlocuiește', no: 'Renunță' })) return; allocation.clearDraftProducts(); }
    else { await add(); if (state.mode !== 'add') return; }
    const missing = intake.apply(detail, { draft: state.draft, allocation, newLine }); state.dirty = true; paint();
    say(`Factura ${detail.number} (${detail.supplier || detail.cui || ''}) din e-Factura este în ciornă: alegeți produsul fiecărui rând.${detail.note ? ' Notă contabil: ' + detail.note : ''}${detail.partner ? '' : ' Furnizorul nu este în lista de parteneri: alegeți-l.'}${missing.length ? ' Unele conturi propuse sunt în altă gestiune și nu au putut fi bifate.' : ''}`);
  }
  let sourceSequence = 0;
  function refreshStornoSources() {
    const draft = state.draft, sequence = ++sourceSequence, day = draft?.header.Data;
    const operation = (async () => {
      if (!day) return;
      const result = await opts.api(url('/sources', `&date=${encodeURIComponent(day)}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`));
      if (abort.signal.aborted || draft !== state.draft || sequence !== sourceSequence || draft.header.Data !== day) return;
      state.sources = result.documents;
      if (draft.source && !state.sources.some(doc => doc.id === draft.source)) {
        draft.source = null; draft.header.CodFiscal = null; state.eligible = []; draft.lines = [newLine()];
        say('Recepția nu mai are produse disponibile la data FCS aleasă. Selectați o recepție.', true);
      }
      paintHeader(); side?.refresh();
    })();
    return trackStorno(operation);
  }
  async function eligible() {
    const draft = state.draft, sequence = ++eligibleSequence, day = draft.header.Data, source = config.holder ? Number(draft.header[config.holder]) : draft.source;
    if (!source) { state.eligible = []; draft.lines = []; return; }
    if (config.holder) { state.eligible = []; draft.lines.forEach(line => { line._choice = -1; line.qty = line.left = '0'; }); }
    const answer = await trackStorno(opts.api(url('/eligible', `&${config.holder ? 'holder' : 'source'}=${source}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}${family === 'storno' || custody ? `&date=${encodeURIComponent(day || '')}` : ''}`)));
    if (abort.signal.aborted || draft !== state.draft || sequence !== eligibleSequence || draft.header.Data !== day || config.holder && Number(draft.header[config.holder]) !== source) return; state.eligible = answer.lines;
    if (family === 'storno') {
      draft.lines.forEach(line => { const item = answer.lines.find(item => item.variant === line.variant); if (item) Object.assign(line, item); else if (line.variant) line.left = '0'; });
      if (!draft.lines.length) draft.lines.push(newLine());
    } else if (!config.holder) draft.lines = answer.lines.map((item) => { const old = draft.lines.find((line) => line.variant === item.variant && (line.origin ?? line.batch) === item.origin); return newLine({ ...item, id: old?.id ?? null, Cant: old?.Cant ?? '' }); });
    else draft.lines.forEach((line) => { line._choice = answer.lines.findIndex((item) => item.variant === line.variant && item.origin === (line.origin ?? line.batch) && item.IdMasina === line.IdMasina && item.PUTVA === line.PUTVA); const item = answer.lines[line._choice]; line.qty = item?.qty ?? '0'; line.left = item?.left ?? '0'; });
  }
  async function loadWarehouseStock() {
    const draft = state.draft, day = draft.header.Data;
    const available = await warehouseStock(opts.api, opts.sel(), day, state.mode === 'edit' ? state.doc.header.IdBonConsum : null);
    if (draft === state.draft && draft.header.Data === day) state.available = available;
  }
  function calculate(line) {
    if (!editing()) return line;
    if (family === 'storno') return calculateStorno(line, state.zec);
    if (family === 'nir' && (line.calc_mode === 'GROSS' || originalLine(line))) return line;
    const originalPrice = state.mode === 'edit' ? state.doc.lines.find((item) => String(item.variant) === String(line.variant))?.PUTVA : null;
    if (custody) {
      if (family === 'bp') {
        const original = state.mode === 'edit' ? state.doc.lines.find(item => item.id === line.id && String(item.variant) === String(line.variant)) : null;
        line.PUTVA = original?.PUTVA ?? variant(line.variant)?.putva ?? '';
      }
      line.ValoareCUTVA = oiLineValue(line.Cant, line.PUTVA, state.zec);
      return line;
    }
    const qty = number(line.Cant), price = family === 'nir' ? number(line.Pret) : bon() ? number(originalPrice ?? variant(line.variant)?.putva) : number(line.PUTVA), vat = state.lookups.vat?.find((item) => String(item.id) === String(line.IDTVA));
    if (family === 'nir') {
      const zec = allocation.precision(line) ?? state.zec;
      const net = qty != null && price != null ? qty * price : null, grossUnit = price != null && vat ? Number(fixed(price * (1 + Number(vat.percent) / 100), zec)) : null, total = qty != null && grossUnit != null ? Number(fixed(qty * grossUnit, zec)) : null;
      line.Valoare = fixed(net, zec); line.TVA = fixed(total != null && net != null ? total - net : null, zec); line.ValoareCUTVA = fixed(total, zec); if (allocation.isMF(line)) line.ValoareLuna = mfMonthly(line.Pret, vat?.percent, line.DNF);
    } else { line.ValoareCUTVA = fixed(qty != null && price != null ? qty * price : null, state.zec); if (bon()) line.PUTVA = fixed(price, state.zec); } return line;
  }
  const normalized = value => {
    const text = String(value ?? '').replace(',', '.');
    return /^-?\d+(\.\d+)?$/.test(text) ? text.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') : text;
  };
  function originalLine(line) {
    if (family !== 'nir' || state.mode !== 'edit' || !line.id) return false;
    const old = state.doc.lines.find(item => item.id === line.id); if (!old) return false;
    return ['product', 'IDTVA', 'Cant', 'Pret', 'calc_mode', 'gross_total', 'input_price', ...(mf() ? ['code', 'data_intrarii', 'DNF', 'DF'] : [])]
      .every(name => normalized(line[name]) === normalized(old[name]));
  }
  function clearCorrection(line) {
    if (line.calc_mode === 'GROSS' && line.input_price != null) line.Pret = line.input_price;
    delete line.calc_mode; delete line.gross_total; delete line.input_price;
  }
  async function validateDraft(action = 'validate', selectedKey = null) {
    if (family !== 'nir' || !editing()) return null;
    const data = collect();
    if (Object.keys(data.errors).some(name => name.startsWith('h.') || name === 'source')) { if (action !== 'validate') throw new Error('Completați antetul înainte de operație.'); return null; }
    const incomplete = state.draft.lines.filter(line => !line.id && line.calc_mode !== 'GROSS' && lineProblems(line).length);
    const excluded = new Set(incomplete.map(line => line.key));
    data.body.lines = data.body.lines.filter(line => !excluded.has(line.key));
    if (action === 'adjust' && incomplete.some(line => !isBlankLine(line))) throw new Error('Completați rândurile înainte de corecție.');
    const draft = state.draft;
    const result = await json('POST', state.mode === 'add' ? '/distribution/validate' : '/validate', { ...data.body, id: state.mode === 'edit' ? state.doc.id : null, version: state.mode === 'edit' ? state.doc.version : null, action, line_key: selectedKey });
    if (abort.signal.aborted || draft !== state.draft) return null;
    if (action === 'adjust' || action === 'undo_adjustment') {
      draft.lines = result.lines.map(row => {
        const old = draft.lines.find(item => item.key === row.key);
        const item = product(row.product);
        return newLine({ ...old, ...row, _productReady: item?.ready || old?._productReady, name: item?.name || old?.name || '', um: item?.um || old?.um || '' });
      }).concat(incomplete);
      if (result.undo_token) (draft.undo_tokens ||= []).push(result.undo_token);
      if (sharedReceipt(invoiceDoc()) && result.declared != null) draft.header.Valoare = result.declared;
    } else {
      for (const row of result.lines) { const line = draft.lines.find(item => item.key === row.key); if (line) Object.assign(line, row); }
    }
    syncInvoiceValue();
    return result;
  }
  function syncInvoiceValue() {
    if (family !== 'nir' || !editing()) return false;
    const input = inputs.get('Valoare'), result = input?._box?.result();
    const rows = displayRows(state.draft.lines);
    const decimals = Math.max(state.zec, ...rows.map(row => row.zec ?? allocation.precision(row) ?? state.zec));
    const difference = receiptAdjustmentState({ declared: result?.ok ? result.value : null, rows,
      local: state.draft.lines, decimals, mode: state.mode, savedLines: state.doc?.lines || [],
      canWrite: state.canWrite, closed: state.closed, busy: state.busy, pending: state.pendingBody,
      blank: isBlankLine, problems: lineProblems, name: row => row.name || product(row.product)?.name });
    input?.classList.toggle('has-total-difference', rows.some(row => !isBlankLine(row)) && difference.different);
    if (invoiceCorrectionButton) {
      invoiceCorrectionButton.disabled = !difference.different;
      invoiceCorrectionButton.title = difference.tooltip;
    }
    return difference;
  }
  function adjustableLine(row) {
    return adjustableReceiptLine(row, state.mode, state.doc?.lines || []);
  }
  function attachInvoiceCorrection(item) {
    item.node.classList.add('vc-invoice-value');
    if (!editing()) return;
    const row = el('div', { className: 'vc-invoice-value__row' });
    const box = item.input._box.element;
    box.replaceWith(row); row.append(box);
    const button = el('button', { type: 'button', className: 'vc-invoice-value__correct', disabled: true,
      title: 'Corectează o diferență nenulă de cel mult 1 leu, dacă există cel puțin 1 unitate rămasă în magazie, fără consum sau predare.',
      'aria-label': 'Corectează la valoarea facturii' });
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<path d="M12 3v18M7 21h10M4 7h16M5 7l-3 7h6L5 7Zm14 0-3 7h6l-3-7Z"/>';
    button.append(icon);
    const onClick = event => { event.preventDefault(); correctTotal().catch(error => {
      if (abort.signal.aborted || error.name === 'AbortError') return;
      if (error.fields) showErrors(error.fields);
      adjustmentError(error, syncInvoiceValue(), dialogs);
    }); };
    button.addEventListener('click', onClick);
    headerUi.own(() => button.removeEventListener('click', onClick));
    row.append(button); invoiceCorrectionButton = button;
  }
  async function correctTotal() {
    if (adjustmentPending) return;
    adjustmentPending = true;
    try {
      if (!await headerUi.commit() || !await linesUi.commit()) throw new Error('Finalizați sau corectați câmpul/celula aflată în editare înainte de ajustare.');
      const check = syncInvoiceValue();
      if (!check?.different) return;
      if (check.reasons.length) throw new Error('Ajustarea nu poate fi aplicată în condițiile curente.');
      const data = collect();
      if (Object.keys(data.errors).length) {
        showErrors(data.errors);
        throw Object.assign(new Error('Completați sau corectați câmpurile marcate înainte de ajustare.'), { fields: data.errors });
      }
      const selected = linesGrid?.getSelectedRows()[0];
      if (!await confirmBox('Ajustați totalul la valoarea NIR, păstrând cota reală de TVA? Diferența poate fi cel mult 1 leu.', { yes: 'Corectează', no: 'Renunță' })) return;
      const result = await validateDraft('adjust', selected && adjustableLine(selected) ? selected.key : null);
      if (result) { state.dirty = true; paintLines(); say(`Total corectat: ${format(receiptTotal(displayRows(result.lines), 'ValoareCUTVA', 4), { type: 'decimal' })} lei.`); }
    } finally { adjustmentPending = false; syncInvoiceValue(); }
  }
  // The modal route is retained, but disabled for NIR while its context cards are in use.
  let frame = null;
  function syncModal() {
    const modal = editing() && (!inline || RECEIPT_MODAL_ENABLED);
    if (modal && !frame) { frame = new ModalFrame(main, { width: 1240, height: 800, caption: title.parentElement, onRequestClose: () => discard().catch(report) }); frame.open(); root.classList.add('is-modal'); }
    else if (!modal && frame) { frame.close(); frame = null; root.classList.remove('is-modal'); }
  }
  function paint() {
    if (abort.signal.aborted) return; inputs.clear(); errors.clear(); syncModal();
    root.classList.toggle('is-editing', editing());
    root.classList.toggle('is-adding', state.mode === 'add');
    root.classList.toggle('vc-doc--mf', mf());
    mfInfo?.setVisible(mf() && !editing());
    title.textContent = `${config.title}${editing() ? state.mode === 'add' ? ' • document nou' : ' • modificare' : state.doc ? ` nr. ${numberText(state.doc)}` : ''}`;
    pill.textContent = editing() ? 'Ciornă' : state.doc ? state.doc.annulled ? 'Anulat' : 'Salvat' : ''; pill.hidden = !pill.textContent; pill.dataset.state = editing() ? 'draft' : state.doc?.annulled ? 'annulled' : 'saved';
    if (family === 'nir' && (editing() || !state.doc?.annulled)) pill.hidden = true;
    paintHeader(); paintLines(); paintBar(); paintSaved(); freezePending();
  }
  // Starting stock hides the supplier/invoice fields in place; its checkbox stays in the title bar.
  function stockHidden() {
    if (family !== 'nir') return;
    const on = (editing() ? state.draft.tip : state.doc?.doc_type) === 'STOC_INITIAL';
    headerHost.querySelector('[data-field="CodFiscal"]')?.classList.toggle('is-stock', on);
    const invoice = headerHost.querySelector('[data-field="NrFact"]'); invoice?.classList.toggle('is-stock', on);
  }
  function paintHeader() {
    vatInput = null;
    invoiceCorrectionButton = null;
    headerUi.clear(); headerHost.replaceChildren(); stockHost.replaceChildren(); stockHost.hidden = true;
    if (!editing() && !state.doc) { headerHost.append(el('p', { textContent: 'Alegeți un document din arbore sau adăugați unul nou.' })); return; }
    const header = editing() ? state.draft.header : { ...state.doc.header, Numar: numberText(state.doc) };
    headerHost.classList.toggle('vc-doc__header--nir', family === 'nir'); headerHost.classList.toggle('vc-doc__header--oi', ['bp', 'bt', 'bs'].includes(family));
    if (family === 'storno') {
      const item = stornoSourceField(headerUi, state.sources, editing() ? state.draft.source : header.source,
        header.source_label, editing(), async value => {
          if (!await linesUi.commit()) { paintHeader(); throw new Error('Finalizați rândul înainte de schimbarea recepției.'); }
          const draft = state.draft;
          if (draft.source === value) return;
          if (draft.lines.some(line => !isBlankLine(line)) && !await confirmBox('Schimbarea recepției elimină produsele selectate din ciornă.', { yes: 'Schimbă recepția', no: 'Păstrează' })) { paintHeader(); return; }
          const source = state.sources.find(doc => doc.id === value);
          draft.source = value; draft.header.CodFiscal = source?.party_cf ?? null;
          draft.header.party = source?.party || ''; draft.lines = [newLine()]; state.dirty = true;
          try { await eligible(); if (draft !== state.draft || draft.source !== value) return; paintLines(); side?.refresh(); } catch (error) { report(error); throw error; }
        }, onHeaderKey);
      headerHost.append(item.node); inputs.set('source', item.input); errors.set('source', item.error);
    } else if (config.source) {
      const missing = !editing() && orphanReturn(), recovery = missing && state.canWrite && !state.doc.annulled;
      const item = field({ key: 'source', label: config.source, type: 'ref', required: true,
        placeholder: missing ? 'Fără bon sursă' : 'Alegeți…', fallback: header.source_label || '',
        options: state.sources.map((doc) => ({ value: doc.id, label: sourceLabel(doc) })) },
      editing() ? state.draft.source : missing ? state.returnSource : header.source,
      editing() && state.mode === 'add' || !!recovery, async value => {
        if (missing) { state.returnSource = Number(value) || null; return; }
        state.draft.source = Number(value) || null; state.dirty = true;
        try { await eligible(); paintLines(); } catch (error) { report(error); }
      }, headerUi);
      headerHost.append(item.node); inputs.set('source', item.input); errors.set('source', item.error);
      if (missing) headerHost.append(el('p', { className: 'vc-hint', textContent: 'Fără bon sursă. Asociați returul cu un bon de consum înainte de modificare.' }));
    }
    const groups = null;
    headerHost.classList.toggle('vc-doc__header--allocation', family === 'nir');
    allocation?.ensure();
    for (const spec of headerSpecs()) {
      if (family === 'nir' && spec.key === 'Accounts') {
        if (editing()) {
          const item = allocation.accountField(headerUi, true, async ids => {
            if (!await linesUi.commit()) throw new Error('Finalizați rândul înainte de schimbarea conturilor.');
            if (state.draft.lines.some(line => line.product && !ids.includes(product(line.product)?.category))) throw new Error('Scoateți întâi produsele conturilor pe care doriți să le debifați.');
            const previousRoot = allocation.roots().join('|');
            state.draft.accounts = ids;
            if (previousRoot !== allocation.roots().join('|')) Object.assign(state.draft.header, { Numar: null, NumarSub: 0 });
            state.dirty = true; paintHeader(); paintLines();
            try { await checkNumber(true); } catch (error) { report(error); }
          }, onHeaderKey);
          headerHost.append(item.node); inputs.set('Accounts', item.input); errors.set('h.Accounts', tooltipSink(item.node));
        } else {
          const ids = [...new Set(displayRows(state.doc.lines).map(line => line.category).filter(Boolean))];
          const item = field({ key: 'Accounts', label: 'Categorii / conturi', type: 'text' }, ids.map(id => allocation.account(id)?.label || String(id)).join(', '), false, () => {}, headerUi);
          headerHost.append(item.node);
        }
        continue;
      }
      // Auto-select singleton accounts and BC receivers, preserving other saved receivers.
      const singleReceiver = family === 'bc' && spec.key === 'IDC' && (state.mode === 'add' || header.IDC == null || String(header.IDC) === String(spec.options?.[0]?.value));
      const only = (['IdMasina', 'IdMasinaP'].includes(spec.key) || singleReceiver) && spec.options.length === 1 ? spec.options[0] : null;
      if (only && editing()) header[spec.key] = only.value;
      const sharedIdentity = family === 'nir' && state.draft?.invoice && !state.draft.invoice.local && ['CodFiscal', 'NrFact', 'DataFc'].includes(spec.key);
      const automatic = family === 'nir' && editing() && allocation.multi() && spec.key === 'Numar';
      const fieldSpec = { ...spec,
        ...(family === 'nir' && spec.key === 'NrFact' ? { textAlign: 'center' } : {}),
        ...((receipt || custody || bc) && spec.key === 'Numar' ? { textAlign: 'right', ...(automatic ? { type: 'text' } : { onLeave: subnumber.leave }) } : {}),
        ...(family === 'nir' && spec.key === 'Valoare' ? { label: 'Valoare Reală Factură (lei)', textAlign: 'right', displayFormat: 'standard', numberLocale: 'en-US' } : {}) };
      const invoiceAmount = family === 'nir' && spec.key === 'Valoare';
      const value = invoiceAmount ? invoiceValue(invoiceDoc(), header) : header[spec.key];
      const makeField = custody && ['IdMasina', 'IdMasinaP'].includes(spec.key)
        ? (spec, value, enabled, callback, owner) => oiAccount(owner, spec, value, enabled, callback, onHeaderKey)
        : family === 'bc' && spec.key === 'IdMasina'
        ? (spec, value, enabled, callback, owner) => consumptionCategory(owner, spec, value, enabled, callback, onHeaderKey) : field;
      const item = makeField(fieldSpec, automatic ? 'auto' : value, editing() && !only && !sharedIdentity && !automatic && !(invoiceAmount && sharedReceipt(invoiceDoc())), async (value) => {
        if (family === 'bt' && ['IDC_OLD', 'IDC_BT'].includes(spec.key) && value !== ''
          && String(value) === String(state.draft.header[spec.key === 'IDC_OLD' ? 'IDC_BT' : 'IDC_OLD'])) {
          paintHeader(); say('Primitorul și predătorul trebuie să fie persoane diferite.', true); return;
        }
        const previous = state.draft.header[spec.key];
        const previousDates = family === 'nir' && ['Data', 'DataFc'].includes(spec.key)
          ? { Data: state.draft.header.Data, DataFc: state.draft.header.DataFc } : null;
        const syncNirDate = family === 'nir' && spec.key === 'DataFc' && previous !== value;
        state.draft.header[spec.key] = value; state.dirty = true;
        if (syncNirDate) {
          state.draft.header.Data = value;
          inputs.get('Data')?._picker?.setValue(value ? new Date(value + 'T00:00:00') : null, false);
        }
        if (family === 'storno' && spec.key === 'Numar') state.draft.autoNumber = false;
        if (family === 'nir' && spec.key === 'Valoare') syncInvoiceValue();
        if (status.textContent.startsWith('Completați mai întâi antetul') && !headerLock()) say('');
        if (spec.key === config.holder && String(previous) !== String(value)) {
          if (!await linesUi.commit()) { state.draft.header[spec.key] = previous; paintHeader(); return; }
          if (state.draft.lines.some(line => !isBlankLine(line)) && !await confirmBox('Schimbarea predătorului elimină produsele selectate din ciornă.', { yes: 'Schimbă predătorul', no: 'Păstrează' })) { state.draft.header[spec.key] = previous; paintHeader(); return; }
          state.draft.lines = []; try { await eligible(); paintLines(); } catch (error) { report(error); }
        }
        if (spec.key === accountKey()) { const incompatible = state.draft.lines.filter((line) => (line.variant || line.product) && !inAccount(family === 'nir' ? product(line.product) : variant(line.variant))); if (incompatible.length) { state.draft.header[spec.key] = previous; paintHeader(); say('Contul nu se poate schimba: există produse care nu îi aparțin.', true); return; } paintLines(); }
        if (family === 'bc' && spec.key === 'IdMasina' && String(previous) !== String(value)) {
          // With numbering per account the number depends on the account: the automatic one is offered again, a typed one is only rechecked.
          const draft = state.draft, offer = state.mode === 'add' && draft.autoNumber;
          if (offer) draft.header.Numar = '';
          try { await checkNumber(offer); } catch (error) { report(error); }
        }
        if (spec.key === 'Data' || syncNirDate) {
          availability();
          if (family === 'bp' && previous !== value) { state.available = {}; try { await loadWarehouseStock(); paintLines(); } catch (error) { report(error); } }
          if (config.holder && previous !== value) { try { await eligible(); paintLines(); } catch (error) { report(error); throw error; } }
          if (family === 'storno' && previous !== value) {
            const draft = state.draft;
            try {
              await refreshStornoSources();
              if (draft !== state.draft || draft.header.Data !== value) return;
              await eligible();
              if (draft !== state.draft || draft.header.Data !== value) return;
              paintLines(); await checkNumber(state.mode === 'add' && draft.autoNumber);
            }
            catch (error) { report(error); }
          } else if (family === 'nir') { try { await checkNumber(); } catch (error) { Object.assign(state.draft.header, previousDates); paintHeader(); report(error); return; } }
        }
        if (family === 'nir' && ['Data', 'DataFc'].includes(spec.key)) { try { await validateDraft(); } catch (error) { Object.assign(state.draft.header, previousDates); paintHeader(); report(error); return; } }
        side?.refresh(); custodySide?.refresh();
      }, headerUi);
      inputs.set(spec.key, item.input); errors.set(`h.${spec.key}`, item.error); if (spec.hint && editing()) item.input.title = spec.hint;
      if (receipt && spec.key === 'Numar' && editing() && !automatic) subnumber.attach(item, header);
      if (family === 'nir' && spec.key === 'Valoare') attachInvoiceCorrection(item);
      if (family === 'nir' && spec.key === 'Valoare' && editing()) {
        item.input.addEventListener('input', syncInvoiceValue, { signal: abort.signal });
      }
      const index = ['CodFiscal', 'IdMasina', 'IdComisie'].includes(spec.key) ? 0 : ['NrFact', 'DataFc'].includes(spec.key) ? 1 : spec.key === 'Valoare' ? 3 : 2; (groups ? groups[index] : headerHost).append(item.node);
      if (family === 'nir' && spec.key === 'CodFiscal' && state.mode === 'add') {
        stockHost.hidden = false;
        const stock = headerUi.own(new CheckBox(stockHost, { label: 'Stoc inițial (fără furnizor)', labelSide: 'left', className: 'vc-nir-stock', checked: state.draft.tip === 'STOC_INITIAL',
          onChange: (checked) => {
            state.draft.tip = checked ? 'STOC_INITIAL' : 'NIR'; state.dirty = true;
            if (checked) { state.draft.invoice = null; state.draft.header.FacturaId = null; }
            for (const name of ['CodFiscal', 'NrFact']) { const input = inputs.get(name); input?._setRequired(!checked); if (checked) { input?._clearValue(); state.draft.header[name] = null; } }
            stockHidden();
            side?.refresh();
          } }));
      }
    }
    stockHidden();
    syncInvoiceValue();
    if (family === 'nir' && editing()) {
      const rates = [...new Map((state.lookups.vat || []).map(item => [Number(item.percent), item])).values()];
      const vat = field({ key: '_vat', label: 'Cotă TVA', type: 'ref', dropDownStyle: 'simple', options: rates.map(item => ({ value: item.id, label: `${item.percent}%` })) }, state.draft.vat, true, async value => {
        if (!await linesUi.commit()) { paintHeader(); return; }
        const backup = structuredClone(state.draft);
        try { state.draft.vat = Number(value) || null; const percent = allocation.tax(value)?.percent;
          state.draft.lines.forEach(line => { if (isBlankLine(line) || line.has_warehouse_exit) return; clearCorrection(line); line.IDTVA = allocation.defaultTax(line.product, percent); if (!line.IDTVA) throw new Error('Cota TVA nu este configurată în destinația produsului.'); calculate(line); });
          await validateDraft(); state.dirty = true; paintLines(); }
        catch (error) { state.draft = backup; paintHeader(); paintLines(); report(error); }
      }, headerUi); vat.node.classList.add('vc-nir-vat'); headerHost.append(vat.node); vatInput = vat.input; syncVatLock();
    }
    if (state.doc?.annulled && !editing()) headerHost.append(el('p', { textContent: `Motiv anulare: ${state.doc.reason || ''}` }));
    bc?.syncDate();
  }
  // An error message for a place that is not a field (the lines grid) is a tooltip, never text on the screen: assigning '' clears it.
  const tooltipSink = (node) => ({ get textContent() { return node.title; }, set textContent(text) { if (text) node.title = text; else node.removeAttribute('title'); } });
  // Keyboard walk through the header, like an Access form: Tab / Shift+Tab and Enter go from one editable control to the next. Checkboxes,
  // buttons (list arrows, calendar), hidden, disabled and invisible controls are skipped. After the last one the lines grid starts editing.
  function headerStops() {
    return [...headerHost.querySelectorAll('input, select, textarea')].filter((node) => !node.disabled && node.type !== 'hidden' && node.type !== 'checkbox'
      && node.getClientRects().length > 0 && getComputedStyle(node).visibility !== 'hidden');
  }
  function onHeaderKey(event, source = event.target) {
    if ((event.key !== 'Tab' && event.key !== 'Enter') || event.altKey || event.ctrlKey || event.metaKey || !editing()) return;
    const stops = headerStops(), at = stops.indexOf(source);
    if (at < 0) return;
    const back = event.key === 'Tab' && event.shiftKey, next = stops[at + (back ? -1 : 1)];
    if (next) { event.preventDefault(); next.focus(); if (next.type === 'text' && !next.readOnly) next.select?.(); return; }
    if (back) return;   // Shift+Tab on the first control: the browser decides
    const row = state.draft?.lines[0], column = lineSpecs().find((spec) => spec.editable);
    if (linesGrid && row && column) { event.preventDefault(); Promise.resolve(headerUi.commit()).then((ok) => { if (ok && linesGrid) linesGrid.beginEdit(row, column.key); }).catch(report); }
  }
  function productOptions() { return (family === 'nir' ? products() : variants()).filter(inAccount).map((item) => ({ value: item.id, label: family === 'nir' ? item.name : `${item.name} • ${format(item.putva, { type: 'decimal' })} lei` })); }
  // The NIR grid always ends with one empty row (the new row, as in an Access datasheet); an empty line is never saved.
  const isBlankLine = (line) => !line.product && !line.variant && [line.Cant, line.Pret].every((value) => value == null || String(value).trim() === '');
  // NIR amounts returned by validation are authoritative; rendering must not recalculate them with binary floats.
  const displayLine = line => { if (bc && line.variant) line.avail = bc.remaining(line); return family === 'nir' ? line : calculate(line); };
  const draftRows = () => { const rows = gridRows(state.draft.lines).map(displayLine); rows.forEach((row, index) => { row._index = index + 1; }); return rows; };
  function dropLine(line) { state.draft.lines = state.draft.lines.filter((other) => other.key !== line.key); linesGrid.setRows(draftRows(), { keepWidths: true }); totals(); availability(); side?.refresh(); custodySide?.refresh(); clearRowError(); }
  // A line that was edited is judged when the operator leaves it: what is missing or wrong (the same rules as the save) and the question.
  function lineProblems(line) {
    if (family === 'nir' && state.mode === 'edit' && line.has_warehouse_exit) return [];
    if (originalLine(line) && !allocation?.isMF(line)) return [];
    return [...lineSpecs().filter((spec) => spec.editable && (!spec.mfOnly || allocation.isMF(line))).filter((spec) => spec.type === 'ref' ? line[spec.key] == null || line[spec.key] === '' : !parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }).ok).map((spec) => spec.label), ...(productEntry?.issues(line) || [])];
  }
  const rowIssue = (row) => { if (config.source && family !== 'storno') return ''; const missing = lineProblems(row); return missing.length ? `Rândul ${row._index} nu este complet. Lipsesc sau sunt greșite: ${missing.join(', ')}.` : ''; };
  const confirmRowIssue = async ({ message }) => await confirmBox(`${message} Continuați editarea rândului?`, { title: 'Rând incomplet', yes: 'Continuă editarea', no: 'Anulează rândul' }) ? 'continue' : 'cancel';
  function paintLines() {
    linesUi.clear(); linesGrid = null; linesHost.replaceChildren(); if (!editing() && !state.doc) return;
    const compactMF = family === 'nir' && opts.sel().type === 'MF';
    const local = editing() ? state.draft.lines : state.doc.lines.map((line) => ({ ...line, key: `saved:${line.id}` }));
    const rows = gridRows(local).map(displayLine); rows.forEach((row, index) => { row._index = index + 1; });
    const specs = lineSpecs().filter(spec => !compactMF || !['Cant', 'IDTVA'].includes(spec.key)), columns = [{ key: '_index', title: '#', width: 46, fixedWidth: true, filter: false, sortable: false, align: 'center' }];
    for (const spec of specs) {
      const choices = row => spec.product ? config.holder ? state.eligible.map((item, index) => ({ value: index, label: `${item.name} • ${item.um} • disponibil ${format(item.left ?? item.qty, { type: 'decimal' })}` })) : productOptions() : (family === 'nir' ? allocation.taxOptions(row) : state.lookups.vat || []).map((item) => ({ value: item.id, label: `${item.percent}%` }));
      columns.push(column({ ...spec, type: spec.type || 'text' }, { width: spec.product || spec.key === 'name' || spec.key === 'ef_name' ? compactMF ? 424 : 234 : inline && spec.key === 'Cant' ? 90 : spec.key === 'um' ? 64 : inline && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? 100 : 128, showArrow: inline, editable: editing() && !!spec.editable, filter: !editing(), sortable: !editing(),
        ...(spec.tooltip ? { tooltip: spec.tooltip } : {}),
        ...(spec.key === 'um' ? { align: 'center' } : receipt && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? { align: 'right' } : {}),
        ...(family === 'nir' && spec.editable ? { editable: row => editing() && !row.external && row.calc_mode !== 'GROSS' && (!spec.mfOnly || allocation.isMF(row)) && (spec.key !== 'um' || !!row.new_product) && (spec.key !== 'Cant' || !allocation.isMF(row)) } : {}),
        ...(spec.type === 'ref' ? { editor: 'list', searchable: true, options: choices, formatter: (value) => family === 'nir' && spec.key === 'IDTVA' ? allocation.tax(value)?.percent || '' : spec.product && (!editing() || spec.key === 'name') ? rows.find((row) => row[spec.key] === value)?.name || String(value ?? '') : choices().find((item) => String(item.value) === String(value))?.label || '', validate: (value) => value == null || value === '' ? 'Alegeți o valoare din listă.' : null } : {}),
        ...(family === 'nir' && spec.key === 'IDTVA' ? { formatter: value => allocation.tax(value)?.percent ?? rows.find(row => String(row.IDTVA) === String(value))?.vat_percent ?? '' } : {}),
        ...(family === 'nir' && spec.product ? { editor: 'tree', allowCustom: true, nodes: allocation.nodes, selectParents: false, dropDownMinWidth: 300, focusAfterSelect: row => row.new_product ? 'um' : allocation.isMF(row) ? 'Pret' : 'Cant', formatter: value => rows.find(row => String(row.product) === String(value))?.name || product(value)?.name || '' } : {}),
        ...(family === 'nir' && spec.key === 'data_intrarii' ? { align: 'center' } : {}),
        ...(family === 'nir' && ['DNF', 'DF', 'ValoareLuna'].includes(spec.key) ? { width: 100, minWidth: 100, fixedWidth: true } : {}),
        ...(family === 'nir' && spec.key === 'ValoareLuna' ? { formatter: value => value == null || value === '' ? '' : new Intl.NumberFormat('ro-RO', { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(number(value)) } : {}),
        ...(family === 'bc' && spec.product ? { editor: 'tree', nodes: row => bc.nodes(variants().filter(inAccount), row), selectParents: false, focusAfterSelect: 'Cant', dropDownMinWidth: 360, dropDownClassName: 'vc-bc-products', formatter: value => variant(value)?.name || rows.find(row => String(row.variant) === String(value))?.name || '' } : {}),
        ...(custody && spec.product ? { editor: 'tree', nodes: row => oiProductNodes(config.holder ? holderChoices(state.eligible, state.draft?.lines || [], row) : warehouseChoices(variants().filter(inAccount), state.available, state.draft?.lines || [], row), state.lookups.categories || [], !!config.holder),
          selectParents: false, focusAfterSelect: 'Cant', dropDownMinWidth: 430,
          formatter: value => !editing() && config.holder ? String(value ?? '') : config.holder ? state.eligible[Number(value)]?.name || rows.find(row => row._choice === value)?.name || '' : variant(value)?.name || rows.find(row => String(row.variant) === String(value))?.name || '' } : {}),
        ...(family === 'storno' && spec.product ? { editor: 'tree', nodes: () => stornoProductNodes(state.eligible, state.lookups.categories || [], state.draft?.lines || []), selectParents: false, dropDownMinWidth: 360,
          focusAfterSelect: 'Cant', formatter: value => rows.find(row => String(row.variant) === String(value))?.name || state.eligible.find(row => String(row.variant) === String(value))?.name || '' } : {}),
        ...(family === 'storno' && spec.key === 'IDTVA' ? { formatter: value => state.lookups.vat.find(item => String(item.id) === String(value))?.percent ?? rows.find(row => String(row.IDTVA) === String(value))?.vat_percent ?? '' } : {}),
        ...(spec.mfOnly ? { required: false } : {}) }));
    }
    columns.push({ key: '_action', title: '⋮', width: 46, fixedWidth: true, filter: false, sortable: false, display: 'button', actionText: '⋮', actionLabel: 'Opțiuni linie' });
    const body = el('div', { className: 'vc-doc__grid' }); errors.set('lines', tooltipSink(body)); linesHost.append(el('h4', { textContent: config.lines }), body);
    linesGrid = grid(body, { columns, rows, rowKey: 'key',
      ...(inline ? { suspendRowLeave: () => state.busy || productEntry?.busy } : {}),
      rowClassName: row => family === 'nir' ? receiptLineClass(row, opts.sel(), allocation.product) : '',
      rowIssue, confirmRowIssue, newRowAlwaysVisible: inline && editing(), isBlankRow: isBlankLine, onCancelNewRow: dropLine,
      onRowLeave: () => { clearRowError(); },
      onNewRow: () => { const line = newLine({ IDTVA: state.draft.vat }); state.draft.lines.push(line); line._index = gridRows(state.draft.lines).length; return line; }, proportionalWidths: true, editable: editing(), allowIncompleteEdits: inline, singleClickEdit: true, editGuard: headerLock, onEditBlocked: lockedLines, footer: true, footerCaption: '', layoutId: `vercon.${opts.sel().type}.${family}.lines.${family === 'bc' ? 'consumption-v2' : mf() ? `receipt-mf-${compactMF ? 'v3' : 'v2'}.${editing() ? 'edit' : 'view'}` : receipt ? 'receipt-v7' : 'compact-v1'}`,
      ...(!inline ? { footerAction: { label: '➕ Adaugă rând', span: 2, disabled: !editing() || !!config.source }, onFooterAction: () => addLine().catch(report) } : {}),
      onCellSave: async ({ row, key: name, value }) => {
        const line = state.draft.lines.find((item) => item.key === row.key); if (!line) throw new Error('Linia nu mai este în document.');
        const before = structuredClone(line);
        if (family === 'nir' && ['product', 'Cant', 'Pret', 'IDTVA'].includes(name)) clearCorrection(line);
        if (family === 'storno' && name === 'variant') {
          const item = state.eligible.find(item => String(item.variant) === String(value));
          if (!item) throw new Error('Alegeți un produs din recepție cu stoc la data FCS.');
          if (state.draft.lines.some(other => other !== line && other.variant != null && stornoProductKey(other) === stornoProductKey(item))) throw new Error('Produsul cu acest preț este deja în document. Modificați cantitatea pe rândul existent.');
          Object.assign(line, item, { id: line.variant === item.variant ? line.id : null });
        } else if (name === '_choice') { const item = state.eligible[Number(value)]; if (!item) throw new Error('Alegeți un produs al predătorului.'); if (state.draft.lines.some((other) => other !== line && other.variant === item.variant && (other.origin ?? other.batch) === item.origin && other.IdMasina === item.IdMasina && other.PUTVA === item.PUTVA)) throw new Error('Produsul din acest lot, cont și preț este deja în document.'); const id = line.variant === item.variant && (line.origin ?? line.batch) === item.origin && line.IdMasina === item.IdMasina && line.PUTVA === item.PUTVA ? line.id : null; Object.assign(line, { ...item, _choice: Number(value), id }); }
        else if (family === 'nir' && name === 'product') productEntry.selectProduct(value, line);
        else { line[name] = ['variant', 'product', 'IDTVA', 'invoice_line'].includes(name) ? value == null || value === '' ? null : Number(value) : value; if (['variant', 'product'].includes(name)) { const item = family === 'nir' ? product(value) : variant(value); line.name = item?.name || ''; line.um = item?.um || ''; if (name === 'product') line.variant = null; } }
        if (family === 'nir' && name === 'product') {
          const item = product(line.product), percent = allocation.tax(before.IDTVA)?.percent ?? allocation.tax(state.draft.vat)?.percent;
          line.destination = item?.destination;
          line.IDTVA = percent == null ? null : allocation.defaultTax(line.product, percent);
          productEntry.defaults(line);
        }
        try {
          bc?.validate(line, name);
          if (family === 'nir' && name === 'um') { await productEntry.finishProduct(line); line.IDTVA = allocation.defaultTax(line.product, allocation.tax(line.IDTVA)?.percent ?? allocation.tax(state.draft.vat)?.percent); productEntry.defaults(line); }
          if (family === 'nir' && ['code', 'DNF', 'data_intrarii'].includes(name) && productEntry.issues(line).length) throw new Error(productEntry.issues(line).join('; '));
          if (family === 'bp' && name === 'variant' && state.draft.lines.some(other => other !== line && other.variant === line.variant)) throw new Error('Produsul este deja pe bon. Modificați cantitatea rândului existent.');
          if (custody && !(number(line.Cant) > 0) && name === 'Cant') throw new Error('Cantitatea trebuie să fie pozitivă.');
          if (config.holder && name === 'Cant' && number(line.Cant) > number(line.left ?? 0)) throw new Error('Cantitatea depășește disponibilul lotului la data bonului.');
          if (family === 'storno' && name === 'Cant' && number(line.Cant) > number(state.eligible.find(item => item.variant === line.variant)?.left ?? 0)) throw new Error('Cantitatea depășește stocul disponibil pentru stornare la data FCS.');
          calculate(line); if (family === 'nir') await validateDraft();
        }
        catch (error) { Object.keys(line).forEach(key => delete line[key]); Object.assign(line, before); throw error; }
        state.dirty = true; return { ...line };
      }, onCellSaved: ({ row, key }) => { totals(); availability(); side?.refresh(row); custodySide?.refresh(row); if (key === null || !rowIssue(row)) clearRowError(); }, onEditError: ({ error }) => say(error.message, true),
      onSelect: (row) => { mfInfo?.select(row); if (!row.external) { side?.refresh(row); custodySide?.refresh(row); } },
      onEditState: ({ state: mode }) => { if (mode === 'editing') { side?.refresh(linesGrid?.getSelectedRows()[0]); custodySide?.refresh(linesGrid?.getSelectedRows()[0]); } },
      onCellAction: ({ row }) => { if (row.external) return; return rowMenu(linesGrid, row, [...(family === 'nir' && allocation.isMF(row) ? [{ icon: '⧉', label: 'Multiplică rând', disabled: !editing() || !row.product || row.calc_mode === 'GROSS', run: () => multiplyLine(row).catch(report) }] : []), { icon: '×', label: 'Scoate rândul', disabled: !editing() || !!config.source && family !== 'storno', run: async () => {
        if (!await linesUi.commit()) return;
        if (row.calc_mode === 'GROSS') {
          if (!await confirmBox('Ștergeți corecția și returnați cantitatea în grupul de origine?', { yes: 'Revino în grup', no: 'Renunță' })) return;
          try { await validateDraft('undo_adjustment', row.key); state.dirty = true; paintLines(); clearRowError(); } catch (error) { report(error); } return;
        }
        if (row.id && !await confirmBox(`Scoateți rândul din ciorna ${family === 'bc' ? 'bonului' : family === 'storno' ? 'FCS-ului' : 'NIR-ului'}?`, { yes: 'Scoate', no: 'Renunță' })) return;
        const before = state.draft.lines; state.draft.lines = before.filter(item => item.key !== row.key);
        try { if (family === 'nir') await validateDraft(); state.dirty = true; paintLines(); clearRowError(); }
        catch (error) { state.draft.lines = before; report(error); }
      } }]); },
    }, linesUi); totals(); availability(); side?.refresh(); custodySide?.refresh(); mfInfo?.select(rows[0]);
  }
  async function multiplyLine(row) {
    if (!await linesUi.commit()) return;
    const source = state.draft.lines.find(line => line.key === row.key);
    if (!source || lineProblems(source).length) throw new Error('Completați rândul MF înainte de multiplicare.');
    if (state.draft.lines.filter(line => !isBlankLine(line)).length >= 500) throw new Error('Cel mult 500 de rânduri pe document.');
    const copy = newLine({ ...structuredClone(source), key: key(), id: null, variant: null, code: productEntry.nextCode(source, source.code), Cant: '1' });
    state.draft.lines.splice(state.draft.lines.indexOf(source) + 1, 0, copy);
    state.dirty = true; paintLines(); linesGrid.beginEdit(copy, 'code');
  }
  // The lines grid stays locked until every required header field is filled; the reason is the message shown on a click in it.
  function headerLock() {
    if (!editing()) return '';
    const blank = (value) => value == null || String(value).trim() === '';
    const missing = headerSpecs().filter((spec) => spec.required && (spec.key === 'Accounts' ? !allocation.selectedAccounts().length : blank(inputs.get(spec.key)?.value ?? state.draft.header[spec.key]))).map((spec) => spec.label);
    if (config.source && !state.draft.source) missing.unshift(config.source);
    return missing.length ? `Completați mai întâi antetul documentului: ${missing.join(', ')}.` : bc?.lockMessage() || '';
  }
  function lockedLines({ message }) { say(message, true); headerUi.validate(); }
  async function addLine(item) {
    if (!editing() || !await linesUi.commit()) return; const lock = headerLock(); if (lock) { lockedLines({ message: lock }); inputs.get(headerSpecs().find((spec) => spec.required && !(inputs.get(spec.key)?.value))?.key)?.closest('.vc-field')?.querySelector('input:not([type=hidden])')?.focus(); return; } if (family === 'nir' && linesGrid) { linesGrid.beginEditNewRow('product'); return; } if (config.holder && !state.eligible.length) { say('Alegeți un predător cu produse disponibile.', true); return; }
    const line = newLine({ ...(family === 'nir' ? { IDTVA: state.draft.vat } : {}), ...(item ? { [family === 'nir' ? 'product' : 'variant']: item.id, name: item.name, um: item.um } : {}) }); state.draft.lines.push(line); state.dirty = true; paintLines(); linesGrid.beginNewRowEdit(line, config.holder ? '_choice' : family === 'nir' ? 'product' : 'variant', () => { state.draft.lines = state.draft.lines.filter((other) => other.key !== line.key); paintLines(); });
  }
  // The header VAT rate is locked as soon as a line has been filled in (the empty new row does not count).
  let vatInput = null;
  const seededDrafts = new WeakSet();
  function syncVatLock() {
    if (!vatInput?._combo || !state.draft) return;
    const rows = state.draft.lines.filter(line => !isBlankLine(line));
    const taxes = rows.map(line => state.lookups.vat.find(item => String(item.id) === String(line.IDTVA)));
    const common = rows.length > 0 && taxes.every(item => item && String(item.percent) === String(taxes[0]?.percent));
    vatInput.closest('.vc-field').hidden = state.mode === 'edit' ? !common : new Set(taxes.filter(Boolean).map(item => Number(item.percent))).size > 1;
    // A saved NIR has no header rate of its own: take it from the lines once, when the draft opens.
    // After that a rate changed in the grid never moves the header (only the header moves the lines).
    if (!seededDrafts.has(state.draft)) {
      seededDrafts.add(state.draft);
      if (common && state.draft.vat == null) state.draft.vat = [...state.lookups.vat].reverse().find(item => Number(item.percent) === Number(taxes[0].percent))?.id;
    }
    vatInput.value = state.draft.vat ?? '';
    const tax = state.lookups.vat.find(item => String(item.id) === String(state.draft.vat));
    vatInput._combo.setValue(String(state.draft.vat ?? ''), tax ? `${tax.percent}%` : '');
    const hasAuto = rows.some(line => line.calc_mode === 'GROSS');
    vatInput._combo.setEnabled(!hasAuto);
    vatInput._combo.input.title = hasAuto ? 'Ștergeți corecția automată pentru a modifica TVA; cantitatea revine în grupul de origine.' : '';
  }
  function totals() {
    syncVatLock();
    syncInvoiceValue();
    if (!linesGrid) return;
    const rows = gridRows(editing() ? state.draft.lines : state.doc.lines);
    const zec = family === 'nir' ? Math.max(state.zec, ...rows.map(row => row.zec ?? allocation.precision(row) ?? state.zec)) : state.zec;
    const sum = (name) => family === 'nir' || custody ? receiptTotal(rows, name, Math.max(zec, custody ? 4 : 0)) : fixed(rows.reduce((total, row) => total + (number(row[name]) || 0), 0), zec);
    const lineTotal = sum('ValoareCUTVA');
    const values = { PUTVA: 'TOTAL', ValoareCUTVA: format(lineTotal, { type: 'decimal' }) };
    if (receipt) { values.Valoare = format(sum('Valoare'), { type: 'decimal' }); values.TVA = format(sum('TVA'), { type: 'decimal' }); if (family === 'storno') values.Pret = 'TOTAL'; }
    linesGrid.setFooter({ values });
    // FCS has no reconciliation between the saved header amount and the line total.
    if (family === 'storno') return;
    let note = linesHost.querySelector('.vc-doc__reconciliation');
    if (!note) { note = el('p', { className: 'vc-hint vc-doc__reconciliation' }); linesHost.append(note); }
    const declared = state.doc ? number(invoiceValue(invoiceDoc(), state.doc.header)) : null, difference = declared == null ? 0 : Number(fixed(declared - number(lineTotal), state.zec));
    note.hidden = editing() || !difference;
    note.textContent = note.hidden ? '' : `Valoare în antet: ${format(invoiceValue(invoiceDoc(), state.doc.header), { type: 'decimal' })} lei. Diferență față de liniile documentului: ${format(fixed(difference, state.zec), { type: 'decimal' })} lei.`;
  }
  function availability() {
    if (bc) { bc.syncDate(); void bc.load().catch(report); return; }
    clearTimeout(availabilityTimer); const sequence = ++availabilitySequence;
    if (!bon() || !editing() && (family !== 'bc' || !state.doc)) return;
    const document = editing() ? state.draft : state.doc, inEdit = editing(), savedId = state.doc?.id;
    if (family !== 'bp') state.available = {};
    availabilityTimer = setTimeout(async () => {
      const date = parse(inputs.get('Data')?.value ?? document.header.Data, { type: 'date', required: true });
      const ids = [...new Set((family === 'bp' && inEdit ? variants().map(item => item.id) : document.lines.map(line => line.variant)).filter(Boolean))];
      if (!date.ok || !ids.length) return;
      try {
        // Consult and edit show the same stock available to this BC: its own
        // saved consumption is excluded in both modes. A new BC has no saved id.
        const excludedId = family === 'bc' ? state.mode !== 'add' ? savedId : null
          : state.mode === 'edit' ? state.doc.header.IdBonConsum : null;
        const exclude = excludedId ? `&exclude_kind=bc&exclude_id=${excludedId}` : '';
        const result = await opts.api(`/api/vercon/stock/available?${query()}&date=${date.value}${family === 'bp' && inEdit ? '' : `&products=${ids.join(',')}`}${exclude}`);
        if (abort.signal.aborted || sequence !== availabilitySequence || inEdit !== editing() || document !== (inEdit ? state.draft : state.doc)) return;
        state.available = result.available;
        document.lines.forEach(line => {
          const base = result.available[String(line.variant)];
          line.avail = family === 'bp' && base != null
            ? receiptTotal([{ quantity: base }, ...document.lines.filter(other => other !== line && other.variant === line.variant && number(other.Cant) > 0).map(other => ({ quantity: '-' + other.Cant }))], 'quantity', 4)
            : base ?? '';
        });
        if (linesGrid && !linesGrid.hasEdit) {
          const rows = inEdit ? draftRows() : document.lines.map((line, index) => ({ ...line, key: `saved:${line.id}`, _index: index + 1 }));
          linesGrid.setRows(rows, { keepWidths: true });
        }
      } catch (error) { report(error); }
    }, 300);
  }
  const treeNodes = () => lazy ? receipts.nodes() : bonOrder.nodes(state.docs);
  function paintSaved() {
    savedHost.hidden = (!!config.holder || mf()) && editing();
    savedHost.inert = custody && editing();
    savedHost.setAttribute('aria-disabled', String(custody && editing()));
    custodySide?.setVisible(editing());
    side?.setVisible(editing() && !mf() && !RECEIPT_MODAL_ENABLED);
    if (savedHost.hidden || side && editing() && !mf() && !RECEIPT_MODAL_ENABLED) return;
    if (savedTree?.treeContainer?.isConnected) {   // the tree is kept alive: opening a document only moves the mark; new data is drawn in place
      if (savedHost.hidden) return;
      if (treeDirty) refreshTree(savedTree, treeNodes(), state.doc?.id); else markSelected(savedTree, state.doc?.id);
      paintTreeAdd();
      treeDirty = false; return;
    }
    treeUi.clear(); savedTree = null; if (savedHost.hidden) return; treeDirty = false;
    savedTree = paintTree(treeHost, treeNodes(), { title: family === 'bc' ? 'Bonuri salvate' : family === 'storno' ? 'FCS salvate' : 'Documente salvate', showSearchBox: false, collapsed: true, selected: state.doc?.id, onToggle: lazy ? opened : undefined, onSelect: (id) => open(Number(id)).catch(report), onAction: (id, anchor) => {
      if (id.startsWith('party:')) { openMenu(anchor, [{ icon: 'ℹ', label: 'Afișează info furnizor', run: () => showSupplierInfo({ api: opts.api, sel: opts.sel, cf: decodeURIComponent(id.slice(6)) }).catch(report) }]); return; }
      const doc = lazy ? receipts.find(id) : state.docs.find((item) => String(item.id) === id); if (!doc) return; const run = async (action) => { if (state.doc?.id !== doc.id) await open(doc.id); if (state.doc?.id === doc.id) await action(); };
      openMenu(anchor, [...(doc.source_missing ? [{ icon: '↔', label: 'Asociază cu un bon de consum', disabled: !state.canWrite || doc.annulled, run: () => run(edit).catch(report) }] : []),
        ...(family === 'nir' && doc.has_efactura ? [{ icon: '📄', label: 'Afișează eFactura', disabled: state.busy, run: () => run(() => showReceiptEfactura({ api: opts.api, selection: opts.sel(), doc: state.doc, owner: dialogs })).catch(report) }] : []),
        ...(family === 'nir' && doc.doc_type !== 'STOC_INITIAL' ? [{ icon: '↔', label: 'Asociază eFactura…', disabled: !state.canWrite || state.busy || editing() || doc.annulled || !!state.pendingBody || !!pendingCancel || !!pendingLink, run: () => run(associateSavedEfactura).catch(report) }] : []),
        { icon: '✎', label: 'Modifică', disabled: !state.canWrite || doc.annulled, run: () => run(edit).catch(report) }, { icon: '◉', label: 'Previzualizează', run: () => run(preview).catch(report) }, { icon: '◷', label: 'Istoric', run: () => run(history).catch(report) }, { icon: '×', label: 'Anulează', rule: true, disabled: !state.canWrite || doc.annulled || doc.order_locked, run: () => run(cancel).catch(report) }], { align: 'right' });
    } }, treeUi);
    paintTreeAdd();
  }
  function paintTreeAdd() {
    bonOrder.mount(custody ? savedHost : treeHost, treeUi, changeTreeOrder, !editing(), report);
    if (!inline) return;
    const heading = treeHost.querySelector(':scope > h4');
    if (!heading) return;
    if (!treeAddButton || treeAddButton.element.parentElement !== heading) {
      treeAddButton?.destroy();
      treeAddButton = new ActionAddButton(heading, { className: 'vc-receipt-tree__add',
        title: `Adaugă ${custody ? config.title.toLocaleLowerCase('ro') : family === 'bc' ? 'bon de consum' : family === 'storno' ? 'FCS' : 'NIR'}`, onClick: () => add().catch(report) });
      treeUi.own(treeAddButton);
    }
    treeAddButton.setEnabled(state.canWrite && !(custody && editing()));
  }
  async function associateSavedEfactura() {
    if (family !== 'nir' || editing() || !state.canWrite || state.busy || !state.doc || state.doc.annulled || state.doc.doc_type === 'STOC_INITIAL' || state.pendingBody || pendingCancel || pendingLink) return;
    const result = await associateEfactura({ api: opts.api, sel: opts.sel, doc: state.doc });
    if (result) { await open(state.doc.id); await refresh(); paint(); }
    if (result) say(result.state === 'PARTIALA' ? 'Asociere parțială salvată. Restul facturii poate fi asociat altor NIR-uri, inclusiv din alte subunități.' : result.state === 'PRELUATA' ? '' : 'Asocierile acestui NIR au fost eliminate.');
  }
  function paintBar() {
    const button = (text, action, attrs = {}) => { const node = el('button', { type: 'button', textContent: text, ...attrs }); node.addEventListener('click', () => Promise.resolve(action()).catch(report), { signal: abort.signal }); return node; };
    const start = [], end = [];
    const note = state.closed ? el('span', { className: 'vc-hint', textContent: 'Perioadă închisă: numai consultare.', 'data-kbot-tooltip-overflow': '', tabIndex: 0 }) : null;
    bar.classList.toggle('vc-doc__bar--nir-editing', inline && editing());
    if (pendingLink || pendingCancel) { statusBar(bar, { status, end: [button(pendingLink ? 'Reia aceeași asociere' : 'Reia aceeași anulare', pendingLink ? linkReturn : cancel)] }); return; }
    if (!editing() && (family === 'storno' || custody)) start.push(button(family === 'storno' ? 'Linii de storno fără recepție' : 'Reglare registru OI', recoverStorno, { disabled: state.busy }));
    if (!editing() && orphanReturn() && !state.doc.annulled) start.push(button('Asociază cu un bon de consum', linkReturn, { disabled: !state.canWrite || state.busy }));
    if (inline && editing()) {
      statusBar(bar, { start: [button('Renunță', discard, { className: 'vc-doc__cancel' })], status, note,
        end: [button(`Salvează ${family === 'bc' || custody ? 'bonul' : family === 'storno' ? 'FCS' : 'NIR'}`, save, { className: 'is-primary vc-doc__save', 'data-retry-save': 'true' })] });
      return;
    }
    if (editing()) {
      start.push(button('Renunță', discard), button('Previzualizează', preview));
      end.push(button(`Salvează ${family === 'nir' ? 'NIR' : 'bonul'}`, save, { className: 'is-primary', 'data-retry-save': 'true' }));
    } else {
      if (!inline) start.push(button('+ Adaugă', add, { disabled: !state.canWrite }));
      if (state.doc) { start.push(button('Previzualizează', preview)); end.push(button('Modifică', edit, { disabled: !state.canWrite || state.doc.annulled })); }
    }
    statusBar(bar, { start, status, note, end });
  }
  function collect() {
    const out = { header: {}, lines: [], source: state.draft.source }, invalid = {};
    for (const spec of headerSpecs()) {
      if (family === 'nir' && spec.key === 'Accounts') { if (!allocation.selectedAccounts().length) invalid['h.Accounts'] = 'Bifați conturile.'; continue; }
      if (family === 'nir' && allocation.multi() && spec.key === 'Numar') { out.header.Numar = null; continue; }
      const input = inputs.get(spec.key);
      // The displayed invoice total is read-only context; writes retain this NIR's local allocation.
      const localAmount = family === 'nir' && spec.key === 'Valoare' && sharedReceipt(invoiceDoc());
      const localized = localAmount ? { ok: true, value: state.draft.header.Valoare } : input?._box?.options.numberLocale ? input._box.result() : null;
      if (localized && !localized.ok) { invalid[`h.${spec.key}`] = localized.message; continue; }
      const raw = localized ? localized.value : input?.value ?? state.draft.header[spec.key]; if (spec.type === 'ref') { out.header[spec.key] = raw === '' || raw == null ? null : spec.key === 'CodFiscal' ? String(raw).trim() : Number(raw); if (spec.required && out.header[spec.key] == null && !(family === 'nir' && state.draft.tip === 'STOC_INITIAL' && spec.key === 'CodFiscal')) invalid[`h.${spec.key}`] = 'Alegeți o valoare.'; } else { const result = parse(raw, spec); if (!result.ok) invalid[`h.${spec.key}`] = result.message; else out.header[spec.key] = result.value; }
    }
    if (family === 'nir') { out.header.TipDoc = state.draft.tip; out.header.NumarSub = allocation.multi() ? 0 : Number(state.draft.header.NumarSub) || 0; out.header.FacturaId = state.draft.header.FacturaId ?? null; out.undo_tokens = state.draft.undo_tokens || []; out.accounts = [...state.draft.accounts]; if (state.draft.efactura) out.efactura = state.draft.efactura; }
    if (family === 'storno') out.header.NumarSub = Number(state.draft.header.NumarSub) || 0;
    if (family === 'bt' && out.header.IDC_OLD != null && out.header.IDC_OLD === out.header.IDC_BT) {
      invalid['h.IDC_OLD'] = invalid['h.IDC_BT'] = 'Primitorul și predătorul trebuie să fie persoane diferite.';
    }
    for (const line of state.draft.lines) {
      if (inline && isBlankLine(line)) continue;   // the always-visible new row
      if (family === 'nir' && state.mode === 'edit' && line.has_warehouse_exit) { out.lines.push({ ...line }); continue; }
      if (family === 'nir' && productEntry.issues(line).length) invalid[`l|${line.key}|product`] = productEntry.issues(line).join('; ');
      if (family === 'nir' && originalLine(line) && !allocation.isMF(line)) { out.lines.push({ ...line }); continue; }
      if (family === 'storno') {
        const entry = state.eligible.find(item => item.variant === line.variant);
        if (!entry) invalid[`l|${line.key}|variant`] = 'Alegeți un produs disponibil din recepția selectată.';
        if (!(number(line.Cant) > 0) || number(line.Cant) > number(entry?.left ?? 0)) invalid[`l|${line.key}|Cant`] = 'Introduceți o cantitate pozitivă în limita stocului de stornat la data FCS.';
      }
      if (config.source || config.holder) { if (!config.holder && (line.Cant == null || line.Cant === '')) continue; const result = parse(String(line.Cant ?? ''), { type: 'decimal', required: true, min: 0 }); if (!result.ok || !line.variant || !(number(line.Cant) > 0) || config.holder && line._choice < 0) invalid[`l|${line.key}|Cant`] = result.message || 'Alegeți un lot disponibil și o cantitate pozitivă.'; else out.lines.push({ key: line.key, id: line.id, variant: line.variant, batch: line.origin ?? line.batch ?? null, IdMasina: line.IdMasina, PUTVA: line.PUTVA, Cant: result.value }); continue; }
      const result = { key: line.key, id: line.id, invoice_line: line.invoice_line }; for (const spec of lineSpecs().filter((item) => item.editable && (!item.mfOnly || allocation.isMF(line)))) { if (spec.type === 'ref') { result[spec.key] = line[spec.key] ?? null; if (result[spec.key] == null) invalid[`l|${line.key}|${spec.key}`] = 'Alegeți o valoare.'; } else { const parsed = parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }); if (!parsed.ok) invalid[`l|${line.key}|${spec.key}`] = parsed.message; else result[spec.key] = parsed.value; } }
      if (line.new_product) result.new_product = { ...line.new_product, UM: line.um };
      if (line.calc_mode === 'GROSS') Object.assign(result, { calc_mode: 'GROSS', gross_total: line.gross_total, input_price: line.input_price, auto_origin: line.auto_origin, auto_token: line.auto_token }); out.lines.push(result);
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
        if (spec?.required && fieldName !== 'Accounts' && blank(inputs.get(fieldName)?.value)) continue;   // empty required: colour and tooltip came from validate(), no message under it
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
  function freezePending() {
    headerHost.inert = linesHost.inert = !!state.pendingBody || !!pendingLink;
    for (const node of bar.querySelectorAll('button')) {
      if (state.pendingBody) { node.dataset.originalDisabled ??= String(node.disabled); node.disabled = node.dataset.retrySave !== 'true'; }
      else if (node.dataset.originalDisabled != null) { node.disabled = node.dataset.originalDisabled === 'true'; delete node.dataset.originalDisabled; }
    }
  }
  async function save() {
    if (!state.canWrite || !editing() || state.busy) return;
    state.busy = true;
    let submitted = false;
    say('Se pregătește salvarea…');
    try {
      if (!await headerUi.commit()) { say('Salvarea este oprită. Finalizați sau corectați câmpul din antet.', true); return; }
      while (stornoPending.size) await Promise.all([...stornoPending]);
      if (!await linesUi.commit()) {
        if (status.getAttribute('role') !== 'alert') say('Salvarea este oprită. Finalizați sau corectați celula din tabel.', true);
        return;
      }
      if (abort.signal.aborted || !editing()) return;
      const data = collect();
      if (Object.keys(data.errors).length) {
        showErrors(data.errors);
        const fields = headerSpecs().filter(spec => Object.hasOwn(data.errors, `h.${spec.key}`)).map(spec => spec.label);
        say(fields.length ? `Completați sau corectați: ${fields.join(', ')}.` : 'Completați sau corectați rândurile marcate din tabel.', true);
        return;
      }
      if (family === 'nir' && !state.pendingBody && !sharedReceipt(invoiceDoc())) {
        // A difference of at most 1 leu between the invoice value and the lines is a warning, not a block.
        const gap = syncInvoiceValue();
        if (gap?.different && gap.canCorrect) {
          const proceed = await confirmBox(`${gap.summary}\n\nPuteți apăsa butonul de corecție de lângă Valoare sau puteți salva NIR-ul cu această diferență. Salvați cu diferență?`,
            { title: 'Diferență între valoarea facturii și produse', yes: 'Salvează cu diferență', no: 'Nu salva', warning: true });
          if (!proceed) { say('Salvarea a fost oprită. Corectați diferența sau salvați cu diferență.', true); return; }
          data.body.accept_difference = true;
        }
      }
      root.inert = true; say('Se salvează…');
      state.requestKey ||= key(); state.pendingBody ||= structuredClone({ ...data.body, ...(state.mode === 'edit' ? { version: state.doc.version } : {}) });
      submitted = true;
      const result = await json(state.mode === 'add' ? 'POST' : 'PUT', state.mode === 'add' ? family === 'nir' ? '/distribution' : '' : `/${state.doc.id}`, { ...state.pendingBody, request_key: state.requestKey });
      if (abort.signal.aborted) return; Object.assign(state, { doc: result.document, mode: 'view', draft: null, dirty: false, requestKey: null, pendingBody: null }); paint(); say(result.receipts ? 'Salvat: ' + result.receipts.map(item => `${item.name} · NIR ${item.number}`).join('; ') : result.result?.recalculated_lists?.length ? 'Salvat. Economiile / depășirile listei de alimente asociate au fost recalculate.' : result.replayed ? 'Salvat (cererea fusese deja aplicată).' : 'Salvat.'); try { if (family === 'nir') { await load(); if (!abort.signal.aborted) paint(); } await refresh(); if (!abort.signal.aborted) paintSaved(); } catch (error) { report(error); } }
    catch (error) {
      if (error.fields) showErrors(error.fields);
      if (submitted && error.status && error.status < 500) { state.requestKey = null; state.pendingBody = null; }
      report(error);
      if (submitted && (!error.status || error.status >= 500)) say('Răspunsul salvării nu este confirmat. Reîncercați salvarea aceleiași ciorne; cererea păstrează aceeași cheie.', true);
    }
    finally { state.busy = false; root.inert = false; freezePending(); syncInvoiceValue(); }
  }
  let pendingCancel = null;
  async function linkReturn() {
    if (!orphanReturn() || !state.canWrite || state.doc.annulled || state.busy) return;
    const source = Number(inputs.get('source')?.value) || null;
    if (!pendingLink && !source) { errors.get('source').textContent = 'Alegeți bonul de consum.'; say('Alegeți Bonul returnat înainte de asociere.', true); return; }
    pendingLink ||= { id: state.doc.id, source, version: state.doc.version, request_key: key() };
    state.busy = true; root.inert = true;
    try {
      const result = await json('POST', `/${pendingLink.id}/link`, pendingLink);
      if (abort.signal.aborted) return;
      pendingLink = null; state.doc = result.document; state.returnSource = null;
      paint(); say('Returul a fost asociat bonului de consum. Puteți modifica documentul.');
      try { await refresh(); if (!abort.signal.aborted) paintSaved(); } catch (error) { report(error); }
    } catch (error) {
      if (error.status && error.status < 500) pendingLink = null;
      if (error.fields) showErrors(error.fields);
      report(error);
      if (pendingLink) say('Rezultatul asocierii nu este confirmat. Reluați aceeași asociere; cererea păstrează aceeași cheie.', true);
    } finally { state.busy = false; root.inert = false; paintBar(); freezePending(); }
  }
  async function recoverStorno() {
    if (state.busy || editing()) return;
    state.busy = true; paintBar();
    try { await (family === 'storno' ? orphanStorno : repairRegister)({ api: opts.api, url, json, owner: dialogs, report, changed: async result => {
      if (state.doc?.id === result.document_id) state.doc = await opts.api(url(`/${result.document_id}`));
      await refresh(); if (!abort.signal.aborted) paint();
    } }); } finally { state.busy = false; if (!abort.signal.aborted) paintBar(); }
  }
  async function cancel() {
    const doc = state.doc; if (!doc || !state.canWrite || doc.annulled || state.busy || pendingLink) return;
    if (doc.order_locked && !pendingCancel) { say(doc.order_message, true); return; }
    if (!pendingCancel) {
      const reason = await reasonBox(`Anulați ${config.title.toLocaleLowerCase('ro')} nr. ${doc.number}?`, { title: 'Anulare document', yes: 'Anulează', no: 'Renunță' });
      if (reason == null) return; pendingCancel = { reason, version: doc.version, request_key: key(), id: doc.id };
    }
    state.busy = true;
    try { const result = await json('POST', `/${pendingCancel.id}/cancel`, pendingCancel); if (abort.signal.aborted) return; pendingCancel = null; state.doc = result.document; await refresh(); paint(); say('Anulat.'); }
    catch (error) { if (error.status && error.status < 500) pendingCancel = null; report(error); }
    finally { state.busy = false; paintBar(); }
  }
  async function history() { const doc = state.doc; if (doc) await showDocumentHistory({ api: opts.api, url: url(`/${doc.id}/history`), title: `Istoric • ${config.title} nr. ${doc.number}`, owner: dialogs, isCurrent: () => !abort.signal.aborted && state.doc === doc }); }
  async function preview() {
    if (editing() && (!await headerUi.commit() || !await linesUi.commit())) return; const data = editing() ? collect() : null; if (data && Object.keys(data.errors).length) { say(showErrors(data.errors) ? 'Corectați câmpurile marcate.' : '', true); return; } const header = editing() ? data.body.header : state.doc ? { ...state.doc.header, Numar: numberText(state.doc) } : null; if (!header) return;
    const token = openSequence, doc = editing() ? null : state.doc, draft = state.draft;
    const headerValue = spec => {
      if (family === 'nir' && spec.key === 'Accounts') {
        const ids = editing() ? state.draft.accounts : [...new Set(state.doc.lines.map(line => line.category).filter(Boolean))];
        return ids.map(id => allocation.account(id)?.label || String(id)).join(', ');
      }
      if (family === 'nir' && editing() && spec.key === 'Numar' && allocation.multi()) return 'auto';
      return spec.type === 'ref' ? (typeof spec.options === 'function' ? spec.options() : spec.options).find(item => String(item.value) === String(header[spec.key]))?.label || '' : format(header[spec.key], spec);
    };
    const sections = [
      { title: 'Antet', columns: [{ key: 'label', title: 'Câmp', width: 240 }, { key: 'value', title: 'Valoare', width: 580 }], rows: headerSpecs().map(spec => ({ label: spec.label, value: headerValue(spec) })) },
      { title: config.lines, columns: lineSpecs().map((spec) => ({ key: spec.product ? 'name' : spec.key, title: spec.label, width: spec.product ? 250 : 120, formatter: (value) => spec.key === 'IDTVA' ? `${state.lookups.vat.find(item => String(item.id) === String(value))?.percent ?? ''}%` : format(value, { type: spec.type === 'ref' ? 'text' : spec.type || 'text' }) })), rows: editing() ? state.draft.lines.filter((line) => family !== 'nir' || !isBlankLine(line)) : state.doc.lines },
    ];
    await documentReport({ api: opts.api, selection: opts.sel(), family, document: doc, header,
      title: `${config.title}${editing() ? ` • Ciornă nesalvată${header.Numar ? ` nr. ${header.Numar}` : ''}` : ` nr. ${state.doc.number}`}`, date: header.Data, sections, owner: dialogs,
      isCurrent: () => !abort.signal.aborted && token === openSequence && draft === state.draft && (!doc || doc === state.doc) });
  }
  // opts.startAdd: the page opens straight on a new document (nothing of the consultation screen is drawn meanwhile).
  function pauseContext() {
    contextAbort.abort(); loadGeneration++; numbering.invalidate(); openSequence++; availabilitySequence++; eligibleSequence++;
    sourceSequence++; stornoPending.clear();
    clearTimeout(numberTimer); clearTimeout(availabilityTimer);
  }
  async function reloadContext() {
    const next = { ...sourceOptions.sel() }, linked = family === 'nir' ? linkedReceipt(state.doc, next) : null;
    if (next.type !== orderType) { orderType = next.type; bonOrder.reset(defaultByDate()); }
    pauseContext(); contextAbort = new AbortController(); currentContext = next;
    side?.setVisible(false);
    custodySide?.setVisible(false);
    [headerUi, linesUi, treeUi, dialogs].forEach(owner => owner.clear());
    savedTree = null; linesGrid = null; treeHost.replaceChildren(); treeHost._vcExpanded = new Set();
    receipts = lazy ? createReceipts() : null;
    Object.assign(state, { doc: null, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null,
      canWrite: false, eligible: [], sources: [], available: {}, busy: false });
    pendingCancel = null; pendingLink = null; state.returnSource = null; say('Se reîncarcă datele gestiunii…');
    await load();
    if (abort.signal.aborted || contextAbort.signal.aborted) return;
    paint(); say('');
    if (linked) { await open(linked.id); if (state.doc) { await refresh(); paintSaved(); } }
  }
  function canChangeContext() {
    if (!state.busy && !productEntry?.busy && !state.pendingBody && !pendingCancel && !pendingLink) return true;
    say('Finalizați operația în curs înainte de a schimba gestiunea.', true);
    return false;
  }
  headerHost.addEventListener('keydown', onHeaderKey, { signal: abort.signal });
  const initialContext = currentContext;
  const ready = load().then(() => {
    if (abort.signal.aborted || contextAbort.signal.aborted || currentContext !== initialContext) return;
    if (opts.startAdd && state.canWrite) return add();
    paint(); if (opts.startAdd) say('Nu aveți dreptul să adăugați documente în această gestiune.', true);
  }).catch(report);
  if (!opts.startAdd) paint();
  async function startEfactura() {
    const context = currentContext;
    await ready;
    if (abort.signal.aborted || contextAbort.signal.aborted || currentContext !== context || !canChangeContext()) return;
    if (!state.canWrite) { say('Nu aveți dreptul să adăugați documente în această gestiune.', true); return; }
    await fromEfactura();
  }
  return { ready, reloadContext, pauseContext, canChangeContext, startEfactura, startAdd: async () => { await ready; if (abort.signal.aborted) return; if (state.canWrite) await add(); else say('Nu aveți dreptul să adăugați documente în această gestiune.', true); }, isDirty: () => state.dirty || headerUi.dirty() || linesUi.dirty(), search: (text) => { const input = savedHost.querySelector('.treeview-search-input'); if (input) { input.value = text; input.dispatchEvent(new Event('input')); } }, destroy: () => { productEntry?.destroy(); frame?.destroy(); side?.destroy(); custodySide?.destroy(); mfInfo?.destroy(); abort.abort(); pauseContext(); [headerUi, linesUi, treeUi, dialogs].forEach((owner) => owner.clear()); host.replaceChildren(); } };
}
