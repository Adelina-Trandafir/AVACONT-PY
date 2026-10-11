import { Combobox } from '../components/combobox/combobox.js';
import { DatePicker } from '../components/datepicker/datepicker.js';
import { showMessage } from '../portal/messages.js';
import { DataGrid, ValueType } from '../dgv/datagrid.js';

const $ = (id) => document.getElementById(id);
const unit = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1));
const base = `/api/adechit/parinti/${encodeURIComponent(unit)}`;
const money = (value) => Number(value).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const balance = (value) => `${money(Math.abs(value))} lei${value > 0 ? ' · Debit' : value < 0 ? ' · Credit' : ''}`;
let challenge = null; let childId = null; let combo = null; let revision = 0; let monthGrid = null;
const pickers = [new DatePicker($('parent-start')), new DatePicker($('parent-end'))];
function report(error) { console.error('[ADE parent portal]', error); showMessage($('parent-message'), error.message, 'error'); }
function reset() {
  ++revision; childId = null; combo?.destroy(); combo = null; $('parent-child-selector').hidden = true;
  monthGrid?.destroy(); monthGrid = null;
  $('parent-dashboard').hidden = true; $('parent-login').hidden = false; $('parent-logout').hidden = true;
}
async function api(path, body) {
  const response = await fetch(base + path, { method: body ? 'POST' : 'GET', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'ADEParentPortal' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const data = await response.json();
  if (!response.ok) { if (data.reason === 'SESSION') reset(); throw new Error(data.error || 'Operația nu a fost finalizată.'); }
  return data;
}
async function busy(form, action) {
  const buttons = [...form.querySelectorAll('button')]; buttons.forEach((b) => { b.disabled = true; });
  try { await action(); } catch (error) { report(error); } finally { buttons.forEach((b) => { b.disabled = false; }); }
}
$('parent-credentials').addEventListener('submit', (event) => {
  event.preventDefault(); busy(event.currentTarget, async () => {
    const data = await api('/code', { cnp: $('parent-cnp').value.trim(), access_code: $('parent-access').value.trim() });
    challenge = data.challenge; $('parent-otp').value = ''; $('parent-credentials').hidden = true; $('parent-verification').hidden = false;
    showMessage($('parent-message'), data.message, 'info'); $('parent-otp').focus();
  });
});
$('parent-back').addEventListener('click', () => { challenge = null; $('parent-verification').hidden = true; $('parent-credentials').hidden = false; });
$('parent-verification').addEventListener('submit', (event) => {
  event.preventDefault(); busy(event.currentTarget, async () => {
    await api('/verify', { challenge, code: $('parent-otp').value.trim() }); await loadChildren();
    showMessage($('parent-message'), '', 'info');
  });
});
$('parent-logout').addEventListener('click', () => busy($('parent-logout').parentElement, async () => { await api('/logout', {}); reset(); $('parent-back').click(); }));
async function loadChildren() {
  const data = await api('/children');
  data.children.sort((a, b) => a.name.localeCompare(b.name, 'ro', { sensitivity: 'base' }));
  combo?.destroy(); combo = null;
  $('parent-child-selector').hidden = data.children.length <= 1;
  if (data.children.length > 1) {
    combo = new Combobox($('parent-child-combo'), { readonly: true, allowEmpty: false,
      staticData: data.children.map((c) => ({ value: String(c.id), label: `${c.name}${c.group ? ' · ' + c.group : ''}` })),
      onSelect: (value) => loadChild(Number(value)).catch(report) });
    combo.input.setAttribute('aria-label', 'Alege copilul');
    const first = data.children[0]; combo.setValue(String(first.id), `${first.name}${first.group ? ' · ' + first.group : ''}`);
  }
  await loadChild(data.children[0].id);
  $('parent-login').hidden = true; $('parent-dashboard').hidden = false; $('parent-logout').hidden = false;
}
function svgNode(kind, attributes, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', kind);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text != null) node.textContent = text;
  return node;
}
function chart(host, rows, financial) {
  host.replaceChildren(); const width = Math.max(440, rows.length * 72 + 50); host.setAttribute('viewBox', `0 0 ${width} 220`); host.style.width = `${width}px`;
  if (!rows.length) { host.append(svgNode('text', { x: 20, y: 90 }, 'Nu există luni înregistrate.')); return; }
  const max = Math.max(1, ...rows.flatMap((r) => financial ? [r.payments, r.refunds] : [r.days]));
  const step = (width - 40) / rows.length;
  rows.forEach((row, index) => {
    const x = 30 + index * step;
    const series = financial ? [['payments', 'payment'], ['refunds', 'refund']] : [['days', 'attendance']];
    series.forEach(([key, css], offset) => {
      const height = 150 * row[key] / max; const px = x + offset * 22;
      const bar = svgNode('rect', { x: px, y: 175 - height, width: financial ? 19 : 30, height, class: css });
      bar.append(svgNode('title', {}, `${row.label}: ${key === 'days' ? row.days + ' zile' : money(row[key]) + ' lei'}`)); host.append(bar);
      if (!financial) host.append(svgNode('text', { x: px + 15, y: 169 - height, 'text-anchor': 'middle' }, String(row[key])));
    });
    host.append(svgNode('text', { x: x + 20, y: 198, 'text-anchor': 'middle' }, row.month));
  });
  host.prepend(svgNode('text', { x: 0, y: 16 }, financial ? `${money(max)} lei` : `${max} zile`));
}
async function loadChild(id) {
  const own = ++revision; childId = null;
  const data = await api(`/child/${id}`); if (own !== revision) return;
  childId = id; $('parent-child-name').textContent = data.name; $('parent-child-group').textContent = data.group;
  $('parent-balance').textContent = balance(data.balance); $('parent-days').textContent = String(data.days);
  $('parent-payments').textContent = money(data.payments) + ' lei'; $('parent-refunds').textContent = money(data.refunds) + ' lei';
  monthGrid?.destroy();
  const number = (key, title, formatter = money) => ({ key, title, valueType: ValueType.Number, formatter, align: 'right', width: 125, filter: false });
  monthGrid = new DataGrid($('parent-month-grid'), { rowKey: 'month', editable: false, footer: true, footerCaption: '{0} luni',
    autoSize: false, theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'modern',
    rows: data.months.map((row) => ({ ...row, caption: row.label + (row.closed ? '' : ' · În curs') })),
    columns: [{ key: 'caption', title: 'Luna', valueType: ValueType.Text, width: 205, filter: false },
      number('opening', 'Sold inițial', balance), number('days', 'Zile prezență', String), number('due', 'Sumă datorată'),
      number('payments', 'Plăți'), number('refunds', 'Restituiri'), number('closing', 'Sold final', balance)] });
  chart($('parent-attendance-chart'), data.months, false); chart($('parent-money-chart'), data.months, true);
}
$('parent-interval').addEventListener('change', () => { $('parent-date-fields').disabled = !$('parent-interval').checked; if (!$('parent-interval').checked) pickers.forEach((p) => p.close()); });
function statementUrl(output) {
  if (childId == null) throw new Error('Alegeți copilul.');
  const query = new URLSearchParams({ interval: $('parent-interval').checked ? '1' : '0' });
  if ($('parent-interval').checked) {
    pickers.forEach((p) => { p.commitText(); if (p.field.classList.contains('is-invalid')) throw new Error('Introduceți date valide.'); });
    const start = $('parent-start').value; const end = $('parent-end').value;
    if (!start || !end || start > end) throw new Error('Completați un interval valid.');
    query.set('start', start); query.set('end', end);
  }
  return `${base}/child/${childId}/statement/${output}?${query}`;
}
for (const output of ['print', 'pdf']) $(`parent-${output}`).addEventListener('click', () => {
  let url; try { url = statementUrl(output); } catch (error) { report(error); return; }
  // Reserve the window during the user gesture; populate it only after a successful response.
  const target = output === 'print' ? window.open('', '_blank') : null;
  if (target) target.opener = null;
  busy($('parent-date-fields').parentElement, async () => {
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) { const error = await response.json(); if (error.reason === 'SESSION') reset(); throw new Error(error.error); }
      if (output === 'print') { if (!target) throw new Error('Permiteți deschiderea ferestrei de imprimare.'); target.location.href = url; }
      else {
        const object = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href = object; a.download = 'Fisa_cont.pdf'; a.click(); setTimeout(() => URL.revokeObjectURL(object), 30000);
      }
    } catch (error) { target?.close(); throw error; }
  });
});
loadChildren().catch((error) => { reset(); if (!error.message.includes('Sesiunea')) report(error); });
addEventListener('pagehide', () => { combo?.destroy(); monthGrid?.destroy(); pickers.forEach((p) => p.destroy()); }, { once: true });
