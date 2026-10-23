// ADECHIT attendance pattern: a move button opens destinations; dragging highlights the target row.
import { el, column, grid } from './ui.js';
import { pickBox } from '../utils/confirm-box.js';
import { compareTyped } from '../utils/typed-order.js';
import { quantity } from './efactura-match.js';

export function sourceGrids(host, { data, available, canMove, commit, selected, pair, error }) {
  const listeners = new AbortController(), grids = {}, containers = {};
  const selection = { nir_line: null, line: null };
  let dragged = null, targetId = null, disposed = false;
  const rows = side => (data()?.[side === 'line' ? 'lines' : 'nir_lines'] || [])
    .filter(line => available(line, side) > 0n)
    .map(line => ({ ...line, left: quantity(available(line, side)) }))
    .sort((a, b) => compareTyped(a.name, b.name, 'text') || compareTyped(a.qty, b.qty, 'number'));
  const fail = failure => error(failure.message || 'Asocierea nu a putut fi făcută.');
  const clearTargets = () => {
    targetId = null;
    containers.line?.querySelectorAll('.is-drop-target').forEach(node => node.classList.remove('is-drop-target'));
  };
  function endDrag() { dragged = null; host.classList.remove('is-dragging-efi'); clearTargets(); }
  function choose(side, id) { selection[side] = id; selected(side, id); }

  async function pickDestination(row) {
    if (!canMove() || !await commit() || disposed) return;
    const options = rows('line').map(line => ({ value: line.id,
      label: `${line.name} (${line.um}) — rest ${line.left.replace('.', ',')}${line.cpv ? ' · CPV ' + line.cpv : ''}` }));
    if (!options.length) { error('Toate pozițiile eFactura sunt asociate integral.'); return; }
    const id = await pickBox('Asociază cu o poziție eFactura', options);
    if (id != null && !disposed && canMove()) await pair(row.id, Number(id), false);
  }

  for (const [title, side] of [['Produse din NIR', 'nir_line'], ['Poziții eFactura', 'line']]) {
    const container = el('div', { className: 'vc-efi__source-grid' }); containers[side] = container;
    host.append(el('section', {}, el('h3', { textContent: title }), container));
    grids[side] = grid(container, {
      rowKey: 'id', rows: [], proportionalWidths: true, footer: false, editable: false, dragRows: side === 'nir_line',
      emptyText: side === 'line' ? 'Nu mai sunt poziții eFactura cu rest de asociat.' : 'Nu mai sunt produse NIR cu rest de asociat.',
      columns: [
        ...(side === 'nir_line' ? [{ key: '_move', title: 'Mută', width: 40, minWidth: 40, fixedWidth: true,
          filter: false, sortable: false, display: 'button', actionText: '↕️', actionLabel: 'Asociază produsul cu o poziție eFactura' }] : []),
        column({ key: 'name', label: 'Denumire' }, { width: 3, minWidth: 110, editable: false }),
        column({ key: 'cpv', label: 'CPV' }, { width: 95, minWidth: 95, fixedWidth: true, editable: false }),
        column({ key: 'um', label: 'UM' }, { width: 55, minWidth: 55, fixedWidth: true, editable: false }),
        ...[['qty', 'Cantitate'], ['left', 'Rest']].map(([key, label]) => column({ key, label, type: 'decimal' },
          { width: 1, minWidth: 75, editable: false })),
      ],
      rowClassName: row => side === 'line' && row.id === targetId ? 'is-drop-target' : '',
      onSelect: row => choose(side, row.id),
      onCellAction: ({ row }) => { if (side === 'nir_line') void pickDestination(row).catch(fail); },
      onRowDragStart: ({ event }) => {
        const row = grids.nir_line.rowFromNode(event.target);
        if (!canMove() || !row || !data()?.nir_lines.some(line => line.id === row.id && available(line, 'nir_line') > 0n)) {
          event.preventDefault(); return;
        }
        dragged = row.id; choose('nir_line', row.id); host.classList.add('is-dragging-efi');
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('application/x-vercon-nir-line', String(row.id));
        event.dataTransfer.setData('text/plain', row.name);
      },
      onRowDragEnd: endDrag,
    });
    grids[side].sortBy('name', 'asc');
  }

  const destination = event => {
    if (disposed || dragged == null || !canMove()) return null;
    const row = grids.line.rowFromNode(event.target);
    const source = data()?.lines.find(line => line.id === row?.id);
    return source && available(source, 'line') > 0n ? { id: source.id, node: event.target.closest('.dgv__row') } : null;
  };
  containers.line.addEventListener('dragover', event => {
    const hit = destination(event); clearTargets();
    if (!hit) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; targetId = hit.id;
    hit.node.classList.add('is-drop-target');
  }, { signal: listeners.signal });
  containers.line.addEventListener('dragleave', event => {
    if (!containers.line.contains(event.relatedTarget)) clearTargets();
  }, { signal: listeners.signal });
  containers.line.addEventListener('drop', event => {
    const hit = destination(event), nid = dragged; endDrag();
    if (!hit) return;
    event.preventDefault(); void pair(nid, hit.id, true).catch(fail);
  }, { signal: listeners.signal });
  return {
    refresh() {
      endDrag();
      for (const side of ['nir_line', 'line']) {
        const source = rows(side), id = selection[side];
        grids[side].setRows(source, { keepWidths: true });
        if (source.some(row => row.id === id)) grids[side].selectRow(id);
        else if (id != null) choose(side, null);
      }
    },
    resetSelection() { for (const side of ['nir_line', 'line']) choose(side, null); },
    setBusy(value) { for (const instance of Object.values(grids)) instance.setBusy(value); if (value) endDrag(); },
    destroy() { disposed = true; endDrag(); listeners.abort(); for (const instance of Object.values(grids)) instance.destroy(); },
  };
}
