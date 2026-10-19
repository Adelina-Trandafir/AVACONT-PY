"""VERCON adapters over the shared report output engine."""
from copy import deepcopy
from reporting import output
from reporting.errors import ReportError
from .nomenclatoare import NomError

# Public compatibility for the existing report builders.
tokens = output.tokens


def pdf(model):
    try:
        if model['layout'].get('form') == 'nir-classic':
            from . import report_reception
            return report_reception.pdf(model)
        return output.pdf(model)
    except ReportError as error:
        raise NomError(error.reason, str(error), error.status) from error


def xlsx(model, column_widths=None):
    from . import report_excel
    built = deepcopy(model)

    def prepare(blocks):
        for block in blocks:
            if block['t'] == 'table':
                columns = deepcopy(block['cols'])
                for index, column in enumerate(block['cols']):
                    column['key'] = report_excel.key(column, index, columns)
            elif block['t'] == 'columns':
                for child in block['children']:
                    prepare(child)
    prepare(built['blocks'])
    return output.xlsx(built, column_widths)
