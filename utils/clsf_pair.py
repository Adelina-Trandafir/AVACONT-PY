# utils/clsf_pair.py
"""
`IdClsf` on seven FX_ tables becomes the MariaDB key (slice 0080-01).

WHAT CHANGED
------------
Seven FX_ tables carried ONE classification column, `IdClsf`, and it held the ACCESS id
(= `Clasificatii.IdClsfAcc`), so every read had to join
`C.IdClsfAcc = T.IdClsf AND C.IdUnitate = <unit>`. The ORD and DDF families hold the MariaDB
key there (`Clasificatii.IDClsf`). Two meanings for one column name across the same database
(operator, 24.09.2026: "that is wrong"). Since 0080-01 `IdClsf` on these seven tables is
`Clasificatii.IDClsf` too, and they keep NO copy of the Access id (operator, 24.09.2026: "I
don't want to keep IdClsfAcc in those tables"). The Access id stays in
`Clasificatii.IdClsfAcc`, reachable through `IdClsf`.

A converted `IdClsf` carries the column comment `MARKER`. That is how the one-off knows a
table is done (a second pass would read MariaDB keys as Access ids), and how the migrators
refuse a target nobody converted.

The seven tables and where each finds its unit -- `Clasificatii` holds several units in one
database, and `IdClsfAcc` is only unique inside a unit:

    FX_Indicatori     own IdUnitate
    FX_Extrase_H      own IdUnitate
    FX_Plati          own IdUnitate, else FX_Indicatori on CodAI
    FX_Receptii       own IdUnitate, else FX_Indicatori on CodAI
    FX_Receptii_RHR   own IdUnitate, else FX_Indicatori on CodAI
    FX_Istoric        FX_Indicatori on CodAI (no IdUnitate column)
    FX_Rezervari      FX_Indicatori on CodAI (no IdUnitate column)

WHO USES THIS
-------------
  * `scripts/extrase_clsf_0080.py` -- the one-off over every existing database, with the
    Access id parked in a temporary column (`problem_sql` / `fill_sql`, `acc_col`);
  * `routes/migrare/execute.py`    -- looks the key up row by row (`Resolver`) before
    writing a row from an Access file.

Both test FIRST and change nothing when a row fails (operator, 24.09.2026: a row that
resolves to no classification, or to more than one, is a prior test that stops the run until
someone has looked). An Access id NULL or 0 is "no classification" and is not a problem:
`IdClsf` stays NULL for it.

`db` is optional everywhere: None = the connection's own database (the migrator), a name =
fully qualified (the one-off, connected to the server rather than to one database).
The VB migrator has the same rules in `src/KBot.Migrator/Transfer` -- keep them in step.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

# The column comment on a converted `IdClsf`. `CONVERTED_TAG` is what is searched for.
MARKER = "Clasificatii.IDClsf (0080-01)"
CONVERTED_TAG = "0080-01"


@dataclass(frozen=True)
class PairTable:
    name: str
    key: str                 # primary key, for the sample rows of a problem report
    own_unit: bool           # the table has its own IdUnitate column
    via_indicator: bool      # the unit can (also) come from FX_Indicatori on CodAI


PAIR_TABLES = (
    PairTable("FX_Indicatori", "CodAI", own_unit=True, via_indicator=False),
    PairTable("FX_Extrase_H", "IDEXH", own_unit=True, via_indicator=False),
    PairTable("FX_Plati", "IdPlataFX", own_unit=True, via_indicator=True),
    PairTable("FX_Receptii", "IDR", own_unit=True, via_indicator=True),
    PairTable("FX_Receptii_RHR", "IDRHR", own_unit=True, via_indicator=True),
    PairTable("FX_Istoric", "ID", own_unit=False, via_indicator=True),
    PairTable("FX_Rezervari", "IDRZ", own_unit=False, via_indicator=True),
)

PAIR_TABLE_NAMES = tuple(t.name for t in PAIR_TABLES)

# How many offending rows a report names, per table. The count is always complete.
SAMPLE_ROWS = 20


def pair_table(name: str) -> Optional[PairTable]:
    """The PairTable for a table name (case-insensitive), or None when it is not one."""
    for t in PAIR_TABLES:
        if t.name.lower() == (name or "").lower():
            return t
    return None


def _q(db: Optional[str], table: str) -> str:
    if "`" in table or (db and "`" in db):
        raise ValueError(f"Identificator invalid: {db!r}.{table!r}")
    return f"`{db}`.`{table}`" if db else f"`{table}`"


def _col(name: str) -> str:
    if "`" in name:
        raise ValueError(f"Identificator invalid: {name!r}")
    return f"`{name}`"


# --- set-based: the one-off ------------------------------------------------------

def _from_and_unit(t: PairTable, db: Optional[str]):
    """`FROM ... ` fragment (aliased T, and I when joined) and the unit expression."""
    src = f"{_q(db, t.name)} T"
    if t.via_indicator:
        src += f" LEFT JOIN {_q(db, 'FX_Indicatori')} I ON I.CodAI = T.CodAI"
    if t.own_unit and t.via_indicator:
        unit = "COALESCE(T.IdUnitate, I.IdUnitate)"
    elif t.own_unit:
        unit = "T.IdUnitate"
    else:
        unit = "I.IdUnitate"
    return src, unit


def problem_sql(t: PairTable, db: Optional[str] = None, acc_col: str = "IdClsf") -> str:
    """
    Rows whose Access id (in `acc_col`) does not resolve to exactly one classification.
    Before the conversion the Access id is still in `IdClsf`; during it, in a temporary.
    """
    src, unit = _from_and_unit(t, db)
    acc = f"T.{_col(acc_col)}"
    return (
        f"SELECT T.`{t.key}` AS cheie, {acc} AS id_acc, {unit} AS id_unitate, "
        f"  (SELECT COUNT(*) FROM {_q(db, 'Clasificatii')} C "
        f"    WHERE C.IdClsfAcc = {acc} AND C.IdUnitate = {unit}) AS potriviri "
        f"FROM {src} "
        f"WHERE {acc} IS NOT NULL AND {acc} <> 0 "
        f"HAVING potriviri <> 1"
    )


def fill_sql(t: PairTable, db: Optional[str], acc_col: str) -> str:
    """Set `IdClsf` from the Access id in `acc_col` + unit. Only after the problem check."""
    src, unit = _from_and_unit(t, db)
    acc = f"T.{_col(acc_col)}"
    return (
        f"UPDATE {src} "
        f"SET T.IdClsf = (SELECT C.IDClsf FROM {_q(db, 'Clasificatii')} C "
        f"                 WHERE C.IdClsfAcc = {acc} AND C.IdUnitate = {unit}) "
        f"WHERE {acc} IS NOT NULL AND {acc} <> 0"
    )


def converted_sql() -> str:
    """Pair tables of one schema (`%s`) whose `IdClsf` carries the marker."""
    names = ", ".join(f"'{n}'" for n in PAIR_TABLE_NAMES)
    return (
        "SELECT TABLE_NAME AS t FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND COLUMN_NAME = 'IdClsf' "
        f"AND TABLE_NAME IN ({names}) "
        f"AND COLUMN_COMMENT LIKE '%{CONVERTED_TAG}%'"
    )


def converted_tables(cursor, db: str) -> set:
    """Names of the pair tables of `db` already converted (marker on `IdClsf`)."""
    cursor.execute(converted_sql(), (db,))
    out = set()
    for r in cursor.fetchall():
        out.add(r["t"] if isinstance(r, dict) else r[0])
    return out


# --- row by row: the migrator ------------------------------------------------------

class Resolver:
    """
    Access id + unit -> `Clasificatii.IDClsf`, for rows about to be written.

    Reads `Clasificatii` and `FX_Indicatori` of the TARGET once (on the migrator's own
    connection, so rows written earlier in the same run are seen), then answers per row.
    Problems are collected, not raised: the caller stops the table after the loop.
    """

    def __init__(self, cursor):
        cursor.execute("SELECT IDClsf, IdClsfAcc, IdUnitate FROM Clasificatii")
        self._clsf: Dict[tuple, List[int]] = {}
        for r in cursor.fetchall():
            idc, acc, unit = (r["IDClsf"], r["IdClsfAcc"], r["IdUnitate"]) \
                if isinstance(r, dict) else r
            self._clsf.setdefault((_int(acc), _int(unit)), []).append(int(idc))
        cursor.execute("SELECT CodAI, IdUnitate FROM FX_Indicatori")
        self._unit_of_ai: Dict[str, Optional[int]] = {}
        for r in cursor.fetchall():
            cod, unit = (r["CodAI"], r["IdUnitate"]) if isinstance(r, dict) else r
            if cod is not None:
                self._unit_of_ai[str(cod).strip()] = _int(unit)
        self.problems: Dict[str, list] = {}

    def unit_of(self, t: PairTable, row: dict) -> Optional[int]:
        unit = _int(row.get("IdUnitate")) if t.own_unit else None
        if unit is None and t.via_indicator:
            cod = row.get("CodAI")
            if cod is not None:
                unit = self._unit_of_ai.get(str(cod).strip())
        return unit

    def resolve(self, t: PairTable, row: dict, access_id) -> Optional[int]:
        """The MariaDB key, or None (no classification, or a problem -- recorded)."""
        acc = _int(access_id)
        if acc is None or acc == 0:
            return None
        unit = self.unit_of(t, row)
        found = self._clsf.get((acc, unit), []) if unit is not None else []
        if len(found) == 1:
            return found[0]
        self.problems.setdefault(t.name, []).append({
            "cheie": row.get(t.key), "id_acc": acc, "id_unitate": unit,
            "potriviri": len(found)})
        return None


def _int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- reporting ----------------------------------------------------------------------

def describe_problems(found: Dict[str, dict]) -> List[str]:
    """
    Operator-facing lines (Romanian). `found` = {table: {"randuri": n, "mostre": [...]}}
    or {table: [row, ...]} (the Resolver's shape).
    """
    lines = []
    for table, info in found.items():
        if isinstance(info, list):
            info = {"randuri": len(info), "mostre": info[:SAMPLE_ROWS]}
        lines.append(
            f"{table}: {info['randuri']} rânduri au o clasificație Access care nu duce la "
            f"exact un rând din Clasificatii (după IdClsfAcc + IdUnitate).")
        for r in info["mostre"]:
            r = _as_dict(r)
            unit = "fără unitate" if r["id_unitate"] is None else f"unitatea {r['id_unitate']}"
            lines.append(
                f"    cheie {r['cheie']}: id Access {r['id_acc']}, {unit}, "
                f"{r['potriviri']} potriviri")
        if info["randuri"] > len(info["mostre"]):
            lines.append(f"    ... și încă {info['randuri'] - len(info['mostre'])}")
    return lines


def _as_dict(row) -> dict:
    """Rows from a dictionary cursor or a tuple cursor, as the same dict."""
    if isinstance(row, dict):
        return {k: row[k] for k in ("cheie", "id_acc", "id_unitate", "potriviri")}
    cheie, id_acc, id_unitate, potriviri = row
    return {"cheie": cheie, "id_acc": id_acc, "id_unitate": id_unitate,
            "potriviri": potriviri}
