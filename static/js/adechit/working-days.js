import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { showMessage } from '../portal/messages.js';
import { confirmBox } from '../utils/confirm-box.js';

function node(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  Object.assign(element, attributes);
  element.append(...children);
  return element;
}

export function bindWorkingDays({ api, context, year, refresh, report }) {
  const dialog = node('dialog', { className: 'ade-catalog ade-working-days' });
  dialog.setAttribute('aria-label', 'Zile lucrătoare pe luni');
  const subtitle = node('span');
  const heading = node('header', { className: 'ade-catalog-header' },
    node('h2', { textContent: 'Zile lucrătoare pe luni' }), subtitle);
  const yearInput = node('input', { type: 'number', min: '1900', max: '2200', step: '1', required: true });
  yearInput.setAttribute('aria-label', 'An');
  const loadButton = node('button', { type: 'button', className: 'btn', textContent: 'Încarcă anul' });
  const importButton = node('button', { type: 'button', className: 'btn', textContent: 'Descarcă zilele libere' });
  const tools = node('div', { className: 'ade-working-days__tools' }, node('label', {}, 'An', yearInput), loadButton, importButton);
  const message = node('p', { className: 'msg' });
  const hint = node('p', { className: 'ade-working-days__hint', textContent:
    'Zile prezență / lună = zile lucrătoare − zile libere legale − zile libere suplimentare. Editați zilele libere suplimentare în celulele colorate. Lunile închise sunt protejate.' });
  const gridHost = node('div', { className: 'ade-working-days__grid' });
  const details = node('details', { className: 'ade-working-days__holidays', hidden: true });
  const content = node('section', { className: 'ade-catalog-content' }, tools, hint, gridHost, details);
  const cancel = node('button', { type: 'button', className: 'btn', textContent: 'Închide' });
  const save = node('button', { type: 'button', className: 'btn is-primary', textContent: 'Salvează' });
  dialog.append(heading, message, content, node('footer', {}, cancel, save));
  document.body.append(dialog);
  let grid = null, rows = [], loadedYear = null, version = '', dirty = false, busy = '', sequence = 0, attempt = null;
  const error = failure => showMessage(message, failure.status === 404
    ? 'Funcția Zile lucrătoare nu este disponibilă pe server. Este necesară publicarea fișierelor Python ADECHIT și repornirea serviciului avacont.'
    : failure.message || 'Operația nu a putut fi finalizată.', 'error');
  function pending(mode = '') {
    busy = mode;
    yearInput.disabled = loadButton.disabled = !!mode;
    importButton.disabled = !!mode || loadedYear == null;
    save.disabled = !!mode || loadedYear == null;
    cancel.disabled = mode === 'save';
    gridHost.inert = !!mode;
    dialog.setAttribute('aria-busy', String(!!mode));
  }
  function paint() {
    grid?.destroy(); gridHost.replaceChildren();
    for (const row of rows) {
      row.state = row.closed ? 'Lună închisă' : row.existing ? 'Lună deschisă' : 'Configurare';
    }
    grid = new DataGrid(gridHost, {
      rows, rowKey: 'month', rowHeight: 30, headerHeight: 48, autoSize: false,
      enableGrouping: false, proportionalWidths: true, footer: false, editable: true, singleClickEdit: true,
      columns: [
        { key: 'name', title: 'Luna', width: 120, valueType: ValueType.Text },
        { key: 'calendar_days', title: 'Zile Luna', width: 80, valueType: ValueType.Number, align: 'center' },
        { key: 'weekdays', title: 'Zile\nlucrătoare', width: 105, valueType: ValueType.Number, align: 'center' },
        { key: 'deducted', title: 'Zile Libere\nLegale', width: 115, valueType: ValueType.Number, align: 'center' },
        { key: 'extra', title: 'Zile Libere\nsuplimentare', width: 150, valueType: ValueType.Number, editor: 'number',
          align: 'center', editable: row => !row.closed, required: true,
          parse: raw => { if (!/^\d+$/.test(String(raw))) throw new Error('Introduceți un număr întreg de zile.'); return Number(raw); } },
        { key: 'days', title: 'Zile prezență /\nLuna', width: 125, valueType: ValueType.Number, align: 'center' },
        { key: 'state', title: 'Stare', width: 135, valueType: ValueType.Text },
      ].map(column => ({ filter: false, sortable: false, ...column })),
      onCellSave: ({ row, key, value }) => {
        if (row.closed || key !== 'extra') throw new Error('Luna închisă nu poate fi modificată.');
        const maximum = row.weekdays - row.deducted;
        if (!Number.isInteger(value) || value < 0 || value > maximum) throw new Error(`Introduceți între 0 și ${maximum} zile libere suplimentare.`);
        const days = maximum - value;
        const current = rows.find(item => item.month === row.month);
        Object.assign(current, { extra: value, days });
        dirty = true; attempt = null; showMessage(message, '', 'info');
        return { extra: value, days };
      },
      onEditError: ({ error: failure }) => error(failure),
    });
  }
  async function commit() { return !grid?.hasEdit || await grid.commitEdit(); }
  async function discard() {
    return !dirty || await confirmBox('Renunțați la modificările nesalvate ale zilelor lucrătoare?',
      { title: 'Zile lucrătoare', yes: 'Renunță', no: 'Continuă editarea' });
  }
  async function load() {
    if (busy || !await commit() || !await discard()) { if (loadedYear != null) yearInput.value = loadedYear; return; }
    const wanted = Number(yearInput.value);
    if (!Number.isInteger(wanted) || wanted < 1900 || wanted > 2200) { error(new Error('Introduceți un an între 1900 și 2200.')); return; }
    const token = ++sequence;
    pending('load'); showMessage(message, 'Se încarcă lunile…', 'info');
    try {
      const result = await api(`/api/adechit/working-days?year=${wanted}`);
      if (!dialog.open || token !== sequence) return;
      loadedYear = result.year; yearInput.value = loadedYear; version = result.version;
      rows = result.months; subtitle.textContent = `${result.subunit} · ${loadedYear}`;
      dirty = false; attempt = null; details.hidden = true; details.replaceChildren();
      paint(); showMessage(message, '', 'info');
    } catch (failure) { if (dialog.open && token === sequence) { if (loadedYear != null) yearInput.value = loadedYear; error(failure); } }
    finally { if (token === sequence) pending(); }
  }
  async function download() {
    if (busy || loadedYear == null || !await commit()) return;
    if (Number(yearInput.value) !== loadedYear) { error(new Error('Apăsați „Încarcă anul” înainte de descărcarea zilelor libere.')); return; }
    const token = ++sequence;
    pending('download'); showMessage(message, 'Se descarcă zilele libere…', 'info');
    try {
      const result = await api(`/api/adechit/working-days/holidays?year=${loadedYear}`);
      if (!dialog.open || token !== sequence) return;
      const proposals = rows.map(row => {
        const proposal = result.months.find(month => month.month === row.month);
        if (!row.closed && row.extra > proposal.days) throw new Error(`${row.name}: zilele libere suplimentare depășesc zilele rămase după sărbătorile legale. Reduceți-le înainte de import.`);
        return { ...row, deducted: proposal.deducted, days: row.closed ? row.days : proposal.days - row.extra };
      });
      rows = proposals;
      dirty = true; attempt = null; paint();
      const holidays = node('ul');
      for (const month of result.months) for (const holiday of month.holidays) {
        holidays.append(node('li', { textContent: `${holiday.date.split('-').reverse().join('.')} — ${holiday.name}${holiday.working_day ? '' : ' (weekend, fără scădere)'}` }));
      }
      const source = node('a', { href: result.source, target: '_blank', rel: 'noopener noreferrer', textContent: 'Sursa zilelor libere' });
      details.replaceChildren(node('summary', { textContent: 'Sărbători descărcate' }), holidays, source); details.hidden = false;
      showMessage(message, `Au fost preluate ${result.holiday_count} zile distincte, dintre care ${result.deducted} luni–vineri. Verificați și salvați valorile; lunile închise au fost păstrate.`, 'info');
    } catch (failure) { if (dialog.open && token === sequence) error(failure); }
    finally { if (token === sequence) pending(); }
  }
  async function close() { if (busy === 'save' || !await commit() || !await discard()) return; dialog.close(); }
  dialog.addEventListener('cancel', event => { event.preventDefault(); void close(); });
  dialog.addEventListener('close', () => { ++sequence; grid?.destroy(); grid = null; rows = []; dirty = false; loadedYear = null; pending(); });
  cancel.addEventListener('click', () => { void close(); });
  loadButton.addEventListener('click', () => { void load(); });
  yearInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); void load(); } });
  importButton.addEventListener('click', () => { void download(); });
  save.addEventListener('click', async () => {
    if (busy || loadedYear == null || !await commit()) return;
    if (Number(yearInput.value) !== loadedYear) { error(new Error('Apăsați „Încarcă anul” înainte de salvare.')); return; }
    attempt ||= { key: crypto.randomUUID(), body: JSON.stringify({ year: loadedYear, version,
      days: rows.map(row => row.days), legal: rows.map(row => row.deducted), extra: rows.map(row => row.extra) }) };
    pending('save'); showMessage(message, 'Se salvează zilele lucrătoare…', 'info');
    try {
      const result = await api('/api/adechit/working-days', { method: 'POST', ...attempt });
      version = result.version; rows = result.months; dirty = false; attempt = null;
      dialog.close();
      await refresh();
    } catch (failure) { if (dialog.open) error(failure); else report(failure); }
    finally { pending(); }
  });
  return {
    async open() {
      if (dialog.open) return;
      if (!context()?.subunit || !context()?.permissions?.includes('catalog')) { report(new Error('Aveți nevoie de o subunitate selectată și dreptul de modificare a catalogului.')); return; }
      yearInput.value = year() || new Date().getFullYear(); dirty = false; loadedYear = null;
      gridHost.replaceChildren(); showMessage(message, '', 'info'); dialog.showModal();
      await load();
    },
  };
}
