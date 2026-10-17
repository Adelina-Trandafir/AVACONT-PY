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
import { receiptAllocation } from './receipt-allocation.js';
import { receiptDifference, receiptTotal } from './receipt-reconciliation.js';
import { sharedReceipt, invoiceRows, invoiceValue, receiptLineClass, linkedReceipt } from './receipt-invoice-view.js';
import { stornoSourceField, stornoProductNodes, stornoProductKey, calculateStorno } from './storno-editor.js';
import { consumptionNodes, consumptionCategory, consumptionBranch } from './consumption-ui.js';
import { oiAccount, oiProductNodes, oiDocumentNodes } from './oi-ui.js';
import { oiLineValue } from './oi-values.js';

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
const treeAlphabet = new Intl.Collator('ro', { sensitivity: 'base' });

export function mountDocuments(host, opts) {
  const family = opts.family, config = families[family], abort = new AbortController();
  const receipt = ['nir', 'storno'].includes(family);
  const custody = ['bp', 'bt', 'bs'].includes(family);
  const inline = receipt || family === 'bc' || custody;
  const sourceOptions = opts;
  let currentContext = { ...opts.sel() }, contextAbort = new AbortController(), loadGeneration = 0;
  opts = { ...opts, sel: () => currentContext,
    api: (path, options = {}) => sourceOptions.api(path, { ...options, signal: AbortSignal.any([abort.signal, contextAbort.signal]) }) };
  const state = { docs: [], lookups: null, doc: null, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null, canWrite: false, closed: false, zec: 4, eligible: [], sources: [], available: {}, busy: false };
  const headerUi = scope(), linesUi = scope(), treeUi = scope(), dialogs = scope();
  const stornoPending = new Set();
  function trackStorno(promise) {
    if (family !== 'storno' && !custody) return promise;
    stornoPending.add(promise);
    promise.finally(() => stornoPending.delete(promise)).catch(() => {});
    return promise;
  }
  const inputs = new Map(), errors = new Map();
  let savedTree = null, treeAddButton = null, treeDirty = true, linesGrid = null, invoiceCorrectionButton = null, availabilityTimer = null, numberTimer = null, numberSequence = 0, availabilitySequence = 0, openSequence = 0, eligibleSequence = 0;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const say = (text, bad = false) => showMessage(status, text, bad ? 'error' : 'info');
  const report = (error) => { if (!abort.signal.aborted && error.name !== 'AbortError') say(error.requestId ? `${error.message} (cod ${error.requestId})` : (error.message || 'Operația a eșuat.'), true); };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const url = (path, extra = '') => `/api/vercon/doc/${family}${path}?${query()}${extra}`;
  const json = (method, path, body) => opts.api(`/api/vercon/doc/${family}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...opts.sel(), ...body }) });
  // NIR: the saved receipts are read level by level, when the operator opens a supplier / a month (receipt-tree.js).
  const lazy = family === 'nir';
  const createReceipts = () => receiptTree((params) => opts.api(url('/tree', `&${new URLSearchParams(params)}`)));
  let receipts = lazy ? createReceipts() : null;
  const allocation = family === 'nir' ? receiptAllocation({ api: opts.api, selection: opts.sel, draft: () => state.draft, mode: () => state.mode }) : null;
  const mf = () => family === 'nir' && (opts.sel().type === 'MF' || allocation.hasMF()), editing = () => state.mode !== 'view';
  const invoiceDoc = () => family === 'nir' && state.mode !== 'add' ? state.doc : null;
  const displayRows = local => invoiceRows(invoiceDoc(), local);
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
  const treeHost = inline ? el('div', { className: 'vc-receipt-tree' }) : savedHost;
  if (treeHost !== savedHost) savedHost.append(treeHost);
  const side = inline && !custody ? receiptContext(savedHost, { root, main, headerHost, linesHost, treeHost, family,
    api: opts.api, selection: opts.sel, lineSelection: line => allocation ? allocation.selectionForProduct(line.product) : opts.sel(), context: () => ({ draft: state.draft }),
    validLine: (line) => !isBlankLine(line) && (['storno', 'bc'].includes(family) ? !!line.variant : !lineProblems(line).length),
    blankLine: (line) => isBlankLine(line) }) : null;

  function headerSpecs() {
    const lookup = state.lookups || {}, specs = [], ref = (name, label, items, required = true) => ({ key: name, label, type: 'ref', required, showArrow: inline, options: options(items), ...(custody && state.mode !== 'add' ? { fallback: state.doc?.header.labels?.[name] || '' } : {}) });
    if (family === 'nir') specs.push({ ...{ ...ref('IdPartener', 'Furnizor', lookup.partners, state.draft?.tip !== 'STOC_INITIAL'), dropDownOnEnter: true }, fallback: state.mode === 'add' ? '' : state.doc?.header.party || '' }, { key: 'Accounts', label: 'Categorie / cont', type: 'tree', required: true }, { ...ref('IdComisie', 'Comisie', lookup.commissions, (lookup.commissions || []).length > 0), dropDownStyle: 'simple' }, { key: 'NrFact', label: 'Număr factură', type: 'text', required: state.draft?.tip !== 'STOC_INITIAL' }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, { key: 'Valoare', label: state.draft?.invoice && !state.draft.invoice.local ? 'Valoare aferentă acestui NIR (lei)' : 'ValoareFC (lei)', type: 'decimal', min: 0, hint: 'Necompletat: suma produselor. Valoarea declarată trebuie egalată exact.' });
    if (family === 'storno') specs.push({ key: 'NrFact', label: 'Număr factură storno', type: 'text', required: true }, { key: 'DataFc', label: 'Data facturii', type: 'date', required: true }, ref('IdComisie', 'Comisie', lookup.commissions, false));
    if (family === 'bc') specs.push({ ...ref('IDC', 'Primitor / gestionar', lookup.receivers), dropDownOnEnter: true, fallback: state.doc?.header.receiver || '' }, ref('IdMasina', 'Categorie / cont', lookup.categories));
    if (family === 'bp') specs.push(ref('IdMasinaP', 'Cont OI în magazie', lookup.warehouse_accounts), ref('IDC_BP', 'Primitor', lookup.holders), ref('IdMasina', 'Cont OI în folosință', lookup.use_accounts));
    if (family === 'bt') specs.push(ref('IDC_OLD', 'Predător', lookup.holders), ref('IDC_BT', 'Primitor', lookup.holders));
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
      ...(config.holder ? [{ key: 'account', label: 'Cont în folosință' }, { key: 'batch', label: 'Lot proveniență' }] : []),
      ...(editing() ? [{ key: 'qty', label: config.holder ? 'Disponibil la predător' : 'Cantitate sursă', type: 'decimal' }, ...(!config.holder ? [{ key: 'done', label: 'Deja', type: 'decimal' }, { key: 'left', label: 'Rămas', type: 'decimal' }] : [])] : []),
      { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, min: 0 }, { key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' },
    ];
    const specs = [{ key: family === 'nir' ? 'product' : 'variant', label: 'Produs', type: 'ref', product: true, editable: true, required: true }, { key: 'um', label: 'UM' },
      ...(['bc', 'bp'].includes(family) ? [{ key: 'avail', label: 'Disponibil', type: 'decimal' }] : []),
      { key: 'Cant', label: 'Cantitate', type: 'decimal', editable: true, required: true, min: 0 }];
    if (family === 'nir') {
      specs.push({ key: 'Pret', label: 'Preț', type: 'decimal', editable: true, required: true, min: 0 }, { key: 'IDTVA', label: 'TVA %', type: 'ref', editable: true, required: true }, { key: 'Valoare', label: 'Valoare', type: 'decimal' }, { key: 'TVA', label: 'TVA', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'TOTAL', type: 'decimal' });

      if (mf()) specs.push(...[{ key: 'code', label: 'Nr. inventar', editable: true, required: true }, { key: 'data_intrarii', label: 'Data intrării', type: 'date', editable: true, required: true }, { key: 'DNF', label: 'Durată normală (luni)', type: 'int', editable: true, required: true, min: 1 }, { key: 'DF', label: 'Durată folosită (luni)', type: 'int', editable: true, min: 0 }, { key: 'ValoareLuna', label: 'Valoare lunară', type: 'decimal' }].map(spec => ({ ...spec, mfOnly: true })));
    } else specs.push({ key: 'PUTVA', label: 'Preț cu TVA (lei)', type: 'decimal' }, { key: 'ValoareCUTVA', label: 'Valoare (lei)', type: 'decimal' }); return specs;
  }
  const docLabel = (doc) => `${receipt ? `${family.toUpperCase()} ${numberText(doc)} • ${doc.invoice || ''}` : `${family.toUpperCase()} ${doc.number}`} • ${format(doc.total, { type: 'decimal' })} lei${doc.annulled ? ' • anulat' : ''}`;
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
    if (lazy) { const shown = await receipts.refresh(state.doc ? { party: state.doc.header.party || '', month: String(state.doc.header.Data || '').slice(0, 7) } : null); if (!abort.signal.aborted && shown.length) { const open = openNodes(), root = shown[0]; for (const id of [...open]) if (id.startsWith('party:') ? id !== root : id.startsWith('month:') && !id.startsWith(`month:${root.slice(6)}|`)) open.delete(id); shown.forEach((id) => open.add(id)); } treeDirty = true; return; }
    const answer = await opts.api(url('/list')); if (!abort.signal.aborted) {
      state.docs = answer.documents; treeDirty = true;
      if (family === 'bc' && state.doc) {
        const expanded = openNodes(), branches = consumptionBranch(state.doc.header.receiver, state.doc.header.Data);
        for (const id of [...expanded]) if (id.startsWith('receiver:') || id.startsWith('receiver-month:')) expanded.delete(id);
        branches.forEach(id => expanded.add(id));
      }
    }
  }
  // A branch of the NIR tree was opened: its placeholder child stays until the server answers, then the real children replace it.
  async function opened(id, isOpen) {
    if (!isOpen || !receipts.needs(id)) return;
    const source = receipts;
    try { await source.expand(id); } catch (error) { if (source === receipts) { openNodes().delete(id); report(error); } }
    if (source !== receipts) return;
    if (!abort.signal.aborted && savedTree?.treeContainer?.isConnected) refreshTree(savedTree, treeNodes(), state.doc?.id);
  }
  async function allowDiscard() { if (state.pendingBody || pendingCancel) { say('Rezultatul operației nu este confirmat. Reluați aceeași operație înainte de a schimba documentul.', true); return false; } return !state.dirty && !headerUi.dirty() && !linesUi.dirty() || await confirmBox('Renunțați la modificările nesalvate?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true }); }
  async function open(id) {
    if (state.busy || !await allowDiscard()) return; const sequence = ++openSequence, doc = await opts.api(url(`/${id}`)); if (sequence !== openSequence || abort.signal.aborted) return;
    Object.assign(state, { doc, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null }); say(''); paint();
  }
  function draftFromDoc(doc) { return { source: doc.header.source ?? null, tip: doc.doc_type || 'NIR', invoice: doc.invoice, header: { ...doc.header, Numar: String(doc.number ?? '').replace(/\D/g, ''), NumarSub: Number(doc.sub_number) || 0 }, lines: doc.lines.map((line) => newLine({ ...line })) }; }
  async function add() { if (!await allowDiscard()) return; const today = state.lookups.today; Object.assign(state, { mode: 'add', dirty: false, requestKey: null, pendingBody: null, draft: { source: null, tip: 'NIR', vat: null, autoNumber: true, header: { Data: today, DataFc: today, NumarSub: 0 }, lines: config.source && family !== 'storno' || config.holder ? [] : [newLine()] } }); say(''); paint(); if (family === 'storno') await refreshStornoSources(); if (receipt) await checkNumber(true); }
  async function edit() { if (!state.doc || !state.canWrite || state.doc.annulled || state.pendingBody || pendingCancel) return; state.mode = 'edit'; state.draft = draftFromDoc(state.doc); state.dirty = false; say(''); if (family === 'storno') await refreshStornoSources(); if (config.source || config.holder) await eligible(); paint(); }
  async function discard() { if (!await allowDiscard()) return; state.mode = 'view'; state.draft = null; state.dirty = false; state.requestKey = null; state.pendingBody = null; say(''); paint(); }
  let sourceSequence = 0;
  function refreshStornoSources() {
    const draft = state.draft, sequence = ++sourceSequence, day = draft?.header.Data;
    const operation = (async () => {
      if (!day) return;
      const result = await opts.api(url('/sources', `&date=${encodeURIComponent(day)}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`));
      if (abort.signal.aborted || draft !== state.draft || sequence !== sourceSequence || draft.header.Data !== day) return;
      state.sources = result.documents;
      if (draft.source && !state.sources.some(doc => doc.id === draft.source)) {
        draft.source = null; draft.header.IdPartener = null; state.eligible = []; draft.lines = [newLine()];
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
      line.Valoare = fixed(net, zec); line.TVA = fixed(total != null && net != null ? total - net : null, zec); line.ValoareCUTVA = fixed(total, zec); if (allocation.isMF(line)) line.ValoareLuna = fixed(number(line.DNF) > 0 && total != null ? total / number(line.DNF) : null, zec);
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
        return newLine({ ...old, ...row, name: item?.name || old?.name || '', um: item?.um || old?.um || '' });
      }).concat(incomplete);
      if (result.undo_token) (draft.undo_tokens ||= []).push(result.undo_token);
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
    const difference = receiptDifference(result?.ok ? result.value : null, rows, decimals);
    input?.classList.toggle('has-total-difference', rows.some(row => !isBlankLine(row)) && difference.different);
    const canCorrect = difference.canCorrect && state.draft.lines.some(row => row.product && number(row.Cant) > 0)
      && !state.draft.lines.some(row => row.calc_mode === 'GROSS') && state.canWrite && !state.busy && !state.pendingBody;
    if (invoiceCorrectionButton) invoiceCorrectionButton.disabled = !canCorrect;
    return canCorrect;
  }
  function attachInvoiceCorrection(item) {
    item.node.classList.add('vc-invoice-value');
    if (!editing()) return;
    const row = el('div', { className: 'vc-invoice-value__row' });
    const box = item.input._box.element;
    box.replaceWith(row); row.append(box);
    const button = el('button', { type: 'button', className: 'vc-invoice-value__correct', disabled: true,
      title: 'Corectează totalul produselor la ValoareFC. Disponibil pentru o diferență nenulă de cel mult 1 leu.',
      'aria-label': 'Corectează la valoarea facturii' });
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<path d="M12 3v18M7 21h10M4 7h16M5 7l-3 7h6L5 7Zm14 0-3 7h6l-3-7Z"/>';
    button.append(icon);
    const onClick = event => { event.preventDefault(); correctTotal().catch(report); };
    button.addEventListener('click', onClick);
    headerUi.own(() => button.removeEventListener('click', onClick));
    row.append(button); invoiceCorrectionButton = button;
  }
  async function correctTotal() {
    if (!await headerUi.commit() || !await linesUi.commit()) return;
    if (!syncInvoiceValue()) return;
    const selected = linesGrid?.getSelectedRows()[0];
    if (!await confirmBox('Ajustați totalul la valoarea NIR, păstrând cota reală de TVA? Diferența poate fi cel mult 1 leu.', { yes: 'Corectează', no: 'Renunță' })) return;
    const result = await validateDraft('adjust', selected && !selected.external && !isBlankLine(selected) ? selected.key : null);
    if (result) { state.dirty = true; paintLines(); say(`Total corectat: ${format(receiptTotal(displayRows(result.lines), 'ValoareCUTVA', 4), { type: 'decimal' })} lei.`); }
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
    title.textContent = `${config.title}${editing() ? state.mode === 'add' ? ' • document nou' : ' • modificare' : state.doc ? ` nr. ${numberText(state.doc)}` : ''}`;
    pill.textContent = editing() ? 'Ciornă' : state.doc ? state.doc.annulled ? 'Anulat' : 'Salvat' : ''; pill.hidden = !pill.textContent; pill.dataset.state = editing() ? 'draft' : state.doc?.annulled ? 'annulled' : 'saved';
    if (family === 'nir' && (editing() || !state.doc?.annulled)) pill.hidden = true;
    paintHeader(); paintLines(); paintBar(); paintSaved(); freezePending();
  }
  // Starting stock hides the supplier/invoice fields in place; its checkbox stays in the title bar.
  function stockHidden() {
    if (family !== 'nir') return;
    const on = (editing() ? state.draft.tip : state.doc?.doc_type) === 'STOC_INITIAL';
    headerHost.querySelector('[data-field="IdPartener"]')?.classList.toggle('is-stock', on);
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
          draft.source = value; draft.header.IdPartener = source?.party_id ?? null;
          draft.header.party = source?.party || ''; draft.lines = [newLine()]; state.dirty = true;
          try { await eligible(); if (draft !== state.draft || draft.source !== value) return; paintLines(); side?.refresh(); } catch (error) { report(error); throw error; }
        }, onHeaderKey);
      headerHost.append(item.node); inputs.set('source', item.input); errors.set('source', item.error);
    } else if (config.source) {
      const item = field({ key: 'source', label: config.source, type: 'ref', required: true, options: state.sources.map((doc) => ({ value: doc.id, label: sourceLabel(doc) })) }, editing() ? state.draft.source : header.source, editing() && state.mode === 'add', async (value) => { state.draft.source = Number(value) || null; state.dirty = true; try { await eligible(); paintLines(); } catch (error) { report(error); } }, headerUi); headerHost.append(item.node); errors.set('source', item.error);
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
      // a single account in the gestiune (e.g. food) is chosen for the operator and cannot be changed
      const only = ['IdMasina', 'IdMasinaP'].includes(spec.key) && spec.options.length === 1 ? spec.options[0] : null;
      if (only && editing()) header[spec.key] = only.value;
      const sharedIdentity = family === 'nir' && state.draft?.invoice && !state.draft.invoice.local && ['IdPartener', 'NrFact', 'DataFc'].includes(spec.key);
      const automatic = family === 'nir' && editing() && allocation.multi() && spec.key === 'Numar';
      const fieldSpec = { ...spec,
        ...(family === 'nir' && spec.key === 'NrFact' ? { textAlign: 'center' } : {}),
        ...(receipt && spec.key === 'Numar' ? { textAlign: 'right', ...(automatic ? { type: 'text' } : { onLeave: leaveNumber }) } : {}),
        ...(family === 'nir' && spec.key === 'Valoare' ? { label: 'ValoareFC (lei)', textAlign: 'right', displayFormat: 'standard', numberLocale: 'en-US' } : {}) };
      const invoiceAmount = family === 'nir' && spec.key === 'Valoare';
      const value = invoiceAmount ? invoiceValue(invoiceDoc(), header) : header[spec.key];
      const makeField = custody && ['IdMasina', 'IdMasinaP'].includes(spec.key)
        ? (spec, value, enabled, callback, owner) => oiAccount(owner, spec, value, enabled, callback, onHeaderKey)
        : family === 'bc' && spec.key === 'IdMasina'
        ? (spec, value, enabled, callback, owner) => consumptionCategory(owner, spec, value, enabled, callback, onHeaderKey) : field;
      const item = makeField(fieldSpec, automatic ? 'auto' : value, editing() && !only && !sharedIdentity && !automatic && !(invoiceAmount && sharedReceipt(invoiceDoc())), async (value) => {
        const previous = state.draft.header[spec.key]; state.draft.header[spec.key] = value; state.dirty = true;
        if (family === 'storno' && spec.key === 'Numar') state.draft.autoNumber = false;
        if (family === 'nir' && spec.key === 'Valoare') syncInvoiceValue();
        if (status.textContent.startsWith('Completați mai întâi antetul') && !headerLock()) say('');
        if (spec.key === config.holder && String(previous) !== String(value)) {
          if (!await linesUi.commit()) { state.draft.header[spec.key] = previous; paintHeader(); return; }
          if (state.draft.lines.length && !await confirmBox('Schimbarea predătorului elimină produsele selectate din ciornă.', { yes: 'Schimbă predătorul', no: 'Păstrează' })) { state.draft.header[spec.key] = previous; paintHeader(); return; }
          state.draft.lines = []; try { await eligible(); paintLines(); } catch (error) { report(error); }
        }
        if (spec.key === accountKey()) { const incompatible = state.draft.lines.filter((line) => (line.variant || line.product) && !inAccount(family === 'nir' ? product(line.product) : variant(line.variant))); if (incompatible.length) { state.draft.header[spec.key] = previous; paintHeader(); say('Contul nu se poate schimba: există produse care nu îi aparțin.', true); return; } paintLines(); }
        if (spec.key === 'Data') {
          availability();
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
          } else if (family === 'nir') { try { await checkNumber(); } catch (error) { state.draft.header.Data = previous; paintHeader(); report(error); return; } }
        }
        if (family === 'nir' && ['Data', 'DataFc'].includes(spec.key)) { try { await validateDraft(); } catch (error) { state.draft.header[spec.key] = previous; paintHeader(); report(error); return; } }
        side?.refresh();
      }, headerUi);
      inputs.set(spec.key, item.input); errors.set(`h.${spec.key}`, item.error); if (spec.hint && editing()) item.input.title = spec.hint;
      if (receipt && spec.key === 'Numar' && editing() && !automatic) attachSub(item, header);
      if (family === 'nir' && spec.key === 'Valoare') attachInvoiceCorrection(item);
      const index = ['IdPartener', 'IdMasina', 'IdComisie'].includes(spec.key) ? 0 : ['NrFact', 'DataFc'].includes(spec.key) ? 1 : spec.key === 'Valoare' ? 3 : 2; (groups ? groups[index] : headerHost).append(item.node);
      if (family === 'nir' && spec.key === 'IdPartener' && state.mode === 'add') {
        stockHost.hidden = false;
        const stock = headerUi.own(new CheckBox(stockHost, { label: 'Stoc inițial (fără furnizor)', labelSide: 'left', className: 'vc-nir-stock', checked: state.draft.tip === 'STOC_INITIAL',
          onChange: (checked) => {
            state.draft.tip = checked ? 'STOC_INITIAL' : 'NIR'; state.dirty = true;
            if (checked) { state.draft.invoice = null; state.draft.header.FacturaId = null; }
            for (const name of ['IdPartener', 'NrFact']) { const input = inputs.get(name); input?._setRequired(!checked); if (checked) { input?._clearValue(); state.draft.header[name] = null; } }
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
          state.draft.lines.forEach(line => { if (isBlankLine(line)) return; clearCorrection(line); line.IDTVA = allocation.defaultTax(line.product, percent); if (!line.IDTVA) throw new Error('Cota TVA nu este configurată în destinația produsului.'); calculate(line); });
          await validateDraft(); state.dirty = true; paintLines(); }
        catch (error) { state.draft = backup; paintHeader(); paintLines(); report(error); }
      }, headerUi); vat.node.classList.add('vc-nir-vat'); headerHost.append(vat.node); vatInput = vat.input; syncVatLock();
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
  // The NIR number box with its optional sub-number box (707,1). A "," or "." typed in the number opens the narrow sub box beside it,
  // INSIDE the same cell (the number box just gets narrower, nothing around it moves). The number is checked once it is complete: when the
  // number box is left, or, when the sub box is open, when that one is left. Backspace in an empty sub box closes it.
  let subBox = null, subGroup = null;
  function attachSub(item, header) {
    const main = item.input._box, input = item.input, row = el('div', { className: 'vc-number-row' });
    main.element.replaceWith(row); row.append(main.element);
    subGroup = el('div', { className: 'vc-number-sub' }, el('span', { textContent: ',' }));
    subBox = headerUi.own(new TextBox(subGroup, { type: 'text', maxLength: 2, ariaLabel: `Sub-număr ${family === 'storno' ? 'FCS' : 'NIR'}`, textAlign: 'right',
      onInput: (text, box) => { const digits = text.replace(/\D/g, ''); if (digits !== text) box.setValue(digits); state.draft.header.NumarSub = subHundredths(digits); state.draft.autoNumber = false; state.dirty = true; },
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
    if (text) { subBox.setValue(text); state.draft.header.NumarSub = subHundredths(text); state.draft.autoNumber = false; state.dirty = true; }
    subBox.focus();
  }
  function closeSub() { if (!subGroup) return; subGroup.hidden = true; subBox.setValue(''); state.draft.header.NumarSub = 0; state.draft.autoNumber = false; state.dirty = true; }
  function leaveNumber(text, box, event) {
    if (subGroup && !subGroup.hidden && event?.relatedTarget === subBox.input) return;   // going to the sub box: the number is not complete yet
    checkNumber().catch(report);
  }
  async function checkNumber(offer = false) {
    if (!receipt || !editing()) return;
    if (allocation?.multi()) return;
    const draft = state.draft, automatic = family === 'storno' && offer;
    const currentNumber = draft.header.Numar || '', currentSub = Number(draft.header.NumarSub) || 0;
    const raw = automatic ? '' : currentNumber, sub = automatic ? 0 : currentSub, seq = ++numberSequence;
    const day = draft.header.Data || '';
    const selection = state.mode === 'add' && allocation ? allocation.numberSelection() : opts.sel();
    const answer = await trackStorno(opts.api(`/api/vercon/doc/${family}/number?${new URLSearchParams(selection)}&number=${encodeURIComponent(raw)}&sub=${sub}&date=${encodeURIComponent(day)}${state.mode === 'edit' ? `&exclude=${state.doc.id}` : ''}`, { headers: { 'X-Vercon-Version': '' } }));
    if (abort.signal.aborted || draft !== state.draft || seq !== numberSequence || draft.header.Data !== day || String(draft.header.Numar || '') !== String(currentNumber) || (Number(draft.header.NumarSub) || 0) !== currentSub) return;
    const input = inputs.get('Numar');
    if (offer && !raw) { draft.header.Numar = String(answer.suggested); if (input?._box) input._box.setValue(draft.header.Numar); else if (input) input.value = draft.header.Numar; }
    if (automatic) {
      draft.header.NumarSub = Number(answer.suggested_sub) || 0;
      if (subGroup) subGroup.hidden = !draft.header.NumarSub;
      subBox?.setValue(subText(draft.header.NumarSub));
    }
    // the verdict is the box's error colour and tooltip, never text on the screen
    const message = !answer.valid ? 'Introduceți un număr întreg pozitiv (și un sub-număr de cel mult 2 cifre).' : answer.duplicate ? 'Numărul este deja folosit. Alegeți alt număr.' : '';
    const error = errors.get('h.Numar'); if (error) error.textContent = message;
    if (input && !message) input.title = offer && !raw ? 'Propus automat. Verificat din nou la salvare.' : !raw ? `Următorul număr disponibil: ${answer.suggested}.` : answer.valid && !answer.duplicate ? 'Număr disponibil. Verificat din nou la salvare.' : `Număr disponibil propus: ${answer.suggested}.`;
  }
  function productOptions() { return (family === 'nir' ? products() : variants()).filter(inAccount).map((item) => ({ value: item.id, label: family === 'nir' ? item.name : `${item.name} • ${format(item.putva, { type: 'decimal' })} lei` })); }
  // The NIR grid always ends with one empty row (the new row, as in an Access datasheet); an empty line is never saved.
  const isBlankLine = (line) => !line.product && !line.variant && [line.Cant, line.Pret].every((value) => value == null || String(value).trim() === '');
  // NIR amounts returned by validation are authoritative; rendering must not recalculate them with binary floats.
  const displayLine = line => family === 'nir' ? line : calculate(line);
  const draftRows = () => { const rows = displayRows(state.draft.lines).map(displayLine); rows.forEach((row, index) => { row._index = index + 1; }); return rows; };
  function dropLine(line) { state.draft.lines = state.draft.lines.filter((other) => other.key !== line.key); linesGrid.setRows(draftRows(), { keepWidths: true }); totals(); availability(); side?.refresh(); }
  // A line that was edited is judged when the operator leaves it: what is missing or wrong (the same rules as the save) and the question.
  function lineProblems(line) {
    if (originalLine(line)) return [];
    return lineSpecs().filter((spec) => spec.editable && (!spec.mfOnly || allocation.isMF(line))).filter((spec) => spec.type === 'ref' ? line[spec.key] == null || line[spec.key] === '' : !parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }).ok).map((spec) => spec.label);
  }
  const rowIssue = (row) => { if (config.source && family !== 'storno' || config.holder) return ''; const missing = lineProblems(row); return missing.length ? `Rândul ${row._index} nu este complet. Lipsesc sau sunt greșite: ${missing.join(', ')}.` : ''; };
  const confirmRowIssue = async ({ message }) => await confirmBox(`${message} Continuați editarea rândului?`, { title: 'Rând incomplet', yes: 'Continuă editarea', no: 'Anulează rândul' }) ? 'continue' : 'cancel';
  function paintLines() {
    linesUi.clear(); linesGrid = null; linesHost.replaceChildren(); if (!editing() && !state.doc) return;
    const local = editing() ? state.draft.lines : state.doc.lines.map((line) => ({ ...line, key: `saved:${line.id}` }));
    const rows = displayRows(local).map(displayLine); rows.forEach((row, index) => { row._index = index + 1; });
    const specs = lineSpecs(), columns = [{ key: '_index', title: '#', width: 46, fixedWidth: true, filter: false, sortable: false, align: 'center' }];
    for (const spec of specs) {
      const choices = row => spec.product ? config.holder ? state.eligible.map((item, index) => ({ value: index, label: `${item.name} • ${item.um} • disponibil ${format(item.left ?? item.qty, { type: 'decimal' })}` })) : productOptions() : (family === 'nir' ? allocation.taxOptions(row) : state.lookups.vat || []).map((item) => ({ value: item.id, label: `${item.percent}%` }));
      columns.push(column({ ...spec, type: spec.type || 'text' }, { width: spec.product || spec.key === 'name' ? 234 : inline && spec.key === 'Cant' ? 90 : spec.key === 'um' ? 64 : inline && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? 100 : 128, showArrow: inline, editable: editing() && !!spec.editable, filter: !editing(), sortable: !editing(),
        ...(spec.key === 'um' ? { align: 'center' } : receipt && ['Pret', 'IDTVA', 'Valoare', 'TVA', 'ValoareCUTVA'].includes(spec.key) ? { align: 'right' } : {}),
        ...(family === 'nir' && spec.editable ? { editable: row => editing() && !row.external && row.calc_mode !== 'GROSS' && (!spec.mfOnly || allocation.isMF(row)) } : {}),
        ...(spec.type === 'ref' ? { editor: 'list', searchable: true, options: choices, formatter: (value) => family === 'nir' && spec.key === 'IDTVA' ? allocation.tax(value)?.percent || '' : spec.product && (!editing() || spec.key === 'name') ? rows.find((row) => row[spec.key] === value)?.name || String(value ?? '') : choices().find((item) => String(item.value) === String(value))?.label || '', validate: (value) => value == null || value === '' ? 'Alegeți o valoare din listă.' : null } : {}),
        ...(family === 'nir' && spec.key === 'IDTVA' ? { formatter: value => allocation.tax(value)?.percent ?? rows.find(row => String(row.IDTVA) === String(value))?.vat_percent ?? '' } : {}),
        ...(family === 'nir' && spec.product ? { editor: 'tree', nodes: allocation.nodes, selectParents: false, dropDownMinWidth: 300, focusAfterSelect: state.mode === 'add' ? 'Cant' : null, formatter: value => rows.find(row => String(row.product) === String(value))?.name || product(value)?.name || '' } : {}),
        ...(family === 'bc' && spec.product ? { editor: 'list', searchable: true, focusAfterSelect: 'Cant', dropDownMinWidth: 300 } : {}),
        ...(custody && spec.product ? { editor: 'tree', nodes: () => oiProductNodes(config.holder ? state.eligible : variants().filter(inAccount), state.lookups.categories || [], !!config.holder),
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
      ...(inline ? { suspendRowLeave: () => state.busy } : {}),
      rowClassName: row => family === 'nir' ? receiptLineClass(row, opts.sel(), allocation.product) : '',
      rowIssue, confirmRowIssue, newRowAlwaysVisible: inline && !custody && editing(), isBlankRow: isBlankLine, onCancelNewRow: dropLine,
      onNewRow: () => { const line = newLine({ IDTVA: state.draft.vat }); state.draft.lines.push(line); line._index = displayRows(state.draft.lines).length; return line; }, proportionalWidths: true, editable: editing(), allowIncompleteEdits: inline, singleClickEdit: true, editGuard: headerLock, onEditBlocked: lockedLines, footer: true, footerCaption: '', layoutId: `vercon.${opts.sel().type}.${family}.lines.${family === 'bc' ? 'consumption-v2' : receipt ? 'receipt-v6' : 'compact-v1'}`,
      ...(!inline || custody ? { footerAction: { label: '➕ Adaugă rând', span: 2, disabled: !editing() || !!config.source }, onFooterAction: () => addLine().catch(report) } : {}),
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
        else { line[name] = ['variant', 'product', 'IDTVA', 'invoice_line'].includes(name) ? value == null || value === '' ? null : Number(value) : value; if (['variant', 'product'].includes(name)) { const item = family === 'nir' ? product(value) : variant(value); line.name = item?.name || ''; line.um = item?.um || ''; if (name === 'product') line.variant = null; } }
        if (family === 'nir' && name === 'product') {
          const item = product(line.product), percent = allocation.tax(before.IDTVA)?.percent ?? allocation.tax(state.draft.vat)?.percent;
          line.destination = item?.destination;
          line.IDTVA = percent == null ? null : allocation.defaultTax(line.product, percent);
          if (item?.type === 'MF') Object.assign(line, { Cant: line.Cant || '1', DNF: line.DNF || item.dnf || '', data_intrarii: line.data_intrarii || state.draft.header.DataFc });
        }
        try {
          if (family === 'bp' && name === 'variant' && state.draft.lines.some(other => other !== line && other.variant === line.variant)) throw new Error('Produsul este deja pe bon. Modificați cantitatea rândului existent.');
          if (custody && !(number(line.Cant) > 0) && name === 'Cant') throw new Error('Cantitatea trebuie să fie pozitivă.');
          if (config.holder && name === 'Cant' && number(line.Cant) > number(line.left ?? 0)) throw new Error('Cantitatea depășește disponibilul lotului la data bonului.');
          if (family === 'storno' && name === 'Cant' && number(line.Cant) > number(state.eligible.find(item => item.variant === line.variant)?.left ?? 0)) throw new Error('Cantitatea depășește stocul disponibil pentru stornare la data FCS.');
          calculate(line); if (family === 'nir') await validateDraft();
        }
        catch (error) { Object.keys(line).forEach(key => delete line[key]); Object.assign(line, before); throw error; }
        state.dirty = true; return { ...line };
      }, onCellSaved: ({ row }) => { totals(); availability(); side?.refresh(row); }, onEditError: ({ error }) => say(error.message, true),
      onSelect: (row) => { if (!row.external) side?.refresh(row); },
      onEditState: ({ state: mode }) => { if (mode === 'editing') side?.refresh(linesGrid?.getSelectedRows()[0]); },
      onCellAction: ({ row }) => { if (row.external) return; return rowMenu(linesGrid, row, [{ icon: '×', label: 'Scoate rândul', disabled: !editing() || !!config.source && family !== 'storno', run: async () => {
        if (!await linesUi.commit()) return;
        if (row.calc_mode === 'GROSS') {
          if (!await confirmBox('Ștergeți corecția și returnați cantitatea în grupul de origine?', { yes: 'Revino în grup', no: 'Renunță' })) return;
          try { await validateDraft('undo_adjustment', row.key); state.dirty = true; paintLines(); } catch (error) { report(error); } return;
        }
        if (row.id && !await confirmBox(`Scoateți rândul din ciorna ${family === 'bc' ? 'bonului' : family === 'storno' ? 'FCS-ului' : 'NIR-ului'}?`, { yes: 'Scoate', no: 'Renunță' })) return;
        const before = state.draft.lines; state.draft.lines = before.filter(item => item.key !== row.key);
        try { if (family === 'nir') await validateDraft(); state.dirty = true; paintLines(); }
        catch (error) { state.draft.lines = before; report(error); }
      } }]); },
    }, linesUi); totals(); availability(); side?.refresh();
  }
  // The lines grid stays locked until every required header field is filled; the reason is the message shown on a click in it.
  function headerLock() {
    if (!editing()) return '';
    const blank = (value) => value == null || String(value).trim() === '';
    const missing = headerSpecs().filter((spec) => spec.required && (spec.key === 'Accounts' ? !allocation.selectedAccounts().length : blank(inputs.get(spec.key)?.value ?? state.draft.header[spec.key]))).map((spec) => spec.label);
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
    const rows = state.draft.lines.filter(line => !isBlankLine(line));
    const taxes = rows.map(line => state.lookups.vat.find(item => String(item.id) === String(line.IDTVA)));
    const common = rows.length > 0 && taxes.every(item => item && String(item.percent) === String(taxes[0]?.percent));
    vatInput.closest('.vc-field').hidden = state.mode === 'edit' ? !common : new Set(taxes.filter(Boolean).map(item => Number(item.percent))).size > 1;
    if (common) state.draft.vat = [...state.lookups.vat].reverse().find(item => Number(item.percent) === Number(taxes[0].percent))?.id;
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
    const rows = displayRows(editing() ? state.draft.lines : state.doc.lines);
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
    clearTimeout(availabilityTimer); const sequence = ++availabilitySequence;
    if (!bon() || !editing() && (family !== 'bc' || !state.doc)) return;
    const document = editing() ? state.draft : state.doc, inEdit = editing(), savedId = state.doc?.id;
    state.available = {};
    availabilityTimer = setTimeout(async () => {
      const date = parse(inputs.get('Data')?.value ?? document.header.Data, { type: 'date', required: true });
      const ids = [...new Set(document.lines.map(line => line.variant).filter(Boolean))];
      if (!date.ok || !ids.length) return;
      try {
        // Consult and edit show the same stock available to this BC: its own
        // saved consumption is excluded in both modes. A new BC has no saved id.
        const excludedId = family === 'bc' ? state.mode !== 'add' ? savedId : null
          : state.mode === 'edit' ? state.doc.header.IdBonConsum : null;
        const exclude = excludedId ? `&exclude_kind=bc&exclude_id=${excludedId}` : '';
        const result = await opts.api(`/api/vercon/stock/available?${query()}&date=${date.value}&products=${ids.join(',')}${exclude}`);
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
  const treeNodes = () => {
    const nodes = lazy ? receipts.nodes() : custody ? oiDocumentNodes(state.docs, family) : family === 'bc' ? consumptionNodes(state.docs) : groupedNodes(state.docs, config.groups.map((name) => (doc) => name === 'date' ? dateText(doc.date) : doc[name]), docLabel);
    nodes.sort((a, b) => treeAlphabet.compare(a.label, b.label));
    return nodes;
  };
  function paintSaved() {
    savedHost.hidden = custody && editing();
    side?.setVisible(editing() && !RECEIPT_MODAL_ENABLED);
    if (side && editing() && !RECEIPT_MODAL_ENABLED) return;
    if (savedTree?.treeContainer?.isConnected) {   // the tree is kept alive: opening a document only moves the mark; new data is drawn in place
      if (savedHost.hidden) return;
      if (treeDirty) refreshTree(savedTree, treeNodes(), state.doc?.id); else markSelected(savedTree, state.doc?.id);
      paintTreeAdd();
      treeDirty = false; return;
    }
    treeUi.clear(); savedTree = null; if (savedHost.hidden) return; treeDirty = false;
    savedTree = paintTree(treeHost, treeNodes(), { title: family === 'bc' ? 'Bonuri salvate' : family === 'storno' ? 'FCS salvate' : 'Documente salvate', showSearchBox: false, collapsed: true, selected: state.doc?.id, onToggle: lazy ? opened : undefined, exclusiveRoots: lazy || family === 'bc', onSelect: (id) => open(Number(id)).catch(report), onAction: (id, anchor) => {
      const doc = lazy ? receipts.find(id) : state.docs.find((item) => String(item.id) === id); if (!doc) return; const run = async (action) => { if (state.doc?.id !== doc.id) await open(doc.id); if (state.doc?.id === doc.id) await action(); };
      openMenu(anchor, [{ icon: '✎', label: 'Modifică', disabled: !state.canWrite || doc.annulled, run: () => run(edit).catch(report) }, { icon: '◉', label: 'Previzualizează', run: () => run(preview).catch(report) }, { icon: '◷', label: 'Istoric', run: () => run(history).catch(report) }, { icon: '×', label: 'Anulează', rule: true, disabled: !state.canWrite || doc.annulled, run: () => run(cancel).catch(report) }], { align: 'right' });
    } }, treeUi);
    paintTreeAdd();
  }
  function paintTreeAdd() {
    if (!inline) return;
    const heading = treeHost.querySelector(':scope > h4');
    if (!heading) return;
    if (!treeAddButton || treeAddButton.element.parentElement !== heading) {
      treeAddButton?.destroy();
      treeAddButton = new ActionAddButton(heading, { className: 'vc-receipt-tree__add',
        title: `Adaugă ${custody ? config.title.toLocaleLowerCase('ro') : family === 'bc' ? 'bon de consum' : family === 'storno' ? 'FCS' : 'NIR'}`, onClick: () => add().catch(report) });
      treeUi.own(treeAddButton);
    }
    treeAddButton.setEnabled(state.canWrite);
  }
  function paintBar() {
    bar.replaceChildren(); const button = (text, action, attrs = {}) => { const node = el('button', { type: 'button', textContent: text, ...attrs }); node.addEventListener('click', () => Promise.resolve(action()).catch(report), { signal: abort.signal }); return node; };
    bar.classList.toggle('vc-doc__bar--nir-editing', inline && !custody && editing());
    if (pendingCancel) { bar.append(button('Reia aceeași anulare', cancel), status); return; }
    if (inline && !custody && editing()) {
      const messages = el('div', { className: 'vc-doc__messages' }, status);
      bar.append(button('Renunță', discard, { className: 'vc-doc__cancel' }), messages,
        button(`Salvează ${family === 'bc' ? 'bonul' : family === 'storno' ? 'FCS' : 'NIR'}`, save, { className: 'is-primary vc-doc__save', 'data-retry-save': 'true' }));
      return;
    }
    if (editing()) bar.append(button('Renunță', discard), button('Previzualizează', preview), button(`Salvează ${family === 'nir' ? 'NIR' : 'bonul'}`, save, { className: 'is-primary', 'data-retry-save': 'true' })); else { if (!inline) bar.append(button('+ Adaugă', add, { disabled: !state.canWrite })); if (state.doc) bar.append(button('Modifică', edit, { disabled: !state.canWrite || state.doc.annulled }), button('Previzualizează', preview)); }
    bar.append(status); if (state.closed) bar.append(el('span', { className: 'vc-hint', textContent: 'Perioadă închisă: numai consultare.' }));
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
      const raw = localized ? localized.value : input?.value ?? state.draft.header[spec.key]; if (spec.type === 'ref') { out.header[spec.key] = raw === '' || raw == null ? null : Number(raw); if (spec.required && out.header[spec.key] == null && !(family === 'nir' && state.draft.tip === 'STOC_INITIAL' && spec.key === 'IdPartener')) invalid[`h.${spec.key}`] = 'Alegeți o valoare.'; } else { const result = parse(raw, spec); if (!result.ok) invalid[`h.${spec.key}`] = result.message; else out.header[spec.key] = result.value; }
    }
    if (family === 'nir') { out.header.TipDoc = state.draft.tip; out.header.NumarSub = allocation.multi() ? 0 : Number(state.draft.header.NumarSub) || 0; out.header.FacturaId = state.draft.header.FacturaId ?? null; out.undo_tokens = state.draft.undo_tokens || []; out.accounts = [...state.draft.accounts]; }
    if (family === 'storno') out.header.NumarSub = Number(state.draft.header.NumarSub) || 0;
    for (const line of state.draft.lines) {
      if (inline && isBlankLine(line)) continue;   // the always-visible new row
      if (family === 'nir' && originalLine(line)) { out.lines.push({ ...line }); continue; }
      if (family === 'storno') {
        const entry = state.eligible.find(item => item.variant === line.variant);
        if (!entry) invalid[`l|${line.key}|variant`] = 'Alegeți un produs disponibil din recepția selectată.';
        if (!(number(line.Cant) > 0) || number(line.Cant) > number(entry?.left ?? 0)) invalid[`l|${line.key}|Cant`] = 'Introduceți o cantitate pozitivă în limita stocului de stornat la data FCS.';
      }
      if (config.source || config.holder) { if (!config.holder && (line.Cant == null || line.Cant === '')) continue; const result = parse(String(line.Cant ?? ''), { type: 'decimal', required: true, min: 0 }); if (!result.ok || !line.variant || !(number(line.Cant) > 0) || config.holder && line._choice < 0) invalid[`l|${line.key}|Cant`] = result.message || 'Alegeți un lot disponibil și o cantitate pozitivă.'; else out.lines.push({ key: line.key, id: line.id, variant: line.variant, batch: line.origin ?? line.batch ?? null, IdMasina: line.IdMasina, PUTVA: line.PUTVA, Cant: result.value }); continue; }
      const result = { key: line.key, id: line.id, invoice_line: line.invoice_line }; for (const spec of lineSpecs().filter((item) => item.editable && (!item.mfOnly || allocation.isMF(line)))) { if (spec.type === 'ref') { result[spec.key] = line[spec.key] ?? null; if (result[spec.key] == null) invalid[`l|${line.key}|${spec.key}`] = 'Alegeți o valoare.'; } else { const parsed = parse(String(line[spec.key] ?? ''), { ...spec, type: spec.type || 'text' }); if (!parsed.ok) invalid[`l|${line.key}|${spec.key}`] = parsed.message; else result[spec.key] = parsed.value; } }
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
    headerHost.inert = linesHost.inert = !!state.pendingBody;
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
      root.inert = true; say('Se salvează…');
      state.requestKey ||= key(); state.pendingBody ||= structuredClone({ ...data.body, ...(state.mode === 'edit' ? { version: state.doc.version } : {}) });
      submitted = true;
      const result = await json(state.mode === 'add' ? 'POST' : 'PUT', state.mode === 'add' ? family === 'nir' ? '/distribution' : '' : `/${state.doc.id}`, { ...state.pendingBody, request_key: state.requestKey });
      if (abort.signal.aborted) return; Object.assign(state, { doc: result.document, mode: 'view', draft: null, dirty: false, requestKey: null, pendingBody: null }); paint(); say(result.receipts ? 'Salvat: ' + result.receipts.map(item => `${item.name} · NIR ${item.number}`).join('; ') : result.replayed ? 'Salvat (cererea fusese deja aplicată).' : 'Salvat.'); try { await refresh(); if (!abort.signal.aborted) paintSaved(); } catch (error) { report(error); } }
    catch (error) {
      if (error.fields) showErrors(error.fields);
      if (submitted && error.status && error.status < 500) { state.requestKey = null; state.pendingBody = null; }
      report(error);
      if (submitted && (!error.status || error.status >= 500)) say('Răspunsul salvării nu este confirmat. Reîncercați salvarea aceleiași ciorne; cererea păstrează aceeași cheie.', true);
    }
    finally { state.busy = false; root.inert = false; freezePending(); syncInvoiceValue(); }
  }
  let pendingCancel = null;
  async function cancel() {
    const doc = state.doc; if (!doc || !state.canWrite || doc.annulled || state.busy) return;
    if (!pendingCancel) {
      const reason = await reasonBox(`Anulați ${config.title.toLocaleLowerCase('ro')} nr. ${doc.number}?`, { title: 'Anulare document', yes: 'Anulează', no: 'Renunță' });
      if (reason == null) return; pendingCancel = { reason, version: doc.version, request_key: key(), id: doc.id };
    }
    state.busy = true;
    try { const result = await json('POST', `/${pendingCancel.id}/cancel`, pendingCancel); if (abort.signal.aborted) return; pendingCancel = null; state.doc = result.document; await refresh(); paint(); say('Anulat.'); }
    catch (error) { if (error.status && error.status < 500) pendingCancel = null; report(error); }
    finally { state.busy = false; paintBar(); }
  }
  async function history() { const doc = state.doc; if (!doc) return; const result = await opts.api(url(`/${doc.id}/history`)); if (!abort.signal.aborted) openPreview(`Istoric • ${config.title} nr. ${doc.number}`, [{ title: 'Operații', columns: [{ key: 'at', title: 'Data', width: 190 }, { key: 'user', title: 'Utilizator', width: 180 }, { key: 'operation', title: 'Operație', width: 160 }, { key: 'after', title: 'Valori', width: 420, formatter: (value) => value && typeof value === 'object' ? JSON.stringify(value) : value ?? '' }], rows: result.history }], dialogs); }
  async function preview() {
    if (editing() && (!await headerUi.commit() || !await linesUi.commit())) return; const data = editing() ? collect() : null; if (data && Object.keys(data.errors).length) { say(showErrors(data.errors) ? 'Corectați câmpurile marcate.' : '', true); return; } const header = editing() ? data.body.header : state.doc ? { ...state.doc.header, Numar: numberText(state.doc) } : null; if (!header) return;
    const token = openSequence, doc = editing() ? null : state.doc, draft = state.draft;
    const headerValue = spec => {
      if (family === 'nir' && spec.key === 'Accounts') {
        const ids = editing() ? state.draft.accounts : [...new Set(state.doc.lines.map(line => line.category).filter(Boolean))];
        return ids.map(id => allocation.account(id)?.label || String(id)).join(', ');
      }
      if (family === 'nir' && editing() && spec.key === 'Numar' && allocation.multi()) return 'auto';
      return spec.type === 'ref' ? spec.options.find(item => String(item.value) === String(header[spec.key]))?.label || '' : format(header[spec.key], spec);
    };
    const sections = [
      { title: 'Antet', columns: [{ key: 'label', title: 'Câmp', width: 240 }, { key: 'value', title: 'Valoare', width: 580 }], rows: headerSpecs().map(spec => ({ label: spec.label, value: headerValue(spec) })) },
      { title: config.lines, columns: lineSpecs().map((spec) => ({ key: spec.product ? 'name' : spec.key, title: spec.label, width: spec.product ? 250 : 120, formatter: (value) => spec.key === 'IDTVA' ? `${state.lookups.vat.find(item => String(item.id) === String(value))?.percent ?? ''}%` : format(value, { type: spec.type === 'ref' ? 'text' : spec.type || 'text' }) })), rows: editing() ? state.draft.lines.filter((line) => family !== 'nir' || !isBlankLine(line)) : state.doc.lines },
    ];
    await documentReport({ api: opts.api, selection: opts.sel(), family, document: doc,
      title: `${config.title}${editing() ? ` • Ciornă nesalvată${header.Numar ? ` nr. ${header.Numar}` : ''}` : ` nr. ${state.doc.number}`}`, date: header.Data, sections, owner: dialogs,
      isCurrent: () => !abort.signal.aborted && token === openSequence && draft === state.draft && (!doc || doc === state.doc) });
  }
  // opts.startAdd: the page opens straight on a new document (nothing of the consultation screen is drawn meanwhile).
  function pauseContext() {
    contextAbort.abort(); loadGeneration++; numberSequence++; openSequence++; availabilitySequence++; eligibleSequence++;
    sourceSequence++; stornoPending.clear();
    clearTimeout(numberTimer); clearTimeout(availabilityTimer);
  }
  async function reloadContext() {
    const next = { ...sourceOptions.sel() }, linked = family === 'nir' ? linkedReceipt(state.doc, next) : null;
    pauseContext(); contextAbort = new AbortController(); currentContext = next;
    side?.setVisible(false);
    [headerUi, linesUi, treeUi, dialogs].forEach(owner => owner.clear());
    savedTree = null; linesGrid = null; treeHost.replaceChildren(); treeHost._vcExpanded = new Set();
    receipts = lazy ? createReceipts() : null;
    Object.assign(state, { doc: null, draft: null, mode: 'view', dirty: false, requestKey: null, pendingBody: null,
      canWrite: false, eligible: [], sources: [], available: {}, busy: false });
    pendingCancel = null; say('Se reîncarcă datele gestiunii…');
    await load();
    if (abort.signal.aborted || contextAbort.signal.aborted) return;
    paint(); say('');
    if (linked) { await open(linked.id); if (state.doc) { await refresh(); paintSaved(); } }
  }
  function canChangeContext() {
    if (!state.busy && !state.pendingBody && !pendingCancel) return true;
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
  return { ready, reloadContext, pauseContext, canChangeContext, startAdd: async () => { await ready; if (abort.signal.aborted) return; if (state.canWrite) await add(); else say('Nu aveți dreptul să adăugați documente în această gestiune.', true); }, isDirty: () => state.dirty || headerUi.dirty() || linesUi.dirty(), search: (text) => { const input = savedHost.querySelector('.treeview-search-input'); if (input) { input.value = text; input.dispatchEvent(new Event('input')); } }, destroy: () => { frame?.destroy(); side?.destroy(); abort.abort(); pauseContext(); [headerUi, linesUi, treeUi, dialogs].forEach((owner) => owner.clear()); host.replaceChildren(); } };
}
