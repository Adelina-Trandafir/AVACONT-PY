# routes/migrare/routing.py
# -----------------------------------------------------------------------------
# Which rows of the pushed file belong to the unit the operator picked?
#
# ONE ANSWER, and the file itself carries everything needed to give it.
#
# The operator picks a target database (a DC, e.g. 045_CTER). A single
# FX_<year>.accdb may well hold SEVERAL units -- that is the normal case, not the
# exception -- and only the rows of the picked one may be written. Everything
# else is left alone: not an error, simply another unit's data.
#
# WHERE THE UNIT COMES FROM (verified against the Access export, TABLES/*.md):
#   * FX_Angajamente carries BOTH `IdUnitate` and `DC`, so the pair
#     IdUnitate <-> DC is in the file itself;
#   * FX_Indicatori carries `IdUnitate` next to `CodAngajament`, which fills in
#     any commitment whose FX_Angajamente row has no unit.
# There is NO cale.accdb anywhere in this pipeline -- the [Cai] table it carried
# said exactly the same thing (IdUnitate -> DC) and is not needed.
#
# Only six of the sixteen tables carry a unit of their own; the rest reach one
# through a chain of parents, so the key sets are built BEFORE any row is tested:
#
#   unit       : IdUnitate       (from FX_Angajamente + FX_Indicatori)
#   commitment : CodAngajament   (FX_Angajamente, and its unit)
#   reservation: IDRZ            (FX_Rezervari, through the commitment)
#   receipt_r  : IDRR            (FX_Receptii_R, through the commitment)
#   receipt_h  : IDRH            (FX_Receptii_H, through the commitment)
#   statement  : IDEXF           (FX_Extrase_H, through the unit)
#   statement_h: IDEXH           (FX_Extrase_H itself — the header a statement
#                                 line reaches through IDFXH when it carries no
#                                 IdUnitate of its own)
#   ddf        : IDDF            (FX_DDF, by its own DC/IdUnitate)
#   rev        : IDREV           (FX_DDF_REV, through the DDF)
#   ord        : IDORD           (FX_ORD, through the commitment)
#
# Every set is kept TWICE: `ours` (the chosen unit's keys) and `known`
# (everything the file carries). The difference between them is the difference
# between "this row belongs to another unit" -- skipped silently, which is
# normal -- and "this key exists nowhere in the file", which is an integrity
# finding and is reported with a reason.
# -----------------------------------------------------------------------------

import logging

from . import accdb, tables

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Selection cannot continue at all. Message is Romanian: the operator reads it."""


def _as_long(value):
    """The number in an mdb-json value, or None. No guessing: "12,5" is not 12."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _as_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _key(value):
    """Commitment codes compare in lower case: Access mixes them freely."""
    return value.lower() if isinstance(value, str) else value


# -----------------------------------------------------------------------------
# The key sets
# -----------------------------------------------------------------------------

# State of one key with respect to the chosen unit.
OURS = "ours"
OTHER_UNIT = "other unit"
UNKNOWN = "unknown"
MISSING = "missing"


class KeySet(object):
    """One family of keys: the chosen unit's, and every one in the file."""

    def __init__(self):
        self.ours = set()
        self.known = set()

    def add(self, key, ours):
        self.known.add(key)
        if ours:
            self.ours.add(key)

    def state(self, key):
        if key is None:
            return MISSING
        if key in self.ours:
            return OURS
        if key in self.known:
            return OTHER_UNIT
        return UNKNOWN


class UnitPlan(object):
    """
    Who belongs to the chosen unit. Resolved ONCE, at the start of the analysis,
    and reused unchanged by the write pass -- otherwise the selection could shift
    between measuring and writing.
    """

    def __init__(self, db_name, sets, units, all_units, single_unit):
        self.db_name = db_name
        self.sets = sets                  # family -> KeySet
        self.units = sorted(units)        # IdUnitate values of the chosen database
        self.all_units = sorted(all_units)
        # The file carries a single unit (or declares none): a row without
        # IdUnitate is then ours, because there is nobody else it could be.
        self.single_unit = single_unit

    def selector_for(self, table):
        return TableSelector(table, self)

    def describe(self):
        if self.single_unit:
            return ("Fișierul poartă o singură unitate (%s), deci tot ce e în el merge "
                    "în baza aleasă, «%s»."
                    % (", ".join(str(u) for u in self.units) or "niciuna declarată",
                       self.db_name))
        return ("Fișierul poartă %d unități (%s). Se scriu DOAR rândurile unității "
                "«%s» (%s); restul rămân neatinse."
                % (len(self.all_units), ", ".join(str(u) for u in self.all_units),
                   self.db_name, ", ".join(str(u) for u in self.units)))


