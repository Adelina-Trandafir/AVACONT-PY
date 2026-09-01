# Tests for the ported ingest steps 3a, 4a and 4b -- slice 0048-03.
#
# OFFLINE. The database is a fake that dispatches by SQL PREFIX rather than by call
# order, so adding a read to a step does not silently shift every later answer by one.
# Writes are recorded, never interpreted -- what these tests assert is exactly what the
# step decided to write.
import io
import json
import os

import pytest

try:
    import routes.forexe.prelucrare_pasi as P
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"imports unavailable: {e}", allow_module_level=True)

COD = "AAB37CNBK95"
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "prelucrare_AAB37CNBK95.json")


def indicatori(*coduri):
    """FX_Indicatori asa cum il vede read_indicatori, pentru codurile date."""
    return {f"{COD}-{c}": {
        "CodAI": f"{COD}-{c}", "CodIndicator": c, "IdClsf": 1200 + i,
        "IdUnitate": 76, "SS": "02E", "IndicatorFX": "650301200301",
        "Clsf": "65.03.01.20.03.01", "CodSSI": "02E650301200301", "NrCrt": i + 1,
    } for i, c in enumerate(coduri)}


class FakeCursor:
    """
    Raspunde dupa PREFIXUL interogarii. `raspunsuri` e {prefix: [randuri]}.

    Fiecare INSERT primeste un `lastrowid` nou, crescator, ca instantaneele si liniile
    lor sa poata fi legate in asertiuni.
    """

    def __init__(self, raspunsuri=None):
        self.raspunsuri = raspunsuri or {}
        self.executed = []
        self._result = []
        self.lastrowid = 0
        self._next_id = 500

    def execute(self, sql, params=None):
        plat = " ".join(sql.split())
        self.executed.append((plat, params))
        if plat.startswith("INSERT"):
            self._next_id += 1
            self.lastrowid = self._next_id
            self._result = []
            return
        for prefix, randuri in self.raspunsuri.items():
            if plat.startswith(prefix):
                self._result = list(randuri)
                return
        self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def inserts(self, tabel):
        return [p for sql, p in self.executed
                if sql.startswith(f"INSERT INTO {tabel} ")]

    def updates(self, tabel):
        return [p for sql, p in self.executed
                if sql.startswith(f"UPDATE {tabel} ")]


# ===========================================================================
# PASUL 3a -- harta indice -> ID, si Rez_Ord
# ===========================================================================
def test_every_payload_row_gets_an_index_even_when_it_already_exists():
    """
    Harta trebuie sa acopere si randurile VECHI, nu doar cele inserate acum.

    `rand_istoric` din decizii e chiar indicele asta (F24), iar D-F cere ca
    instantaneele ramase neasezate din rulari anterioare sa poata fi decise acum --
    randurile lor de istoric exista deja in baza.
    """
    from datetime import datetime
    vechi = {"ID": 77, "DataFX": datetime(2026, 2, 10, 22, 45, 23),
             "Utilizator": "op", "Descriere": "Angajament nou.", "Observatii": ""}
    cur = FakeCursor({"SELECT ID, DataFX": [vechi], "SELECT MAX(Rez_Ord)": []})
    randuri = [
        {"Timp": "10/02/2026 22:45:23", "Utilizator": "op",
         "Descriere": "Angajament nou.", "Observatii": ""},
        {"Timp": "13/02/2026 18:33:30", "Utilizator": "op",
         "Descriere": "Salvare receptie.", "Observatii": "Rand: AAB,Suma receptie: 1029 RON"},
    ]
    harta, noi = P.step3a_populeaza_istoric(cur, COD, randuri, indicatori("AAB"))
    assert harta[0] == 77            # existent, NU reinserat
    assert harta[1] == noi[0]        # inserat acum
    assert len(noi) == 1


