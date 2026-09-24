# routes/inregistrare/randuri.py
"""
The `Clasificatii` rows of a new unit (slice 0075-03, plan 6 and decision D5).

One row per ticked F leaf x ticked E leaf x chosen sector-source. The request stores
only the three lists (`FX_Inregistrari.Payload`); this module turns them into rows and
proves, in memory, that every row will pass the foreign keys before a single INSERT
runs -- the same gate the Migrator's `Verifier.CheckDictionary` keeps.

WHY THE CHECK IS REPEATED HERE. `/cerere` checked the codes when the request was
filed, but approval can come days later and the dictionaries can change in between.
A foreign-key failure at step 6 would come back as a bare `1452` after the database
is already built; checked here it is a Romanian sentence naming the codes, raised
before anything exists.

THE SPLIT (plan 2a, confirmed on real rows `650402` / `200104`):

    Capitol    = Left(F,2) + "." + Left(SS,2)     -- except SS 02E: Left(F,2) + ".10"
    Subcapitol = Mid(F,3,2) + "." + Mid(F,5,2)
    Articol    = Left(E,2) + "." + Mid(E,3,2)
    Alineat    = Right(E,2)
    Denumire   = DefaClsfE.Denumire, trimmed, cut to 255 (strict mode: 1406 otherwise)

`Sector`, `Sursa` and `SS` are written columns since 0075-00 and come from the one rule
in `routes/clasificatii_ss.py`, given the source letter explicitly. The derived SS is
compared with the chosen one: if the capitol rule and the SS rule ever disagree, that is
a bug in this file, and it stops here rather than writing a row under the wrong source.

Nothing here writes. `read_dictionaries` reads five lists; everything else is pure.
"""
from routes.clasificatii_ss import ss_values

# `Clasificatii.Denumire` is varchar(255) NOT NULL.
DENUMIRE_MAX_LENGTH = 255

# The one sector-source whose capitol does not end in its own sector digits.
_SS_CAPITOL_EXCEPTIONS = {"02E": "10"}

_SQL_CLSF_F = ("SELECT DISTINCT ClsfF FROM AVACONT_COMUN.DefaClsfF "
               "WHERE ClsfF IS NOT NULL AND ClsfF <> ''")
_SQL_CLSF_E = ("SELECT ClsfE, MAX(Denumire) FROM AVACONT_COMUN.DefaClsfE "
               "WHERE ClsfE IS NOT NULL AND ClsfE <> '' GROUP BY ClsfE")
_SQL_ARTICOL = "SELECT DISTINCT Articol FROM AVACONT_COMUN.DefaArticol"
_SQL_TITLU = "SELECT DISTINCT Titlu FROM AVACONT_COMUN.DefaTitlu"
_SQL_SS = "SELECT SursaSector FROM AVACONT_COMUN.DefaSursaSector"


class RanduriInvalide(ValueError):
    """At least one row would fail a foreign key or has no caption. The message is the
    Romanian sentence that goes into `FX_Inregistrari.Motiv`."""


def read_dictionaries(conn) -> dict:
    """The five lists the rows are checked against, read once per run."""
    cur = conn.cursor()
    out = {}
    cur.execute(_SQL_CLSF_F)
    out["f"] = {_text(r[0]) for r in cur.fetchall()}
    cur.execute(_SQL_CLSF_E)
    out["e"] = {_text(r[0]): _text(r[1]) for r in cur.fetchall()}
    cur.execute(_SQL_ARTICOL)
    out["articol"] = {_text(r[0]) for r in cur.fetchall()}
    cur.execute(_SQL_TITLU)
    out["titlu"] = {_text(r[0]) for r in cur.fetchall()}
    cur.execute(_SQL_SS)
    out["ss"] = {_text(r[0]) for r in cur.fetchall()}
    return out


def capitol_for(clsf_f, ss) -> str:
    """D5: `65.01` for F `650101` under `01A`; `65.10` under `02E`."""
    return clsf_f[:2] + "." + _SS_CAPITOL_EXCEPTIONS.get(ss, ss[:2])


def build(ss_list, f_list, e_list, id_unitate_by_ss, dictionaries) -> list:
    """
    Every row, as a tuple in the column order of `INSERT_SQL`.

    Checks everything first and raises RanduriInvalide with ALL the offending codes
    named at once -- the operator fixes a dictionary once, not once per retry.
    """
    _check(ss_list, f_list, e_list, dictionaries)

    rows = []
    for ss in ss_list:
        id_unitate = id_unitate_by_ss[ss]
        for f in f_list:
            capitol = capitol_for(f, ss)
            sector, sursa, derived = ss_values(capitol, ss[2:])
            if derived != ss:
                # See the note at the top of the file: a disagreement between two
                # rules of this codebase, never something the applicant sent.
                raise RanduriInvalide(
                    f"Sursa-sector {ss} nu se poate reconstitui din capitolul {capitol} "
                    f"(rezultă {derived}). Eroare de program; nu s-a creat nimic."
                )
            subcapitol = f[2:4] + "." + f[4:6]
            for e in e_list:
                rows.append((
                    id_unitate,
                    capitol,
                    subcapitol,
                    e[:2] + "." + e[2:4],
                    e[4:6],
                    dictionaries["e"][e].strip()[:DENUMIRE_MAX_LENGTH],
                    sector,
                    sursa,
                    ss,
                ))
    return rows


# Column order of every tuple `build` returns. `IdClsfAcc` = 0: these rows have no
# Access counterpart. The generated columns (Clsf, Titlu, ClsfF, ClsfE, ...) are left
# to the server.
INSERT_SQL = (
    "INSERT INTO Clasificatii "
    "(IdClsfAcc, IdUnitate, Capitol, Subcapitol, Articol, Alineat, Denumire, "
    "Sector, Sursa, SS) "
    "VALUES (0, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _check(ss_list, f_list, e_list, d):
    problems = []

    bad = [ss for ss in ss_list if ss not in d["ss"] or len(ss) != 3]
    if bad:
        problems.append("surse-sector inexistente: " + ", ".join(bad))

    bad = [f for f in f_list if len(f) != 6 or f not in d["f"]]
    if bad:
        problems.append("coduri funcționale inexistente: " + _few(bad))

    missing_e = [e for e in e_list if len(e) != 6 or e not in d["e"]]
    if missing_e:
        problems.append("coduri economice inexistente: " + _few(missing_e))
    no_caption = [e for e in e_list if e in d["e"] and not d["e"][e].strip()]
    if no_caption:
        problems.append("coduri economice fără denumire: " + _few(no_caption))

    known_e = [e for e in e_list if len(e) == 6]
    bad = sorted({e[:2] + "." + e[2:4] for e in known_e} - d["articol"])
    if bad:
        problems.append("articole lipsă din DefaArticol: " + _few(bad))
    bad = sorted({e[:2] for e in known_e} - d["titlu"])
    if bad:
        problems.append("titluri lipsă din DefaTitlu: " + _few(bad))

    if problems:
        raise RanduriInvalide(
            "Clasificațiile cererii nu mai trec de nomenclatoare ("
            + "; ".join(problems) + "). Nu s-a creat nimic."
        )


def _few(codes, limit=10):
    shown = ", ".join(codes[:limit])
    return shown if len(codes) <= limit else f"{shown} și încă {len(codes) - limit}"


def _text(value) -> str:
    return "" if value is None else str(value).strip()
