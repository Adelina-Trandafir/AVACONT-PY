# routes/migrare/validate.py
# -----------------------------------------------------------------------------
# Does this Access row fit the MariaDB column it is headed for?
#
# The rules come from the TARGET, never from Access: MariaDB is what accepts or
# refuses the row, so information_schema is the only honest source for the type,
# the length, the nullability and the foreign keys.
#
# Findings fall into two classes, and the class is what drives the two buttons in
# the migrator:
#
#   BLOCANT  -- structura sau valoarea nu incap: tabel/coloana lipsa, tip gresit,
#               depasire de lungime sau de interval, NULL intr-o coloana NOT NULL.
#               Cat timp exista unul, NICIUN buton nu porneste.
#   FORTABIL -- link integrity: duplicate primary key, row whose key exists
#               nowhere in the file, foreign key with no match on a column that
#               ACCEPTS NULL. «Ruleaza» ramane oprit, «Forteaza rularea»
#               porneste si SARE peste randurile vinovate, fara sa le piarda din
#               raport.
#
# A foreign key with no match on a NOT NULL column is BLOCANT instead: there is
# no NULL to fall back on, so the row can be neither written nor emptied. On a
# nullable one the row IS written, with that column set to NULL -- the row is
# kept and only the link is lost (operator decision, 2026-08-22).
#
# The write ORDER is checked too, before any row is read: a ticked table written
# before a ticked table it depends on is BLOCANT, because a foreign key needs
# the referenced row present at INSERT time and emptying the parent first makes
# that failure certain rather than avoiding it.
#
# The operator can narrow WHICH Access columns travel (the `columns` argument):
# an unticked column is simply not written, so it is neither reported as missing
# from the target nor measured against it. The primary-key columns always
# travel -- without them there is no row identity to upsert on.
#
# A foreign key whose parent table is written IN THE SAME RUN is checked against
# the union of the target's rows and the rows this run itself will write: on an
# empty database everything is "missing" otherwise, which is exactly wrong. Ten
# of the constraints point outside the migrated set (Parteneri, Clasificatii,
# Unitati); there is no "will be written" to add, so the target's rows are the
# whole answer.
# -----------------------------------------------------------------------------

import datetime
import decimal
import logging

from . import parser, tables

logger = logging.getLogger(__name__)

BLOCANT = "BLOCANT"
FORTABIL = "FORTABIL"

# --- felurile de constatare --------------------------------------------------
F_TABEL_LIPSA = "TABEL_LIPSA"
F_COLOANA_LIPSA = "COLOANA_LIPSA"
F_TIP = "TIP"
F_DIMENSIUNE = "DIMENSIUNE"
F_NUL_INTERZIS = "NUL_INTERZIS"
F_CHEIE_STRAINA = "CHEIE_STRAINA"
F_CHEIE_DUBLA = "CHEIE_DUBLA"
F_SELECTION = "SELECTIE"
# A target column MariaDB will not let out of the INSERT (primary key, or NOT
# NULL with no default) that is not in it -- unticked or uncorrelated. ASCII on
# the wire like every other token here (rule 0), so the migrator can match it.
F_COLOANA_OBLIGATORIE = "COLOANA_OBLIGATORIE"
# A ticked table written BEFORE a ticked table it depends on. Read from the live
# constraint set, never from a list in tables.py.
F_ORDINE_TABELE = "ORDINE_TABELE"
# A foreign-key value with no parent, in a NOT NULL column: NULL is not
# available there, so the row cannot be kept and nothing may be written.
F_CHEIE_STRAINA_OBLIGATORIE = "CHEIE_STRAINA_OBLIGATORIE"

CLASS_OF = {
    F_TABEL_LIPSA: BLOCANT,
    F_COLOANA_LIPSA: BLOCANT,
    F_TIP: BLOCANT,
    F_DIMENSIUNE: BLOCANT,
    F_NUL_INTERZIS: BLOCANT,
    F_CHEIE_STRAINA: FORTABIL,
    F_CHEIE_DUBLA: FORTABIL,
    F_SELECTION: FORTABIL,
    F_COLOANA_OBLIGATORIE: BLOCANT,
    F_ORDINE_TABELE: BLOCANT,
    F_CHEIE_STRAINA_OBLIGATORIE: BLOCANT,
}

# Cate exemple pastram pentru fiecare (tabel, coloana, fel). Numaratoarea e
# INTREAGA; doar lista de exemple e plafonata, ca raportul sa incapa pe ecran.
MAX_EXAMPLES = 25

# Cate valori punem intr-un singur `IN (...)` cand verificam cheile straine.
FK_BATCH = 1000

_INT_RANGES = {
    "tinyint": (-128, 127, 0, 255),
    "smallint": (-32768, 32767, 0, 65535),
    "mediumint": (-8388608, 8388607, 0, 16777215),
    "int": (-2147483648, 2147483647, 0, 4294967295),
    "integer": (-2147483648, 2147483647, 0, 4294967295),
    "bigint": (-9223372036854775808, 9223372036854775807, 0, 18446744073709551615),
}

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%y %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


class ValidationError(Exception):
    """Analiza nu poate continua deloc — mesaj in romana."""


# -----------------------------------------------------------------------------
# Schema tintei
# -----------------------------------------------------------------------------

