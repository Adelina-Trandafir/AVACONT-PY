// SLICE-VC09-11: a compact warehouse card over the PDF, using its frozen context.
import { el, modal, grid, column } from './ui.js';
import { decimalText } from './decimal-text.js';

const kinds = { nir: 'NIR', storno: 'Storno', bc: 'Bon de consum', br: 'Bon de retur' };
const dateText = value => String(value).slice(0, 10).split('-').reverse().join('.');
const negate = value => /^[-+]?0(?:\.0*)?$/.test(String(value)) ? '0'
  : String(value).startsWith('-') ? String(value).slice(1) : '-' + String(value);

export function stockCardLinks(api, model, isCurrent = () => true) {
  const card = model.stock_card;
  if (!card || !Object.keys(card.variants || {}).length) return undefined;
  const context = Object.fromEntries(Object.entries(card.context).map(([key, value]) => [key, String(value)]));
  let popup = null;
  return (href, report) => {
    const url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== '/api/vercon/stock/balance') return false;
    // Consume only links generated for this model, never the page's current selection.
    const variant = url.searchParams.get('variant');
    const valid = /^[1-9]\d*$/.test(variant || '') && Object.hasOwn(card.variants, variant) && url.searchParams.getAll('variant').length === 1
      && Object.entries(context).every(([key, value]) => url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === value)
      && [...url.searchParams.keys()].every(key => key === 'variant' || Object.hasOwn(context, key));
    if (!valid || !isCurrent() || report.closed) return true;
    if (popup && !popup.closed) { popup.dialog.focus(); return true; }
    popup = modal('Fișă de magazie • ' + card.variants[variant].name, report.scope);
    popup.dialog.classList.add('vc-stock-card');
    const view = popup, abort = new AbortController();
    view.scope.own(() => abort.abort());
    const outside = event => {
      if (event.target !== view.dialog) return false;
      const rect = view.dialog.getBoundingClientRect();
      return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    };
    let pressedOutside = false;
    view.dialog.addEventListener('pointerdown', event => { pressedOutside = outside(event); }, { signal: abort.signal });
    view.dialog.addEventListener('click', event => {
      if (pressedOutside && outside(event)) view.close();
      pressedOutside = false;
    }, { signal: abort.signal });
    const alive = () => !view.closed && !report.closed && isCurrent();
    const close = el('button', { type: 'button', textContent: 'Închide' });
    close.addEventListener('click', view.close); view.footer.append(close);
    close.focus({ preventScroll: true });
    const status = el('p', { role: 'status', textContent: 'Se încarcă mișcările produsului…' });
    view.body.append(status); view.dialog.setAttribute('aria-busy', 'true');
    void (async () => {
      try {
        const data = await api(url.pathname + url.search, { signal: abort.signal });
        if (!alive()) return;
        const row = data.rows.find(item => String(item.variant) === variant);
        if (!row) throw new Error('Produsul nu mai are o poziție de stoc disponibilă în gestiunea și perioada raportului.');
        const number = value => decimalText(value, data.zec);
        const facts = el('dl', { className: 'vc-stock-card__facts' });
        for (const [label, value] of [
          ['Gestiune', model.gestiune_name], ['Perioada', `${dateText(context.start)} — ${dateText(context.end)}`],
          ['Cod', row.code || variant], ['UM', row.um || ''], ['Preț cu TVA', number(row.price)],
        ]) facts.append(el('dt', { textContent: label }), el('dd', { textContent: String(value || '') }));
        const summary = el('dl', { className: 'vc-stock-card__summary' });
        for (const [label, quantity, value] of [
          ['Sold inițial', row.quantities.opening, row.values.opening],
          ['Intrări nete', row.quantities.incoming, row.values.incoming],
          ['Ieșiri nete', row.quantities.outgoing, row.values.outgoing],
          ['Sold final', row.quantities.closing, row.values.closing],
        ]) summary.append(el('dt', { textContent: label }), el('dd', { textContent: `${number(quantity)} / ${number(value)} lei` }));
        const host = el('div', { className: 'vc-stock-card__grid' });
        const movements = data.statement.filter(item => String(item.variant) === variant);
        const rows = [{ id: 'opening', date: context.start, document: 'SOLD INIȚIAL', kind: '', incoming: '', outgoing: '',
          closing: row.quantities.opening, closing_value: row.values.opening },
        ...movements.map((item, index) => ({ id: `movement:${index}`, date: item.date, document: String(item.number || ''),
          kind: kinds[item.kind] || item.kind,
          incoming: ['nir', 'storno'].includes(item.kind) ? item.quantity : '',
          outgoing: ['bc', 'br'].includes(item.kind) ? negate(item.quantity) : '',
          closing: item.balance, closing_value: item.balance_value }))];
        const numeric = (key, label) => column({ key, label, type: 'decimal' }, { formatter: number, width: 125 });
        const columns = [column({ key: 'date', label: 'Data', type: 'date' }, { width: 100 }),
          column({ key: 'document', label: 'Document / factură' }, { width: 155 }),
          column({ key: 'kind', label: 'Tip' }, { width: 135 }), numeric('incoming', 'Intrări'), numeric('outgoing', 'Ieșiri'),
          numeric('closing', 'Sold'), numeric('closing_value', 'Valoare sold')]
          .map(item => ({ ...item, editable: false, sortable: false, filter: false }));
        status.remove(); view.body.append(facts, summary, host);
        grid(host, { rows, columns, rowKey: 'id', footer: false, defaultSort: false,
          rowClassName: item => item.id === 'opening' ? 'vc-stock-card__opening' : '',
          proportionalWidths: true }, view.scope);
        if (!movements.length) view.body.append(el('p', { textContent: 'Nu există mișcări în perioada selectată.' }));
      } catch (error) {
        if (alive() && error.name !== 'AbortError') {
          if (!status.isConnected) view.body.append(status);
          status.textContent = error.message || 'Fișa de magazie nu a putut fi încărcată.';
          status.setAttribute('role', 'alert');
        }
      } finally {
        if (alive()) view.dialog.setAttribute('aria-busy', 'false');
        else if (!view.closed) view.close();
      }
    })();
    return true;
  };
}
