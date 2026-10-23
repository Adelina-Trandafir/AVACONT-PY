// The same editable DataGrid used by NIR/BC; edits only change the association draft.
import { column, grid, rowMenu } from './ui.js';
import { closeMenu } from '../adechit/menu.js';
import { compareTyped } from '../utils/typed-order.js';
import { units, quantity } from './efactura-match.js';

const pairKey = link => `${link.nir_line}:${link.line}`;

export function associationGrid(host, { data, links, canEdit, changed, remove, error, editState }) {
  const rows = () => !data() ? [] : links().map(link => {
    const n = data().nir_lines.find(line => line.id === link.nir_line), e = data().lines.find(line => line.id === link.line);
    return { ...link, key: pairKey(link), nir_name: n.name, ef_name: e.name,
      nir_label: `${n.name} (${n.um})`, ef_label: `${e.name} (${e.um})`,
      reason: link.reason || 'Confirmată anterior' };
  }).sort((a, b) => compareTyped(a.nir_name, b.nir_name, 'text') || compareTyped(a.ef_name, b.ef_name, 'text')
    || compareTyped(a.nir_qty, b.nir_qty, 'number') || compareTyped(a.qty, b.qty, 'number'));

  function saveCell({ row, key, value }) {
    if (!canEdit()) throw new Error('Așteptați confirmarea operației în curs.');
    const link = links().find(item => pairKey(item) === row.key);
    if (!link || !['qty', 'nir_qty'].includes(key)) throw new Error('Asocierea nu mai există.');
    const next = units(value);
    if (next == null || next <= 0n) throw new Error('Introduceți o cantitate pozitivă, cu maximum 4 zecimale.');
    const side = key === 'qty' ? 'line' : 'nir_line';
    const source = (key === 'qty' ? data().lines : data().nir_lines).find(line => line.id === link[side]);
    const used = links().filter(other => other !== link && other[side] === link[side])
      .reduce((sum, other) => sum + (units(other[key]) ?? 0n), 0n);
    const limit = (units(source.left) ?? 0n) - used;
    if (next > limit) throw new Error(`Cantitatea depășește restul disponibil ${key === 'qty' ? 'în eFactura' : 'în NIR'} (${quantity(limit)}).`);
    link[key] = quantity(next); link.learn = true; changed();
    return { ...row, [key]: link[key], learn: true };
  }

  const instance = grid(host, {
    rowKey: 'key', rows: [], proportionalWidths: true, editable: true, singleClickEdit: true, footer: false,
    emptyText: 'Asociați rândurile prin drag and drop sau selectați câte un rând din fiecare listă.',
    editGuard: () => canEdit() ? '' : 'Așteptați confirmarea operației în curs.',
    columns: [
      column({ key: 'nir_label', label: 'Produs NIR' }, { width: 3, minWidth: 120, editable: false,
        sortValue: (_value, row) => row.nir_name, sortType: 'text' }),
      column({ key: 'ef_label', label: 'Produs eFactura' }, { width: 3, minWidth: 120, editable: false,
        sortValue: (_value, row) => row.ef_name, sortType: 'text' }),
      ...[['nir_qty', 'Cant. NIR'], ['qty', 'Cant. eFactura']].map(([key, label]) =>
        column({ key, label, type: 'decimal', required: true }, { width: 1, minWidth: 100, editable: () => canEdit() })),
      column({ key: 'reason', label: 'Propunere' }, { width: 2, minWidth: 100, editable: false }),
      { key: '_action', title: '⋮', width: 40, minWidth: 40, fixedWidth: true, editable: false,
        filter: false, sortable: false, display: 'button', actionText: '⋮', actionLabel: 'Opțiuni asociere' },
    ],
    onCellSave: saveCell, onCellSaved: () => changed(), onEditState: editState,
    onEditError: ({ error: failure }) => error(failure.message), onEditBlocked: ({ message }) => error(message),
    onCellAction: ({ row }) => rowMenu(instance, row, [{ icon: '×', label: 'Elimină asocierea', disabled: !canEdit(), run: async () => {
      if (instance.hasEdit && !await instance.commitEdit() || !canEdit()) return;
      remove(row.nir_line, row.line);
    } }]),
  });
  return { refresh: () => instance.setRows(rows(), { keepWidths: true }), commit: async () => !instance.hasEdit || await instance.commitEdit(),
    get hasEdit() { return instance.hasEdit; }, setBusy: value => instance.setBusy(value),
    destroy: () => { closeMenu(); instance.destroy(); } };
}
