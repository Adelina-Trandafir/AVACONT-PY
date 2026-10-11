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
  const dialog = document.getElementById('ade-catalog');
  const content = document.getElementById('ade-catalog-content');
  const message = document.getElementById('ade-catalog-message');
  const save = document.getElementById('ade-catalog-save');
  let grids = [];
  let drafts = new Map();
  let busy = false;
  let loaded = false;
  let sequence = 0;
  let request = null;
  let taxRows = [];
  let taxOriginals = new Map();
  let taxGrid = null;
  let validationPrompt = null;
  const error = (failure) => { console.error('[ADE catalog]', failure); showMessage(message, failure.message, 'error'); };
  function closePrevious(row) {
    for (const other of taxRows) if (other.IDV !== row.IDV && (other.Activ || other._closedBy === row.IDV)) {
      other.Activ = false; other.PanaLa = previousMonth(row.DeLa); other._closedBy = row.IDV;
    }
    if (row.Activ) row.PanaLa = null;
  }
  function rebuildTaxes() {
    for (const row of taxRows) {
      const draft = drafts.get(`ValoriTaxe:${row.IDV}`);
      Object.assign(row, taxOriginals.get(row.IDV) || { PanaLa: null }, draft?.values || {});
      delete row._closedBy;
    }
    const active = taxRows.filter((row) => row.Activ).sort((a, b) => (a.DeLa || '').localeCompare(b.DeLa || ''));
    for (const row of active) {
      row.Activ = true; closePrevious(row);
    }
  }
  function removeTax(row) {
    taxGrid?.cancelEdit();
    if (row.IDV < 0) {
      taxRows.splice(taxRows.findIndex((item) => item.IDV === row.IDV), 1);
    }
    drafts.delete(`ValoriTaxe:${row.IDV}`);
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
  const grid = (host, table, rows, columns, onSelect) => {
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
        const updated = taxRows.find((item) => item.IDV === row.IDV);
        rebuildTaxes();
        return { ...updated };
      }, onRowValidate: ({ row }) => validateTaxes(row),
      onEditError: ({ error: failure, row, key }) => invalidTax(row, failure, key).catch(error) });
    instance.sortBy('Expl', 'asc');
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
  async function open(kind) {
    if (dialog.open || !context()) return;
    drafts = new Map(); grids = []; request = null; loaded = false;
    save.disabled = true; content.replaceChildren();
    document.getElementById('ade-catalog-title').textContent = kind === 'taxes' ? 'Taxe' : 'Plătitori';
    showMessage(message, '', 'info'); dialog.showModal();
    try {
      if (kind === 'taxes') {
        const rows = (await api('/api/adechit/rows/ValoriTaxe')).rows;
        taxRows = rows;
        taxOriginals = new Map(rows.map((row) => [row.IDV, { ...row }]));
        const host = document.createElement('div'); host.className = 'ade-catalog-grid';
        const instance = grid(host, 'ValoriTaxe', rows, [
          { ...text('TaxaZilnica', 'Taxă zilnică', 140), editor: 'number', valueType: ValueType.Number,
            validate: (value) => value > 0 ? '' : 'Valoarea taxei trebuie să fie mai mare decât zero.' },
          { ...text('Expl', 'Explicație', 250), validate: (value) => value?.trim() ? '' : 'Explicația taxei este obligatorie.' },
          { ...text('DeLa', 'Început', 120), editor: 'monthYear', parse: monthValue, formatter: monthText, nullable: true },
          { ...text('PanaLa', 'Sfârșit', 120), editable: false, formatter: monthText },
          { ...text('Activ', 'Activ', 80), valueType: ValueType.Boolean, display: 'checkbox', editor: 'checkbox' }]);
        taxGrid = instance;
        content.append(addButton('Adaugă taxă', async () => {
          if (busy || !await finishEdits()) return;
          const today = new Date();
          let start = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
          const latest = rows.map((item) => item.DeLa || '').sort().at(-1);
          if (latest >= start) {
            const [year, month] = latest.split('-').map(Number);
            start = month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`;
          }
          const row = { IDV: --sequence, TaxaZilnica: 0, Expl: '', Activ: true, DeLa: start, PanaLa: null };
          drafts.set(`ValoriTaxe:${row.IDV}`, { table: 'ValoriTaxe', id: null,
            values: { TaxaZilnica: 0, Expl: '', Activ: true, DeLa: start } });
          rows.push(row); rebuildTaxes(); instance.setRows(rows);
          instance.beginNewRowEdit(row, 'TaxaZilnica', () => removeTax(row));
        }), host);
      }
      loaded = true; save.disabled = false;
    } catch (failure) {
      error(failure); save.disabled = false; save.textContent = 'Închide';
    }
  }
  dialog.addEventListener('cancel', (event) => event.preventDefault());
  save.addEventListener('click', async () => {
    if (busy) return;
    try {
      if (!await finishEdits()) return;
      if (!await validateTaxes()) return;
      busy = true; save.disabled = true; content.inert = true;
      if (drafts.size) {
        const items = [...drafts.entries()].map(([token, draft]) => {
          const row = taxRows.find((item) => `ValoriTaxe:${item.IDV}` === token);
          return { ...draft, values: { ...draft.values, ...('Activ' in draft.values ? { Activ: row.Activ } : {}) } };
        });
        const body = JSON.stringify({ items });
        if (!request || request.body !== body) request = { body, key: crypto.randomUUID() };
        await api('/api/adechit/catalog-save', { method: 'POST', ...request });
        drafts.clear();
      }
      dialog.close(); grids.forEach((instance) => instance.destroy()); grids = [];
      if (loaded) await refresh();
    } catch (failure) { error(failure); }
    finally { busy = false; content.inert = false; save.disabled = false; save.textContent = 'Salvează și închide'; }
  });
  addEventListener('beforeunload', (event) => {
    if (dialog.open && (drafts.size || grids.some((instance) => instance.hasEdit))) { event.preventDefault(); event.returnValue = ''; }
  });
  const payers = bindPayers({ api, context, refresh, moveToSubunit });
  document.getElementById('ade-taxes').addEventListener('click', () => open('taxes'));
  return payers;
}
