import { el } from './ui.js';
import { mountProductStock } from './product-stock.js';
import { mountCpvPicker } from './cpv-picker.js';

export function mountProducts(host, opts, mountCrud) {
  const root = el('div', { className: 'vc-nom-page vc-nom-page--products' });
  const main = el('div', { className: 'vc-nom-panel vc-products-main' });
  const stockHost = el('div', { className: 'vc-products-stock-host' });
  root.append(main, stockHost); host.replaceChildren(root);
  const stock = mountProductStock(stockHost, opts);
  let picker = null, disposed = false, opening = false;
  const products = mountCrud(main, { ...opts, kind: 'produse', title: 'Produse',
    columnOrder: ['IdMasina', 'DenumireP', 'UM', 'CPV'], searchKeys: ['DenumireP', 'CPV', 'IdMasina'], filterByLabel: true,
    hideSearch: true, productVisibility: true, visibility: 'used', pickerColumn: 'CPV',
    hideState: true, explicitAdd: true, rowSave: true,
    gridOptions: { proportionalWidths: false, layout: { fill: 'DenumireP' }, suspendRowLeave: () => opening || !!picker },
    columnOptions: (spec) => {
      const width = { IdMasina: 240, DenumireP: 180, UM: 100, CPV: 200 }[spec.key] ?? 120;
      return { width, minWidth: width, fixedWidth: spec.key !== 'DenumireP',
        ...(spec.key === 'CPV' ? { editable: false } : {}),
        filter: ['DenumireP', 'CPV', 'IdMasina'].includes(spec.key) };
    },
    onSelect: (row) => stock.load(row),
    onSaved: (row) => products.selected()?.id === row.id ? stock.load(row) : undefined,
    onCellAction: async ({ row, key }) => {
      if (key !== 'CPV' || picker || opening || disposed) return;
      opening = true;
      try {
        if (!await products.commitCell() || disposed) return;
        root.inert = true;
        picker = mountCpvPicker(host, { ...opts, name: row.DenumireP || 'Produs nou', current: row.CPV,
          onPick: (value) => products.setValue(row.__key, 'CPV', value),
          onClose: () => { picker = null; if (!disposed) root.inert = false; },
        });
      } finally { opening = false; }
    },
  });
  return {
    ready: products.load(),
    isDirty: () => products.isDirty(),
    search: (text) => products.search(text),
    destroy: () => { disposed = true; picker?.destroy(); products.destroy(); stock.destroy(); host.replaceChildren(); },
  };
}
