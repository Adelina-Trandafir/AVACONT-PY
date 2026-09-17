# Tests for the rebuild of missing FX_Receptii_H / FX_Receptii rows from FX_Istoric --
# slice 0062 (POST /api/forexe/receptii/refacere).
#
# OFFLINE, same fake as test_forexe_prelucrare_pasi.py: the database dispatches by SQL
# PREFIX, writes are recorded and never interpreted. What is asserted is exactly what
# the walk decided to write, and that the dry run writes nothing at all.
import pytest

try:
    import routes.forexe.receptii_refacere as R
    from test_forexe_prelucrare_pasi import FakeCursor, COD, indicatori, _istoric_receptie
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"imports unavailable: {e}", allow_module_level=True)


# The FakeCursor answers `read_indicatori` too; give it the two indicators used below.
def _ind_rows(*coduri):
    return list(indicatori(*coduri).values())


def _cursor(istoric, h_existente=(), linii_existente=(), coduri=("AAB", "AA2")):
    return FakeCursor({
        "SELECT ID, HASH": list(istoric),
        "SELECT I.CodAI": _ind_rows(*coduri),
        "SELECT IDRH, IDRR, IDH FROM FX_Receptii_H": list(h_existente),
        "SELECT IDR, IDRH, IDH FROM FX_Receptii": list(linii_existente),
        "SELECT CodIndicator FROM FX_Receptii ": [],
        "SELECT MAX(NrCrt)": [],
        # step4d reads
        "SELECT H.IDRH, H.Total FROM FX_Receptii_H H": [],
        "SELECT R.IDR, R.CodAI": [],
    })


ISTORIC = [
    _istoric_receptie(7, "Rand: AAB,Suma receptie: 210 RON", 210),
    _istoric_receptie(8, "Rand: AA2,Suma receptie: 300 RON", 300, cod_ind="AA2"),
    _istoric_receptie(9, "Receptie: PLATA FACT., valoare: 510, (activ:true)", 510),
]


def test_reads_only_processed_history_rows():
    cur = _cursor(ISTORIC)
    R.refa_receptii(cur, COD, aplica=False)
    sql = cur.executed[0][0]
    assert sql.startswith("SELECT ID, HASH")
    assert "Prelucrat = 1" in sql
    assert "'Receptie'" in sql


def test_dry_run_counts_everything_and_writes_nothing():
    cur = _cursor(ISTORIC)
    rez = R.refa_receptii(cur, COD, aplica=False)
    assert rez["antete_lipsa"] == 1
    assert rez["linii_lipsa"] == 2
    assert rez["antete_scrise"] == 0
    assert rez["linii_scrise"] == 0
    assert cur.inserts("FX_Receptii_H") == []
    assert cur.inserts("FX_Receptii") == []
    assert cur.updates("FX_Receptii") == []


def test_apply_inserts_the_missing_header_and_its_lines():
    cur = _cursor(ISTORIC)
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_scrise"] == 1
    assert rez["linii_scrise"] == 2
    h = cur.inserts("FX_Receptii_H")
    assert len(h) == 1
    # _H_INSERT_SQL: (IDH, NrCrt, CodAngajament, DataH, Total, Descriere, EsteStergere)
    assert h[0][0] == 9
    assert h[0][4] == 510.0
    assert h[0][5] == "PLATA FACT."
    linii = cur.inserts("FX_Receptii")
    assert len(linii) == 2
    idrh = linii[0][0]
    assert all(p[0] == idrh for p in linii)
    # _REC_INSERT_SQL: (IDRH, IDH, ...) -- each line keeps its own history anchor.
    assert sorted(p[1] for p in linii) == [7, 8]
    # A new header has no IDRR, so there is no chain to recompute.
    assert rez["receptii_recalculate"] == []