class TargetSchema(object):
    """Coloanele, cheile primare si cheile straine ale bazei de unitate."""

    def __init__(self, conn, db_name, table_names):
        self.db_name = db_name
        self.columns = {}       # tabel -> {coloana: meta}
        self.primary_key = {}   # tabel -> [coloane]
        self.foreign_keys = {}  # tabel -> [ {coloana, tabel_ref, coloana_ref, nume} ]
        # (schema, tabel, coloana) -> auto_increment?, for the columns the
        # foreign keys POINT AT -- including the ones outside the migrated set
        # (Parteneri, Clasificatii, Unitati), which `columns` never loads.
        self.referenced = {}
        self._load(conn, table_names)

    def has(self, table):
        return table in self.columns

    def referenced_is_auto(self, fk):
        """
        Is the column this foreign key points at AUTO_INCREMENT?

        It decides how a 0 reads: no auto-increment table can hold a row 0, so a
        0 in the child is an orphan whatever a probe of the parent answers.
        Unknown (the parent could not be read) answers False -- we do not invent
        an orphan.
        """
        return bool(self.referenced.get(
            (fk["schema_ref"], fk["tabel_ref"], fk["coloana_ref"])))

    def _load(self, conn, table_names):
        placeholders = ",".join(["%s"] * len(table_names))
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, "
                "       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, "
                "       IS_NULLABLE, COLUMN_DEFAULT, EXTRA, COLUMN_KEY "
                "  FROM information_schema.COLUMNS "
                " WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN (" + placeholders + ") "
                " ORDER BY TABLE_NAME, ORDINAL_POSITION",
                tuple([self.db_name] + list(table_names)))
            for row in cur.fetchall():
                (table, name, data_type, column_type, char_len, precision, scale,
                 nullable, default, extra, key) = row
                self.columns.setdefault(table, {})[name] = {
                    "nume": name,
                    "tip": (data_type or "").lower(),
                    "tip_complet": column_type if isinstance(column_type, str)
                                   else (column_type or b"").decode("utf-8", "replace"),
                    "lungime": int(char_len) if char_len is not None else None,
                    "precizie": int(precision) if precision is not None else None,
                    "scara": int(scale) if scale is not None else None,
                    "accepta_nul": (nullable or "").upper() == "YES",
                    "are_implicit": default is not None,
                    "auto": "auto_increment" in (extra or "").lower(),
                    # The raw EXTRA, kept whole: `auto` above answers only one of
                    # its questions, and `is_required` needs the others
                    # (generated columns, on-update expressions).
                    "extra": (extra or "").lower(),
                    "cheie": key,
                }

            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME "
                "  FROM information_schema.KEY_COLUMN_USAGE "
                " WHERE TABLE_SCHEMA = %s AND CONSTRAINT_NAME = 'PRIMARY' "
                "   AND TABLE_NAME IN (" + placeholders + ") "
                " ORDER BY TABLE_NAME, ORDINAL_POSITION",
                tuple([self.db_name] + list(table_names)))
            for table, column in cur.fetchall():
                self.primary_key.setdefault(table, []).append(column)

            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, "
                "       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, CONSTRAINT_NAME "
                "  FROM information_schema.KEY_COLUMN_USAGE "
                " WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
                "   AND TABLE_NAME IN (" + placeholders + ")",
                tuple([self.db_name] + list(table_names)))
            for table, column, ref_schema, ref_table, ref_column, name in cur.fetchall():
                self.foreign_keys.setdefault(table, []).append({
                    "coloana": column,
                    "schema_ref": ref_schema,
                    "tabel_ref": ref_table,
                    "coloana_ref": ref_column,
                    "nume": name,
                })

            # One more pass over information_schema, for the tables the keys
            # POINT AT. Ten of the constraints leave the migrated set entirely
            # (Parteneri, Clasificatii, Unitati), so their columns are not in
            # `self.columns` and there is nowhere else to read this from.
            wanted = {}
            for fks in self.foreign_keys.values():
                for fk in fks:
                    wanted.setdefault(fk["schema_ref"], set()).add(fk["tabel_ref"])
            for ref_schema, ref_tables in wanted.items():
                names = sorted(ref_tables)
                slots = ",".join(["%s"] * len(names))
                cur.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME, EXTRA "
                    "  FROM information_schema.COLUMNS "
                    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN (" + slots + ")",
                    tuple([ref_schema] + names))
                for ref_table, ref_column, extra in cur.fetchall():
                    self.referenced[(ref_schema, ref_table, ref_column)] = (
                        "auto_increment" in (extra or "").lower())
        finally:
            cur.close()


# -----------------------------------------------------------------------------
# Verificarea unei valori fata de coloana ei
# -----------------------------------------------------------------------------