def test_two_identical_payload_rows_collapse_to_one():
    """
    FX_Istoric.HASH are index UNIQUE. Doua randuri identice din acelasi payload trebuie
    sa se colapseze, exact cum se colapsau in Access, altfel al doilea INSERT ar lovi
    indexul.
    """
    cur = FakeCursor({"SELECT ID, DataFX": [], "SELECT MAX(Rez_Ord)": []})
    rand = {"Timp": "10/02/2026 22:45:23", "Utilizator": "op",
            "Descriere": "sume nemodificate", "Observatii": ""}
    harta, noi = P.step3a_populeaza_istoric(cur, COD, [rand, dict(rand)],
                                            indicatori("AAB"))
    assert len(noi) == 1
    assert harta[0] == harta[1]
    assert len(cur.inserts("FX_Istoric")) == 1


def test_rez_ord_follows_the_multiplier_as_it_moves():
    """
    0 la «Angajament nou», 100 la «Initial ->», 1000 la «definitivare ->», iar un rand
    «RAND CONTRACT:» ia ordinalul indicatorului PLUS multiplicatorul in vigoare atunci.
    """
    cur = FakeCursor({"SELECT ID, DataFX": [], "SELECT MAX(Rez_Ord)": []})
    randuri = [
        {"Timp": "10/02/2026 22:45:20", "Descriere": "Angajament nou.",
         "Observatii": "", "Utilizator": "op"},
        {"Timp": "10/02/2026 22:45:23", "Descriere": "Initializare angajament",
         "Observatii": "Rand contract: AAB, angajament legal: n/a,", "Utilizator": "op"},
        {"Timp": "10/02/2026 22:45:51", "Descriere": "Modificare stare: Initial -> X",
         "Observatii": "", "Utilizator": "op"},
        {"Timp": "10/02/2026 22:45:55", "Descriere": "adaugare rand",
         "Observatii": "Rand contract: AAB, angajament legal: 210,", "Utilizator": "op"},
    ]
    P.step3a_populeaza_istoric(cur, COD, randuri, indicatori("AAB"))
    # parametrul Rez_Ord e al saselea din _ISTORIC_INSERT_SQL
    rez_ord = [p[5] for p in cur.inserts("FX_Istoric")]
    assert rez_ord == [0, 1, 100, 101]


def test_the_real_payload_maps_all_44_history_rows():
    """Sarcina utila reala (AAB37CNBK95): 44 randuri, toate indexate."""
    date = json.load(io.open(FIXTURE, encoding="utf-8"))
    randuri = date["tabele"]["TabelIstoric"]
    cur = FakeCursor({"SELECT ID, DataFX": [], "SELECT MAX(Rez_Ord)": []})
    harta, noi = P.step3a_populeaza_istoric(cur, COD, randuri, indicatori("AAB", "AA2"))
    assert len(randuri) == 44
    assert sorted(harta) == list(range(44))
    assert len(noi) == 44


# ===========================================================================
# PASUL 4a -- antete, linii, si randul de STERGERE (F21)
# ===========================================================================
def _istoric_receptie(id_, obs, val, descriere="Salvare receptie.", cod_ind="AAB"):
    from datetime import datetime
    return {"ID": id_, "HASH": "h", "CodAI": f"{COD}-{cod_ind}",
            "CodAngajament": COD, "CodIndicator": cod_ind, "IdClsf": 1200,
            "DataFX": datetime(2026, 2, 10, 22, 46, 54), "TipRand": "Receptie",
            "Descriere": descriere, "Observatii": obs, "Val_Receptie": val}


def test_a_header_flushes_the_lines_collected_before_it():
    cur = FakeCursor({
        "SELECT ID, HASH": [
            _istoric_receptie(7, "Rand: AAB,Suma receptie: 210 RON", 210),
            _istoric_receptie(8, "Rand: AA2,Suma receptie: 300 RON", 300,
                              cod_ind="AA2"),
            _istoric_receptie(9, "Receptie: PLATA FACT., valoare: 510, (activ:true)",
                              510),
        ],
        "SELECT CodIndicator FROM FX_Receptii ": [],
        "SELECT MAX(NrCrt)": [],
    })
    antete = P.step4a_populeaza_receptii(cur, COD, indicatori("AAB", "AA2"))
    assert antete == 1
    assert len(cur.inserts("FX_Receptii_H")) == 1
    assert len(cur.inserts("FX_Receptii")) == 2
    # Ambele linii arata catre antetul tocmai creat.
    idrh = cur.inserts("FX_Receptii")[0][0]
    assert all(p[0] == idrh for p in cur.inserts("FX_Receptii"))


