import { DataGrid, ValueType } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { Combobox } from '../components/combobox/combobox.js';
import { showMessage } from '../portal/messages.js';

export function bindAnnual({ api, refresh, message }) {
  const $ = (id) => document.getElementById(id);
  const dialog = $('ade-annual'), save = $('ade-annual-save'), cancel = $('ade-annual-cancel');
  const mobile = matchMedia('(max-width: 980px)');
  let plan, groups = [], children = [], currentId, grid, educatorGrid, tree, targetCombo;
  let busy = false, retry = null, nextId = -1, dragIds = [], targetId = null;
  const say = (text) => showMessage($('ade-annual-message'), text, 'info');
  const fail = (error) => { console.error('[ade.annual]', error); showMessage($('ade-annual-message'), error.message, 'error'); };
  const current = () => groups.find((group) => group.id === currentId);
  const selected = () => grid?.getSelectedRows() || [];
  function selectionChanged() { $('ade-annual-selected').textContent = `${selected().length} copii selectați`; $('ade-annual-move').disabled = busy || !selected().length; }
  function renderTree() {
    groups.sort((a, b) => a.name.localeCompare(b.name, 'ro', { sensitivity: 'base' }));
    tree.setData(groups.map((group) => ({ id: String(group.id), label: `${group.name || 'Grupă nouă'}${group.id < 0 ? ' · nouă' : ''} (${children.filter((child) => child.group_id === group.id && !child.departed).length})` })));
    tree.isTreeRendered = false; tree.renderTree('');
    targetCombo.options.staticData = groups.map((group) => ({ value: String(group.id), label: group.name || 'Grupă nouă' }));
    const target = groups.find((group) => group.id === targetId);
    if (target) targetCombo.setValue(String(target.id), target.name || 'Grupă nouă'); else targetCombo.clear();
  }
  function renderChildren() {
    const rows = children.filter((child) => child.group_id === currentId);
    if (grid) grid.setRows(rows, { keepWidths: true });
    else grid = new DataGrid($('ade-annual-grid'), { columns: [
      { key: 'Selected', title: 'Selectat', selectionCheckbox: true, width: 70, filter: false, sortable: false },
      { key: 'Nume', title: 'Copil', valueType: ValueType.Text, width: 230, filter: false },
      { key: 'OriginalGroup', title: 'Grupa din august', valueType: ValueType.Text, width: 180, filter: false },
      { key: 'departed', title: 'Plecat', valueType: ValueType.Boolean, display: 'checkbox', editor: 'checkbox', editable: true, width: 70, filter: false },
    ], rows, rowKey: 'IDP', headerHeight: 32, editable: true, multiSelect: true, dragRows: true, enableGrouping: false,
    footer: true, footerCaption: '{0} copii', layout: { fill: 'Nume' },
    onSelectionChange: selectionChanged,
    onCellSave: async ({ row, value }) => { if (busy) throw new Error('Închiderea este în curs.'); row.departed = value; return row; },
    onCellSaved: renderTree,
    onRowDragStart: ({ event, rows: picked }) => {
      if (busy) { event.preventDefault(); return; }
      dragIds = picked.map((row) => row.IDP);
      event.dataTransfer.setData('application/x-ade-children', JSON.stringify(dragIds)); event.dataTransfer.effectAllowed = 'move';
    }, onRowDragEnd: () => { dragIds = []; clearDropTargets(); } });
    grid.sortBy('Nume', 'asc'); selectionChanged();
  }
  function renderEducators() {
    educatorGrid?.destroy();
    educatorGrid = new DataGrid($('ade-annual-educator-grid'), { headerHeight: 32, columns: [
      { key: 'name', title: 'Educator pentru noul an', valueType: ValueType.Text, editable: true, editor: 'text', width: 260,
        validate: (value) => typeof value === 'string' && value.trim() && value.length <= 50 ? '' : 'Completați numele (maximum 50 de caractere).' },
      { key: 'start', title: 'Începând cu', valueType: ValueType.Text, width: 140, filter: false },
      { key: 'remove', title: 'Elimină', display: 'button', actionText: 'Șterge', width: 75, sortable: false, filter: false },
    ], rows: current().educators, rowKey: 'id', editable: true, enableGrouping: false, layout: { fill: 'name' },
    onCellSave: async ({ row, value }) => { if (busy) throw new Error('Închiderea este în curs.'); row.name = value; return row; },
    onCellAction: async ({ row }) => {
      if (busy || !await commit()) return;
      current().educators = current().educators.filter((item) => item.id !== row.id); renderEducators();
    }, onEditError: ({ error }) => fail(error) });
    educatorGrid.sortBy('name', 'asc');
  }
  async function commit() {
    if (grid?.hasEdit && !await grid.commitEdit()) return false;
    if (educatorGrid?.hasEdit && !await educatorGrid.commitEdit()) return false;
    return true;
  }
  async function showGroup(id) {
    if (busy || !await commit()) return;
    currentId = id;
    $('ade-annual-name').value = current().name;
    $('ade-annual-group-title').textContent = current().name || 'Grupă nouă';
    $('ade-annual-delete-group').hidden = id >= 0;
    renderChildren(); renderEducators();
  }
  function clearDropTargets() { $('ade-annual-tree').querySelectorAll('.is-drop-target').forEach((node) => node.classList.remove('is-drop-target')); }
  async function move(target, ids = selected().map((row) => row.IDP)) {
    if (busy || !groups.some((group) => group.id === target) || !await commit()) return;
    children.filter((child) => ids.includes(child.IDP)).forEach((child) => { child.group_id = target; });
    renderTree(); renderChildren(); say(`${ids.length} copii alocați grupei „${groups.find((group) => group.id === target).name || 'Grupă nouă'}”. Modificările așteaptă închiderea anului.`);
  }
  const treeHost = $('ade-annual-tree');
  treeHost.addEventListener('dragover', (event) => {
    const node = event.target.closest('[data-value]');
    if (busy || !dragIds.length || !node || !groups.some((group) => String(group.id) === node.dataset.value)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; clearDropTargets(); node.classList.add('is-drop-target');
  });
  treeHost.addEventListener('dragleave', (event) => event.target.closest('[data-value]')?.classList.remove('is-drop-target'));
  treeHost.addEventListener('drop', (event) => {
    const node = event.target.closest('[data-value]'); if (!node || !dragIds.length || busy) return;
    event.preventDefault(); move(Number(node.dataset.value), [...dragIds]).catch(fail); dragIds = []; clearDropTargets();
  });
  $('ade-annual-move').addEventListener('click', () => move(targetId).catch(fail));
  $('ade-annual-name').addEventListener('input', (event) => { current().name = event.target.value; $('ade-annual-group-title').textContent = event.target.value || 'Grupă nouă'; renderTree(); });
  $('ade-annual-add-educator').addEventListener('click', async () => {
    if (busy || !await commit()) return;
    const row = { id: nextId--, name: '', start: `01.09.${plan.month.Anul}` };
    current().educators.push(row); renderEducators(); educatorGrid.beginEdit(row, 'name');
  });
  $('ade-annual-add-group').addEventListener('click', async () => {
    if (busy || !await commit()) return;
    const group = { id: nextId--, name: '', educators: [] }; groups.push(group); renderTree(); await showGroup(group.id); $('ade-annual-name').focus();
  });
  $('ade-annual-delete-group').addEventListener('click', async () => {
    if (busy || currentId >= 0) return;
    if (children.some((child) => child.group_id === currentId)) { say('Mutați copiii înainte de a elimina grupa nouă.'); return; }
    educatorGrid?.cancelEdit(); groups = groups.filter((group) => group.id !== currentId); renderTree(); await showGroup(groups[0].id);
  });
  cancel.addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', (event) => { if (busy) event.preventDefault(); });
  mobile.addEventListener('change', () => { if (mobile.matches && dialog.open && !busy) dialog.close(); });
  $('ade-annual-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (busy || !plan || mobile.matches || !await commit()) return;
    for (const group of groups) {
      if (!group.name.trim() || group.educators.some((row) => !row.name.trim())) { await showGroup(group.id); say('Completați numele grupei și numele educatorilor.'); return; }
      if (group.id < 0 && (groups.filter((other) => other.name.trim().toLocaleLowerCase('ro') === group.name.trim().toLocaleLowerCase('ro')).length !== 1
        || !group.educators.length || !children.some((child) => child.group_id === group.id && !child.departed))) {
        await showGroup(group.id); say('Grupa nouă trebuie să aibă nume unic, cel puțin un educator și cel puțin un copil care rămâne.'); return;
      }
    }
    const payload = (group) => ({ id: group.id, version: group.version, name: group.name.trim(), educators: group.educators.map((row) => row.name.trim()) });
    const body = JSON.stringify({ id: plan.month.IDL, annual: { version: plan.month.Version,
      groups: groups.filter((group) => group.id >= 0).map(payload), new_groups: groups.filter((group) => group.id < 0).map(payload),
      children: children.map((child) => ({ id: child.IDP, version: child.Version, departed: child.departed, group_id: child.group_id })),
      history: plan.history.map((row) => ({ id: row.IDGE, version: row.Version })) } });
    if (retry?.body !== body) retry = { body, key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}` };
    busy = true; $('ade-annual-workspace').disabled = true; save.disabled = true; cancel.disabled = true;
    say('Se închide anul și se deschide septembrie…');
    try {
      await api('/api/adechit/close', { method: 'POST', body, key: retry.key }); dialog.close();
      try { await refresh(); message('August a fost închis și septembrie a fost deschis cu noua organizare.'); }
      catch (error) { console.error('[ade.annual.refresh]', error); message(`Închiderea a fost salvată. Reîncărcați pagina: ${error.message}`); }
    } catch (error) { fail(error); }
    finally { busy = false; $('ade-annual-workspace').disabled = false; save.disabled = false; cancel.disabled = false; selectionChanged(); }
  });
  return async (monthId) => {
    if (mobile.matches) throw new Error('Închiderea anuală se face pe desktop.');
    if (busy || dialog.open) return;
    const data = await api(`/api/adechit/annual/${monthId}`);
    if (mobile.matches || dialog.open) return;
    grid?.destroy(); grid = null; educatorGrid?.destroy(); educatorGrid = null; tree?.destroy?.(); targetCombo?.destroy?.();
    plan = data; nextId = -1; retry = null; targetId = null; $('ade-annual-message').hidden = true;
    groups = plan.groups.map((group) => ({ id: group.IDG, version: group.Version, name: group.Grupa,
      educators: group.educators.map((name) => ({ id: nextId--, name, start: `01.09.${plan.month.Anul}` })) }));
    children = plan.children.map((child) => ({ ...child, group_id: child.IDG, departed: false, OriginalGroup: groups.find((group) => group.id === child.IDG).name }));
    $('ade-annual-title').textContent = `Închidere anuală · august ${plan.month.Anul}`;
    targetCombo = new Combobox($('ade-annual-target'), { readonly: true, placeholder: 'Grupa destinație', staticData: [], onSelect: (id) => { targetId = Number(id); } });
    tree = new TreeView(treeHost, { inline: true, showSearchBox: false, onSelect: ({ id }) => showGroup(Number(id)).catch(fail) });
    renderTree(); dialog.showModal();
    if (groups.length) await showGroup(groups[0].id);
  };
}
