"""VERCON wipe for the private KBotAdmin CLI (slice ME01-16): empties every VC_ table of ONE unit database and the Vercon-only
EF_ tables, so a wrongly migrated database can be migrated again.

WRITES. Never touches FX_, AD_, TZ_, Parteneri or any other table: the targets are a fixed prefix (`VC_`) plus a fixed list of
EF_ tables, and every target is checked against that rule again right before it is emptied. The structure stays; only rows go.
The operator must type the database name as the confirmation (the command's typed value), checked here and not only in the client.
"""
from routes.admin_cli.admin_common import AdminError, _exec, _journal, _q, _rows

TITLE = "Golire VERCON"
VC_PREFIX = "VC_"

# EF_ tables that exist only for the Vercon hand-over (sendings of received invoices to Vercon, their allocations and the
# supplier/product correlations). EF_Primite and the other EF_ tables stay: the received invoices themselves are not Vercon's.
EF_VERCON_TABLES = (
    "EF_PrimiteVercon",
    "EF_PrimiteVerconLinii",
    "EF_PrimiteVerconNir",
    "EF_VERCON_ALOCARI",
    "EF_VERCON_ASOCIERI",
    "EF_VERCON_CORELARI",
    "EF_VERCON_VIZIBILITATE",     # created on first use, may not exist
)


def _is_target(table):
    return table.startswith(VC_PREFIX) or table in EF_VERCON_TABLES


def _targets(conn, db):
    """The base tables of `db` to empty: every VC_ table plus the Vercon EF_ tables that exist, in name order."""
    names = [r["name"] for r in _rows(
        conn, "SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' "
              "ORDER BY TABLE_NAME", (db,))]
    found = [n for n in names if _is_target(n)]
    if not any(n.startswith(VC_PREFIX) for n in found):
        raise AdminError("Baza «%s» nu are tabele VC_: nu este o baza VERCON, nu se goleste nimic." % db)
    return found


def _count(conn, table):
    return int(_rows(conn, "SELECT COUNT(*) AS n FROM %s" % _q(table))[0]["n"])


def wipe(conn, db, param, block):
    typed = (param or "").strip()
    if typed.casefold() != db.casefold():
        raise AdminError("Confirmarea nu se potriveste: scrie exact numele bazei «%s» (ai scris «%s»). Nu s-a sters nimic."
                         % (db, typed))
    targets = _targets(conn, db)
    before = {t: _count(conn, t) for t in targets}
    done = []
    try:
        # TRUNCATE is DDL (it commits by itself and resets AUTO_INCREMENT, which a new migration wants); the parents and
        # children reference each other, so the checks are off for this session and back on afterwards.
        _exec(conn, "SET SESSION FOREIGN_KEY_CHECKS=0")
        for table in targets:
            if not _is_target(table):
                raise AdminError("Tabela «%s» nu este a VERCON: golirea se opreste." % table)
            _exec(conn, "TRUNCATE TABLE %s" % _q(table))
            done.append(table)
    except Exception as err:
        _journal_wipe(conn, db, "EROARE", "Golire VERCON oprita dupa %d din %d tabele (%s): %s"
                      % (len(done), len(targets), ", ".join(done), err))
        raise
    finally:
        _exec(conn, "SET SESSION FOREIGN_KEY_CHECKS=1")
    left = {t: _count(conn, t) for t in targets}
    total = sum(before.values())
    _journal_wipe(conn, db, "OK", "%d tabele golite (%d tabele VC_, %d EF_), %d randuri sterse; FX_, AD_, TZ_ si Parteneri neatinse; "
                  "au ramas randuri in: %s" % (len(targets), sum(1 for t in targets if t.startswith(VC_PREFIX)),
                                                sum(1 for t in targets if not t.startswith(VC_PREFIX)), total,
                                                ", ".join(t for t in targets if left[t]) or "niciuna"))
    rows = [[t, str(before[t]), str(left[t])] for t in targets]
    problems = [t for t in targets if left[t]]
    summary = block(db, TITLE, ["rezultat"],
                    [["%d tabele golite, %d randuri sterse din baza %s." % (len(targets), total, db)],
                     ["TOATE tabelele sunt goale." if not problems else "ATENTIE, au ramas randuri in: " + ", ".join(problems)]],
                    "Parteneri, FX_, AD_ si TZ_ nu au fost atinse. Migrarea poate fi rulata din nou pe aceasta baza.", raw=True)
    detail = block(db, TITLE + " - pe tabele", ["tabela", "randuri inainte", "randuri dupa"], rows)
    return [summary, detail]


def _journal_wipe(conn, db, result, details):
    _journal(conn, "vercon_golire", db, "VC_ + EF_ Vercon", details, result)
    conn.commit()