def test_stergere_receptie_becomes_a_snapshot_with_no_lines():
    """
    F21. Randul de stergere poarta `(activ:true)` ca orice antet, deci devine instantaneu
    pe calea NORMALA -- nu e deviat si nu e filtrat. Nu are randuri pe indicator, deci nu
    produce nicio linie, si asta iese de la sine.
    """
    cur = FakeCursor({
        "SELECT ID, HASH": [
            _istoric_receptie(38, "Receptie: Plata ces, valoare: 7150, (activ:true)",
                              7150, descriere="Stergere receptie"),
        ],
        "SELECT CodIndicator FROM FX_Receptii ": [],
        "SELECT MAX(NrCrt)": [],
    })
    antete = P.step4a_populeaza_receptii(cur, COD, indicatori("AAB"))
    assert antete == 1
    h = cur.inserts("FX_Receptii_H")[0]
    # _H_INSERT_SQL: (IDH, NrCrt, CodAngajament, DataH, Total, Descriere, EsteStergere)
    assert h[0] == 38
    assert h[4] == 7150.0
    assert h[6] == 1                       # EsteStergere
    assert cur.inserts("FX_Receptii") == []


def test_an_ordinary_header_is_not_marked_as_a_deletion():
    cur = FakeCursor({
        "SELECT ID, HASH": [
            _istoric_receptie(9, "Receptie: PLATA FACT., valoare: 510, (activ:true)",
                              510),
        ],
        "SELECT CodIndicator FROM FX_Receptii ": [],
        "SELECT MAX(NrCrt)": [],
    })
    P.step4a_populeaza_receptii(cur, COD, indicatori("AAB"))
    assert cur.inserts("FX_Receptii_H")[0][6] == 0


def test_a_line_whose_indicator_is_unknown_raises():
    cur = FakeCursor({
        "SELECT ID, HASH": [
            _istoric_receptie(7, "Rand: ZZZ,Suma receptie: 210 RON", 210,
                              cod_ind="ZZZ")],
        "SELECT CodIndicator FROM FX_Receptii ": [],
        "SELECT MAX(NrCrt)": [],
    })
    with pytest.raises(ValueError) as e:
        P.step4a_populeaza_receptii(cur, COD, indicatori("AAB"))
    assert "ZZZ" in str(e.value)


# ===========================================================================
# PASUL 4b -- si regula F25
# ===========================================================================
def _receptie_payload(data="11/02/2026", suma="510,00", cod_ind="AAB"):
    return {"Tip": "Partial", "Data": data, "Suma": suma,
            "DescriereReceptie": "PLATA FACT.",
            "Detaliu": [{"Cod": cod_ind, "Sector_Sursa_Indicator": "02E- 65. 03. 01. 20. 03. 01",
                         "Credit_bugetar_rezervat_definitiv": "10.502,19",
                         "Valoare_nereceptionata": "0,00", "Valoare": suma}]}


def test_a_new_reception_is_inserted_with_its_lines():
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": [], "SELECT IDRR, DataR": []})
    r, rhr = P.step4b_receptii_prelucrare(cur, COD, [_receptie_payload()],
                                          indicatori("AAB"))
    assert (r, rhr) == (1, 1)
    assert len(cur.inserts("FX_Receptii_R")) == 1
    assert len(cur.inserts("FX_Receptii_RHR")) == 1


def test_step4b_never_matches_a_deleted_reception_even_on_the_same_calendar_day():
    """
    F25, si de ce conteaza. Potrivirea Access e pe ZI (`CLng(DataR)`). O receptie stearsa
    nu mai poate aparea in ListaReceptii, deci orice potrivire aparenta cu ea e o
    COLIZIUNE. Fara filtru, o receptie noua creata in aceeasi zi calendaristica in care
    fusese creata cea stearsa i-ar suprascrie tacut valorile.

    Filtrul traieste in SQL, deci testul verifica interogarea SI faptul ca -- cand baza
    nu intoarce niciun candidat, fiindca cel stears a fost exclus -- se INSEREAZA o
    receptie noua in loc sa se actualizeze cea veche.
    """
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": [],
                      "SELECT IDRR, DataR": []})     # cel stears nu e printre candidati
    r, _ = P.step4b_receptii_prelucrare(cur, COD, [_receptie_payload()],
                                        indicatori("AAB"))
    candidati = [sql for sql, _ in cur.executed if sql.startswith("SELECT IDRR, DataR")]
    assert candidati and "Sters = 0" in candidati[0]
    assert r == 1                                   # inserata, nu suprascrisa
    assert len(cur.updates("FX_Receptii_R")) == 0


