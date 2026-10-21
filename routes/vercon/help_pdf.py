"""VERCON help PDF, backed by the common help renderer."""
from pathlib import Path
from reporting.help_pdf import HelpError, pdf_bytes as render_pdf
from .nomenclatoare import NomError

CONTENT = Path(__file__).resolve().parents[2] / 'static' / 'help' / 'vercon.json'


def pdf_bytes():
    try:
        return render_pdf(CONTENT, author='VERCON')
    except HelpError as error:
        raise NomError(error.code, str(error), error.status) from error
