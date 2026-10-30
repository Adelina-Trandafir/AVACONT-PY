import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { showMessage } from '../portal/messages.js';
import { bindPayers } from './payers.js';

const text = (key, title, width = 150) => ({ key, title, width, valueType: ValueType.Text, editable: true, filter: key === 'Nume' || key === 'Grupa' });
const monthText = (value) => value ? `${value.slice(5, 7)}.${value.slice(0, 4)}` : '';
function monthValue(raw) {
  const value = raw.trim();
  if (!value) return null;
  if (/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(value)) return value;
  const match = /^(0?[1-9]|1[0-2])[./-]([1-9]\d{3})$/.exec(value);
  if (!match) throw new Error('Introduceți luna și anul în format ll.aaaa.');
  return `${match[2]}-${match[1].padStart(2, '0')}`;
}
function previousMonth(value) {
  if (!value) return null;
  const [year, month] = value.split('-').map(Number);
  return month === 1 ? `${year - 1}-12` : `${year}-${String(month - 1).padStart(2, '0')}`;
}

export function bindCatalogs({ api, context, refresh, moveToSubunit }) {
  // Two windows share the tax grid: the catalog window (Taxe) and the month-closing window (slice AD14-01).
  const catalogUi = { dialog: document.getElementById('ade-catalog'), content: document.getElementById('ade-catalog-content'),
    message: document.getElementById('ade-catalog-message'), save: document.getElementById('ade-catalog-save'), saveLabel: 'Salvează și închide' };
  const closeUi = { dialog: document.getElementById('ade-close-month'), content: document.getElementById('ade-close-month-content'),
    message: document.getElementById('ade-close-month-message'), save: document.getElementById('ade-close-month-save'), saveLabel: 'Închide luna' };
  let { dialog, content, message, save } = catalogUi;
  let saveLabel = catalogUi.saveLabel;
  let closing = null; // {resolve, startMonth, ...} while the month-closing window is open
  let grids = [];
  let drafts = new Map();
  let busy = false;
  let loaded = false;
  let sequence = 0;
  let request = null;
  let taxRows = [];
  let taxOriginals = new Map();
  let taxGrid = null;
  let activationEdits = [];
  let validationPrompt = null;
  const error = (failure) => { console.error('[ADE catalog]', failure); showMessage(message, failure.message, 'error'); };
  // the month-closing window says which tax the next month will open with
  function paintActive() {
    const k_node = closing && document.getElementById('ade-close-month-active');
    if (!k_node) return;
    const k_row = taxRows.find((row) => row.Activ);
    k_node.textContent = k_row
      ? `Taxa activă (aplicată lunii următoare): ${Number(k_row.TaxaZilnica || 0).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} lei · ${k_row.Expl || 'fără explicație'}`
      : 'Nu există o taxă activă.';
  }
  function rebuildTaxes() {
    for (const row of taxRows) {
      const draft = drafts.get(`ValoriTaxe:${row.IDV}`);
      Object.assign(row, taxOriginals.get(row.IDV) || { PanaLa: null }, draft?.values || {});
    }
    // Checkbox choices follow the operator's actions, never the dates of the tax rows.
    for (const edit of activationEdits) {
      const selected = taxRows.find(row => row.IDV === edit.id);
      if (!selected) continue;
      if (edit.active) taxRows.forEach(row => { row.Activ = false; });
      selected.Activ = edit.active;
    }
    // Only newly added periods close earlier open periods; reactivation preserves history.
    const previousActive = new Set([...taxOriginals.values()].filter(row => row.Activ).map(row => row.IDV));
    const added = taxRows.filter(row => row.IDV < 0).sort((a, b) => (a.DeLa || '').localeCompare(b.DeLa || ''));
    for (const row of added) for (const other of taxRows) {
      if (other.IDV === row.IDV || !row.DeLa || other.DeLa && other.DeLa >= row.DeLa) continue;
      if (previousActive.has(other.IDV) || other.DeLa && !other.PanaLa) {
        other.PanaLa = previousMonth(row.DeLa); previousActive.delete(other.IDV);
      }
    }
    paintActive();
  }
  function removeTax(row) {
    taxGrid?.cancelEdit();
    if (row.IDV < 0) {
      taxRows.splice(taxRows.findIndex((item) => item.IDV === row.IDV), 1);
    }
    drafts.delete(`ValoriTaxe:${row.IDV}`);
    activationEdits = activationEdits.filter(edit => edit.id !== row.IDV);
    rebuildTaxes(); taxGrid?.setRows(taxRows); request = null;
  }
  async function invalidTax(row, failure, key) {
    if (validationPrompt) return validationPrompt;
    validationPrompt = new Promise((resolve) => {
      const prompt = document.createElement('dialog');
      prompt.className = 'ade-catalog ade-tax-validation';
      prompt.setAttribute('aria-label', 'Taxă invalidă');
      const header = document.createElement('header'); header.className = 'ade-catalog-header';
      const title = document.createElement('h2'); title.textContent = 'Taxă invalidă'; header.append(title);
      const text = document.createElement('p'); text.className = 'ade-tax-validation-text';
      text.textContent = `${failure.message} Continuați editarea sau anulați înregistrarea?`;
      const footer = document.createElement('footer');
      const finish = (keep) => {
        prompt.close(); prompt.remove(); validationPrompt = null;
        if (keep) {
          if (!taxGrid.hasEdit) taxGrid.beginEdit(row, key);
          else taxGrid.focusEdit();
        } else removeTax(row);
        resolve(false);
      };
      for (const [label, keep] of [['Continuă editarea', true], ['Anulează înregistrarea', false]]) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'btn'; button.textContent = label;
        button.addEventListener('click', () => finish(keep)); footer.append(button);
      }
      prompt.addEventListener('cancel', (event) => { event.preventDefault(); finish(true); });
      prompt.append(header, text, footer); document.body.append(prompt); prompt.showModal();
    });
    return validationPrompt;
  }
  async function validateTaxes(onlyRow = null) {
    for (const row of onlyRow ? [onlyRow] : taxRows) {
      const draft = drafts.get(`ValoriTaxe:${row.IDV}`);
      if (!draft) continue;
      let key; let message;
      if (!(row.TaxaZilnica > 0)) { key = 'TaxaZilnica'; message = 'Valoarea taxei trebuie să fie mai mare decât zero.'; }
      else if (!row.Expl?.trim()) { key = 'Expl'; message = 'Explicația taxei este obligatorie.'; }
      else if (row.IDV < 0 || 'DeLa' in draft.values && row.DeLa !== taxOriginals.get(row.IDV)?.DeLa) {
        const latest = taxRows.filter((other) => other.IDV !== row.IDV && (other.IDV > 0 || row.IDV > 0 || other.IDV > row.IDV))
          .map((other) => other.DeLa || '').sort().at(-1) || '';
        if (!row.DeLa || row.DeLa <= latest) { key = 'DeLa'; message = 'Începutul taxei este obligatoriu și trebuie să fie după ultimul început existent.'; }
      }
      if (message) return invalidTax(row, new Error(message), key);
    }
    return true;
  }
  const grid = (host, table, rows, columns, onSelect, sortKey = 'Expl') => {
    const key = context().schema[table].key;
    const instance = new DataGrid(host, { columns, rows, rowKey: key, headerHeight: 32, mobileRowScale: 1.2, editable: context().permissions.includes('catalog'),
      theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'modern',
      footer: true, footerCaption: '{0} înregistrări', layoutId: `ade.catalog.${table}`, onSelect,
      onCellSave: ({ row, key: field, value }) => {
        if (busy) throw new Error('Așteptați finalizarea salvării.');
        const token = `${table}:${row[key]}`;
        const draft = drafts.get(token) || { table, id: row[key] < 0 ? null : row[key], version: row.Version, values: {} };
        draft.values[field] = value;
        drafts.set(token, draft);
        if (field === 'Activ') activationEdits.push({ id: row.IDV, active: Boolean(value) });
        const updated = taxRows.find((item) => item.IDV === row.IDV);
        rebuildTaxes();
        return { ...updated };
      }, onRowValidate: ({ row }) => validateTaxes(row),
      onEditError: ({ error: failure, row, key }) => invalidTax(row, failure, key).catch(error) });
    if (sortKey) instance.sortBy(sortKey, 'asc'); else instance.sortBy(null, null);
    grids.push(instance);
    return instance;
  };
  const finishEdits = async () => {
    for (const instance of grids) if (instance.hasEdit && !await instance.commitEdit()) return false;
    return true;
  };
  const addButton = (label, callback) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn'; button.textContent = label;
    button.disabled = !context().permissions.includes('catalog');
    button.addEventListener('click', () => callback().catch(error));
    return button;
  };
  // `close` = the month-closing window: {title, text, startMonth, resolve}; its tax grid shows the active tax first.
  async function open(kind, close = null) {
    if (catalogUi.dialog.open || closeUi.dialog.open || !context()) return;
    ({ dialog, content, message, save, saveLabel } = close ? closeUi : catalogUi);
    closing = close;
    drafts = new Map(); activationEdits = []; grids = []; request = null; loaded = false;
    save.disabled = true; content.replaceChildren();
    if (close) {
      document.getElementById('ade-close-month-title').textContent = close.title;
      document.getElementById('ade-close-month-intro').textContent = close.text;
      document.getElementById('ade-close-month-active').textContent = '';
    } else document.getElementById('ade-catalog-title').textContent = kind === 'taxes' ? 'Taxe' : 'Plătitori';
    showMessage(message, '', 'info'); dialog.showModal();
    try {
      if (kind === 'taxes') {
        const rows = (await api('/api/adechit/rows/ValoriTaxe')).rows;
        // closing: the active tax first, then the newest ones
        if (close) rows.sort((a, b) => Number(Boolean(b.Activ)) - Number(Boolean(a.Activ)) || (b.DeLa || '').localeCompare(a.DeLa || ''));
        taxRows = rows;
        taxOriginals = new Map(rows.map((row) => [row.IDV, { ...row }]));
        const host = document.createElement('div'); host.className = 'ade-catalog-grid';
        const instance = grid(host, 'ValoriTaxe', rows, [
          { ...text('TaxaZilnica', 'Taxă zilnică', 140), editor: 'number', valueType: ValueType.Number,
            validate: (value) => value > 0 ? '' : 'Valoarea taxei trebuie să fie mai mare decât zero.' },
          { ...text('Expl', 'Explicație', 250), validate: (value) => value?.trim() ? '' : 'Explicația taxei este obligatorie.' },
          { ...text('DeLa', 'Început', 120), editor: 'monthYear', parse: monthValue, formatter: monthText, nullable: true },
          { ...text('PanaLa', 'Sfârșit', 120), editable: false, formatter: monthText },
          { ...text('Activ', 'Activ', 80), valueType: ValueType.Boolean, display: 'checkbox', editor: 'checkbox' }], undefined, close ? null : 'Expl');
        taxGrid = instance;
        content.append(addButton('Adaugă taxă', async () => {
          if (busy || !await finishEdits()) return;
          const today = new Date();
          let start = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
          if (closing?.startMonth) start = closing.startMonth;   // the new tax applies from the month that opens now
          const latest = rows.map((item) => item.DeLa || '').sort().at(-1);
          if (latest >= start) {
            const [year, month] = latest.split('-').map(Number);
            start = month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`;
          }
          const row = { IDV: --sequence, TaxaZilnica: 0, Expl: '', Activ: true, DeLa: start, PanaLa: null };
          drafts.set(`ValoriTaxe:${row.IDV}`, { table: 'ValoriTaxe', id: null,
            values: { TaxaZilnica: 0, Expl: '', Activ: true, DeLa: start } });
          rows.push(row); activationEdits.push({ id: row.IDV, active: true }); rebuildTaxes(); instance.setRows(rows);
          instance.beginNewRowEdit(row, 'TaxaZilnica', () => removeTax(row));   // opens the editor on the new row, scrolled into view
        }), host);
        paintActive();
      }
      loaded = true; save.disabled = false;
    } catch (failure) {
      // the month is never closed on a tax list that did not load
      error(failure); save.disabled = Boolean(close); save.textContent = 'Închide';
    }
  }
  // Leaves the month-closing window without closing the month: the tax changes made in it are dropped.
  function cancelClose() {
    if (busy || !closing) return;
    const { resolve } = closing; closing = null;
    drafts.clear(); grids.forEach((instance) => instance.destroy()); grids = [];
    dialog.close(); resolve(false);
  }
  catalogUi.dialog.addEventListener('cancel', (event) => event.preventDefault());
  closeUi.dialog.addEventListener('cancel', (event) => { event.preventDefault(); cancelClose(); });
  document.getElementById('ade-close-month-cancel').addEventListener('click', cancelClose);
  closeUi.save.addEventListener('click', () => commit());
  catalogUi.save.addEventListener('click', () => commit());
  async function commit() {
    if (busy) return;
    try {
      if (!await finishEdits()) return;
      if (!await validateTaxes()) return;
      busy = true; save.disabled = true; content.inert = true;
      if (drafts.size) {
        const pending = new Map(drafts);
        // Include automatic unchecks even if the operator finally leaves every checkbox clear.
        for (const row of taxRows) {
          const original = taxOriginals.get(row.IDV);
          if (!original || Boolean(original.Activ) === Boolean(row.Activ)) continue;
          const token = `ValoriTaxe:${row.IDV}`;
          const draft = pending.get(token) || { table: 'ValoriTaxe', id: row.IDV, version: original.Version, values: {} };
          pending.set(token, { ...draft, values: { ...draft.values, Activ: Boolean(row.Activ) } });
        }
        const items = [...pending.entries()].map(([token, draft]) => {
          const row = taxRows.find((item) => `ValoriTaxe:${item.IDV}` === token);
          return { ...draft, values: { ...draft.values, ...('Activ' in draft.values ? { Activ: row.Activ } : {}) } };
        });
        const body = JSON.stringify({ items });
        if (!request || request.body !== body) request = { body, key: crypto.randomUUID() };
        await api('/api/adechit/catalog-save', { method: 'POST', ...request });
        drafts.clear();
      }
      dialog.close(); grids.forEach((instance) => instance.destroy()); grids = [];
      if (closing) { const { resolve } = closing; closing = null; resolve(true); }   // the caller closes the month (and reloads the context)
      else if (loaded) await refresh();
    } catch (failure) { error(failure); }
    finally { busy = false; content.inert = false; save.disabled = false; save.textContent = saveLabel; }
  }
  const payers = bindPayers({ api, context, refresh, moveToSubunit });
  document.getElementById('ade-taxes').addEventListener('click', () => open('taxes'));
  // Month closing: the question and the tax grid in one window. Resolves true when the operator chose «Închide luna»
  // (tax changes already saved), false on «Renunță».
  function confirmMonthClose({ title, text, startMonth }) {
    return new Promise((resolve) => open('taxes', { title, text, startMonth, resolve })
      .then(() => { if (!closeUi.dialog.open) resolve(false); })   // the window did not open (no context, another window open): nothing is closed
      .catch((failure) => { closing = null; resolve(false); error(failure); }));
  }
  return { ...payers, confirmMonthClose, isDirty: () => dialog.open && (drafts.size > 0 || grids.some(instance => instance.hasEdit)) || payers.isDirty() };
}