class TableSelector(object):
    """
    Keep this row or not? Returns `(keep, reject)`:

      keep=True                 -- the row belongs to the chosen unit; it is written;
      keep=False, reject=None   -- the row belongs to ANOTHER unit; skipped, which
                                   is normal and not a problem;
      keep=False, reject="..."  -- the key resolves nowhere in the file. The row is
                                   not lost silently: it goes into the report with
                                   its primary key and the reason.
    """

    def __init__(self, table, plan):
        self.table = table
        self.plan = plan

    def primary_key_of(self, row):
        value = row.get(self.table.primary_key)
        return "?" if value is None else str(value)

    def keep(self, row):
        kind = self.table.selection

        if kind == tables.OWN_DC_THEN_UNIT:
            dc = _as_text(row.get("DC"))
            if dc:
                return dc.lower() == self.plan.db_name.lower(), None
            return self._by_unit(row)

        if kind == tables.OWN_UNIT:
            return self._by_unit(row)

        if kind == tables.BY_ANGAJAMENT:
            code = _as_text(row.get(self.table.key_column))
            # The key is lower case, but the reason shows the code exactly as it
            # is in Access: that is where the operator will look for it.
            return self._by_set("commitment", _key(code), self.table.key_column,
                                "FX_Angajamente", shown=code)

        if kind == tables.BY_REZERVARE:
            return self._by_set("reservation", _as_long(row.get(self.table.key_column)),
                                self.table.key_column, "FX_Rezervari")

        if kind == tables.BY_EXTRAS:
            return self._by_set("statement", _as_long(row.get(self.table.key_column)),
                                self.table.key_column, "FX_Extrase_H")

        if kind == tables.BY_EXTRAS_HEADER:
            # The row's own IdUnitate wins when it is filled in; most statement
            # lines leave it NULL and carry only IDFXH, the header. The header
            # has the unit, so the line is routed through it -- a NULL IdUnitate
            # is NOT an error here (that reading is what once rejected 3110
            # perfectly attributable FX_Extrase rows).
            unit = _as_long(row.get("IdUnitate"))
            if unit is not None:
                return unit in self.plan.sets["unit"].ours, None
            header = _as_long(row.get(self.table.key_column))
            if header is None:
                if self.plan.single_unit:
                    return True, None
                return False, ("nici IdUnitate, nici %s nu sunt completate, iar "
                               "fișierul poartă mai multe unități (%s)"
                               % (self.table.key_column,
                                  ", ".join(str(u) for u in self.plan.all_units)))
            return self._by_set("statement_h", header, self.table.key_column,
                                "FX_Extrase_H")

        if kind == tables.BY_DDF:
            return self._by_set("ddf", _as_long(row.get(self.table.key_column)),
                                self.table.key_column, "FX_DDF")

        if kind == tables.BY_REV:
            return self._by_set("rev", _as_long(row.get(self.table.key_column)),
                                self.table.key_column, "FX_DDF_REV")

        if kind == tables.BY_ORD:
            return self._by_set("ord", _as_long(row.get(self.table.key_column)),
                                self.table.key_column, "FX_ORD")

        if kind == tables.TWO_PARENTS:
            return self._two_parents(row)

        raise RoutingError("Regulă de selecție necunoscută pentru «%s»." % self.table.name)

    # --- helpers -------------------------------------------------------------

    def _by_unit(self, row):
        unit = _as_long(row.get("IdUnitate"))
        if unit is None:
            # Single-unit file: there is nobody else the row could belong to. With
            # several units a row without one cannot be attributed, and writing it
            # into the chosen database is exactly the guess that misplaces data.
            if self.plan.single_unit:
                return True, None
            return False, ("IdUnitate lipsește, iar fișierul poartă mai multe unități "
                           "(%s)" % ", ".join(str(u) for u in self.plan.all_units))
        return unit in self.plan.sets["unit"].ours, None

    def _by_set(self, family, key, column, parent_table, shown=None):
        if key is None:
            return False, "%s lipsește" % column
        state = self.plan.sets[family].state(key)
        if state == OURS:
            return True, None
        if state == OTHER_UNIT:
            return False, None
        return False, ("%s «%s» nu există în %s"
                       % (column, key if shown is None else shown, parent_table))

    def _two_parents(self, row):
        """
        Two candidate parents. If one belongs to the chosen unit and the other is
        definitely another unit's, that is a hard error, not a fallback: the links
        in Access contradict each other, and picking one would misplace the row.
        """
        first_family, first_parent = self._family_of(self.table.key_column)
        second_family, second_parent = self._family_of(self.table.key_column2)

        first = self.plan.sets[first_family].state(_as_long(row.get(self.table.key_column)))
        second = self.plan.sets[second_family].state(_as_long(row.get(self.table.key_column2)))

        if OURS in (first, second) and OTHER_UNIT in (first, second):
            raise RoutingError(
                "%s, rândul cu cheia «%s»: cei doi părinți nu sunt de acord — %s duce la "
                "unitatea aleasă, iar %s la alta. Migrarea se oprește; nu ghicim care are "
                "dreptate."
                % (self.table.name, self.primary_key_of(row),
                   self.table.key_column if first == OURS else self.table.key_column2,
                   self.table.key_column2 if first == OURS else self.table.key_column))

        if OURS in (first, second):
            return True, None
        if OTHER_UNIT in (first, second):
            return False, None
        if first == MISSING and second == MISSING:
            return False, ("nici %s, nici %s nu sunt completate"
                           % (self.table.key_column, self.table.key_column2))
        return False, ("nici %s (%s), nici %s (%s) nu există în fișier"
                       % (self.table.key_column, first_parent,
                          self.table.key_column2, second_parent))

    @staticmethod
    def _family_of(column):
        if column.upper() == "IDRR":
            return "receipt_r", "FX_Receptii_R"
        if column.upper() == "IDRH":
            return "receipt_h", "FX_Receptii_H"
        if column.upper() == "IDRZ":
            return "reservation", "FX_Rezervari"
        raise RoutingError("Coloana de părinte «%s» nu are o familie de chei." % column)