def test_a_matching_reception_with_a_changed_sum_is_updated_not_inserted():
    from datetime import date
    cur = FakeCursor({
        "SELECT CodIndicator FROM FX_Receptii_RHR": [],
        "SELECT MAX(NRCRT)": [],
        "SELECT IDRR, DataR": [{"IDRR": 271, "DataR": date(2026, 2, 11),
                                "SumaAntet": 400.0}],
        "SELECT IDRHR, CodIndicator": [{"IDRHR": 9, "CodIndicator": "AAB",
                                        "Valoare": 400.0}],
    })
    P.step4b_receptii_prelucrare(cur, COD, [_receptie_payload()], indicatori("AAB"))
    assert len(cur.inserts("FX_Receptii_R")) == 0
    assert cur.updates("FX_Receptii_R")[0] == (510.0, 271)
    # Suma antetului s-a schimbat, deci si linia.
    assert cur.updates("FX_Receptii_RHR")[0][3] == 9


def test_an_identical_sum_still_checks_the_lines():
    """
    Suma unei receptii poate ramane aceeasi cu distributia schimbata (AAB -100,
    AAC +100), deci RHR se verifica intotdeauna, nu doar cand antetul s-a miscat.
    """
    from datetime import date
    cur = FakeCursor({
        "SELECT CodIndicator FROM FX_Receptii_RHR": [],
        "SELECT MAX(NRCRT)": [],
        "SELECT IDRR, DataR": [{"IDRR": 271, "DataR": date(2026, 2, 11),
                                "SumaAntet": 510.0}],
        "SELECT IDRHR, CodIndicator": [],      # indicatorul nu e inca acolo
    })
    r, rhr = P.step4b_receptii_prelucrare(cur, COD, [_receptie_payload()],
                                          indicatori("AAB"))
    assert r == 0                                   # antetul nu s-a atins
    assert rhr == 1 and len(cur.inserts("FX_Receptii_RHR")) == 1


def test_a_reception_without_detaliu_is_rejected():
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": []})
    rec = _receptie_payload()
    del rec["Detaliu"]
    with pytest.raises(ValueError) as e:
        P.step4b_receptii_prelucrare(cur, COD, [rec], indicatori("AAB"))
    assert "Detaliu" in str(e.value)


# ===========================================================================
# PASUL 7
# ===========================================================================
def test_step7_marks_exactly_the_rows_this_run_inserted():
    cur = FakeCursor()
    assert P.step7_actualizeaza_rezolvat(cur, [11, 12, 13]) == 3
    sql, params = cur.executed[0]
    assert sql.startswith("UPDATE FX_Istoric SET Prelucrat = 1 WHERE ID IN (%s, %s, %s)")
    assert params == (11, 12, 13)


def test_step7_does_nothing_when_no_row_was_inserted():
    cur = FakeCursor()
    assert P.step7_actualizeaza_rezolvat(cur, []) == 0
    assert cur.executed == []


# ===========================================================================
# Ajutorul de formatare a clasificatiei pentru IBAN
# ===========================================================================
def test_the_iban_classification_mask_groups_from_the_right():
    assert P._format_clsf_iban("650301200301") == "65.03.01.20.03.01"
    assert P._format_clsf_iban("") == ""
    # Un sir impar se alinieaza la DREAPTA, ca masca `@` din VBA.
    assert P._format_clsf_iban("12345") == "1.23.45"


