"""ADECHIT help PDF, backed by the common help renderer."""
from pathlib import Path
from reporting.help_pdf import HelpError, pdf_bytes as render_pdf
from .domain import DomainError

CONTENT = Path(__file__).resolve().parents[2] / 'static' / 'help' / 'adechit.json'


def pdf_bytes():
    try:
        return render_pdf(CONTENT, author='ADECHIT')
    except HelpError as error:
        raise DomainError(error.code, str(error), error.status) from error