# -----------------------------------------------------------------------------
# Building the plan
# -----------------------------------------------------------------------------

FAMILIES = ("unit", "commitment", "reservation", "receipt_r", "receipt_h",
            "statement", "statement_h", "ddf", "rev", "ord")


def build_plan(fx_path, db_name, progress=None):
    """
    Read the file's key tables and decide what belongs to database `db_name`.

    The Memo-heavy tables (FX_Receptii_IMG, FX_Rezervarii_IMG) are NOT among
    them: their keys come from parents, so this step does not pay the price of
    the images.

    A key table missing from the file does not stop the build: that absence is a
    finding of the analysis, and the question here is a different one.
    """
    def say(msg):
        logger.info("migrare/selection: %s", msg)
        if progress:
            progress(msg)

    code_unit = {}      # code (lower case) -> IdUnitate
    code_dc = {}        # code (lower case) -> DC written on the row itself
    dc_units = {}       # DC (lower case) -> set(IdUnitate)
    all_units = set()

    say("Se citește FX_Angajamente (IdUnitate + DC).")
    for row in _rows(fx_path, "FX_Angajamente", say):
        code = _key(_as_text(row.get("CodAngajament")))
        unit = _as_long(row.get("IdUnitate"))
        dc = _as_text(row.get("DC"))
        if unit is not None:
            all_units.add(unit)
        if dc and unit is not None:
            dc_units.setdefault(dc.lower(), set()).add(unit)
        if code:
            if unit is not None:
                code_unit[code] = unit
            if dc:
                code_dc[code] = dc

    say("Se citește FX_Indicatori (IdUnitate pentru angajamentele rămase).")
    for row in _rows(fx_path, "FX_Indicatori", say):
        unit = _as_long(row.get("IdUnitate"))
        if unit is None:
            continue
        all_units.add(unit)
        code = _key(_as_text(row.get("CodAngajament")))
        if code and code not in code_unit:
            code_unit[code] = unit

    units = set(dc_units.get(db_name.lower(), ()))
    single_unit = False

    if not units:
        if len(all_units) <= 1:
            # A single-unit file is that unit's, however the DC was written.
            units = set(all_units)
            single_unit = True
            say("Fișierul nu numește baza «%s» pe niciun rând, dar poartă o singură "
                "unitate (%s) — se ia aceea."
                % (db_name, ", ".join(str(u) for u in sorted(all_units)) or "niciuna"))
        else:
            raise RoutingError(
                "Fișierul poartă %d unități (%s), dar niciun rând din FX_Angajamente nu "
                "arată spre baza «%s» (DC-urile găsite: %s). Alegeți baza care se "
                "potrivește fișierului, ori împingeți fișierul unității ăsteia."
                % (len(all_units), ", ".join(str(u) for u in sorted(all_units)),
                   db_name, ", ".join(sorted(dc_units)) or "niciunul"))
    elif len(all_units) <= 1:
        single_unit = True

    sets = dict((name, KeySet()) for name in FAMILIES)

    for unit in all_units:
        sets["unit"].add(unit, unit in units)

    for code in set(list(code_unit.keys()) + list(code_dc.keys())):
        dc = code_dc.get(code)
        if dc:
            ours = dc.lower() == db_name.lower()
        else:
            ours = code_unit.get(code) in units
        sets["commitment"].add(code, ours)

    say("Unitatea «%s»: %d angajamente din %d."
        % (db_name, len(sets["commitment"].ours), len(sets["commitment"].known)))

    _children_of_commitment(fx_path, "FX_Rezervari", "IDRZ", sets, "reservation", say)
    _children_of_commitment(fx_path, "FX_Receptii_R", "IDRR", sets, "receipt_r", say)
    _children_of_commitment(fx_path, "FX_Receptii_H", "IDRH", sets, "receipt_h", say)

    say("Se citește FX_Extrase_H (fișierele de extras ale unității).")
    for row in _rows(fx_path, "FX_Extrase_H", say):
        unit = _as_long(row.get("IdUnitate"))
        ours = unit in units if unit is not None else single_unit

        # The header itself, for the FX_Extrase lines that reach their unit
        # through IDFXH.
        header = _as_long(row.get("IDEXH"))
        if header is not None:
            sets["statement_h"].add(header, ours)

        statement = _as_long(row.get("IDEXF"))
        if statement is None:
            continue
        # One statement file can carry lines of several units; if ONE header is
        # ours, the file is ours too.
        sets["statement"].add(statement, ours or statement in sets["statement"].ours)
    say("Extrase: %d fișiere ale unității din %d."
        % (len(sets["statement"].ours), len(sets["statement"].known)))

    # The DDF chain: FX_DDF decides by its own DC/IdUnitate (the same rule its
    # selector applies), FX_DDF_REV follows the DDF, and the REV_* children
    # follow the revision.
    say("Se citește FX_DDF (angajamentele de plată ale unității).")
    for row in _rows(fx_path, "FX_DDF", say):
        ddf = _as_long(row.get("IDDF"))
        if ddf is None:
            continue
        dc = _as_text(row.get("DC"))
        unit = _as_long(row.get("IdUnitate"))
        if dc:
            ours = dc.lower() == db_name.lower()
        elif unit is not None:
            ours = unit in units
        else:
            ours = single_unit
        sets["ddf"].add(ddf, ours)
    say("DDF: %d ale unității din %d."
        % (len(sets["ddf"].ours), len(sets["ddf"].known)))

    say("Se citește FX_DDF_REV (reviziile).")
    for row in _rows(fx_path, "FX_DDF_REV", say):
        rev = _as_long(row.get("IDREV"))
        if rev is None:
            continue
        ddf = _as_long(row.get("IDDF"))
        sets["rev"].add(rev, ddf in sets["ddf"].ours if ddf is not None else False)
    say("Revizii: %d ale unității din %d."
        % (len(sets["rev"].ours), len(sets["rev"].known)))

    _children_of_commitment(fx_path, "FX_ORD", "IDORD", sets, "ord", say)

    plan = UnitPlan(db_name, sets, units, all_units, single_unit)
    say(plan.describe())
    return plan


def _children_of_commitment(fx_path, table, key_column, sets, family, say):
    say("Se citește %s." % table)
    for row in _rows(fx_path, table, say):
        key = _as_long(row.get(key_column))
        if key is None:
            continue
        code = _key(_as_text(row.get("CodAngajament")))
        sets[family].add(key, code in sets["commitment"].ours if code else False)
    say("%s: %d chei ale unității din %d."
        % (table, len(sets[family].ours), len(sets[family].known)))


def _rows(fx_path, table, say):
    """
    The rows of one key table. A table that is missing or unreadable is SAID in
    the log and skipped: its absence is a finding of the analysis, not of the
    selection.
    """
    try:
        for row in accdb.iter_rows(fx_path, table):
            yield row
    except accdb.AccdbError as exc:
        say("«%s» nu a putut fi citit la construirea selecției: %s" % (table, exc))
