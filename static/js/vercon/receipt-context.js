// Context cards occupy the saved-tree column; they never change the document layout.
import { el } from './ui.js';
import { parse, format } from './fields.js';
import { decimalText } from './decimal-text.js';

const kinds = { nir: 'NIR', storno: 'Storno', bc: 'BC', br: 'Retur', bp: 'Predare' };

export function receiptContext(host, { root, main, headerHost, linesHost, treeHost, api, selection, context, validLine, blankLine }) {
  const supplier = el('section', { className: 'vc-receipt-card', 'aria-label': 'Fișa furnizorului' });
  const product = el('section', { className: 'vc-receipt-card', 'aria-label': 'Fișa de magazie' });
  const panels = el('div', { className: 'vc-receipt-context', hidden: true }, supplier, product);
  host.append(panels);
  let visible = false, destroyed = false, draft = null, selected = null, remembered = null, layoutFrame = 0;
  const requests = { supplier: { sequence: 0, signature: '', timer: 0 }, product: { sequence: 0, signature: '', timer: 0 } };
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
    const request = requests[name], signature = params.toString();
    if (signature === request.signature) return;
    stop(name); request.signature = signature;
    const sequence = request.sequence;
    message(panel, title, 'Se încarcă…');
    request.timer = setTimeout(async () => {
      try {
        const data = await api(`/api/vercon/doc/nir/${name}-statement?${params}`);
        if (destroyed || !visible || sequence !== request.sequence) return;
        render(data);
      } catch (error) {
        if (destroyed || !visible || sequence !== request.sequence || error.name === 'AbortError') return;
        request.signature = ''; message(panel, title, error.message || 'Fișa nu a putut fi încărcată.', true);
      }
    }, 160);
  }
  function table(panel, columns, rows) {
    const head = el('tr', {}, ...columns.map(([label, , numeric]) => el('th', { textContent: label, className: numeric ? 'is-number' : '', scope: 'col' })));
    const body = el('tbody', {}, ...rows.map((row) => el('tr', {}, ...columns.map(([, value, numeric]) => el('td', { textContent: value(row), className: numeric ? 'is-number' : '' })))));
    panel.append(el('div', { className: 'vc-receipt-card__body' }, el('table', {}, el('thead', {}, head), body)));
  }
  function renderSupplier(data) {
    supplier.replaceChildren(el('h4', { textContent: 'Fișă furnizor' }), el('p', { className: 'vc-receipt-card__identity', textContent: data.name, title: data.fiscal_code }));
    if (data.rows.length) table(supplier, [
      ['Data', (row) => day(row.date)], ['Factură', (row) => `${row.kind === 'Storno' ? 'Storno ' : ''}${row.invoice}`],
      ['Valoare', (row) => money(row.value), true],
    ], data.rows);
    else supplier.append(el('p', { className: 'vc-receipt-card__message', textContent: 'Fără facturi salvate până la data aleasă.' }));
    supplier.append(el('footer', { className: 'vc-receipt-card__total', textContent: `Total facturi: ${money(data.total)} lei` }));
  }
  function renderProduct(data) {
    product.replaceChildren(el('h4', { textContent: 'Fișă de magazie' }), el('p', { className: 'vc-receipt-card__identity', textContent: data.name }),
      el('p', { className: 'vc-receipt-card__note', textContent: `Preț cu TVA: ${money(data.price)} lei • ${data.um}` }));
    if (data.rows.length) table(product, [
      ['Data', (row) => day(row.date)], ['Document', (row) => `${kinds[row.kind] || row.kind} ${row.number ?? ''}`],
      ['Cant.', (row) => money(row.quantity), true], ['Sold', (row) => money(row.balance), true],
    ], data.rows);
    else product.append(el('p', { className: 'vc-receipt-card__message', textContent: 'Fără mișcări salvate pentru acest produs și preț până la data aleasă.' }));
    product.append(el('footer', { className: 'vc-receipt-card__total', textContent: `Stoc: ${money(data.closing)} ${data.um}` }));
  }
  function refresh(row) {
    if (!visible || destroyed) return;
    const current = context();
    if (!current.draft) return;
    if (draft !== current.draft) {
      draft = current.draft; selected = remembered = null;
      stop('supplier'); stop('product');
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
    const date = parse(draft.header.DataFc || draft.header.Data || '', { type: 'date', required: true });
    const base = new URLSearchParams(Object.entries(selection()).filter(([, value]) => value != null && value !== ''));
    if (date.ok) base.set('date', date.value);
    if (draft.tip === 'STOC_INITIAL' || !draft.header.IdPartener || !date.ok) {
      stop('supplier'); message(supplier, 'Fișă furnizor', draft.tip === 'STOC_INITIAL' ? 'Stoc inițial • fără furnizor.' : !date.ok ? 'Completați data facturii.' : 'Alegeți furnizorul.');
    } else {
      const params = new URLSearchParams(base); params.set('supplier', draft.header.IdPartener);
      load('supplier', supplier, 'Fișă furnizor', params, renderSupplier);
    }
    if (!line || !validLine(line) || !date.ok) {
      stop('product'); message(product, 'Fișă de magazie', !date.ok ? 'Completați data facturii.' : 'Selectați un rând validat, cu produs, cantitate, preț și TVA.');
    } else {
      const params = new URLSearchParams(base);
      params.set('product', line.product); params.set('price', line.Pret); params.set('vat', line.IDTVA);
      if (line.variant) params.set('variant', line.variant);
      load('product', product, 'Fișă de magazie', params, renderProduct);
    }
    scheduleLayout();
  }
  function layout() {
    layoutFrame = 0;
    if (!visible || destroyed || !root.isConnected) return;
    const grid = linesHost.querySelector('.vc-doc__grid'), heading = linesHost.querySelector('h4');
    if (!grid || !heading) return;
    const box = panels.getBoundingClientRect(), lines = grid.getBoundingClientRect();
    const titleTop = heading.getBoundingClientRect().top;
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
    setVisible(on) {
      visible = on; panels.hidden = !on; treeHost.hidden = on;
      if (on) refresh();
      else { stop('supplier'); stop('product'); draft = null; selected = remembered = null; panels.classList.remove('is-aligned'); }
    },
    destroy() { destroyed = true; stop('supplier'); stop('product'); observer.disconnect(); cancelAnimationFrame(layoutFrame); panels.remove(); },
  };
}
