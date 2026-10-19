import { el } from './ui.js';
import { format, formatMoney } from './fields.js';

// Saved history only. Each card owns its request so a changed holder/date cannot show an older response.
export function oiContext(root, { family, api, selection, draft }) {
  const cards = new Map();
  const host = el('aside', { className: `vc-oi-context vc-oi-context--${family}`, hidden: true,
    'aria-label': 'Fișe deținători și produs' });
  const definitions = [['giver', 'Fișă predător'], ...(family === 'bt' ? [['receiver', 'Fișă primitor']] : []),
    ['product', 'Mișcări produs']];
  for (const [key, title] of definitions) {
    const panel = el('section', { className: 'vc-oi-card', 'aria-label': title });
    cards.set(key, { panel, title, signature: '', sequence: 0, timer: null }); host.append(panel);
  }
  root.append(host);
  let visible = false, destroyed = false, current = null, selected = null;
  function message(card, text, bad = false) {
    card.panel.replaceChildren(el('h4', { textContent: card.title }),
      el('p', { className: 'vc-oi-card__message', textContent: text, role: bad ? 'alert' : 'status' }));
  }
  function reset(card) { clearTimeout(card.timer); card.sequence++; card.signature = ''; }
  function render(card, data, product) {
    card.panel.replaceChildren(el('h4', { textContent: card.title }),
      el('p', { className: 'vc-oi-card__identity', textContent: data.name }));
    if (!data.rows.length) {
      card.panel.append(el('p', { className: 'vc-oi-card__message', textContent: 'Fără bonuri salvate până la data aleasă.' })); return;
    }
    const columns = [
      ['Data', row => format(row.date, { type: 'date' }), 'date'],
      ['Bon', row => `${row.kind} ${row.number ?? ''}`, 'document'],
      ...(product ? [['Cant.', row => format(row.quantity, { type: 'decimal' }), 'number']] : []),
      ['Valoare', row => formatMoney(row.value), 'number'],
    ];
    const body = el('tbody');
    for (const row of data.rows) {
      const tr = el('tr');
      for (const [, value, type] of columns) {
        const cell = el('td', { textContent: value(row), className: `vc-oi-card__${type}`,
          title: `${row.giver} → ${row.receiver}` });
        if (product && type === 'document') cell.append(el('small', { textContent: `${row.giver} → ${row.receiver}` }));
        tr.append(cell);
      }
      body.append(tr);
    }
    card.panel.append(el('div', { className: 'vc-oi-card__body' }, el('table', {},
      el('thead', {}, el('tr', {}, ...columns.map(([title, , type]) => el('th', {
        textContent: title, scope: 'col', className: `vc-oi-card__${type}` })))), body)));
  }
  function load(card, params, product) {
    params.sort(); const signature = params.toString();
    if (signature === card.signature) return;
    reset(card); card.signature = signature; const sequence = card.sequence;
    message(card, 'Se încarcă…');
    card.timer = setTimeout(async () => {
      try {
        const result = await api(`/api/vercon/doc/${family}/custody-statement?${params}`);
        if (!destroyed && visible && sequence === card.sequence) render(card, result, product);
      } catch (error) {
        if (destroyed || !visible || sequence !== card.sequence || error.name === 'AbortError') return;
        card.signature = ''; message(card, error.message || 'Fișa nu a putut fi încărcată.', true);
      }
    }, 160);
  }
  function refresh(row) {
    if (!visible || destroyed) return;
    const document = draft(); if (!document) return;
    if (current !== document) { current = document; selected = null; cards.forEach(reset); }
    if (row?.variant) selected = row.key;
    const line = document.lines.find(item => item.key === selected && item.variant)
      || document.lines.find(item => item.variant);
    selected = line?.key ?? null;
    const date = document.header.Data;
    for (const [name, card] of cards) {
      const value = name === 'product' ? line?.variant : document.header[name === 'giver'
        ? family === 'bt' ? 'IDC_OLD' : 'IDC_BC' : 'IDC_BT'];
      if (!value || !date) {
        reset(card); message(card, !date ? 'Completați data bonului.' : name === 'product'
          ? 'Selectați un produs din grilă.' : `Alegeți ${name === 'giver' ? 'predătorul' : 'primitorul'}.`); continue;
      }
      const params = new URLSearchParams(Object.entries(selection()).filter(([, value]) => value != null && value !== ''));
      params.set('date', date); params.set(name === 'product' ? 'variant' : 'holder', value);
      load(card, params, name === 'product');
    }
  }
  return {
    refresh,
    setVisible(on) {
      visible = on; host.hidden = !on;
      if (on) refresh(); else { current = null; selected = null; cards.forEach(reset); }
    },
    destroy() { destroyed = true; cards.forEach(reset); host.remove(); },
  };
}
