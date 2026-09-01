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
# F13 -- RETRAS ca veto pe 31.08.2026; a ramas SEMN
# ===========================================================================
# Cele doua teste de mai jos verificau vetoul. Au fost rescrise, nu sterse: regula nu a
# disparut, a coborat. `DataR` e un camp tastat pe site si schimbabil dupa aceea, iar
# `FX_Receptii_R` nu are nicio coloana cu momentul crearii (F29), deci un refuz cladit pe
# el poate opri o plasare corecta -- si pe calea de ingestie asta infunda operatorul pe o
# receptie pe care nu o poate repara (F10).
def test_the_date_rule_no_longer_rejects_and_warns_instead():
    r = rec(1, "2026-03-01 08:00:00", 510)
    i = inst(9, "2026-01-19 10:00:00", 510)
    avertismente = []
    A.valideaza_plasarile({1: [i]}, {1: r}, avertismente=avertismente)   # nu ridica
    assert len(avertismente) == 1
    assert "mai vechi decât data recepției" in avertismente[0]


def test_the_date_sign_is_measured_on_the_DAY_not_on_the_second():
    """
    Formularea veche cerea timestamp complet, pornind de la ideea ca ambele capete sunt
    momente. Nu sunt: `DataR` e o data TASTATA, deci soseste la miezul noptii, iar `DataH`
    e ceasul sistemului. Comparate ca momente, orice instantaneu din chiar ziua receptiei
    ar iesi «inainte de ea», si semnul s-ar aprinde pe date perfect corecte.
    """
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
