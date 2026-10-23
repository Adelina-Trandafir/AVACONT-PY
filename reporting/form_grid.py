"""Merged form cells shared by PDF and Excel; no application-specific values."""
from math import ceil


def pdf(block, available, paragraph):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import Table, TableStyle

    data = [[''] * len(block['widths']) for _ in block['heights']]
    commands = [('GRID', (0, 0), (-1, -1), .4, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
    for cell in block['cells']:
        column, row = cell['column'], cell['row']
        data[row][column] = paragraph(cell['text'], TA_CENTER, cell.get('size', 8), cell.get('bold', False))
        last = (column + cell.get('colspan', 1) - 1, row + cell.get('rowspan', 1) - 1)
        if last != (column, row):
            commands.append(('SPAN', (column, row), last))
    total = sum(block['widths'])
    table = Table(data, colWidths=[available * value / total for value in block['widths']],
                  minRowHeights=block['heights'], splitByRow=0, hAlign='CENTER')
    table.setStyle(TableStyle(commands))
    return table


def xlsx(sheet, block, first_row, write, widths):
    from openpyxl.styles import Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    edge = Side(style='thin', color='000000')
    for index, weight in enumerate(block['widths'], 1):
        dimension = sheet.column_dimensions[get_column_letter(index)]
        dimension.width = widths.observe(index, 'ANTET_BON_' + block['keys'][index - 1],
                                         weight * 15, dimension.width)
    for cell in block['cells']:
        column, row = cell['column'] + 1, first_row + cell['row']
        last_column = column + cell.get('colspan', 1) - 1
        last_row = row + cell.get('rowspan', 1) - 1
        item = write(row, column, cell['text'], cell.get('bold', False), cell.get('size', 8))
        item.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        item.border = Border(left=edge, right=edge, top=edge, bottom=edge)
        if last_column != column or last_row != row:
            sheet.merge_cells(start_row=row, start_column=column, end_row=last_row, end_column=last_column)
    return first_row + len(block['heights']) + 1


def fit_rows(sheet, block, first_row):
    """Size merged cells after all tables and configured widths are resolved."""
    from openpyxl.utils import get_column_letter

    heights = list(block['heights'])
    for cell in block['cells']:
        column = cell['column'] + 1
        last_column = column + cell.get('colspan', 1) - 1
        characters = max(1, sum(sheet.column_dimensions[get_column_letter(i)].width
                                for i in range(column, last_column + 1)) * 10 / cell.get('size', 8) - 2)
        lines = sum(max(1, ceil(len(line) / characters)) for line in str(cell['text']).split('\n'))
        required = lines * cell.get('size', 8) * 1.4 + 5
        end = cell['row'] + cell.get('rowspan', 1)
        if required > sum(heights[cell['row']:end]):
            heights[end - 1] += required - sum(heights[cell['row']:end])
    for index, height in enumerate(heights, first_row):
        sheet.row_dimensions[index].height = height
