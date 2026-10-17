// VERCON v2: small adapters around the application's shared controls. Business state stays in the screen.
import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { Combobox } from '../components/combobox/combobox.js';
import { DatePicker } from '../components/datepicker/datepicker.js';
import { TextBox } from '../components/textbox/textbox.js';
import { CheckBox } from '../components/checkbox/checkbox.js';
import { openMenu, closeMenu } from '../adechit/menu.js';
import { showMessage } from '../portal/messages.js';
import { snapshotPdf } from './snapshot-pdf.js';
import { parse, format } from './fields.js';

export { DataGrid, ValueType, TreeView, Combobox, DatePicker, TextBox, CheckBox, openMenu, closeMenu, showMessage };
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('aria-') || key.startsWith('data-') || key === 'role') node.setAttribute(key, value);
    else node[key] = value;
  }
  node.append(...children.filter((child) => child != null));
  return node;
}
export function scope() {
  const resources = new Set();
  return {
    own(resource) { resources.add(resource); return resource; },
    clear() { for (const item of resources) { if (typeof item === 'function') item(); else item.destroy?.(); } resources.clear(); },
    async commit() {
      for (const item of [...resources]) {
        if (item.hasEdit && !await item.commitEdit()) return false;
        if (item instanceof DatePicker && !item.field.disabled) { item.commitText(); if (item.field.classList.contains('is-invalid')) { item.field.focus(); return false; } }
      }
      return true;
    },
    // The validation event: every owned control that can judge itself does so (a TextBox goes into the error state, a list / date field
    // and a grid mark their empty required cells). True when nothing was wrong.
    validate() { let ok = true; for (const item of [...resources]) { if (typeof item.validate !== 'function') continue; const result = item.validate(); if (result && result.ok === false) ok = false; } return ok; },
    dirty() { return [...resources].some((item) => item.hasEdit || item._pendingSelection || item instanceof DatePicker && !item.field.disabled && item.field.value !== (item.value ? format(item.iso, { type: 'date' }) : '')); },
    busy(on) { for (const item of resources) item.setBusy?.(on); },
  };
}
export function field(column, value, editable, onChange, owner) {
  const label = el('label', { className: 'vc-field', 'data-field': column.key }, el('span', {}, column.label + (column.required && editable ? ' *' : '')));
  let source, box = null, required = !!column.required, touched = false;
  // An error is NEVER written on the screen (text under a field would move the controls around). It is the control's error colour and its
  // tooltip. `error.textContent = message` keeps working for the code that pushes a message (the server, a rule of the screen): the message
  // becomes the tooltip, '' clears it.
  let remote = '';
  const error = { get textContent() { return remote; }, set textContent(text) { remote = text || ''; if (box) { if (remote) box.setError(remote); else box.clearError(); } else markRequired(); } };
  // A list / date field shows "required and empty" only once the operator left it empty, or on the validation event (scope().validate());
  // a text field is a TextBox, which judges itself.
  const blank = () => source.value == null || String(source.value).trim() === '';
  const markRequired = () => {
    if (box) return;
    const empty = editable && required && touched && blank();
    label.classList.toggle('is-required-empty', !!remote || empty);
    tip(remote || (empty ? 'Campul este obligatoriu.' : ''));
  };
  const touch = () => { touched = true; markRequired(); };
  // The visible control of a list / date field shows the reason as its tooltip while it is marked; a tooltip it had comes back afterwards.
  let tipTarget = null, tipOriginal = null;
  const tip = (message) => {
    if (!tipTarget) return;
    if (message) { if (tipOriginal === null) tipOriginal = tipTarget.title; tipTarget.title = message; }
    else if (tipOriginal !== null) { if (tipOriginal) tipTarget.title = tipOriginal; else tipTarget.removeAttribute('title'); tipOriginal = null; }
  };
  if (column.type === 'ref' || column.type === 'select') {
    source = el('input', { type: 'hidden', value: value == null ? '' : String(value) });
    const host = el('div', { className: 'vc-field__combo' }); label.append(host, source);
    const items = (column.options || []).map((item) => ({ value: String(item.value ?? item.id), label: item.label }));
    const combo = owner.own(new Combobox(host, { placeholder: column.placeholder || 'Alegeți…', dropDownStyle: column.dropDownStyle, dropDownOnEnter: column.dropDownOnEnter, maxResults: column.maxResults, dropDownMinWidth: column.dropDownMinWidth, showArrow: column.showArrow,
      onSearch: async (query) => items.filter((item) => item.label.toLocaleLowerCase('ro').includes(query.toLocaleLowerCase('ro'))),
      onSelect: (id) => { combo._pendingSelection = false; source.value = id; source.dispatchEvent(new Event('change', { bubbles: true })); } }));
    combo.setValue(source.value, items.find((item) => item.value === source.value)?.label || column.fallback || ''); combo.setEnabled(editable);
    combo.input.setAttribute('aria-label', column.label); tipTarget = combo.input; source._combo = combo;
    combo.input.addEventListener('input', () => { source.value = ''; combo._pendingSelection = true; error.textContent = ''; markRequired(); });
    combo.input.addEventListener('blur', () => setTimeout(touch, 200));   // after the click that picks an option has landed
  } else if (column.type === 'date') {
    source = el('input', { type: 'date', value: value || '', readOnly: !editable, autocomplete: 'off', 'aria-label': column.label });
    label.append(source);
    const picker = owner.own(new DatePicker(source)); picker.field.disabled = picker.button.disabled = !editable;
    source._picker = picker; tipTarget = picker.field;
    picker.field.addEventListener('blur', () => setTimeout(touch, 200));
  } else {
    box = owner.own(new TextBox(label, { type: column.displayFormat === 'standard' && column.type === 'decimal' ? 'money' : ['int', 'decimal', 'money', 'search'].includes(column.type) ? column.type : 'text', value, required, readOnly: !editable,
      textAlign: column.textAlign || 'left', numberLocale: column.numberLocale, maxLength: column.type === 'text' || !column.type ? column.max : undefined, min: column.min, ariaLabel: column.label, onLeave: column.onLeave, allowCopyPaste: column.allowCopyPaste }));
    source = box.input; source._box = box;
  }
  if (!box) owner.own({ validate: () => { touch(); return { ok: !(editable && required && blank()) && !remote }; }, destroy() {} });
  source._clearValue = () => { if (box) box.setValue(''); else if (source._combo) { source.value = ''; source._combo.setValue('', ''); error.textContent = ''; } };
  source._setRequired = (on) => { required = on; label.firstElementChild.textContent = column.label + (required && editable ? ' *' : ''); if (box) box.setRequired(on); else markRequired(); };
  const changeValue = () => column.numberLocale && box ? box.getValue() : source.value;
  source.addEventListener('change', () => { if (!box) error.textContent = ''; markRequired(); if (editable) onChange?.(changeValue(), source); });
  if (column.type !== 'date' && column.type !== 'ref' && column.type !== 'select') source.addEventListener('input', () => { if (editable) onChange?.(changeValue(), source); });
  markRequired();
  return { node: label, input: source, error };
}
export function column(spec, extras = {}) {
  const type = spec.type || 'text';
  return { key: spec.key, title: spec.label, required: !!spec.required, valueType: type === 'date' ? ValueType.DateTime : ['decimal', 'int'].includes(type) ? ValueType.Number : ValueType.Text,
    width: type === 'text' ? 180 : 120, editable: true, editor: type === 'date' ? 'date' : ['decimal', 'int'].includes(type) ? 'number' : type === 'ref' ? 'list' : 'text',
    formatter: (value) => format(value, { type }),
    parse: (raw) => { if (type === 'ref') return raw == null || raw === '' ? null : raw; const result = parse(raw, { ...spec, type }); if (!result.ok) throw new Error(result.message); return result.value; },
    align: ['decimal', 'int'].includes(type) ? 'right' : 'left', ...extras };
}
export function grid(host, options, owner) {
  host.classList.add('vc-dgv');
  const instance = new DataGrid(host, { rowHeight: 30, headerHeight: 30, autoSize: false, enableGrouping: false,
    theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'modern', ...options,
    columns: options.columns.map((item) => ({ width: 120, ...item })) });
  owner?.own(instance); return instance;
}
export function paintTree(host, nodes, { selected, onSelect, onAction, onToggle, exclusiveRoots = false, title = 'Documente salvate', checkable = false, onCheckChange, branchChecks = false, showSearchBox = true, collapsed = false } = {}, owner) {
  host.replaceChildren();
  const body = el('div', { className: 'vc-tree-body' }); host.append(el('h4', { textContent: title }), body);
  const tree = new TreeView(body, { inline: true, autoCollapse: false, autoResize: false, indent: 14, checkable, onCheckChange, branchChecks, showSearchBox, onToggle, exclusiveRoots,
    onSelect: (selection) => { if (String(selection.id).startsWith('doc:')) onSelect?.(String(selection.id).slice(4)); } });
  owner?.own(() => { if (collapsed) host._vcExpanded = new Set(tree.expandedNodes); tree.destroy(); });
  tree.setData(nodes);
  if (collapsed) tree.expandedNodes = new Set(host._vcExpanded || []); else tree.expandAllNodes(nodes);
  tree.treeContainer.replaceChildren(); tree.isTreeRendered = false;
  if (selected != null) tree.selectedValue = `doc:${selected}`;
  if (nodes.length) tree.renderTree(''); else tree.renderNoResults();
  const action = (event) => {
    const target = event.target.closest('[data-action]'); if (!target || !body.contains(target)) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation(); onAction?.(target.dataset.nodeId.slice(4), target);
  };
  body.addEventListener('click', action, true); body.addEventListener('keydown', action, true);
  return tree;
}
// Draws new data into a tree already on screen (a lazy branch just arrived): open nodes and scroll position stay.
export function refreshTree(tree, nodes, selected) {
  const scroller = tree.treeContainer.closest('.treeview-dropdown') || tree.treeContainer, top = scroller.scrollTop;
  const open = new Set(tree.expandedNodes), chosen = tree.selectedValue;
  tree.setData(nodes);
  tree.expandedNodes = open;   // setData ends in clearLocalSearch(), which closes every node: put the open ones back
  tree.treeContainer.replaceChildren(); tree.isTreeRendered = false;
  tree.selectedValue = selected != null ? `doc:${selected}` : chosen;
  if (nodes.length) tree.renderTree(''); else tree.renderNoResults();
  scroller.scrollTop = top;
}
// Moves the "selected" mark to another document without drawing the tree again (no flicker, scroll and open branches untouched).
export function markSelected(tree, selected) {
  tree.selectedValue = selected != null ? `doc:${selected}` : null;
  for (const item of tree.treeContainer.querySelectorAll('.treeview-item')) item.classList.toggle('selected', item.dataset.value === tree.selectedValue);
}
export const docNode =(row, label, rightText) => ({ id: `doc:${row.id}`, label, rightText, error: !!row.annulled, rightIcon: { icon: '⋮', action: 'options', title: 'Opțiuni document' } });
export function groupedNodes(rows, groups, leaf) {
  const walk = (items, level, prefix) => {
    if (level === groups.length) return items.map((row) => docNode(row, leaf(row)));
    const grouped = new Map();
    for (const row of items) { const label = String(groups[level](row) || '—'); if (!grouped.has(label)) grouped.set(label, []); grouped.get(label).push(row); }
    return [...grouped].map(([label, children], index) => ({ id: `group:${prefix}:${index}`, label, children: walk(children, level + 1, `${prefix}:${index}`) }));
  }; return walk(rows, 0, 'root');
}
export function rowMenu(gridInstance, row, entries) {
  const index = gridInstance._items.findIndex((item) => item.kind === 'row' && item.row[gridInstance._opts.rowKey] === row[gridInstance._opts.rowKey]);
  const anchor = gridInstance._root.querySelector('.dgv__row[data-i="' + index + '"] .dgv__cell-action') || gridInstance._root;
  openMenu(anchor, entries, { label: 'Opțiuni rând', align: 'right' });
}
export function modal(title, owner) {
  const dialog = el('dialog', { className: 'confirm-box vc-modal', 'aria-label': title });
  const body = el('div', { className: 'vc-modal__body' }), footer = el('footer');
  const local = scope(); let closed = false;
  const close = () => { if (closed) return; closed = true; local.clear(); closeMenu(); dialog.close(); dialog.remove(); };
  dialog.append(el('header', {}, el('h2', { textContent: title })), body, footer);
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  owner?.own(close); document.body.append(dialog); dialog.showModal();
  return { dialog, body, footer, close, scope: local, get closed() { return closed; } };
}
export function preview(title, sections, owner) {
  const view = modal(title, owner);
  for (const section of sections) {
    view.body.append(el('h4', { textContent: section.title }));
    const host = el('div'); view.body.append(host); host.style.height = Math.min(280, Math.max(92, 32 + section.rows.length * 30 + 16)) + 'px'; grid(host, { columns: section.columns, rows: section.rows, footer: false }, view.scope);
  }
  const close = el('button', { type: 'button', textContent: 'Închide' }); close.addEventListener('click', view.close);
  const pdf = el('button', { type: 'button', textContent: 'Previzualizare PDF' });
  pdf.addEventListener('click', async () => {
    pdf.disabled = true;
    try { const data = await snapshotPdf(title, sections); if (!view.closed) await pdfPreview(title, data, view.scope); }
    catch (error) { if (!view.closed) { const message = el('p'); view.body.append(message); showMessage(message, error.message || 'PDF-ul nu a putut fi pregătit.', 'error'); } }
    finally { pdf.disabled = false; }
  });
  view.footer.append(close, pdf);
  return view;
}
// Reports that return PDF use the same PDF.js viewer as Portal; no endpoint is invented by the UI.
export async function snapshotPreview(title, sections, owner, isCurrent = () => true) {
  const data = await snapshotPdf(title, sections);
  if (isCurrent()) return pdfPreview(title, data, owner, isCurrent);
}

export async function pdfPreview(title, data, owner, isCurrent = () => true) {
  const { openPdfReport } = await import('./pdf-preview.js');
  if (isCurrent()) return openPdfReport(title, data, owner);
}
