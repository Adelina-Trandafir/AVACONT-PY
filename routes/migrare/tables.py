# routes/migrare/tables.py
# -----------------------------------------------------------------------------
# The migrated FX_ tables, in their DEFAULT write order (parents before
# children), and the rule that says which unit each row belongs to. The
# operator can reorder and untick tables in the migrator; the order HE sends is
# the order that runs. (The set used to mirror ALLOWED_TABLES in
# routes/forexe/seed.py; since the DDF and ORD families joined, it is wider.)
#
# The list is FIXED. Nothing is discovered from relationships and no prefix is
# matched: discovery is exactly what falls over on this schema.
# -----------------------------------------------------------------------------

# --- selection kinds ---------------------------------------------------------
OWN_DC_THEN_UNIT = "own_dc_then_unit"   # its own DC column, else IdUnitate
OWN_UNIT = "own_unit"                   # its own IdUnitate
BY_ANGAJAMENT = "by_angajament"         # CodAngajament, through the commitment set
BY_REZERVARE = "by_rezervare"           # IDRZ, through the reservation set
TWO_PARENTS = "two_parents"             # two candidate parents
BY_EXTRAS = "by_extras"                 # IDEXF, through the FX_Extrase_H headers
BY_EXTRAS_HEADER = "by_extras_header"   # own IdUnitate when present, else IDFXH
                                        # through its FX_Extrase_H header
BY_DDF = "by_ddf"                       # IDDF, through the FX_DDF set
BY_REV = "by_rev"                       # IDREV, through the FX_DDF_REV set
BY_ORD = "by_ord"                       # IDORD, through the FX_ORD set


class SeedTable(object):
    """Description of one migrated table. No logic."""

    def __init__(self, name, primary_key, selection,
                 key_column=None, key_column2=None):
        self.name = name
        self.primary_key = primary_key
        self.selection = selection
        self.key_column = key_column
        self.key_column2 = key_column2


