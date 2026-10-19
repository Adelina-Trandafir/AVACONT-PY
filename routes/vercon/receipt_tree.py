"""Saved receipts, two levels: month or supplier roots, then their documents.

The page reads document summaries only when the operator opens their root.
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


def months(cursor, ctx, party=None):
    """Month roots, or the months of one supplier for legacy callers."""
    where, params = (' AND ' + _PARTY + '=%s', (party,)) if party is not None else ('', ())
    rows = _all(cursor, 'SELECT YEAR(n.Data) AS y, MONTH(n.Data) AS m, COUNT(*) AS total ' + _FROM +
                where + ' GROUP BY y, m ORDER BY y DESC, m DESC', _scope(ctx) + params)
    return [{'month': '%04d-%02d' % (r['y'], r['m']) if r['y'] and r['m'] else 'undated', 'count': r['total']} for r in rows]


def documents(cursor, ctx, party=None, month=None):
    """One supplier or one month; filters remain inside the active exercise."""
    if party is None and month is None:
        raise NomError('INVALID', 'Alegeți furnizorul sau luna.', 400)
    where, params = '', _scope(ctx)
    if party is not None:
        where += ' AND ' + _PARTY + '=%s'
        params += (party,)
    if month == 'undated':
        where += ' AND n.Data IS NULL'
    elif month is not None:
        first, after = _month_bounds(month)
        where += ' AND n.Data>=%s AND n.Data<%s'
        params += (first, after)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT n.IdIntrare, n.Numar, COALESCE(n.NumarSub,0) AS sub_number, n.Data, n.DataFc, n.NrFact, n.Valoare, n.IdPartener, n.IdStorno, n.TipDoc, n.Anulat, n.Version, ' +
                _PARTY + ' AS party ' + _FROM + where + ' ORDER BY n.Data DESC, n.Numar DESC,n.NumarSub DESC,n.IdIntrare DESC', params)
    from .receipt_invoice_view import summary_totals
    return summary_totals(cursor, ctx, [{'id': r['IdIntrare'], 'number': abs(r['Numar'] or 0), 'sub_number': r['sub_number'], 'date': _iso(r['Data']), 'invoice_date': _iso(r['DataFc']), 'invoice': r['NrFact'],
             'total': _money(r['Valoare'], scale), 'party_id': r['IdPartener'], 'party': r['party'], 'doc_type': r['TipDoc'],
             'source': r['IdStorno'], 'annulled': bool(r['Anulat']), 'version': r['Version']} for r in rows])


def _month_bounds(month):
    found = re.fullmatch(r'(\d{4})-(\d{2})', month or '')
    if not found or not 1 <= int(found.group(2)) <= 12:
        raise NomError('INVALID', 'Cerere invalidă.', 400)
    year, number = int(found.group(1)), int(found.group(2))
    return '%04d-%02d-01' % (year, number), ('%04d-01-01' % (year + 1)) if number == 12 else '%04d-%02d-01' % (year, number + 1)