def test_existing_header_is_reused_and_only_missing_lines_are_added():
    cur = _cursor(ISTORIC,
                  h_existente=[{"IDRH": 40, "IDRR": 5, "IDH": 9}],
                  linii_existente=[{"IDR": 100, "IDRH": 40, "IDH": 7}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_lipsa"] == 0
    assert cur.inserts("FX_Receptii_H") == []
    linii = cur.inserts("FX_Receptii")
    assert len(linii) == 1
    assert linii[0][0] == 40            # under the EXISTING header
    assert linii[0][1] == 8             # the line that was missing
    # The header sits on receptie 5, whose chain changed -> DIF recomputed.
    assert rez["receptii_recalculate"] == [5]


def test_orphan_line_is_relinked_not_duplicated():
    cur = _cursor(ISTORIC,
                  h_existente=[{"IDRH": 40, "IDRR": None, "IDH": 9}],
                  linii_existente=[{"IDR": 100, "IDRH": None, "IDH": 7},
                                   {"IDR": 101, "IDRH": None, "IDH": 8}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["linii_orfane"] == 2
    assert rez["linii_relegate"] == 2
    assert rez["linii_lipsa"] == 0
    assert cur.inserts("FX_Receptii") == []
    upd = cur.updates("FX_Receptii")
    assert sorted(upd) == [(40, 100), (40, 101)]
    # Header is unplaced (IDRR NULL) -> nothing to recompute yet.
    assert rez["receptii_recalculate"] == []


def test_zero_line_dropped_by_an_earlier_ingest_is_backfilled_under_its_header():
    """
    F31. Earlier ingests wrote only `Val_Receptie <> 0`, so the zero line of AA2 never
    reached FX_Receptii. The rebuild sees it as "missing by IDH", inserts it with
    Valoare = 0 under the existing header, and recomputes the chain it sits on.
    """
    istoric = [
        _istoric_receptie(7, "Rand: AAB,Suma receptie: 210 RON", 210),
        _istoric_receptie(8, "Rand: AA2,Suma receptie: 0 RON", 0, cod_ind="AA2"),
        _istoric_receptie(9, "Receptie: PLATA FACT., valoare: 210, (activ:true)", 210),
    ]
    cur = _cursor(istoric,
                  h_existente=[{"IDRH": 40, "IDRR": 5, "IDH": 9}],
                  linii_existente=[{"IDR": 100, "IDRH": 40, "IDH": 7}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["linii_lipsa"] == 1
    assert rez["linii_scrise"] == 1
    linii = cur.inserts("FX_Receptii")
    assert len(linii) == 1
    assert linii[0][0] == 40
    assert linii[0][1] == 8
    assert linii[0][8] == "AA2"
    assert linii[0][10] == 0.0
    assert rez["receptii_recalculate"] == [5]


def test_nothing_missing_means_nothing_written():
    cur = _cursor(ISTORIC,
                  h_existente=[{"IDRH": 40, "IDRR": 5, "IDH": 9}],
                  linii_existente=[{"IDR": 100, "IDRH": 40, "IDH": 7},
                                   {"IDR": 101, "IDRH": 40, "IDH": 8}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_lipsa"] == rez["linii_lipsa"] == rez["linii_orfane"] == 0
    assert cur.inserts("FX_Receptii_H") == []
    assert cur.inserts("FX_Receptii") == []
    assert cur.updates("FX_Receptii") == []
    assert rez["receptii_recalculate"] == []


def test_header_with_lines_lacking_idh_is_left_alone_and_reported():
    cur = _cursor(ISTORIC,
                  h_existente=[{"IDRH": 40, "IDRR": 5, "IDH": 9}],
                  linii_existente=[{"IDR": 100, "IDRH": 40, "IDH": None}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert cur.inserts("FX_Receptii") == []
    assert rez["linii_lipsa"] == 0
    assert any("IDRH 40" in a for a in rez["avertismente"])


def test_unknown_indicator_skips_the_line_instead_of_raising():
    """The header still gets its anchor (the line ROW exists in history, so this is not
    the F32 case): a later rebuild can attach the line once the indicator exists."""
    istoric = [
        _istoric_receptie(7, "Rand: ZZZ,Suma receptie: 210 RON", 210, cod_ind="ZZZ"),
        _istoric_receptie(9, "Receptie: PLATA FACT., valoare: 210, (activ:true)", 210),
    ]
    cur = _cursor(istoric)
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["linii_sarite"] == 1
    assert rez["antete_scrise"] == 1
    assert cur.inserts("FX_Receptii") == []
    assert any("ZZZ" in a for a in rez["avertismente"])


def test_header_only_snapshot_is_neither_inserted_nor_counted_missing():
    """F32: a header with no line row before it is an old-app error, not a snapshot."""
    istoric = [
        _istoric_receptie(5, "Receptie: PLATA FACT., valoare: 100, (activ:true)", 100),
    ] + ISTORIC
    cur = _cursor(istoric)
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_lipsa"] == 1
    assert rez["antete_scrise"] == 1
    assert [a[0] for a in cur.inserts("FX_Receptii_H")] == [9]
    assert len(cur.inserts("FX_Receptii")) == 2
    assert any("rând de istoric 5" in a and "nicio linie" in a
               for a in rez["avertismente"])


def test_header_only_snapshot_already_in_the_base_is_named_and_left_alone():
    """One that an earlier ingest (or the migration) already wrote stays -- every
    reader filters it out -- but the operator is told which IDRH it is."""
    istoric = [
        _istoric_receptie(5, "Receptie: PLATA FACT., valoare: 100, (activ:true)", 100),
    ]
    cur = _cursor(istoric, h_existente=[{"IDRH": 41, "IDRR": 3, "IDH": 5}])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_lipsa"] == 0
    assert cur.inserts("FX_Receptii_H") == []
    assert cur.updates("FX_Receptii_H") == []
    assert any("IDRH 41" in a for a in rez["avertismente"])


def test_trailing_lines_without_a_header_are_reported_not_invented():
    istoric = ISTORIC + [
        _istoric_receptie(10, "Rand: AAB,Suma receptie: 99 RON", 99),
    ]
    cur = _cursor(istoric)
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["linii_fara_antet"] == 1
    assert len(cur.inserts("FX_Receptii_H")) == 1
    assert len(cur.inserts("FX_Receptii")) == 2
    assert any("antet" in a for a in rez["avertismente"])


def test_deletion_header_keeps_its_flag():
    istoric = [
        _istoric_receptie(38, "Receptie: Plata ces, valoare: 7150, (activ:true)",
                          7150, descriere="Stergere receptie"),
    ]
    cur = _cursor(istoric)
    R.refa_receptii(cur, COD, aplica=True)
    assert cur.inserts("FX_Receptii_H")[0][6] == 1


def test_empty_history_returns_zero_counts_without_further_reads():
    cur = _cursor([])
    rez = R.refa_receptii(cur, COD, aplica=True)
    assert rez["antete_lipsa"] == 0
    assert len(cur.executed) == 1


# ===========================================================================
# Toata baza: un angajament per tranzactie, sumar + detalii doar unde e ceva
# ===========================================================================
class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class CursorPeCoduri(FakeCursor):
    """
    Raspunde la interogarea de istoric DIFERIT pe angajament (dupa parametrul `cod`),
    ca sa se poata proba o baza cu un angajament curat si unul cu lipsuri.
    """

    def __init__(self, istoric_pe_cod, coduri):
        super().__init__({
            "SELECT DISTINCT CodAngajament": [{"CodAngajament": c} for c in coduri],
            "SELECT I.CodAI": _ind_rows("AAB", "AA2"),
            "SELECT IDRH, IDRR, IDH FROM FX_Receptii_H": [],
            "SELECT IDR, IDRH, IDH FROM FX_Receptii": [],
            "SELECT CodIndicator FROM FX_Receptii ": [],
            "SELECT MAX(NrCrt)": [],
        })
        self.istoric_pe_cod = istoric_pe_cod

    def execute(self, sql, params=None):
        plat = " ".join(sql.split())
        if plat.startswith("SELECT ID, HASH"):
            self.executed.append((plat, params))
            valoare = self.istoric_pe_cod.get(params[0], [])
            if isinstance(valoare, Exception):
                raise valoare
            self._result = list(valoare)
            return
        super().execute(sql, params)


def test_sweep_visits_every_angajament_and_reports_only_the_ones_with_findings():
    conn = FakeConn()
    cur = CursorPeCoduri({"A1": ISTORIC, "A2": []}, ["A1", "A2"])
    sumar = R.refa_toate(conn, cur, aplica=False)
    assert sumar["angajamente"] == 2
    assert sumar["cu_lipsuri"] == 1
    assert sumar["totaluri"]["antete_lipsa"] == 1
    assert sumar["totaluri"]["linii_lipsa"] == 2
    assert [d["cod"] for d in sumar["detalii"]] == ["A1"]
    assert sumar["erori"] == []
    # Dry run: one rollback per angajament, never a commit.
    assert conn.commits == 0
    assert conn.rollbacks == 2


def test_sweep_commits_once_per_angajament_when_applying():
    conn = FakeConn()
    cur = CursorPeCoduri({"A1": ISTORIC, "A2": ISTORIC}, ["A1", "A2"])
    sumar = R.refa_toate(conn, cur, aplica=True)
    assert conn.commits == 2
    assert sumar["totaluri"]["antete_scrise"] == 2
    assert sumar["totaluri"]["linii_scrise"] == 4
    assert len(cur.inserts("FX_Receptii_H")) == 2


def test_sweep_isolates_a_failing_angajament_and_carries_on():
    conn = FakeConn()
    cur = CursorPeCoduri({"A1": ISTORIC, "A2": RuntimeError("FK a refuzat"), "A3": ISTORIC},
                         ["A1", "A2", "A3"])
    sumar = R.refa_toate(conn, cur, aplica=True)
    assert sumar["angajamente"] == 3
    assert [e["cod"] for e in sumar["erori"]] == ["A2"]
    assert "FK a refuzat" in sumar["erori"][0]["eroare"]
    # A1 and A3 were written and committed; A2 was rolled back.
    assert conn.commits == 2
    assert conn.rollbacks == 1
    assert [d["cod"] for d in sumar["detalii"]] == ["A1", "A3"]


def test_sweep_over_an_empty_database_is_an_empty_summary():
    conn = FakeConn()
    cur = CursorPeCoduri({}, [])
    sumar = R.refa_toate(conn, cur, aplica=True)
    assert sumar["angajamente"] == 0
    assert sumar["detalii"] == [] and sumar["erori"] == []
    assert conn.commits == 0
