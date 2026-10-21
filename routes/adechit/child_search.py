"""Month-scoped child lookup: attendance's historical group, never the entire payer catalog."""
from .domain import require

MAX_RESULTS = 100


def search(repo, month_id, query):
    repo.get('LunaD', month_id)  # Also verifies membership in the authenticated subunit.
    query = query.strip()
    require(len(query) <= 100, 'SEARCH', 'Căutarea poate avea maximum 100 de caractere.', 400)
    if len(query) < 3:
        return {'rows': [], 'has_more': False}
    # Treat %, _ and the escape character as literal typed characters.
    pattern = '%' + query.replace('=', '==').replace('%', '=%').replace('_', '=_') + '%'
    rows = repo.query(
        'SELECT p.IDZ, p.IDL, p.IDG, p.IDP, c.Nume, c.CNP, g.Grupa, g.Ascunsa '
        'FROM AD_Prezenta p '
        'JOIN AD_Platitori c ON c.SubunitId=p.SubunitId AND c.IDP=p.IDP '
        'JOIN AD_Grupe g ON g.SubunitId=p.SubunitId AND g.IDG=p.IDG '
        'WHERE p.SubunitId=%s AND p.IDL=%s '
        "AND (c.Nume LIKE %s ESCAPE '=' OR c.CNP LIKE %s ESCAPE '=') "
        'ORDER BY c.Nume, g.Grupa, p.IDZ LIMIT %s',
        (repo.scope(), month_id, pattern, pattern, MAX_RESULTS + 1))
    more = len(rows) > MAX_RESULTS
    rows = rows[:MAX_RESULTS]
    for row in rows:
        # The suffix disambiguates namesakes without returning a full CNP in this lookup.
        row['CNP_ULTIME'] = str(row.pop('CNP') or '')[-4:]
    return {'rows': rows, 'has_more': more}
