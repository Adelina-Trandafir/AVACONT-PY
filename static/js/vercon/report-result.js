import { el, grid, column } from './ui.js';

export function paintReportLetterhead(model, target) {
  const head = model.identity.letterhead;
  target.append(el('div', { className: 'vc-report-letterhead' },
    el('div', {}, ...head.left.map(text => el('p', { textContent: text }))),
    el('p', { className: 'vc-report-letterhead__subunit', textContent: head.right })));
}

export function paintReportBlock(block, target, owner) {
  if (block.t === 'form_grid') {
    const table = el('table', { className: 'vc-voucher-header' },
      el('colgroup', {}, ...block.widths.map(() => el('col'))));
    const body = el('tbody'); table.append(body);
    block.heights.forEach((_, row) => {
      body.append(el('tr', {}, ...block.cells.filter(cell => cell.row === row)
        .sort((a, b) => a.column - b.column).map(cell => el(cell.bold ? 'th' : 'td', {
          textContent: cell.text, colSpan: cell.colspan || 1, rowSpan: cell.rowspan || 1,
          className: cell.column === 7 ? 'vc-voucher-header__title' : '',
        }))));
    });
    target.append(table);
  } else if (block.t === 'positioned') {
    block.elements.forEach(item => target.append(el('p', { textContent: item.text })));
  } else if (block.t === 'columns') {
    const row = el('div', { className: `vc-reports__columns vc-reports__columns--${block.children.length}` });
    for (const child of block.children) { const cell = el('div'); child.forEach(part => paintReportBlock(part, cell, owner)); row.append(cell); }
    target.append(row);
    if (block.footer) {
      target.append(el('div', { className: 'vc-reports__table-footer' },
        el('span', { textContent: block.footer.label }), el('span', { textContent: block.footer.value })));
    }
  } else if (block.t === 'table') {
    const panel = el('div', { className: 'vc-reports__table' + (block.show_column_header === false ? ' vc-reports__table--no-header' : '') }); target.append(panel);
    const rows = block.rows.map((row, index) => ({ id: index, ...Object.fromEntries(row.cells.map((value, i) => [`c${i}`, typeof value === 'object' && value ? value.t : value])) }));
    grid(panel, { rowKey: 'id', rows, footer: false, proportionalWidths: true, defaultSort: false,
      ...(block.show_column_header === false ? { headerHeight: 0 } : {}),
      columns: block.cols.map((c, i) => column({ key: `c${i}`, label: c.label }, {
        width: c.w, editable: false, align: c.align === 'r' ? 'right' : 'left',
        sortType: ['decimal', 'int'].includes(c.type) ? 'number' : c.type === 'date' ? 'datetime' : 'text',
        sortValue: (value, row) => block.raw?.[row.id]?.[c.key] ?? value,
      })) }, owner);
  } else if (block.t === 'box') {
    const list = el('dl', { className: 'vc-reports__metadata' });
    block.rows.forEach(([label, value]) => list.append(el('dt', { textContent: label }), el('dd', { textContent: String(value ?? '') }))); target.append(list);
  } else if (['text', 'group', 'title'].includes(block.t)) {
    target.append(el(block.t === 'title' ? 'h3' : block.t === 'group' ? 'h4' : 'p', {
      textContent: block.text, className: block.align === 'c' ? 'vc-reports__centered' : ''
    }));
  } else if (block.t === 'sign') {
    if (block.box_width_ratio === .5 && block.heads.length === 2) {
      target.append(el('div', { className: 'vc-reports__signatures vc-reports__signatures--separated' },
        ...block.heads.map(text => el('span', { textContent: text }))));
    } else target.append(el('p', { className: 'vc-reports__signatures', textContent: block.heads.join('     |     ') }));
  } else if (block.t === 'break') target.append(el('hr'));
}

function buildReportNodes(reports) {
  if (reports.some(report => report.tree_group)) {
    const warehouse = reports.filter(report => !report.tree_group);
    const custody = reports.filter(report => report.tree_group);
    const children = [...new Set(custody.map(report => report.tree_group))].map(group => ({
      id: 'reports:' + group, label: group,
      children: custody.filter(report => report.tree_group === group)
        .map(report => ({ id: 'doc:' + report.id, label: report.label })),
    }));
    return [...(warehouse.length ? buildReportNodes(warehouse) : []),
      { id: 'oi-reports', label: 'Obiecte de inventar', children }];
  }
  reports = reports.filter(report => !['OI_Predare', 'OI_Transfer', 'OI_Casare', 'FM1-u', 'FM2', 'Fisa_Magazie_BF',
    'NIR_SFP', 'NIR_Storno', 'Centralizator_Conturi_Furnizori', 'BUConsum', 'BUConsum_F', 'BUConsum_F1', 'BonRetur',
    'MF_Balanta_Subinv'].includes(report.id));
  const mf = reports.some(report => report.types.length === 1 && report.types[0] === 'MF');
  const oi = reports.filter(report => report.ledger === 'custody' || ['bp', 'bt', 'bs'].includes(report.family) || report.kind === 'depreciation');
  if (oi.length) {
    const nodes = reports.filter(report => !oi.includes(report));
    const mfDocuments = ['MF_Bon_BP', 'MF_Bon_BT', 'MF_Bon_BS'];
    const children = mf
      ? [...mfDocuments.flatMap(id => oi.filter(report => report.id === id)),
        ...oi.filter(report => !mfDocuments.includes(report.id))]
        .map(report => ({ id: 'doc:' + report.id, label: report.label }))
      : [...new Set(oi.map(report => report.group))].map((group, index) => ({
        id: `oi-reports:${index}`, label: group,
        children: oi.filter(report => report.group === group).map(report => ({ id: 'doc:' + report.id, label: report.label })),
      }));
    return [...(nodes.length ? buildReportNodes(nodes) : []), { id: 'oi-reports', label: mf ? 'Mijloace fixe' : 'Obiecte de inventar în folosință',
      children }];
  }
  const groups = new Map();
  for (const report of reports.filter(item => !item.id.endsWith('subreport') && item.id !== 'BC subreport 2013')) {
    const group = report.kind === 'food' ? 'Liste de alimente'
      : report.family === 'bc' || report.family === 'br' || report.kind.startsWith('consumption') || report.id === 'Centralizator_BC' ? 'Ieșiri'
        : report.family === 'nir' || report.family === 'storno' || ['receipts_summary', 'suppliers'].includes(report.kind)
          || report.kind === 'purchases' && ['Nota_Comanda', 'Referat_periodic_Intrari'].includes(report.id) ? 'Intrări' : 'Produse';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ id: 'doc:' + report.id, label: report.label });
  }
  const products = groups.get('Produse');
  if (products) products.sort((first, second) => Number(second.id === 'doc:Fisa_Magazie') - Number(first.id === 'doc:Fisa_Magazie'));
  return [{ id: 'warehouse', label: 'Magazie', children: ['Produse', 'Intrări', 'Ieșiri', 'Liste de alimente']
    .filter(group => groups.has(group)).map(group => ({ id: 'reports:' + group, label: group, children: groups.get(group) })) }];
}

export function reportNodes(reports) {
  const uppercase = nodes => nodes.map(node => ({ ...node, label: node.label.toLocaleUpperCase('ro-RO'),
    ...(node.children ? { children: uppercase(node.children) } : {}) }));
  return uppercase(buildReportNodes(reports));
}
