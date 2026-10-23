// Context cards occupy the saved-tree column; they never change the document layout.
import { el } from './ui.js';
import { layoutRect } from '../utils/layout-geometry.js';
import { parse, format } from './fields.js';
import { decimalText } from './decimal-text.js';
import { statementCache } from './statement-cache.js';
import { renderConsumptionStock } from './consumption-ui.js';

const kinds = { nir: 'NIR', storno: 'Storno', bc: 'BC', br: 'Retur', bp: 'Predare' };

export function receiptContext(host, { root, main, headerHost, linesHost, treeHost, api, selection, lineSelection, context, validLine, blankLine, family = 'nir' }) {
  const consumption = family === 'bc', partyTitle = consumption ? 'Fișă de cont gestionar' : 'Fișă furnizor';
  const productTitle = consumption ? 'Stoc produs' : 'Fișă de magazie';
  const supplier = el('section', { className: 'vc-receipt-card', 'aria-label': partyTitle });
  const product = el('section', { className: 'vc-receipt-card', 'aria-label': productTitle });
  const panels = el('div', { className: 'vc-receipt-context', hidden: true }, supplier, product);
  host.append(panels);
  let visible = false, destroyed = false, draft = null, selected = null, remembered = null, layoutFrame = 0;
  const requests = { supplier: { sequence: 0, signature: '', timer: 0 }, product: { sequence: 0, signature: '', timer: 0 } };
  const warehouseCache = statementCache();
  const money = (value) => value == null ? '—' : decimalText(value, 2);
  const day = (value) => format(value, { type: 'date' });

  function message(panel, title, text, bad = false) {
    panel.replaceChildren(el('h4', { textContent: title }), el('p', { className: 'vc-receipt-card__message', textContent: text, role: bad ? 'alert' : 'status' }));
  }
  function stop(name) {
    const request = requests[name];
    request.sequence++; request.signature = ''; clearTimeout(request.timer);
  }
  function load(name, panel, title, params, render) {
    const normalized = new URLSearchParams(params); normalized.sort();
    const request = requests[name], signature = normalized.toString();
    if (signature === request.signature) return;
    stop(name); request.signature = signature;
    const sequence = request.sequence;
    const cached = name === 'product' ? warehouseCache.get(signature) : null;
    if (cached) { render(cached); scheduleLayout(); return; }
    message(panel, title, 'Se încarcă…');
    request.timer = setTimeout(async () => {
      try {
        const endpoint = consumption && name === 'supplier' ? 'receiver' : name;
        const download = () => api(`/api/vercon/doc/${family}/${endpoint}-statement?${normalized}`, { headers: { 'X-Vercon-Version': '' } });
        const data = name === 'product' ? await warehouseCache.load(signature, download) : await download();
        if (destroyed || !visible || sequence !== request.sequence) return;
        render(data); scheduleLayout();
      } catch (error) {
        if (destroyed || !visible || sequence !== request.sequence || error.name === 'AbortError') return;
        request.signature = ''; message(panel, title, error.message || 'Fișa nu a putut fi încărcată.', true);
      }
    }, 160);
  }
  function table(panel, columns, rows, layout = 'account') {
    const keys = layout === 'stock' ? ['date', 'document', 'quantity', 'balance'] : ['date', 'document', 'value'];
    const cols = el('colgroup', {}, ...keys.map(key => el('col', { className: `vc-receipt-card__column--${key}` })));
    const head = el('tr', {}, ...columns.map(([label, , numeric]) => el('th', { textContent: label, className: numeric ? 'is-number' : '', scope: 'col' })));
    const body = el('tbody', {}, ...rows.map(row => el('tr', {}, ...columns.map(([, value, numeric]) => {
      const text = value(row);
      return el('td', { textContent: text, title: String(text ?? ''), className: numeric ? 'is-number' : '' });
    }))));
    panel.append(el('div', { className: 'vc-receipt-card__body' },
      el('table', { className: `vc-receipt-card__table vc-receipt-card__table--${layout}` }, cols, el('thead', {}, head), body)));
  }
  function renderSupplier(data) {
    supplier.replaceChildren(el('h4', { textContent: 'Fișă furnizor' }));
    if (data.rows.length) table(supplier, [
      ['Data', (row) => day(row.date)], ['Factură', (row) => `${row.kind === 'Storno' ? 'Storno ' : ''}${row.invoice}`],
      ['Valoare', (row) => money(row.value), true],
    ], data.rows);
    else supplier.append(el('p', { className: 'vc-receipt-card__message', textContent: 'Fără facturi salvate până la data aleasă.' }));
    supplier.append(el('footer', { className: 'vc-receipt-card__total', textContent: `Total facturi: ${money(data.total)} lei` }));
  }
  function renderReceiver(data) {
    supplier.replaceChildren(el('h4', { textContent: partyTitle }),
      el('p', { className: 'vc-receipt-card__identity', textContent: data.name }));
    if (data.rows.length) table(supplier, [
      ['Data', row => day(row.date)], ['Document', row => `${kinds[row.kind] || row.kind} ${row.number ?? ''}`],
      ['Valoare', row => money(row.value), true],
    ], data.rows);
    else supplier.append(el('p', { className: 'vc-receipt-card__message', textContent: 'Fără bonuri salvate până la data aleasă.' }));
    supplier.append(el('footer', { className: 'vc-receipt-card__total', textContent: `Consum net: ${money(data.total)} lei` }));
  }
  function renderProduct(data) {
    if (consumption) { renderConsumptionStock(product, data); return; }
    product.replaceChildren(el('h4', { textContent: 'Fișă de magazie' }), el('p', { className: 'vc-receipt-card__identity', textContent: data.name }),
      el('p', { className: 'vc-receipt-card__note', textContent: `Preț cu TVA: ${money(data.price)} lei • ${data.um}` }));
    if (data.rows.length) table(product, [
      ['Data', (row) => day(row.date)], ['Document', (row) => `${kinds[row.kind] || row.kind} ${row.number ?? ''}`],
      ['Cant.', (row) => money(row.quantity), true], ['Sold', (row) => money(row.balance), true],
    ], data.rows, 'stock');
    else product.append(el('p', { className: 'vc-receipt-card__message', textContent: 'PRODUS NOU' }));
    product.append(el('footer', { className: 'vc-receipt-card__total', textContent: `Stoc: ${money(data.closing)} ${data.um}` }));
  }
  function refresh(row) {
    if (!visible || destroyed) return;
    const current = context();
    if (!current.draft) return;
    if (draft !== current.draft) {
      draft = current.draft; selected = remembered = null;
      stop('supplier'); stop('product'); warehouseCache.clear();
    }
    if (row) selected = blankLine(row) ? remembered?.key ?? null : row.key;
    let line = draft.lines.find((item) => item.key === selected);
    if (!line && selected == null) line = draft.lines.find((item) => !blankLine(item) && validLine(item));
    if (remembered && !draft.lines.some((item) => item.key === remembered.key)) remembered = null;
    if (line && validLine(line)) {
      selected = line.key;
      // A snapshot survives temporary invalid edits, including clearing the current price.
      remembered = { ...line };
    } else line = remembered;
    const date = parse((family === 'storno' || consumption ? draft.header.Data : draft.header.DataFc || draft.header.Data) || '', { type: 'date', required: true });
    const base = new URLSearchParams(Object.entries(selection()).filter(([, value]) => value != null && value !== ''));
    if (date.ok) base.set('date', date.value);
    if (consumption) {
      if (!draft.header.IDC || !date.ok) {
        stop('supplier'); message(supplier, partyTitle, !date.ok ? 'Completați data bonului.' : 'Alegeți primitorul / gestionarul.');
      } else {
        const params = new URLSearchParams(base); params.set('receiver', draft.header.IDC);
        load('supplier', supplier, partyTitle, params, renderReceiver);
      }
    } else if (draft.tip === 'STOC_INITIAL' || !draft.header.CodFiscal || !date.ok) {
      stop('supplier'); message(supplier, 'Fișă furnizor', draft.tip === 'STOC_INITIAL' ? 'Stoc inițial • fără furnizor.' : !date.ok ? `Completați data ${family === 'storno' ? 'FCS' : 'facturii'}.` : family === 'storno' ? 'Alegeți recepția stornată.' : 'Alegeți furnizorul.');
    } else {
      const params = new URLSearchParams(base); params.set('supplier', draft.header.CodFiscal);
      load('supplier', supplier, 'Fișă furnizor', params, renderSupplier);
    }
    if (!line || !validLine(line) || !date.ok) {
      stop('product'); message(product, productTitle, !date.ok ? `Completați data ${consumption ? 'bonului' : family === 'storno' ? 'FCS' : 'facturii'}.` : consumption ? 'Selectați produsul de pe rândul curent.' : family === 'storno' ? 'Selectați un produs din recepția stornată.' : 'Selectați un rând validat, cu produs, cantitate, preț și TVA.');
    } else {
      const params = new URLSearchParams(lineSelection ? Object.entries(lineSelection(line)).filter(([, value]) => value != null && value !== '') : base);
      if (date.ok) params.set('date', date.value);
      if (family === 'nir') { params.set('product', line.product); params.set('price', line.Pret); params.set('vat', line.IDTVA); }
      if (line.variant) params.set('variant', line.variant);
      if (consumption && current.excludeBC) params.set('exclude', current.excludeBC);
      load('product', product, productTitle, params, renderProduct);
    }
    scheduleLayout();
  }
  function layout() {
    layoutFrame = 0;
    if (!visible || destroyed || !root.isConnected) return;
    const grid = linesHost.querySelector('.vc-doc__grid'), heading = linesHost.querySelector('h4');
    if (!grid || !heading) return;
    const box = layoutRect(panels), lines = layoutRect(grid);
    const titleTop = layoutRect(heading).top;
    for (const [name, value] of Object.entries({
      '--receipt-supplier-footer-height': Math.max(32, lines.top - titleTop),
      '--receipt-grid-top': Math.max(0, lines.top - box.top),
      '--receipt-stock-footer-height': Math.max(32, box.bottom - lines.bottom),
    })) panels.style.setProperty(name, `${value}px`);
    panels.classList.add('is-aligned');
  }
  function scheduleLayout() {
    if (!layoutFrame && visible && !destroyed) layoutFrame = requestAnimationFrame(layout);
  }
  const observer = new ResizeObserver(scheduleLayout);
  for (const node of [root, main, headerHost, linesHost]) observer.observe(node);
  return {
    refresh,
    configureCache({ enabled = false, ttl_seconds = 0 } = {}) {
      stop('product');
      warehouseCache.configure({ lifetime: enabled ? ttl_seconds * 1000 : 0 });
    },
    setVisible(on) {
      visible = on; panels.hidden = !on; treeHost.hidden = on;
      if (on) refresh();
      else { stop('supplier'); stop('product'); warehouseCache.clear(); draft = null; selected = remembered = null; panels.classList.remove('is-aligned'); }
    },
    destroy() { destroyed = true; stop('supplier'); stop('product'); warehouseCache.clear(); observer.disconnect(); cancelAnimationFrame(layoutFrame); panels.remove(); },
  };
}