def check_value(meta, value):
    """
    Intoarce (fel, mesaj) sau None daca valoarea incape.

    Nu converteste nimic: doar spune daca MariaDB ar accepta-o. Conversia reala o
    face driverul la scriere, din aceleasi valori.
    """
    # NULL: mdb-json omite coloana, deci `value is None` inseamna chiar NULL in
    # Access, nu sir gol. Diferenta conteaza exact aici.
    if value is None:
        if not meta["accepta_nul"] and not meta["are_implicit"] and not meta["auto"]:
            return F_NUL_INTERZIS, "coloana nu acceptă NULL și nu are valoare implicită"
        return None

    tip = meta["tip"]

    # Sirul gol intr-o coloana care nu e text: MariaDB il refuza sau il aduce la
    # zero tacut, dupa mod. Il tratam ca lipsa de valoare, nu ca eroare de tip.
    if isinstance(value, str) and not value.strip() and tip not in (
            "char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set"):
        if not meta["accepta_nul"] and not meta["are_implicit"] and not meta["auto"]:
            return F_NUL_INTERZIS, "valoare goală într-o coloană care nu acceptă NULL"
        return None

    # --- text -------------------------------------------------------------
    if tip in ("char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set"):
        text = value if isinstance(value, str) else str(value)
        limit = meta["lungime"]
        if limit is not None and len(text) > limit:
            return (F_DIMENSIUNE,
                    "text de %d caractere într-o coloană de %d" % (len(text), limit))
        return None

    # --- intregi ----------------------------------------------------------
    if tip in _INT_RANGES:
        number = _as_int(value)
        if number is None:
            return F_TIP, "valoare care nu este un număr întreg: «%s»" % _short(value)
        low, high, ulow, uhigh = _INT_RANGES[tip]
        if "unsigned" in meta["tip_complet"].lower():
            low, high = ulow, uhigh
        if number < low or number > high:
            return (F_DIMENSIUNE,
                    "%d în afara intervalului %s (%d … %d)" % (number, tip, low, high))
        return None

    # --- zecimale ---------------------------------------------------------
    if tip in ("decimal", "numeric"):
        number = _as_decimal(value)
        if number is None:
            return F_TIP, "valoare care nu este un număr: «%s»" % _short(value)
        precision = meta["precizie"] or 10
        scale = meta["scara"] or 0
        whole = precision - scale
        limit = decimal.Decimal(10) ** whole
        if abs(number) >= limit:
            return (F_DIMENSIUNE,
                    "%s depășește DECIMAL(%d,%d)" % (number, precision, scale))
        return None

    if tip in ("float", "double", "real"):
        if _as_float(value) is None:
            return F_TIP, "valoare care nu este un număr: «%s»" % _short(value)
        return None

    # --- date -------------------------------------------------------------
    if tip in ("date", "datetime", "timestamp"):
        if _as_datetime(value) is None:
            return F_TIP, "valoare care nu este o dată: «%s»" % _short(value)
        return None

    if tip == "time":
        return None

    # --- binare / bit ------------------------------------------------------
    if tip in ("blob", "tinyblob", "mediumblob", "longblob", "varbinary", "binary"):
        raw = value if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8", "replace")
        limit = meta["lungime"]
        if limit is not None and len(raw) > limit:
            return F_DIMENSIUNE, "%d octeți într-o coloană de %d" % (len(raw), limit)
        return None

    if tip in ("bit", "boolean", "bool"):
        if _as_int(value) is None:
            return F_TIP, "valoare care nu este 0 sau 1: «%s»" % _short(value)
        return None

    # Tip pe care nu-l cunoastem: nu inventam o regula, il lasam sa treaca si sa
    # fie MariaDB cel care refuza, cu eroarea lui.
    return None


def is_required(meta):
    """
    Is this a target column MariaDB will refuse to leave out of an INSERT?

    True for NOT NULL columns with no default that the server does not fill in
    by itself. Under strict mode, omitting one of those is error 1364, «Field
    '<col>' doesn't have a default value» -- which is about the COLUMN LIST, not
    about the values (a NULL in a NOT NULL column is 1048, a different error).

    False for anything the server supplies on its own: `auto_increment`, a
    generated column, an `on update` expression. A column with a DEFAULT is
    already covered by `are_implicit`, which is tested first.
    """
    if meta.get("accepta_nul"):
        return False
    if meta.get("are_implicit"):
        return False
    if meta.get("auto"):
        return False
    extra = (meta.get("extra") or "").lower()
    if "generated" in extra or "on update" in extra:
        return False
    return True


def required_columns_of(target_columns, pk_columns):
    """
    The target columns that must be in the INSERT: the primary key plus every
    `is_required` one. The names are the TARGET's own -- never the Access-side
    key from `tables.py`, which may not even be a column over there.
    """
    protected = set(pk_columns or [])
    for name, meta in target_columns.items():
        if is_required(meta):
            protected.add(name)
    return protected


# Why an Access column did not make it into the INSERT. ASCII tokens, rendered
# in Romanian by `describe_skipped` at the two places the operator reads them.
SKIP_UNCORRELATED = "necorelata"
SKIP_UNTICKED = "debifata"

_SKIP_TEXT = {
    SKIP_UNCORRELATED: "necorelată",
    SKIP_UNTICKED: "debifată",
}


