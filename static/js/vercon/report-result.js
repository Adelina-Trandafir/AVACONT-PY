import { el, grid, column } from './ui.js';

export function paintReportBlock(block, target, owner) {
  if (block.t === 'positioned') {
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
    grid(panel, { rowKey: 'id', rows, footer: false, proportionalWidths: true,
      ...(block.show_column_header === false ? { headerHeight: 0 } : {}),
      columns: block.cols.map((c, i) => column({ key: `c${i}`, label: c.label }, { width: c.w, editable: false, align: c.align === 'r' ? 'right' : 'left' })) }, owner);
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

export function reportNodes(reports) {
  reports = reports.filter(report => !['OI_Predare', 'OI_Transfer', 'OI_Casare'].includes(report.id));
  const oi = reports.filter(report => report.ledger === 'custody' || ['bp', 'bt', 'bs'].includes(report.family));
  if (oi.length) {
    const nodes = reports.filter(report => !oi.includes(report));
    return [...(nodes.length ? reportNodes(nodes) : []), { id: 'oi-reports', label: 'Obiecte de inventar în folosință',
      children: [...new Set(oi.map(report => report.group))].map((group, index) => ({ id: `oi-reports:${index}`, label: group,
        children: oi.filter(report => report.group === group).map(report => ({ id: 'doc:' + report.id, label: report.label })) })) }];
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
  return [{ id: 'warehouse', label: 'Magazie', children: ['Produse', 'Intrări', 'Ieșiri', 'Liste de alimente']
    .filter(group => groups.has(group)).map(group => ({ id: 'reports:' + group, label: group, children: groups.get(group) })) }];
}
