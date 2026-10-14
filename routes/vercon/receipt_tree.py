"""The saved-receipts tree, one level per request: suppliers, then the months of one supplier, then the receipts of one month.

The page asks for a level only when the operator opens its parent, so opening the NIR page never reads every receipt.
A supplier is identified by the name the tree shows (the same supplier registered twice is one branch, as before);
the empty name is the branch of the receipts without a supplier (opening stock).
"""
import re

from .documents import _all, _iso, _money, _scope
from .nomenclatoare import NomError

# The supplier as the tree shows it: the registered partner, else the free-text name, else '' (opening stock).
_PARTY = "COALESCE(NULLIF(p.DenumirePartener,''), NULLIF(n.FurnizorV,''), '')"
_FROM = ('FROM VC_NIR n LEFT JOIN Parteneri p ON p.IdPartener=n.IdPartener '
         'WHERE n.SubunitId=%s AND n.GestiuneId=%s AND n.ExercitiuId=%s AND COALESCE(n.Storno,0)=0')


def parties(cursor, ctx):
    """Suppliers with their receipt count, the one with the newest receipt first."""
    rows = _all(cursor, 'SELECT ' + _PARTY + ' AS party, COUNT(*) AS total, MAX(n.Data) AS newest ' + _FROM +
                ' GROUP BY party ORDER BY newest DESC, party', _scope(ctx))
    return [{'party': r['party'], 'count': r['total']} for r in rows]


def months(cursor, ctx, party):
    """The months of one supplier that hold receipts, newest first."""
    rows = _all(cursor, 'SELECT YEAR(n.Data) AS y, MONTH(n.Data) AS m, COUNT(*) AS total ' + _FROM +
                ' AND ' + _PARTY + '=%s AND n.Data IS NOT NULL GROUP BY y, m ORDER BY y DESC, m DESC', _scope(ctx) + (party,))
    return [{'month': '%04d-%02d' % (r['y'], r['m']), 'count': r['total']} for r in rows]


def documents(cursor, ctx, party, month):
    """The receipts of one supplier in one month (month = YYYY-MM), newest first; the same summaries the old flat list held."""
    first, after = _month_bounds(month)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT n.IdIntrare, n.Numar, n.Data, n.DataFc, n.NrFact, n.Valoare, n.IdPartener, n.IdStorno, n.TipDoc, n.Anulat, n.Version, ' +
                _PARTY + ' AS party ' + _FROM + ' AND ' + _PARTY + '=%s AND n.Data>=%s AND n.Data<%s ORDER BY n.Data DESC, n.Numar DESC',
                _scope(ctx) + (party, first, after))
    return [{'id': r['IdIntrare'], 'number': abs(r['Numar'] or 0), 'date': _iso(r['Data']), 'invoice_date': _iso(r['DataFc']), 'invoice': r['NrFact'],
             'total': _money(r['Valoare'], scale), 'party_id': r['IdPartener'], 'party': r['party'], 'doc_type': r['TipDoc'],
             'source': r['IdStorno'], 'annulled': bool(r['Anulat']), 'version': r['Version']} for r in rows]


def _month_bounds(month):
    found = re.fullmatch(r'(\d{4})-(\d{2})', month or '')
    if not found or not 1 <= int(found.group(2)) <= 12:
        raise NomError('INVALID', 'Cerere invalidă.', 400)
    year, number = int(found.group(1)), int(found.group(2))
    return '%04d-%02d-01' % (year, number), ('%04d-01-01' % (year + 1)) if number == 12 else '%04d-%02d-01' % (year, number + 1)
