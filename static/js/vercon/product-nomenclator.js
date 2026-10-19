import { el } from './ui.js';
import { mountProductStock } from './product-stock.js';
import { mountCpvPicker } from './cpv-picker.js';

export function mountProducts(host, opts, mountCrud) {
  const mf = opts.sel().type === 'MF';
  const root = el('div', { className: 'vc-nom-page vc-nom-page--products' + (mf ? ' vc-nom-page--mf-products' : '') });
  const main = el('div', { className: 'vc-nom-panel vc-products-main' });
  const stockHost = el('div', { className: 'vc-products-stock-host' });
  root.append(main); if (!mf) root.append(stockHost); host.replaceChildren(root);
  const stock = mf ? null : mountProductStock(stockHost, opts);
  let picker = null, disposed = false, opening = false;
  const products = mountCrud(main, { ...opts, kind: 'produse', title: 'Produse',
    columnOrder: ['IdMasina', 'DenumireP', 'UM', 'CPV'], searchKeys: ['DenumireP', 'CPV', 'IdMasina'], filterByLabel: true,
    hideSearch: true, productVisibility: true, visibility: 'used', pickerColumns: mf ? ['CPV', 'CC'] : ['CPV'],
    hideState: true, explicitAdd: true, rowSave: true,
    gridOptions: { proportionalWidths: false, layout: { fill: 'DenumireP' }, suspendRowLeave: () => opening || !!picker },
    columnOptions: (spec) => {
      const width = { IdMasina: 240, DenumireP: 180, UM: 100, CPV: 200, CC: 180, DNF: 150 }[spec.key] ?? 120;
      return { width, minWidth: width, fixedWidth: spec.key !== 'DenumireP',
        ...(['CPV', 'CC'].includes(spec.key) ? { editable: false } : {}),
        filter: ['DenumireP', 'CPV', 'IdMasina'].includes(spec.key) };
    },
    onSelect: (row) => stock?.load(row),
    onSaved: (row) => products.selected()?.id === row.id ? stock?.load(row) : undefined,
    onCellAction: async ({ row, key }) => {
      if (!['CPV', ...(mf ? ['CC'] : [])].includes(key) || picker || opening || disposed) return;
      opening = true;
      try {
        if (!await products.commitCell() || disposed) return;
        root.inert = true;
        picker = mountCpvPicker(host, { ...opts, name: row.DenumireP || 'Produs nou', current: row[key],
          ...(key === 'CC' ? { catalogName: 'Clasificare MF', catalogEndpoint: '/api/vercon/mf/classifications' } : {}),
          onPick: (value) => products.setValue(row.__key, key, value),
          onClose: () => { picker = null; if (!disposed) root.inert = false; },
        });
      } finally { opening = false; }
    },
  });
  return {
    ready: products.load(),
    isDirty: () => products.isDirty(),
    search: (text) => products.search(text),
    destroy: () => { disposed = true; picker?.destroy(); products.destroy(); stock?.destroy(); host.replaceChildren(); },
  };
}
