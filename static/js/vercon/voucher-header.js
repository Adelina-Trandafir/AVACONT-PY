// The local draft/reader printout uses the same merged-cell structure as reports.
const FAMILIES = { bp: ['BP', 'BON DE PREDARE'], bt: ['BT', 'BON DE TRANSFER'],
  bs: ['BS', 'BON DE CASARE'], bc: ['BC', 'BON DE CONSUM'], br: ['BX', 'BON DE RETUR'] };
const TYPES = { MF: 'MIJLOACE FIXE ÎN FOLOSINȚĂ', OI: 'OBIECTE DE INVENTAR ÎN FOLOSINȚĂ',
  MATERIALE: 'MATERIALE', ALIMENTE: 'ALIMENTE' };

export function voucherHeader(selection, family, header, fields, title) {
  if (!FAMILIES[family] || !header) return null;
  const value = (...labels) => fields.find(item => labels.includes(item.label))?.value || '';
  const [prefix, caption] = FAMILIES[family];
  const [year = '', month = '', day = ''] = String(header.Data || '').slice(0, 10).split('-');
  const giver = ['bt', 'bs'].includes(family) ? value('Predător') || header.labels?.[family === 'bt' ? 'IDC_OLD' : 'IDC_BC'] || ''
    : family === 'br' ? header.receiver || value('Primitor / gestionar') : 'Magazie';
  const receiver = family === 'bs' ? '' : family === 'br' ? 'Magazie'
    : value('Primitor', 'Primitor / gestionar') || header.labels?.[family === 'bp' ? 'IDC_BP' : 'IDC_BT'] || header.receiver || '';
  return { type: selection.type, caption, number: `${prefix} ${header.Numar ?? ''}`.trim(), day, month, year, giver, receiver,
    order: header.order || '', draft: title.includes('Ciornă nesalvată') ? 'Ciornă nesalvată' : '' };
}

export function drawVoucherHeader(context, top, left, width, header, wrap) {
  const weights = [125, 74, 73, 74, 177, 174, 117, 262], sum = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map(value => width * value / sum), heights = [64, 36, 36, 40], cells = [];
  const add = (column, row, text, colspan = 1, rowspan = 1, bold = false, size = 19) => {
    const cellWidth = widths.slice(column, column + colspan).reduce((a, b) => a + b, 0);
    context.font = `${bold ? 'bold ' : ''}${size}px Arial`;
    const lines = wrap(text, cellWidth - 12), required = lines.length * size * 1.22 + 12;
    const available = heights.slice(row, row + rowspan).reduce((a, b) => a + b, 0);
    if (required > available) heights[row + rowspan - 1] += required - available;
    cells.push({ column, row, cellWidth, rowspan, lines, bold, size });
  };
  add(0, 0, header.gestiuneName || header.type, 7, 1, true, 23);
  add(7, 0, header.caption, 1, 4, true, 25);
  add(0, 1, 'Număr\ndocument', 1, 2, true);
  add(1, 1, 'Data', 3, 1, true);
  add(4, 1, TYPES[header.type] || header.type, 3, 1, true, 21);
  ['Ziua', 'Luna', 'Anul', 'Predător', 'Primitor', 'Nr. comandă'].forEach((text, index) => add(index + 1, 2, text, 1, 1, true));
  [header.number, header.day, header.month, header.year, header.giver, header.receiver, header.order]
    .forEach((text, index) => add(index, 3, text));
  context.save(); context.strokeStyle = '#000'; context.fillStyle = '#000'; context.lineWidth = 1;
  context.textAlign = 'center'; context.textBaseline = 'middle';
  for (const cell of cells) {
    const x = left + widths.slice(0, cell.column).reduce((a, b) => a + b, 0);
    const y = top + heights.slice(0, cell.row).reduce((a, b) => a + b, 0);
    const height = heights.slice(cell.row, cell.row + cell.rowspan).reduce((a, b) => a + b, 0);
    context.strokeRect(x, y, cell.cellWidth, height);
    context.font = `${cell.bold ? 'bold ' : ''}${cell.size}px Arial`;
    cell.lines.forEach((line, index) => context.fillText(line, x + cell.cellWidth / 2,
      y + height / 2 + (index - (cell.lines.length - 1) / 2) * cell.size * 1.22));
  }
  context.restore();
  let bottom = top + heights.reduce((a, b) => a + b, 0) + 20;
  if (header.draft) { context.font = '19px Arial'; context.fillText(header.draft, left, bottom); bottom += 28; }
  return bottom;
}