# ===========================================================================
# Forma coloanelor imbricate (decizia D-N)
# ===========================================================================
# Coloanele care sosesc ca LISTA nu se ghicesc: sunt citite din definitiile de workflow.
# Tiparul e un `ForEachVar` al carui `collectFields` numeste un camp pe care un
# `ScrapeTable` interior il scrie cu `saveTo`. Peste toate cele sase `.wfl` din
# `src/KBot.Forexe/Workflows/` sunt exact doua:
#
#     ListaReceptii_results[].Detaliu          -- liniile receptiei, CITITE de pasul 4b
#     TabelIndicatori_results[].BugetIndicator -- bugetul indicatorului, necitit (D18)
#
# Pana pe 26.08.2026 amandoua puteau sosi si ca SIR care contine JSON, fiindca
# `ForexeRunner.TryParseTable` aplatiza fiecare celula cu `.ToString()`. Calea toleranta
# a plecat odata cu aplatizarea; testele de mai jos pinuiaza plecarea ei.
def test_detaliu_as_a_list_is_accepted():
    rec = _receptie_payload()
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": [], "SELECT IDRR, DataR": []})
    P.step4b_receptii_prelucrare(cur, COD, [rec], indicatori("AAB"))
    assert len(cur.inserts("FX_Receptii_RHR")) == 1


def test_a_flattened_detaliu_string_is_rejected_and_the_message_names_the_column():
    """
    Un `Detaliu` aplatizat e RESPINS, chiar daca sirul contine JSON perfect valid.

    Mesajul spune coloana SI spune ce e in neregula cu clientul -- nu «JSON nevalid»,
    care ar trimite pe cine il citeste sa caute o virgula lipsa intr-un sir corect.
    """
    rec = _receptie_payload()
    rec["Detaliu"] = json.dumps(rec["Detaliu"])          # JSON valid, dar TURTIT
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": []})
    with pytest.raises(ValueError) as e:
        P.step4b_receptii_prelucrare(cur, COD, [rec], indicatori("AAB"))
    mesaj = str(e.value)
    assert "Detaliu" in mesaj
    assert "aplatizat" in mesaj
    assert "listă" in mesaj


def test_a_detaliu_string_that_is_not_json_is_rejected_the_same_way():
    """
    Acelasi mesaj si pentru un sir care NU e JSON. Diferenta dintre cele doua siruri nu
    mai intereseaza pe nimeni: niciunul nu e o lista, si asta e tot ce conteaza.
    """
    rec = _receptie_payload()
    rec["Detaliu"] = "nu-i JSON"
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": []})
    with pytest.raises(ValueError) as e:
        P.step4b_receptii_prelucrare(cur, COD, [rec], indicatori("AAB"))
    assert "Detaliu" in str(e.value)


def test_an_empty_detaliu_is_still_rejected_as_empty():
    """
    Un `Detaliu` gol nu e o aplatizare, e o receptie fara linii -- si tot nu trece.
    Mesajul e cel vechi, ca sa nu trimita pe nimeni sa caute un client stricat.
    """
    rec = _receptie_payload()
    rec["Detaliu"] = ""
    cur = FakeCursor({"SELECT CodIndicator FROM FX_Receptii_RHR": [],
                      "SELECT MAX(NRCRT)": []})
    with pytest.raises(ValueError) as e:
        P.step4b_receptii_prelucrare(cur, COD, [rec], indicatori("AAB"))
    assert "este gol" in str(e.value)


def test_cere_lista_accepts_a_list_and_rejects_a_flattened_string():
    """Ajutorul in sine, pe cele patru intrari pe care le poate primi."""
    assert P.cere_lista([{"a": 1}], "undeva", "Detaliu") == [{"a": 1}]

    with pytest.raises(ValueError) as e:
        P.cere_lista('[{"a": 1}]', "undeva", "Detaliu")
    assert "aplatizat" in str(e.value)

    # `gol_permis` acopera un tabel interior care chiar nu a avut randuri:
    # `BuildCollectedRow` scrie "" cand variabila iteratiei a ramas goala.
    assert P.cere_lista("", "undeva", "BugetIndicator", gol_permis=True) == []
    with pytest.raises(ValueError):
        P.cere_lista("", "undeva", "Detaliu")

    with pytest.raises(ValueError) as e:
        P.cere_lista(17, "undeva", "Detaliu")
    assert "nu este o listă" in str(e.value)
