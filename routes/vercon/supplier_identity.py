"""Deployment guard only; all fiscal identities and partner lookups share one utility."""
from utils import vercon_suppliers as suppliers
from .nomenclatoare import NomError


def require(cursor):
    if not suppliers.schema_ready(cursor):
        raise NomError('SUPPLIER_SCHEMA_REQUIRED',
                       'VERCON online folosește exclusiv codul fiscal al furnizorului. '
                       'Aplicați manual sql/VC_20261023_FURNIZORI_CF_STRUCTURA.sql, sincronizați structura '
                       'și rulați sql/VC_20261023_FURNIZORI_CF_KBOTADMIN_V3.sql prin KBotAdmin.', 503)