def insert_columns(table_name, access_columns, rename, chosen_cols):
    """
    The TARGET columns one INSERT into `table_name` will carry, in the Access
    file's own column order, plus what was left out and why.

    Two filters, and telling them apart is the whole point: a column is dropped
    either because no correlation sends it anywhere (`rename` has no entry --
    the operator set «(nu se scrie)», or MariaDB simply has no such column), or
    because the operator unticked it (`chosen_cols`). Both used to be silent.

    Returns (columns, skipped), where `skipped` is [(access_name, reason)].

    Two Access columns landing on the SAME target column stop everything: one of
    the two values would be thrown away and nobody can say which.

    ONE function on purpose. The analysis has to measure exactly the list the
    write will build; two copies of this rule drifting apart is how a required
    column got dropped from the statement in the first place.
    """
    columns = []
    skipped = []
    for name in access_columns:
        target_name = rename.get(name.lower())
        if target_name is None:
            skipped.append((name, SKIP_UNCORRELATED))
            continue
        if chosen_cols is not None and target_name not in chosen_cols:
            skipped.append((name, SKIP_UNTICKED))
            continue
        if target_name in columns:
            raise ValidationError(
                "În «%s», două coloane din Access sunt corelate cu «%s» de pe "
                "MariaDB. Repară corelațiile și analizează din nou."
                % (table_name, target_name))
        columns.append(target_name)
    return columns, skipped


def describe_skipped(skipped):
    """The skipped Access columns as one Romanian phrase, each with its reason,
    for the job log and the dump file headers."""
    return ", ".join("%s (%s)" % (name, _SKIP_TEXT.get(reason, reason))
                     for name, reason in skipped)


def missing_required(target_columns, columns):
    """
    The required target columns that are NOT in the INSERT column list, in the
    target's own order. Empty is the only good answer: anything else is MariaDB
    error 1364 waiting to happen.
    """
    present = set(columns)
    return [name for name, meta in target_columns.items()
            if name not in present and is_required(meta)]


def required_columns_message(table_name, missing):
    """The one sentence both the analysis and the write say about it."""
    return ("«%s»: coloanele %s de pe MariaDB nu acceptă lipsa lor (cheie primară "
            "sau NOT NULL fără valoare implicită), dar nu ajung în INSERT — sunt "
            "debifate sau necorelate. MariaDB ar răspunde «doesn't have a default "
            "value»." % (table_name, ", ".join("«%s»" % n for n in missing)))


def _short(value):
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def as_int(value):
    """Public: aceleasi reguli si la pasul de scriere."""
    return _as_int(value)


def _as_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _as_decimal(value):
    try:
        return decimal.Decimal(str(value).strip())
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None


