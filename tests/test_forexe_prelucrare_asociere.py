# Tests for the association half of the FOREXE ingest -- slice 0048-03.
#
# OFFLINE. Almost everything here is pure: the placement rules (F13-F16), the shape of
# `decizii`, the label rules of 4c-bis and the coverage check take plain dictionaries and
# no database at all. The two functions that do need a cursor get a scripted fake that
# answers with queued rows and records what was run.
#
# The one test that genuinely needs a populated MariaDB -- "phase one writes nothing" --
# lives in test_forexe_prelucrare_live.py and skips off-host.
from datetime import datetime

import pytest

try:
    import routes.forexe.prelucrare_asociere as A
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"imports unavailable: {e}", allow_module_level=True)

DecizieInvalida = A.DecizieInvalida
COD = "AAB37CNBK95"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def dt(s):
    """'2026-05-20 00:36:12' -> datetime. Shorter than writing the constructor."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def inst(rand, data_h, total, indicatori=("AAB",), stergere=False, idrh=None):
    return {
        "idrh": idrh if idrh is not None else 1000 + rand,
        "rand_istoric": rand,
        "data_h": dt(data_h),
        "descriere": "Plata fact.",
        "total": float(total),
        "stergere": stergere,
        "linii": [{"cod_indicator": c, "cod_ai": f"{COD}-{c}", "cod_ssi": "",
                   "id_clsf": 1, "valoare": float(total)} for c in indicatori],
    }


def rec(idrr, data_r, suma, indicatori=("AAB",), sters=False):
    return {
        "idrr": idrr, "nr_crt": idrr, "data_r": dt(data_r),
        "suma_antet": float(suma), "descriere": "Plata fact.",
        "sters": sters, "reconstituit": False,
        "rhr": [{"cod_indicator": c, "cod_ai": f"{COD}-{c}", "cod_ssi": "",
                 "credit_bugetar": 10502.19, "valoare": float(suma),
                 "valoare_n": 0.0} for c in indicatori],
    }


def dec(rand, actiune, data_h, idrr=None, eticheta=None):
    d = {"rand_istoric": rand, "actiune": actiune, "data_h": data_h}
    if idrr is not None:
        d["idrr"] = idrr
    if eticheta is not None:
        d["receptie_noua"] = eticheta
    return d


class FakeCursor:
    """Answers each execute() with the next queued result; records every call."""

    def __init__(self, rezultate=None):
        self.rezultate = list(rezultate or [])
        self.executed = []
        self._result = []
        self.lastrowid = 900

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._result = self.rezultate.pop(0) if self.rezultate else []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


# ===========================================================================
# The shape of `decizii` (2.2)
# ===========================================================================
def test_decizii_must_be_a_list():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii({"rand_istoric": 0})


def test_an_unknown_action_is_rejected():
    with pytest.raises(DecizieInvalida) as e:
        A.normalizeaza_decizii([dec(0, "poate", "2026-01-01 00:00:00")])
    assert "poate" in str(e.value)


def test_asociat_needs_exactly_one_target():
    """Ambele sau niciuna e 400 -- niciodata ghicit."""
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec(0, "asociat", "2026-01-01 00:00:00")])
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec(0, "asociat", "2026-01-01 00:00:00",
                                    idrr=1, eticheta="R1")])
    ok = A.normalizeaza_decizii([dec(0, "asociat", "2026-01-01 00:00:00", idrr=1)])
    assert ok[0]["idrr"] == 1 and ok[0]["receptie_noua"] is None


def test_ignorat_cannot_carry_a_reception():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec(0, "ignorat", "2026-01-01 00:00:00", idrr=1)])


def test_reconstituire_needs_a_label_and_refuses_an_idrr():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec(0, "reconstituire", "2026-01-01 00:00:00")])
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec(0, "reconstituire", "2026-01-01 00:00:00",
                                    idrr=1, eticheta="R1")])


def test_data_h_is_mandatory():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([{"rand_istoric": 0, "actiune": "ignorat"}])


# ===========================================================================
# Coverage: every snapshot must be decided, exactly once, with the right date
# ===========================================================================
def test_a_missing_rand_istoric_is_rejected():
    """Tacerea NU are voie sa insemne «ignora-l»."""
    instantanee = [inst(9, "2026-02-10 22:46:54", 510),
                   inst(13, "2026-02-13 18:33:30", 1029)]
    decizii = A.normalizeaza_decizii([dec(9, "asociat", "2026-02-10 22:46:54", idrr=1)])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_acoperirea(decizii, instantanee)
    assert "Lipsesc deciziile" in str(e.value)


def test_a_rand_istoric_that_is_not_a_snapshot_is_rejected():
    instantanee = [inst(9, "2026-02-10 22:46:54", 510)]
    decizii = A.normalizeaza_decizii([dec(9, "ignorat", "2026-02-10 22:46:54"),
                                      dec(41, "ignorat", "2026-05-28 20:11:34")])
    with pytest.raises(DecizieInvalida):
        A.verifica_acoperirea(decizii, instantanee)


def test_the_same_row_twice_is_rejected():
    instantanee = [inst(9, "2026-02-10 22:46:54", 510)]
    decizii = A.normalizeaza_decizii([dec(9, "ignorat", "2026-02-10 22:46:54"),
                                      dec(9, "ignorat", "2026-02-10 22:46:54")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_acoperirea(decizii, instantanee)
    assert "de două ori" in str(e.value)


def test_a_stale_data_h_fails_loudly_instead_of_associating_the_wrong_row():
    """
    Fisierul de decizii poarta `data_h` alaturi de indice tocmai pentru asta: daca
    payload-ul s-a schimbat sub el, indicele arata catre alt rand si tacerea ar
    asocia gresit. O secunda diferenta e de ajuns.
    """
    instantanee = [inst(9, "2026-02-10 22:46:54", 510)]
    decizii = A.normalizeaza_decizii([dec(9, "ignorat", "2026-02-10 22:46:55")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_acoperirea(decizii, instantanee)
    assert "învechit" in str(e.value)


def test_iso_with_a_T_matches_the_database_datetime():
    """Clientul .NET serializeaza cu «T»; baza raspunde cu spatiu. Acelasi moment."""
    instantanee = [inst(9, "2026-02-10 22:46:54", 510)]
    decizii = A.normalizeaza_decizii([dec(9, "ignorat", "2026-02-10T22:46:54")])
    assert A.verifica_acoperirea(decizii, instantanee)[9]["actiune"] == "ignorat"


# ===========================================================================
# Labels for reconstructed receptions (4c-bis)
# ===========================================================================
def test_a_label_used_but_never_declared_is_rejected():
    decizii = A.normalizeaza_decizii([
        dec(34, "asociat", "2026-03-30 22:22:23", eticheta="R1")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_etichetele(decizii)
    assert "nu sunt declarate" in str(e.value)


def test_a_label_declared_twice_is_rejected():
    decizii = A.normalizeaza_decizii([
        dec(31, "reconstituire", "2026-01-01 10:00:00", eticheta="R1"),
        dec(32, "reconstituire", "2026-01-02 10:00:00", eticheta="R1"),
        dec(38, "stergere", "2026-03-01 10:00:00", eticheta="R1")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_etichetele(decizii)
    assert "declarată de două ori" in str(e.value)


def test_a_reconstructed_chain_without_a_deletion_is_rejected():
    """
    O receptie reconstituita exista TOCMAI fiindca a fost stearsa (F26). Un lant fara
    stergere inseamna ca operatorul a grupat gresit, si ar produce o receptie care nu
    apare niciodata in ListaReceptii si nu se reconciliaza cu nimic.
    """
    decizii = A.normalizeaza_decizii([
        dec(31, "reconstituire", "2026-01-01 10:00:00", eticheta="R1"),
        dec(34, "asociat", "2026-02-01 10:00:00", eticheta="R1")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_etichetele(decizii)
    assert "0 rânduri de ștergere" in str(e.value)


def test_a_reconstructed_chain_with_two_deletions_is_rejected():
    decizii = A.normalizeaza_decizii([
        dec(31, "reconstituire", "2026-01-01 10:00:00", eticheta="R1"),
        dec(38, "stergere", "2026-03-01 10:00:00", eticheta="R1"),
        dec(39, "stergere", "2026-03-02 10:00:00", eticheta="R1")])
    with pytest.raises(DecizieInvalida) as e:
        A.verifica_etichetele(decizii)
    assert "2 rânduri de ștergere" in str(e.value)


def test_a_well_formed_reconstructed_chain_passes():
    decizii = A.normalizeaza_decizii([
        dec(31, "reconstituire", "2026-01-01 10:00:00", eticheta="R1"),
        dec(34, "asociat", "2026-02-01 10:00:00", eticheta="R1"),
        dec(38, "stergere", "2026-03-01 10:00:00", eticheta="R1")])
    assert list(A.verifica_etichetele(decizii)) == ["R1"]


# ===========================================================================
# F13 -- RETRAS ca veto pe 31.08.2026, STERS si ca semn pe 09.09.2026
# ===========================================================================
# Testele astea au trecut prin trei forme. La inceput verificau vetoul; pe 31.08.2026 au
# fost rescrise pe semn; acum verifica TACEREA. Motivul e acelasi de fiecare data si e in
# date, nu in gust: `DataR` e un camp tastat pe site si schimbabil dupa aceea, iar
# `FX_Receptii_R` nu are nicio coloana cu momentul crearii (F29). Ca semn se aprindea pe
# date perfect corecte -- o data tastata soseste la miezul noptii -- deci aparea pe rand
# dupa rand fara sa spuna nimic. Operatorul a cerut sa dispara cu totul (09.09.2026).
def test_the_date_rule_neither_rejects_nor_warns_any_more():
    r = rec(1, "2026-03-01 08:00:00", 510)
    i = inst(9, "2026-01-19 10:00:00", 510)
    avertismente = []
    A.valideaza_plasarile({1: [i]}, {1: r}, avertismente=avertismente)   # nu ridica
    assert avertismente == []


def test_a_snapshot_on_the_very_day_of_the_reception_is_silent_too():
    """Cazul care a starnit prima data «se aprinde pe date corecte»."""
    r = rec(1, "2026-02-11 00:00:00", 510)
    i = inst(9, "2026-02-11 10:00:00", 510)
    avertismente = []
    A.valideaza_plasarile({1: [i]}, {1: r}, avertismente=avertismente)
    assert avertismente == []


# ===========================================================================
# F14 / F16 -- indicators
# ===========================================================================
def test_a_snapshot_naming_an_indicator_the_reception_lacks_is_rejected():
    r = rec(1, "2026-01-01 00:00:00", 510, indicatori=("AAB",))
    i = inst(9, "2026-02-11 10:00:00", 510, indicatori=("AAB", "AA2"))
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({1: [i]}, {1: r})
    assert "AA2" in str(e.value)


def test_indicator_sets_may_grow_but_never_shrink():
    r = rec(1, "2026-01-01 00:00:00", 300, indicatori=("AAB", "AA2"))
    devreme = inst(9, "2026-02-01 10:00:00", 300, indicatori=("AAB", "AA2"))
    tarziu = inst(13, "2026-03-01 10:00:00", 300, indicatori=("AAB",))
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({1: [devreme, tarziu]}, {1: r})
    assert "pierde indicatorii" in str(e.value)


def test_growing_sets_are_fine():
    r = rec(1, "2026-01-01 00:00:00", 300, indicatori=("AAB", "AA2"))
    devreme = inst(9, "2026-02-01 10:00:00", 300, indicatori=("AAB",))
    tarziu = inst(13, "2026-03-01 10:00:00", 300, indicatori=("AAB", "AA2"))
    A.valideaza_plasarile({1: [devreme, tarziu]}, {1: r})


# ===========================================================================
# F15 -- the chain end
# ===========================================================================
def test_the_chain_end_check_passes_for_a_normal_chain():
    r = rec(1, "2026-01-01 00:00:00", 460)
    lant = [inst(9, "2026-02-01 10:00:00", 510),
            inst(13, "2026-03-01 10:00:00", 460)]
    A.valideaza_plasarile({1: lant}, {1: r})


def test_the_chain_end_check_fails_when_the_last_snapshot_disagrees():
    r = rec(1, "2026-01-01 00:00:00", 460)
    lant = [inst(9, "2026-02-01 10:00:00", 460),
            inst(13, "2026-03-01 10:00:00", 510)]
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({1: lant}, {1: r})
    assert "Lanțul nu se închide" in str(e.value)


def test_several_lines_on_one_indicator_are_summed_not_overwritten():
    """
    Plangerea din 09.09.2026: «pun corect recepțiile la locul lor si tot imi apare
    mesajul». Comparatia veche era o dictionar-comprehensiune pe `cod_indicator`, deci o
    receptie cu doua linii pe acelasi indicator pastra doar ULTIMA -- si cele doua laturi,
    citite din tabele diferite, puteau pastra linii diferite. Aici totalurile se potrivesc
    (300 = 100 + 200) si liniile la fel, doar ca sunt sparte altfel pe cele doua laturi.
    """
    r = rec(1, "2026-01-01 00:00:00", 300)
    r["rhr"] = [
        {"cod_indicator": "AAB", "cod_ai": "x", "cod_ssi": "S1",
         "credit_bugetar": 0.0, "valoare": 100.0, "valoare_n": 0.0},
        {"cod_indicator": "AAB", "cod_ai": "x", "cod_ssi": "S2",
         "credit_bugetar": 0.0, "valoare": 200.0, "valoare_n": 0.0},
    ]
    i = inst(9, "2026-02-01 10:00:00", 300)
    i["linii"] = [
        {"cod_indicator": "AAB", "cod_ai": "x", "cod_ssi": "S2",
         "id_clsf": 1, "valoare": 200.0},
        {"cod_indicator": "AAB", "cod_ai": "x", "cod_ssi": "S1",
         "id_clsf": 1, "valoare": 100.0},
    ]
    A.valideaza_plasarile({1: [i]}, {1: r})       # nu ridica


def test_an_indicator_that_fell_to_zero_does_not_break_the_chain_end():
    """F16 spune ca un indicator poate cadea la zero. Zerourile ies din comparatie."""
    r = rec(1, "2026-01-01 00:00:00", 300)
    r["rhr"].append({"cod_indicator": "AA2", "cod_ai": "x", "cod_ssi": "",
                     "credit_bugetar": 0.0, "valoare": 0.0, "valoare_n": 0.0})
    i = inst(9, "2026-02-01 10:00:00", 300)
    A.valideaza_plasarile({1: [i]}, {1: r})       # nu ridica


def test_a_real_line_mismatch_is_still_refused_and_says_which_indicator():
    r = rec(1, "2026-01-01 00:00:00", 300, indicatori=("AAB", "AA2"))
    # 150 + 150 pe receptie, 300 + 0 pe instantaneu: acelasi total, alta impartire.
    r["rhr"][0]["valoare"] = 150.0
    r["rhr"][1]["valoare"] = 150.0
    r["suma_antet"] = 300.0
    i = inst(9, "2026-02-01 10:00:00", 300, indicatori=("AAB", "AA2"))
    i["linii"][0]["valoare"] = 300.0
    i["linii"][1]["valoare"] = 0.0
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({1: [i]}, {1: r})
    assert "AAB" in str(e.value)
    assert "Lanțul nu se închide" in str(e.value)


def test_on_the_ingest_path_the_message_names_the_reception_by_date_and_value():
    """
    `IDRR`-ul unei receptii nascute in rularea curenta se da inauntrul tranzactiei si nu
    supravietuieste derularii inapoi, deci «Recepția 235» nu se gaseste nicaieri in lista
    operatorului (plangere, 09.09.2026). Cu `id_stabil=False` numarul nu se scrie deloc.
    """
    r = rec(235, "2026-02-11 00:00:00", 460)
    lant = [inst(9, "2026-02-01 10:00:00", 460),
            inst(13, "2026-03-01 10:00:00", 510)]
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({235: lant}, {235: r}, id_stabil=False)
    mesaj = str(e.value)
    assert "235" not in mesaj
    assert "11.02.2026" in mesaj
    assert "460.00" in mesaj


def test_in_the_anytime_editor_the_number_is_real_and_stays_in_the_message():
    r = rec(41, "2026-02-11 00:00:00", 460)
    lant = [inst(9, "2026-02-01 10:00:00", 460),
            inst(13, "2026-03-01 10:00:00", 510)]
    with pytest.raises(DecizieInvalida) as e:
        A.valideaza_plasarile({41: lant}, {41: r})
    assert "Recepția 41" in str(e.value)


def test_the_chain_end_check_is_skipped_for_a_chain_ending_in_a_deletion():
    """
    Ultimul instantaneu al unei receptii sterse E randul de stergere; a-l compara cu
    starea de ACUM nu inseamna nimic. Receptiile reconstituite sunt mereu aici.
    """
    r = rec(1, "2026-01-01 00:00:00", 460, sters=True)
    lant = [inst(9, "2026-02-01 10:00:00", 460),
            inst(38, "2026-03-01 10:00:00", 7150, indicatori=(), stergere=True)]
    A.valideaza_plasarile({1: lant}, {1: r})      # nu ridica


# ===========================================================================
# The automatic pass (phase one only)
# ===========================================================================
def test_the_automatic_pass_matches_newest_first_and_consumes_each_reception_once():
    """
    Doua receptii de 460,00 -- exact cazul F5 -- si doua instantanee de 460,00. LIFO:
    cel mai nou instantaneu ia cea mai noua receptie.
    """
    cur = FakeCursor([[{"IDRR": 6, "SumaAntet": 460.0},
                       {"IDRR": 5, "SumaAntet": 460.0}]])
    instantanee = [inst(35, "2026-05-20 00:36:12", 460, idrh=105),
                   inst(41, "2026-05-28 20:11:34", 460, idrh=106)]
    sugestii = A.pas4c_automat(cur, COD, instantanee)
    assert sugestii == {106: 6, 105: 5}


def test_the_automatic_pass_leaves_unmatched_snapshots_alone():
    cur = FakeCursor([[{"IDRR": 1, "SumaAntet": 510.0}]])
    instantanee = [inst(9, "2026-02-10 22:46:54", 510, idrh=101),
                   inst(13, "2026-02-13 18:33:30", 1029, idrh=102)]
    sugestii = A.pas4c_automat(cur, COD, instantanee)
    assert sugestii == {101: 1}


def test_the_automatic_pass_writes_nothing():
    cur = FakeCursor([[{"IDRR": 1, "SumaAntet": 510.0}]])
    A.pas4c_automat(cur, COD, [inst(9, "2026-02-10 22:46:54", 510)])
    assert all(sql.startswith("SELECT") for sql, _ in cur.executed)


def test_deleted_receptions_are_not_automatic_candidates():
    """
    Nimic nu se mai poate adauga pe site unei receptii sterse, deci o potrivire automata
    pe ea ar fi mereu o coliziune -- acelasi rationament ca F25 la pasul 4b. Filtrul e
    in SQL, deci testul verifica interogarea.
    """
    cur = FakeCursor([[]])
    A.pas4c_automat(cur, COD, [inst(9, "2026-02-10 22:46:54", 510)])
    sql = cur.executed[0][0]
    assert "Sters = 0" in sql


# ===========================================================================
# Final / Partial
# ===========================================================================
def test_final_lands_on_the_latest_snapshot_by_data_h_not_the_last_attached():
    """
    Access facea `Final` orice tocmai atasase. Cu plasare manuala regula aceea ar lasa un
    instantaneu din februarie sa devina `Final` pe o receptie care are deja unul din mai.
    Aici lantul soseste ordonat dupa DataH si ULTIMUL il ia.
    """
    lant = [
        {"IDRH": 106, "DataH": dt("2026-02-01 10:00:00"), "Descriere": "a",
         "TipReceptie": "Final", "CodAngajament": COD},
        {"IDRH": 105, "DataH": dt("2026-05-01 10:00:00"), "Descriere": "b",
         "TipReceptie": "Partial", "CodAngajament": COD},
    ]
    cur = FakeCursor([lant])
    A.recalculeaza_final(cur, 1)
    scrieri = [(p[0], p[2]) for sql, p in cur.executed if sql.startswith("UPDATE")]
    assert ("Partial", 106) in scrieri     # februarie retrogradat
    assert ("Final", 105) in scrieri       # mai promovat


def test_a_snapshot_already_carrying_the_right_type_is_not_rewritten():
    """HASH se rescrie doar cand TipReceptie se schimba; o scriere inutila l-ar atinge."""
    lant = [
        {"IDRH": 105, "DataH": dt("2026-02-01 10:00:00"), "Descriere": "a",
         "TipReceptie": "Partial", "CodAngajament": COD},
        {"IDRH": 106, "DataH": dt("2026-05-01 10:00:00"), "Descriere": "b",
         "TipReceptie": "Final", "CodAngajament": COD},
    ]
    cur = FakeCursor([lant])
    A.recalculeaza_final(cur, 1)
    assert [sql for sql, _ in cur.executed if sql.startswith("UPDATE")] == []


# ===========================================================================
# The fingerprint
# ===========================================================================
def _amprenta_row(**kw):
    baza = {"ic": 44, "im": 812, "id_": "2026-05-30 08:19:33",
            "rc": 6, "rm": 271, "hc": 6, "hm": 812, "hn": 5}
    baza.update(kw)
    return [baza]


def test_the_fingerprint_is_stable_for_the_same_state():
    a = A.amprenta(FakeCursor([_amprenta_row()]), COD)
    b = A.amprenta(FakeCursor([_amprenta_row()]), COD)
    assert a == b and len(a) == 32


@pytest.mark.parametrize("camp", ["ic", "im", "id_", "rc", "rm", "hc", "hm", "hn"])
def test_every_component_moves_the_fingerprint(camp):
    """
    Fiecare parte trebuie sa conteze, altfel amprenta are un unghi mort. `hn` -- numarul
    celor inca neasociate -- e cel care prinde «alta sesiune a asociat ceva intre timp».
    """
    baza = A.amprenta(FakeCursor([_amprenta_row()]), COD)
    vechi = _amprenta_row()[0][camp]
    nou = 99999 if not isinstance(vechi, str) else "2020-01-01 00:00:00"
    assert A.amprenta(FakeCursor([_amprenta_row(**{camp: nou})]), COD) != baza


def test_the_fingerprint_reads_only_the_angajament():
    cur = FakeCursor([_amprenta_row()])
    A.amprenta(cur, COD)
    sql, params = cur.executed[0]
    assert params == (COD,) * 8
    assert "DTQ" not in sql          # nimic care se misca la citire


# ---------------------------------------------------------------------------
# F28 -- reconstituirea neverificabila
# ---------------------------------------------------------------------------
# Regula e o functie PURA peste lista de IDRR reconstituite ale angajamentului, deci se
# testeaza fara nicio baza. Conditia e exact cea a lui F27 si nimic mai larg.
def test_a_single_reconstruction_is_not_marked_uncertain():
    """
    Una singura ▸ nimic. Instantaneele ei nu concureaza cu ale nimanui, deci gruparea e
    ingradita de F13/F14/F16 si atat -- ceea ce e destul.
    """
    assert A.f28_de_marcat([17]) == []


def test_two_reconstructions_mark_both_not_just_the_new_one():
    """
    Ambiguitatea e INTRE ele. Fiecare instantaneu al oricareia ar fi putut sta pe
    cealalta, deci nu apartine niciuneia singure -- si nici celei adaugate ultima.
    """
    assert A.f28_de_marcat([17, 18]) == [17, 18]


def test_three_reconstructions_mark_all_three():
    assert A.f28_de_marcat([4, 9, 12]) == [4, 9, 12]


def test_no_reconstruction_marks_nothing():
    assert A.f28_de_marcat([]) == []


def test_the_flag_is_never_cleared_by_a_later_run_seeing_only_one():
    """
    Steagul NU se sterge. Functia spune doar pe cine sa marchezi, niciodata pe cine sa
    demarchezi: o rulare de mai tarziu care vede o singura reconstituire nu face
    gruparea de atunci mai verificabila decat era in clipa in care s-a facut.

    Testul pinuiaza chiar absenta acelui drum -- daca cineva ar adauga o «curatare», ea
    ar trebui sa treaca pe aici, si aici nu are ce sa intoarca.
    """
    # Rularea 1: doua reconstituiri ▸ amandoua marcate.
    marcate = set(A.f28_de_marcat([17, 18]))
    assert marcate == {17, 18}

    # Rularea 3: una dintre ele a disparut din tabel (sters, sau alt angajament).
    # Functia nu cere demarcarea celeilalte -- nu are cum, nu intoarce demarcari.
    assert A.f28_de_marcat([18]) == []
    assert marcate == {17, 18}


# ===========================================================================
# `rand_receptie` -- a treia tinta a unei decizii (felia 0056)
# ===========================================================================
# Prima rulare adevarata a contractului in doua faze (08.09.2026) a cazut cu «Recepția 188
# nu există pe acest angajament». `IDRR`-ul din propunere fusese dat INAUNTRUL tranzactiei
# derulate inapoi, iar contorul AUTO_INCREMENT nu se deruleaza cu ea: la salvare aceeasi
# receptie primea alt numar. Ancora corecta e indicele randului in `ListaReceptii`, exact
# ca `rand_istoric` pentru instantanee (F24).
def dec_rand(rand, actiune, data_h, rand_receptie):
    return {"rand_istoric": rand, "actiune": actiune, "data_h": data_h,
            "rand_receptie": rand_receptie}


def test_rand_receptie_is_a_third_target_and_the_three_exclude_each_other():
    ok = A.normalizeaza_decizii([dec_rand(0, "asociat", "2026-01-01 00:00:00", 3)])
    assert ok[0]["rand_receptie"] == 3
    assert ok[0]["idrr"] is None and ok[0]["receptie_noua"] is None

    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([{"rand_istoric": 0, "actiune": "asociat",
                                 "data_h": "2026-01-01 00:00:00",
                                 "idrr": 5, "rand_receptie": 3}])
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([{"rand_istoric": 0, "actiune": "asociat",
                                 "data_h": "2026-01-01 00:00:00",
                                 "rand_receptie": 3, "receptie_noua": "R1"}])


def test_rand_receptie_must_be_a_number():
    with pytest.raises(DecizieInvalida) as e:
        A.normalizeaza_decizii([dec_rand(0, "asociat", "2026-01-01 00:00:00", "trei")])
    assert "rand_receptie" in str(e.value)


def test_ignorat_and_reconstituire_refuse_a_rand_receptie():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([dec_rand(0, "ignorat", "2026-01-01 00:00:00", 3)])
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_decizii([{"rand_istoric": 0, "actiune": "reconstituire",
                                 "data_h": "2026-01-01 00:00:00",
                                 "receptie_noua": "R1", "rand_receptie": 3}])


def test_citeste_receptii_names_only_the_receptions_born_in_this_run():
    """
    `rand_receptie` pleaca DOAR pe receptiile pe care le-a nascut rularea curenta. Restul
    au un `IDRR` real, dinainte, care nu se misca -- si el ramane numele lor.
    """
    from datetime import datetime as _d
    cur = FakeCursor([
        [],                                          # liniile RHR
        [{"IDRR": 271, "NRCRT": 1, "DataR": _d(2026, 2, 11), "SumaAntet": 510.0,
          "Descriere": "veche", "Sters": 0, "Reconstituit": 0, "ReconstituitNesigur": 0},
         {"IDRR": 900, "NRCRT": 2, "DataR": _d(2026, 5, 3), "SumaAntet": 700.0,
          "Descriere": "nascuta acum", "Sters": 0, "Reconstituit": 0,
          "ReconstituitNesigur": 0}],
    ])
    out = A.citeste_receptii(cur, COD, {4: 900})
    dupa_idrr = {r["idrr"]: r for r in out}
    assert dupa_idrr[900]["rand_receptie"] == 4
    assert dupa_idrr[271]["rand_receptie"] is None


def _cursor_pentru_aplica(receptii_randuri, linii=None):
    """Falsul cu care `aplica_decizii` ajunge pana la `_tinta`: doua citiri de receptii."""
    return FakeCursor([linii or [], receptii_randuri])


def _linie_rhr(idrr, cod_ind="AAB", valoare=510.0):
    return {"IDRR": idrr, "CodIndicator": cod_ind, "CodAI": f"{COD}-{cod_ind}",
            "CodSSI": "", "CreditBugetar": 10502.19, "Valoare": valoare,
            "ValoareN": 0.0}


def _rand_receptie_db(idrr, suma):
    from datetime import datetime as _d
    return {"IDRR": idrr, "NRCRT": 1, "DataR": _d(2026, 2, 11), "SumaAntet": float(suma),
            "Descriere": "PLATA FACT.", "Sters": 0, "Reconstituit": 0,
            "ReconstituitNesigur": 0}


def test_a_decision_anchored_on_a_row_lands_on_the_id_of_THIS_run():
    """
    Chiar drumul care cadea. Propunerea a numit receptia 188; la salvare ea s-a nascut cu
    901. Decizia poarta indicele randului, deci ateriza pe 901 fara sa stie nimic despre
    188.
    """
    i = inst(0, "2026-02-12 10:00:00", 510)
    d = A.normalizeaza_decizii([dec_rand(0, "asociat", "2026-02-12 10:00:00", 2)])
    cur = _cursor_pentru_aplica([_rand_receptie_db(901, 510)], [_linie_rhr(901)])

    numarat = A.aplica_decizii(cur, COD, d, [i], [], [], ancore={2: 901})

    assert numarat["asociat"] == 1
    scrieri = [(sql, p) for sql, p in cur.executed
               if sql.startswith("UPDATE FX_Receptii_H SET IDRR")]
    assert scrieri and scrieri[0][1] == (901, 0, i["idrh"])


def test_a_rand_receptie_that_created_nothing_in_this_run_is_rejected_loudly():
    d = A.normalizeaza_decizii([dec_rand(0, "asociat", "2026-02-12 10:00:00", 7)])
    cur = _cursor_pentru_aplica([_rand_receptie_db(901, 510)])
    with pytest.raises(DecizieInvalida) as e:
        A.aplica_decizii(cur, COD, d, [inst(0, "2026-02-12 10:00:00", 510)], [], [],
                         ancore={2: 901})
    assert "ListaReceptii" in str(e.value)


def test_an_idrr_that_does_not_exist_still_fails_loudly():
    """Purtarea veche ramane neatinsa pentru receptiile numite prin `IDRR`."""
    d = A.normalizeaza_decizii([dec(0, "asociat", "2026-02-12 10:00:00", idrr=188)])
    cur = _cursor_pentru_aplica([_rand_receptie_db(901, 510)])
    with pytest.raises(DecizieInvalida) as e:
        A.aplica_decizii(cur, COD, d, [inst(0, "2026-02-12 10:00:00", 510)], [], [])
    assert "188" in str(e.value)


# ===========================================================================
# Contextul propunerii -- restul instantaneelor (felia 0056)
# ===========================================================================
def _rand_h(idrh, idrr, data_h, total=510.0, sters=0, tip="Final"):
    return {"IDRH": idrh, "IDRR": idrr, "IDH": 70 + idrh, "DataH": dt(data_h),
            "Total": total, "Descriere": "Salvare receptie.", "TipReceptie": tip,
            "Sters": sters, "EsteStergere": 0}


def test_the_context_is_exactly_the_complement_of_the_rows_to_decide():
    cur = FakeCursor([
        [],                                          # liniile
        [_rand_h(11, 5, "2026-02-12 10:00:00"),      # deja asezat -> context
         _rand_h(12, None, "2026-03-01 09:00:00"),   # de decis    -> lipseste de aici
         _rand_h(13, None, "2026-04-01 09:00:00", sters=1)],   # ignorat -> context
    ])
    out = A.citeste_instantanee_context(cur, COD, {12}, {})
    assert [r["idrh"] for r in out] == [11, 13]
    assert all(r["blocat"] for r in out)
    assert out[1]["ignorat"] is True


def test_a_placed_snapshot_carries_the_real_blocking_reasons():
    cur = FakeCursor([[], [_rand_h(11, 5, "2026-02-12 10:00:00")]])
    out = A.citeste_instantanee_context(cur, COD, set(), {11: ["Are ordonanțarea nr. 4."]})
    assert out[0]["motive"] == ["Are ordonanțarea nr. 4."]


def test_an_unresolvable_unplaced_snapshot_says_why_instead_of_claiming_a_link():
    """
    Un instantaneu neasezat care nu si-a gasit randul de istoric in descarcarea asta
    ajunge tot in context -- altfel ar disparea de pe ecran cu totul -- dar motivul lui NU
    e «legătura este deja scrisă»: nu are nicio legatura.
    """
    cur = FakeCursor([[], [_rand_h(14, None, "2026-05-01 09:00:00")]])
    out = A.citeste_instantanee_context(cur, COD, set(), {})
    assert out[0]["idrr"] == 0
    assert out[0]["motive"] == [A.MOTIV_FARA_ISTORIC]
