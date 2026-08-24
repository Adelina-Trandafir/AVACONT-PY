# Offline unit tests for the mdb-schema column parser (routes/migrare/accdb.py).
# Run from the PYTHON folder:
#   python -m pytest tests/test_migrare_accdb_columns.py
#
# These need NO config.py, NO MariaDB, NO Access file and NO mdbtools:
# `parse_columns` is pure text, which is exactly why it is a separate function.
#
# The bug they exist for (2026-08-21): the parser was ONE anchored expression
# covering name + type + optional size and nothing else, so a line with anything
# AFTER the size -- `NOT NULL` on every primary key -- matched nothing and the
# column was dropped from the list without a word. `CodAngajament` disappeared
# from the migrator's column grid and from the INSERT, and MariaDB answered
# «1364 ... doesn't have a default value» about a column nobody chose to omit.

from routes.migrare import accdb


SCHEMA = """CREATE TABLE `FX_Angajamente`
 (
\t`CodAngajament`\t\t\tvarchar (50) NOT NULL, \n\t`Denumire`\t\t\tvarchar (255), \n\t`IdUnitate`\t\t\tlong int NOT NULL, \n\t`Valoare`\t\t\tnumeric (19,4) NOT NULL, \n\t`DataFX`\t\t\tdatetime, \n\t`Nota`\t\t\ttext (536870910), \n);
"""


def names(text):
    return [c["nume"] for c in accdb.parse_columns(text)]


def by_name(text, name):
    return dict((c["nume"], c) for c in accdb.parse_columns(text))[name]


# --- the regression itself ---------------------------------------------------

def test_o_coloana_not_null_cu_dimensiune_nu_se_pierde():
    assert "CodAngajament" in names(SCHEMA)


def test_toate_coloanele_ajung_in_lista():
    assert names(SCHEMA) == ["CodAngajament", "Denumire", "IdUnitate",
                             "Valoare", "DataFX", "Nota"]


def test_ordinea_din_fisier_se_pastreaza():
    assert names(SCHEMA)[0] == "CodAngajament"
    assert names(SCHEMA)[-1] == "Nota"


# --- type and size -----------------------------------------------------------

def test_tipul_nu_inghite_constrangerea():
    # «long int NOT NULL» used to come back as the type «LONG INT NOT NULL».
    assert by_name(SCHEMA, "IdUnitate")["tip"] == "LONG INT"


def test_dimensiunea_vine_din_paranteza():
    assert by_name(SCHEMA, "CodAngajament")["marime"] == 50
    assert by_name(SCHEMA, "Denumire")["marime"] == 255


def test_zecimalul_pastreaza_prima_cifra_a_perechii():
    assert by_name(SCHEMA, "Valoare")["marime"] == 19


def test_tipul_fara_dimensiune_nu_are_marime():
    assert by_name(SCHEMA, "DataFX")["tip"] == "DATETIME"
    assert by_name(SCHEMA, "DataFX")["marime"] is None


# --- the rule that matters more than the type --------------------------------

def test_un_tip_de_necitit_pastreaza_totusi_coloana():
    """
    The type decides nothing -- validation reads it from MariaDB. A missing NAME
    silently changes the INSERT. So an unreadable type must never cost a column.
    """
    text = "CREATE TABLE `T`\n (\n\t`Ciudat`\t\t\t?!?, \n);\n"
    cols = accdb.parse_columns(text)
    assert [c["nume"] for c in cols] == ["Ciudat"]
    assert cols[0]["tip"] == ""


def test_o_constrangere_necunoscuta_nu_costa_coloana():
    text = ("CREATE TABLE `T`\n (\n"
            "\t`A`\t\t\tvarchar (10) COMMENT 'a, b', \n"
            "\t`B`\t\t\tlong int NOT NULL AUTO_INCREMENT, \n);\n")
    assert [c["nume"] for c in accdb.parse_columns(text)] == ["A", "B"]


# --- the frame ---------------------------------------------------------------

def test_ce_e_inaintea_lui_create_table_se_ignora():
    text = "-- comentariu cu `ceva` in el\n" + SCHEMA
    assert names(text) == names(SCHEMA)


def test_se_opreste_la_paranteza_de_inchidere():
    text = SCHEMA + "\nCREATE INDEX x ON `FX_Angajamente` (`Denumire`);\n"
    assert names(text) == names(SCHEMA)