def _as_float(value):
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _as_datetime(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# -----------------------------------------------------------------------------
# Raportul
# -----------------------------------------------------------------------------

class Report(object):

    def __init__(self, db_name):
        self.db_name = db_name
        self._buckets = {}      # (tabel, coloana, fel) -> {"numar", "exemple"}
        self.tables = []        # the ticked tables, in write order
        self.per_table = {}     # tabel -> {"citite", "ale_unitatii", "de_scris", "sarite"}
        # Valorile de cheie straina care LIPSESC pe tinta, pastrate ca sa poata fi
        # sarite la rulare fara sa mai intrebam serverul inca o data.
        self.missing_fk = {}    # (tabel, coloana) -> set(valori)
        # Aceleasi valori, dar pe coloane care ACCEPTA NULL: acolo randul se
        # pastreaza si se pierde doar legatura, deci scrierea le goleste in loc
        # sa sara randul. Cele doua dictionare nu se suprapun niciodata.
        self.null_fk = {}       # (tabel, coloana) -> set(valori)
        # Coloanele alese de operator (tabel -> [coloane]), pastrate pe raport ca
        # scrierea sa foloseasca EXACT ce a masurat analiza, nu o alta alegere.
        self.columns = None
        # Corelatiile lui (tabel -> {coloana_access: coloana_tinta}), pastrate din
        # acelasi motiv: rularea scrie in coloanele pe care le-a MASURAT analiza.
        self.mappings = None

    def add(self, table, column, kind, key, message, value=None, count=1):
        """
        `count` > 1 says «this same finding, on this many rows». The foreign-key
        checks use it: they collect DISTINCT values but the operator needs to
        read how many ROWS carry each one. The example list still shows the
        first row's key, which is the one worth looking up.
        """
        bucket = self._buckets.setdefault((table, column, kind),
                                          {"numar": 0, "exemple": []})
        bucket["numar"] += count
        if len(bucket["exemple"]) < MAX_EXAMPLES:
            bucket["exemple"].append({
                "cheie": key,
                "mesaj": message,
                "valoare": None if value is None else _short(value),
            })

    def counts(self):
        stats = {}
        for (table, column, kind), bucket in self._buckets.items():
            stats[kind] = stats.get(kind, 0) + bucket["numar"]
        return stats

    def has_blocking(self):
        return any(CLASS_OF.get(kind, BLOCANT) == BLOCANT
                   for (_, _, kind) in self._buckets)

    def is_clean(self):
        return not self._buckets

    def to_dict(self):
        constatari = []
        for (table, column, kind), bucket in sorted(self._buckets.items()):
            constatari.append({
                "tabel": table,
                "coloana": column,
                "fel": kind,
                "clasa": CLASS_OF.get(kind, BLOCANT),
                "numar": bucket["numar"],
                "exemple": bucket["exemple"],
            })
        # Blocantele primele: alea decid daca porneste ceva.
        constatari.sort(key=lambda c: (c["clasa"] != BLOCANT, c["tabel"], c["fel"]))
        return {
            "baza": self.db_name,
            "tabele": list(self.tables),
            "curat": self.is_clean(),
            "are_blocante": self.has_blocking(),
            "poate_rula": self.is_clean(),
            "poate_forta": (not self.is_clean()) and (not self.has_blocking()),
            "pe_fel": self.counts(),
            "pe_tabel": self.per_table,
            "constatari": constatari,
        }


# -----------------------------------------------------------------------------
# Analiza
# -----------------------------------------------------------------------------

def missing_table_dependents(schema, chosen_names, missing_name, db_name):
    """
    The ticked tables whose MariaDB foreign keys point at `missing_name`.

    A table absent from the target only STOPS the run when something else being
    written depends on it; a leaf (FX_Receptii_Plati was the case) is simply
    skipped, with a word in the log. Blocking the whole run over a table nobody
    ticked a dependency on helps no one.
    """
    dependents = []
    for name in chosen_names:
        if name == missing_name or name not in schema.columns:
            continue
        for fk in schema.foreign_keys.get(name, []):
            if fk["tabel_ref"] == missing_name and fk["schema_ref"] == db_name:
                dependents.append(name)
                break
    return dependents


def order_findings(schema, chosen_names, db_name):
    """
    The child-before-parent pairs in the order the operator arranged, measured
    against the LIVE constraint set.

    A foreign key needs the referenced row PRESENT AT INSERT TIME. Emptying the
    parent first in «Inlocuieste tot» does not relax that -- it makes the failure
    certain. So a ticked table written before a ticked table it depends on is a
    refusal, not a warning.

    Read from `information_schema` (through TargetSchema), never from a list in
    tables.py: the constraint that failed the run of 2026-08-21,
    FX_Rezervari__FX_DDF_REV, did not exist in the schema dump we had. Adding a
    constraint to the database has to change this answer with no code change.

    Returns [(child, parent, constraint_name, child_column)], each pair once.
    A key a table has on ITSELF cannot be ordered away and is left alone; so is
    one whose parent the operator did not tick (that parent is not written in
    this run at all, and its rows are checked against the target as they stand).
    """
    position = dict((name, index) for index, name in enumerate(chosen_names))
    seen = set()
    out = []
    for name in chosen_names:
        for fk in schema.foreign_keys.get(name, []):
            parent = fk["tabel_ref"]
            if fk["schema_ref"] != db_name or parent == name:
                continue
            if parent not in position or position[parent] < position[name]:
                continue
            key = (name, parent, fk["nume"])
            if key in seen:
                continue
            seen.add(key)
            out.append((name, parent, fk["nume"], fk["coloana"]))
    return out


def order_message(child, parent, constraint, column):
    """The one sentence both the analysis and the write say about it."""
    return ("«%s» se scrie înaintea lui «%s», dar depinde de el prin «%s» (%s). "
            "Mută-l după el în lista de tabele."
            % (child, parent, constraint, column))


def column_case_map(target_columns):
    """
    Access column names are case-insensitive («Cual» and «CUAL» are the same
    column there), MariaDB's exact spelling is what we write with. This is the
    bridge: lower-cased name -> the target's exact name.
    """
    return dict((name.lower(), name) for name in target_columns)


def column_rename_map(table_name, target_columns, mappings, protected=None):
    """
    Access column -> the TARGET column it is written into, keyed by the LOWER
    -cased Access name (Access is case-insensitive). A name absent from the
    result has no counterpart on MariaDB at all.

    The base is the plain one-to-one match by name. On top of it come the
    correlations: first the defaults from `tables.COLUMN_RENAMES` (the IdClsf /
    IdClsfPY crossover), then whatever the operator arranged in «Corelatii
    coloane» and sent as `mappings` -- {tabel: {coloana_access: coloana_tinta}}.
    His arrangement wins over the default, and the default over the name match.

    A correlation pointing at a column the target does not have is IGNORED, not
    obeyed: the analysis would otherwise measure the row against a column that
    is not there. An empty target means «this column does not travel». Two of
    them pointing at the SAME target column stop everything -- one of the two
    values would be thrown away, and neither we nor the operator can say which.

    `protected` is the set of TARGET column names MariaDB will not let out of
    the INSERT (see `required_columns_of`). An empty target aimed at one of them
    is REFUSED, not obeyed: the migrator sends the whole correlation map for
    every ticked table, so a single «(nu se scrie)» on a primary key used to
    delete the column from the statement and leave MariaDB to answer «doesn't
    have a default value» about a column the operator never meant to drop.
    `None` protects nothing, which is what the callers that do not know the
    target's keys want.
    """
    by_lower = column_case_map(target_columns)
    rename = tables.default_rename_map(target_columns)
    taken = {}
    for access_name, target_name in (mappings or {}).get(table_name, {}).items():
        key = str(access_name).lower()
        if not target_name:
            default = rename.get(key)
            if default is not None and protected and default in protected:
                raise ValidationError(
                    "În «%s», coloana «%s» este corelată cu «(nu se scrie)», dar "
                    "«%s» de pe MariaDB nu acceptă lipsa ei: e cheie primară sau "
                    "NOT NULL fără valoare implicită. Corelează-o înapoi înainte "
                    "de a rula din nou."
                    % (table_name, access_name, default))
            rename.pop(key, None)
            continue
        exact = by_lower.get(str(target_name).lower())
        if exact is None:
            continue
        if exact in taken and taken[exact][0] != key:
            raise ValidationError(
                "În «%s», coloanele «%s» și «%s» sunt corelate amândouă cu «%s» "
                "de pe MariaDB. O coloană a țintei poate primi o singură coloană "
                "din Access."
                % (table_name, taken[exact][1], access_name, exact))
        taken[exact] = (key, access_name)
        rename[key] = exact
    return rename


def with_target_names(row, rename):
    """
    A copy of the row keyed by the TARGET's exact column names, following
    `rename` (see `column_rename_map`); a key with no counterpart stays as it
    came, and is then reported as truly missing. The original row is left alone
    — the ROUTING reads it with the Access names.
    """
    out = {}
    for key, value in row.items():
        out[rename.get(key.lower(), key)] = value
    return out


def chosen_columns_of(table_name, columns, pk_columns, rename=None):
    """
    The set of TARGET columns the operator wants written for `table_name`, or
    None for "all of them". The primary-key columns are ALWAYS in: without them
    there is no row identity to upsert on, whatever was unticked. With a
    `rename` map the ticked Access names come back as the target columns they
    correlate to; the primary keys are added AFTER that, because they are the
    target's own names and must not be run through a correlation.
    """
    if not columns or table_name not in columns:
        return None
    chosen = set(str(c) for c in columns[table_name])
    if rename is not None:
        chosen = set(rename.get(c.lower(), c) for c in chosen)
    chosen.update(pk_columns)
    return chosen


def analyze(conn, db_name, fx_path, plan, only=None, columns=None,
            mappings=None, progress=None):
    """
    Read the file once, keep the chosen unit's rows and measure them against the
    target schema.

    `plan` is the UnitPlan resolved beforehand: it says what belongs to `db_name`.
    `only` is the list of tables the operator ticked, in the operator's order;
    None means the whole migrated set.
    `columns` is {tabel: [coloane]} — the Access columns the operator wants
    written. A table absent from the dict keeps all its columns. An unticked
    column is not written, so it is not measured either.
    `mappings` is {tabel: {coloana_access: coloana_tinta}} — the correlations the
    operator arranged in «Corelatii coloane». A table absent from the dict, or a
    column absent from its map, keeps the default correlation (see
    `column_rename_map`).

    Returns a Report. Writes nothing, nowhere.
    """
    def say(msg):
        logger.info("migrare/analiză: %s", msg)
        if progress:
            progress(msg)

    chosen = tables.selected(only)
    report = Report(db_name)
    report.tables = [t.name for t in chosen]
    report.columns = columns
    report.mappings = mappings
    schema = TargetSchema(conn, db_name, [t.name for t in chosen])
    in_run = set(t.name for t in chosen)

    # The arrangement, before a single row is read: a child written before its
    # parent cannot succeed whatever the rows say.
    for child, parent, constraint, column in order_findings(
            schema, [t.name for t in chosen], db_name):
        report.add(child, column, F_ORDINE_TABELE, "",
                   order_message(child, parent, constraint, column))

    # Cheile primare pe care ACEASTA rulare le va scrie, tabel cu tabel, ca o
    # cheie straina spre un parinte migrat in acelasi lot sa nu fie "lipsa".
    written_pks = {}

    for table in chosen:
        stats = {"citite": 0, "ale_unitatii": 0, "de_scris": 0, "sarite": 0}
        report.per_table[table.name] = stats

        if not schema.has(table.name):
            dependents = missing_table_dependents(schema, in_run, table.name, db_name)
            if dependents:
                report.add(table.name, "", F_TABEL_LIPSA, "",
                           "tabelul lipsește din baza «%s», iar %s arată spre el "
                           "prin cheie străină"
                           % (db_name, ", ".join(dependents)))
            else:
                say("«%s» lipsește din baza «%s» și niciun tabel bifat nu "
                    "depinde de el — se sare, nu se blochează nimic."
                    % (table.name, db_name))
            continue

        say("Se verifică «%s»." % table.name)
        target_columns = schema.columns[table.name]
        # The primary key first: `column_rename_map` needs it to know which
        # correlations it may NOT obey.
        pk_columns = schema.primary_key.get(table.name) or [table.primary_key]
        protected = required_columns_of(target_columns,
                                        schema.primary_key.get(table.name))
        rename = column_rename_map(table.name, target_columns, mappings, protected)
        selector = plan.selector_for(table)
        chosen_cols = chosen_columns_of(table.name, columns, pk_columns, rename)

        # Exactly the column list the write will build, measured HERE so a
        # column MariaDB will not let out of the INSERT is named in the report
        # instead of surfacing as «doesn't have a default value» mid-run.
        access_columns = [c["nume"] for c in accdb_columns(fx_path, table.name)]
        insert_cols, skipped = insert_columns(table.name, access_columns,
                                              rename, chosen_cols)
        if skipped:
            say("«%s»: coloane Access sărite — %s."
                % (table.name, describe_skipped(skipped)))
        for name in missing_required(target_columns, insert_cols):
            report.add(table.name, name, F_COLOANA_OBLIGATORIE, "",
                       required_columns_message(table.name, [name]))

        seen_keys = set()
        unknown_reported = set()
        converted = 0
        ambiguous = 0
        pk_single = pk_columns[0] if len(pk_columns) == 1 else None
        own_written = set()
        written_pks[table.name] = own_written
        # {(coloana): set(valori)} pentru fiecare cheie straina a tabelului
        fk_values = dict(((fk["coloana"], fk["nume"]), {})
                         for fk in schema.foreign_keys.get(table.name, [])
                         if chosen_cols is None or fk["coloana"] in chosen_cols)

        for row in _iter_table(fx_path, table.name, say):
            stats["citite"] += 1
            key = selector.primary_key_of(row)

            keep, reject = selector.keep(row)
            if reject:
                report.add(table.name, "", F_SELECTION, key, reject)
                continue
            if not keep:
                # The row belongs to another unit in the same file. Not a
                # problem: only the chosen unit gets written.
                continue
            stats["ale_unitatii"] += 1

            row_ok = True
            # Numele Access se potrivesc cu tinta FARA litere mari/mici; de
            # aici incolo randul e citit cu numele EXACTE ale tintei. Randul
            # original ramane neatins - selectia l-a citit deja cu numele lui.
            vrow = with_target_names(row, rename)
            # Shaped for the target BEFORE it is measured, with the same call the
            # write uses. Measuring the raw value and sending the shaped one --
            # or the other way round -- is how «04/28/26 15:28:03» passed the
            # analysis and was then refused by MariaDB.
            vrow, changes = parser.parse_row(vrow, target_columns)
            converted += len(changes)
            ambiguous += sum(1 for c in changes if c.ambiguous)

            # coloane care exista in Access si lipsesc din tinta. O coloana pe
            # care operatorul a debifat-o nu se scrie, deci lipsa ei din tinta
            # nu e o problema (asa ies din drum coloanele de rutare - IdUnitate,
            # DC - care in MariaDB nu mai exista, intentionat).
            for column in vrow.keys():
                if chosen_cols is not None and column not in chosen_cols:
                    continue
                if column not in target_columns and column not in unknown_reported:
                    unknown_reported.add(column)
                    report.add(table.name, column, F_COLOANA_LIPSA, key,
                               "coloana există în Access și lipsește din «%s»" % db_name)
                    row_ok = False

            # valorile, fata de coloanele tintei. O coloana debifata nu se
            # scrie, deci pe tinta ajunge NULL/implicitul ei - exact asta se
            # masoara.
            for name, meta in target_columns.items():
                if meta["auto"] and name not in vrow:
                    continue
                value = vrow.get(name)
                if chosen_cols is not None and name not in chosen_cols:
                    value = None
                problem = check_value(meta, value)
                if problem:
                    kind, message = problem
                    report.add(table.name, name, kind, key, message, value)
                    row_ok = False

            # cheie primara dubla in interiorul fisierului
            pk_value = tuple(vrow.get(c) for c in pk_columns)
            if pk_value in seen_keys:
                report.add(table.name, ",".join(pk_columns), F_CHEIE_DUBLA, key,
                           "cheia primară apare de mai multe ori în fișier")
                row_ok = False
            else:
                seen_keys.add(pk_value)

            # valorile de cheie straina, stranse acum si verificate dupa
            for (column, name), collected in fk_values.items():
                value = vrow.get(column)
                if value is not None and value != "":
                    seen_value = collected.get(value)
                    if seen_value is None:
                        # First row carrying it: its key is the example. The
                        # count is what the operator reads -- one orphan value
                        # on 300 rows is a different problem from one on one.
                        collected[value] = {"cheie": key, "numar": 1}
                    else:
                        seen_value["numar"] += 1

            if row_ok:
                stats["de_scris"] += 1
                if pk_single is not None:
                    _add_key_forms(own_written, vrow.get(pk_single))
            else:
                stats["sarite"] += 1

        if converted:
            say("«%s»: %d valori aduse la forma cerută de MariaDB (date, "
                "zecimale, Da/Nu)%s."
                % (table.name, converted,
                   "" if not ambiguous
                   else ", dintre care %d date cu zi/lună ambiguă" % ambiguous))

        _check_foreign_keys(conn, schema, table, fk_values, report, say,
                            db_name, in_run, written_pks, target_columns)

    return report


def _add_key_forms(target, value):
    """A key in every form a membership test may meet it: as it came, as an
    int, and lower-cased — Access mixes the case of its codes freely, and the
    target's collation compares them case-insensitively."""
    if value is None or value == "":
        return
    target.add(value)
    number = _as_int(value)
    if number is not None:
        target.add(number)
    if isinstance(value, str):
        target.add(value.lower())


def _key_known(known, value):
    if value in known:
        return True
    number = _as_int(value)
    if number is not None and number in known:
        return True
    return isinstance(value, str) and value.lower() in known


def accdb_columns(fx_path, table_name):
    """The Access column names of one table, in the file's own order."""
    from . import accdb
    try:
        return accdb.columns(fx_path, table_name)
    except accdb.AccdbError as exc:
        raise ValidationError(
            "Coloanele tabelului «%s» nu au putut fi citite din fișierul Access: %s"
            % (table_name, exc))


def _iter_table(fx_path, table_name, say):
    from . import accdb
    try:
        for row in accdb.iter_rows(fx_path, table_name):
            yield row
    except accdb.AccdbError as exc:
        raise ValidationError(
            "Tabelul «%s» nu a putut fi citit din fișierul Access: %s" % (table_name, exc))


def _check_foreign_keys(conn, schema, table, fk_values, report, say,
                        db_name, in_run, written_pks, target_columns):
    """
    O interogare pe cheie straina, in loturi. Nu incarcam tabelul referit in
    memorie: pot fi nomenclatoare de zeci de mii de randuri.

    Cand tabelul referit se scrie IN ACEEASI RULARE (e in `in_run` si vine
    inaintea acestuia in ordinea de scriere), o valoare care nu e inca pe tinta
    dar E printre randurile care se vor scrie NU lipseste: pe o baza goala
    absolut totul ar iesi «lipsa» altfel — exact pe dos.

    Ten of the constraints point OUTSIDE the migrated set (Parteneri,
    Clasificatii, Unitati). «Inlocuieste tot» cannot help there -- those parents
    are neither emptied nor written -- so the probe below is the only answer, and
    it is the same probe: `incoming` is simply None for them.

    An orphan is then judged by the CHILD COLUMN, not by the table:

      * the column accepts NULL -> the row is WRITTEN, with that column emptied.
        The row is kept; only the link is lost. FORTABIL, one finding per row.
        (Operator decision, 2026-08-22, replacing «skip the row» for this case.)
      * the column is NOT NULL -> NULL is not available, so there is no way to
        keep the row and nothing may be written. BLOCANT, with the column named.
        FX_ORD_TBL.IdUnitate is the one that will meet this.
    """
    for fk in schema.foreign_keys.get(table.name, []):
        collected = fk_values.get((fk["coloana"], fk["nume"]))
        if not collected:
            continue

        # Randurile pe care chiar aceasta rulare le scrie in tabelul referit —
        # doar cand cheia arata spre cheia primara a unui tabel din acelasi lot.
        incoming = None
        if (fk["schema_ref"] == db_name and fk["tabel_ref"] in in_run
                and fk["coloana_ref"] in (schema.primary_key.get(fk["tabel_ref"]) or [])):
            incoming = written_pks.get(fk["tabel_ref"], set())

        say("Se verifică cheia străină «%s» (%d valori distincte%s)."
            % (fk["nume"], len(collected),
               "" if incoming is None
               else "; se socotesc și rândurile scrise în aceeași rulare"))

        # A parent whose key is AUTO_INCREMENT can hold no row 0, so a 0 in the
        # child is an orphan whatever the probe answers -- including when this
        # very run would write a 0 into the parent.
        zero_is_orphan = schema.referenced_is_auto(fk)

        ref = "`%s`.`%s`" % (fk["schema_ref"], fk["tabel_ref"])
        values = list(collected.keys())
        present = set()
        cur = conn.cursor()
        try:
            for start in range(0, len(values), FK_BATCH):
                batch = values[start:start + FK_BATCH]
                placeholders = ",".join(["%s"] * len(batch))
                cur.execute(
                    "SELECT DISTINCT `%s` FROM %s WHERE `%s` IN (%s)"
                    % (fk["coloana_ref"], ref, fk["coloana_ref"], placeholders),
                    tuple(batch))
                for (found,) in cur.fetchall():
                    _add_key_forms(present, found)
        finally:
            cur.close()

        def _orphan(value):
            if zero_is_orphan and _as_int(value) == 0:
                return True
            if _key_known(present, value):
                return False
            return not (incoming is not None and _key_known(incoming, value))

        missing = [v for v in values if _orphan(v)]
        if not missing:
            continue

        meta = target_columns.get(fk["coloana"]) or {}
        rows = sum(collected[v]["numar"] for v in missing)
        if meta.get("accepta_nul"):
            report.null_fk.setdefault((table.name, fk["coloana"]), set()).update(missing)
            for value in missing:
                report.add(table.name, fk["coloana"], F_CHEIE_STRAINA,
                           collected[value]["cheie"],
                           "valoarea nu există în %s.%s — coloana acceptă NULL, "
                           "deci rândul se scrie cu «%s» gol"
                           % (fk["tabel_ref"], fk["coloana_ref"], fk["coloana"]),
                           value, count=collected[value]["numar"])
            say("«%s»: %d valori %s fără corespondent, scrise ca NULL."
                % (table.name, rows, fk["coloana"]))
        else:
            report.missing_fk.setdefault((table.name, fk["coloana"]), set()).update(missing)
            for value in missing:
                report.add(table.name, fk["coloana"], F_CHEIE_STRAINA_OBLIGATORIE,
                           collected[value]["cheie"],
                           "valoarea nu există în %s.%s, iar «%s» nu acceptă NULL "
                           "— rândul nu poate fi nici scris, nici golit"
                           % (fk["tabel_ref"], fk["coloana_ref"], fk["coloana"]),
                           value, count=collected[value]["numar"])
            say("«%s»: %d rânduri au «%s» fără corespondent, iar coloana nu "
                "acceptă NULL — blocant."
                % (table.name, rows, fk["coloana"]))