# The default order is the operator's requested order (2026-08-21): the twelve
# core tables, then the DDF family, then the ORD family. The four tables the
# operator left OUT of that order (Salarii and the IMG/Plati leaves) close the
# list — every parent they need comes earlier, and they can be unticked or
# moved like any other row.
ALL = [
    # FX_Angajamente is the root, and carries both IdUnitate and DC: that is
    # where the chosen database's IdUnitate comes from.
    SeedTable("FX_Angajamente", "CodAngajament", OWN_DC_THEN_UNIT),
    SeedTable("FX_Indicatori", "CodAI", OWN_UNIT),
    SeedTable("FX_Istoric", "ID", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_Rezervari", "IDRZ", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_Receptii_R", "IDRR", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_Receptii_RHR", "IDRHR", OWN_UNIT),
    SeedTable("FX_Receptii_H", "IDRH", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_Receptii", "IDR", OWN_UNIT),
    SeedTable("FX_Plati", "IdPlataFX", OWN_UNIT),
    # A statement file can carry lines of several units; it is ours if at least
    # one of its FX_Extrase_H headers belongs to the chosen unit.
    SeedTable("FX_Extrase_F", "IDEXF", BY_EXTRAS, key_column="IDEXF"),
    SeedTable("FX_Extrase_H", "IDEXH", OWN_UNIT),
    # Statement lines often carry no IdUnitate of their own; their header does.
    SeedTable("FX_Extrase", "IDFXE", BY_EXTRAS_HEADER, key_column="IDFXH"),
    # The DDF family. FX_DDF carries DC + IdUnitate like the root; everything
    # below it chains through IDDF, then IDREV.
    SeedTable("FX_DDF", "IDDF", OWN_DC_THEN_UNIT),
    SeedTable("FX_DDF_REV", "IDREV", BY_DDF, key_column="IDDF"),
    SeedTable("FX_DDF_REV_SA", "ID", BY_REV, key_column="IDREV"),
    SeedTable("FX_DDF_REV_SB", "ID", BY_REV, key_column="IDREV"),
    SeedTable("FX_DDF_REV_ATT", "ID", BY_REV, key_column="IDREV"),
    SeedTable("FX_DDF_REV_PRT", "ID", BY_REV, key_column="IDREV"),
    # The ORD family. FX_ORD hangs off the commitment; its children chain
    # through IDORD.
    SeedTable("FX_ORD", "IDORD", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_ORD_PART", "IDORDPART", BY_ORD, key_column="IDORD"),
    SeedTable("FX_ORD_TBL", "IDORDTBL", BY_ORD, key_column="IDORD"),
    SeedTable("FX_ORD_DOC", "IDORDDOC", BY_ORD, key_column="IDORD"),
    SeedTable("FX_ORD_ATT", "IDORDATT", BY_ORD, key_column="IDORD"),
    # The four tables outside the operator's numbered order.
    SeedTable("FX_Salarii", "IDFXS", BY_ANGAJAMENT, key_column="CodAngajament"),
    SeedTable("FX_Rezervarii_IMG", "IDRZC", BY_REZERVARE, key_column="IDRZ"),
    # Two parents: IDRR first, then IDRH.
    SeedTable("FX_Receptii_IMG", "IDRDC", TWO_PARENTS,
              key_column="IDRR", key_column2="IDRH"),
    # Two parents, in the reverse order of the one above.
    SeedTable("FX_Receptii_Plati", "IDRP", TWO_PARENTS,
              key_column="IDRH", key_column2="IDRR"),
]

BY_NAME = dict((t.name, t) for t in ALL)

# Declared out of scope. If they appear in the file they are REPORTED, not migrated.
OUT_OF_SCOPE = ("FX_PRT_EXPL", "FX_CopacAngajamente")


def by_name(name):
    """Unknown key -> exception, never a silent no-op."""
    table = BY_NAME.get(name)
    if table is None:
        raise KeyError("Tabelul «%s» nu face parte din setul migrat." % name)
    return table


def selected(names):
    """
    The tables the operator ticked, in the ORDER the operator arranged them —
    the migrator lets the rows be reordered, and that order IS the write order.
    `None` means all of them, in the default parents-before-children order.

    A name outside the migrated set raises; it is never ignored silently, or the
    operator would believe he ticked something that does not get written. A name
    sent twice raises too: writing the same table twice is never what was meant.
    """
    if names is None:
        return list(ALL)
    chosen = []
    seen = set()
    for name in names:
        table = by_name(name)
        if name in seen:
            raise KeyError("Tabelul «%s» apare de două ori în listă." % name)
        seen.add(name)
        chosen.append(table)
    return chosen


# -----------------------------------------------------------------------------
# Column correlations: which MariaDB column an Access column is written into
# -----------------------------------------------------------------------------
# By default the two sides correlate ONE TO ONE by name, matched without regard
# to case (Access is case-insensitive, MariaDB's spelling is what we write).
# The rules below are the exceptions, and they exist because of the two
# classification columns:
#
#   * in Access, `IdClsf` points at a table in ANOTHER .accdb, while `IdClsfPY`
#     carries the id of the MariaDB `Clasificatii` row;
#   * in MariaDB the two swap names: `IdClsfAcc` holds the Access id and
#     `IdClsf` holds the MariaDB one.
#
# Correlating by name alone would write each id straight into the other's
# column. Each rule is applied ONLY when the target really has the column it
# names — a table whose MariaDB side never grew an `IdClsfAcc` keeps the plain
# one-to-one match. The operator sees the result in «Corelatii coloane» and can
# override any row of it; what he arranges is what travels.
#
# The routing columns (IdUnitate, DC, CodAngajament…) are NOT affected: routing
# reads the row with its ACCESS names, before any of this.
COLUMN_RENAMES = {
    "IdClsf": "IdClsfAcc",
    "IdClsfPY": "IdClsf",
}


def default_rename_map(target_columns):
    """
    Lower-cased Access column name -> the MariaDB column it is written into,
    for the DEFAULT correlations: the plain by-name match, plus every
    `COLUMN_RENAMES` exception whose target column really exists. A name absent
    from the result has no counterpart on MariaDB at all.
    """
    by_lower = dict((name.lower(), name) for name in target_columns)
    out = dict(by_lower)
    for access_name, target_name in COLUMN_RENAMES.items():
        exact = by_lower.get(target_name.lower())
        # A rename applies only when the target really has the column it names:
        # a table whose MariaDB side never grew an `IdClsfAcc` keeps the plain
        # one-to-one match.
        if exact is not None:
            out[access_name.lower()] = exact
    return out


def default_correlations(access_columns, target_columns):
    """
    The proposed Access column -> MariaDB column map for one table, as the
    migrator shows it in «Corelatii coloane». Every Access column appears; the
    value is `None` when the column has no counterpart at all -- that is what
    gets reported as missing, if the operator leaves it ticked.
    """
    rename = default_rename_map(target_columns)
    return dict((name, rename.get(name.lower())) for name in access_columns)
