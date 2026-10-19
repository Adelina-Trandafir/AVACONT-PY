"""Excel output of the same report blocks used by the print and PDF renderers."""
from html import escape
from reporting.excel_widths import column_key, configured_widths, ColumnWidths

from .domain import DomainError, require

MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def table_schemas(model):
    """Repeated tables (groups, children and totals) share one column selection."""
    schemas = []
    for block in model['blocks']:
        if block['t'] == 'table':
            labels = tuple(column['label'] for column in block['cols'])
            if labels not in schemas:
                schemas.append(labels)
    return schemas


def column_options(model):
    return [(f'{table}:{index}', label.replace('-\n', '').replace('\n', ' ') or ('Nr. crt.' if index == 0 else f'Coloana {index + 1}'))
            for table, labels in enumerate(table_schemas(model)) for index, label in enumerate(labels)]


def dialog_html(model):
    options = column_options(model)
    choices = ''.join(f'<label><input type="checkbox" name="column" value="{key}" checked><span>{escape(label)}</span></label>'
                      for key, label in options)
    return ('<dialog id="report-excel-dialog" class="ade-export" aria-labelledby="report-excel-title" aria-describedby="report-excel-note">'
            '<form id="report-excel-form"><header><h2 id="report-excel-title">Export Excel</h2>'
            '<button id="report-excel-close" type="button" aria-label="Închide">✕</button></header>'
            '<div class="ade-export-content"><p id="report-excel-note">Alegeți coloanele pentru export. Antetul se exportă întotdeauna.</p>'
            + ('<label class="ade-export-all"><input id="report-excel-all" type="checkbox" checked><span>Toate coloanele</span></label>'
               f'<div class="ade-export-columns">{choices}</div>' if options else '<p>Acest raport nu are coloane de selectat. Se exportă integral.</p>')
            + '<p id="report-excel-error" role="alert" hidden></p></div><footer>'
            '<button id="report-excel-cancel" type="button">Renunță</button>'
            '<button id="report-excel-save" type="submit">Exportă Excel</button></footer></form></dialog>')


def selected_columns(model, args):
    allowed = {key for key, _ in column_options(model)}
    raw = args.get('columns')
    if raw is None:
        return allowed
    chosen = set(raw.split(',')) if raw else set()
    require(chosen <= allowed, 'EXPORT_COLUMNS', 'Selecția coloanelor este invalidă.', 400)
    require(bool(chosen) or not allowed, 'EXPORT_COLUMNS', 'Selectați cel puțin o coloană.', 400)
    return chosen


def xlsx_bytes(model, args, column_widths=None):
    from reporting import output
    from .report_model import shared_model
    chosen = selected_columns(model, args)
    try:
        return output.xlsx(shared_model(model, chosen), column_widths)
    except ImportError as error:
        raise DomainError('EXCEL_DEPENDENCY', 'Exportul Excel necesită dependențele din requirements-reporting.txt pe server.', 503) from error
