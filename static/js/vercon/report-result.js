import { el, grid, column } from './ui.js';

export function paintReportBlock(block, target, owner) {
  if (block.t === 'positioned') {
    block.elements.forEach(item => target.append(el('p', { textContent: item.text })));
  } else if (block.t === 'columns') {
    const row = el('div', { className: `vc-reports__columns vc-reports__columns--${block.children.length}` });
    for (const child of block.children) { const cell = el('div'); child.forEach(part => paintReportBlock(part, cell, owner)); row.append(cell); }
    target.append(row);
  } else if (block.t === 'table') {
    const panel = el('div', { className: 'vc-reports__table' }); target.append(panel);
    const rows = block.rows.map((row, index) => ({ id: index, ...Object.fromEntries(row.cells.map((value, i) => [`c${i}`, typeof value === 'object' && value ? value.t : value])) }));
    grid(panel, { rowKey: 'id', rows, footer: false, proportionalWidths: true,
      columns: block.cols.map((c, i) => column({ key: `c${i}`, label: c.label }, { width: c.w, editable: false, align: c.align === 'r' ? 'right' : 'left' })) }, owner);
  } else if (block.t === 'box') {
    const list = el('dl', { className: 'vc-reports__metadata' });
    block.rows.forEach(([label, value]) => list.append(el('dt', { textContent: label }), el('dd', { textContent: String(value ?? '') }))); target.append(list);
  } else if (['text', 'group', 'title'].includes(block.t)) {
    target.append(el(block.t === 'title' ? 'h3' : block.t === 'group' ? 'h4' : 'p', { textContent: block.text }));
  } else if (block.t === 'sign') {
    target.append(el('p', { className: 'vc-reports__signatures', textContent: block.heads.join('     |     ') }));
  } else if (block.t === 'break') target.append(el('hr'));
}

export function reportNodes(reports) {
  const groups = new Map();
  for (const report of reports.filter(item => !item.id.endsWith('subreport') && item.id !== 'BC subreport 2013')) {
    const group = report.kind === 'food' ? 'Liste de alimente'
      : report.family === 'bc' || report.family === 'br' || report.kind.startsWith('consumption') || report.id === 'Centralizator_BC' ? 'Ieșiri'
        : report.family === 'nir' || report.family === 'storno' || ['receipts_summary', 'suppliers'].includes(report.kind)
          || report.kind === 'purchases' && ['Nota_Comanda', 'Referat_periodic_Intrari'].includes(report.id) ? 'Intrări' : 'Produse';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ id: 'doc:' + report.id, label: report.label });
  }
  return [{ id: 'warehouse', label: 'Magazie', children: ['Produse', 'Intrări', 'Ieșiri', 'Liste de alimente']
    .filter(group => groups.has(group)).map(group => ({ id: 'reports:' + group, label: group, children: groups.get(group) })) }];
}
