# Unit tests for routes/forexe/prelucrare_unitate.py (slice 0048-02).
#
# OFFLINE. There is no MariaDB here: the module under test only ever talks to a
# cursor, so the tests hand it a fake one. That is the whole reason the resolver
# lives in its own module instead of inside the route -- the decision tree that
# decides whether to ask the operator is the risky part, and it is testable.
#
# Run with:
#     PYTHON/.venv/Scripts/python.exe -m pytest tests/test_forexe_prelucrare_unitate.py
import pytest
import mysql.connector

from routes.forexe.prelucrare_unitate import (
    UnitChoiceRequired,
    UnitChoiceTableMissing,
    find_id_clsf_acc,
    find_unit_candidates,
    load_remembered_choices,
    normalize_supplied_choices,
    resolve_units,
    save_remembered_choice,
)


# ---------------------------------------------------------------------------
# A cursor that answers by SQL shape
# ---------------------------------------------------------------------------
class FakeCursor:
    """
    Stands in for conn.cursor(dictionary=True).

    `candidates` maps (SS, ClsfE) -> list of Clasificatii/Unitati rows.
    `remembered` is the FX_Alegeri_Unitate content, or the string "missing" to make
    the table look absent (errno 1146, what the driver really raises).
    `clsf` maps (IdUnitate, ClsfSal) -> list of IdClsfAcc rows.
    Every statement executed is recorded in `.executed` so a test can assert that a
    write happened -- or, just as important, that it did NOT.
    """

    def __init__(self, candidates=None, remembered=None, clsf=None):
        self.candidates = candidates or {}
        self.remembered = remembered if remembered is not None else {}
        self.clsf = clsf or {}
        self.executed = []
        self._result = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM Clasificatii C" in sql:
            self._result = list(self.candidates.get((params[0], params[1]), []))
        elif "FROM FX_Alegeri_Unitate" in sql:
            if self.remembered == "missing":
                raise mysql.connector.Error(msg="no such table", errno=1146)
            self._result = [
                {"SS": ss, "ClsfE": ce, "IdUnitate": idu}
                for (ss, ce), idu in self.remembered.items()
            ]
        elif "INSERT INTO FX_Alegeri_Unitate" in sql:
            if self.remembered == "missing":
                raise mysql.connector.Error(msg="no such table", errno=1146)
            self.remembered[(params[0], params[1])] = params[2]
            self._result = []
        elif "SELECT DISTINCT IdClsfAcc" in sql:
            self._result = list(self.clsf.get((params[0], params[1]), []))
        else:                                    # pragma: no cover - guard
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def writes_to_memory(self):
        """How many rows were pushed into FX_Alegeri_Unitate."""
        return sum(1 for sql, _ in self.executed
                   if "INSERT INTO FX_Alegeri_Unitate" in sql)

    def memory_reads(self):
        return sum(1 for sql, _ in self.executed
                   if sql.startswith("SELECT SS, ClsfE, IdUnitate"))


def unit(id_unitate, detalii, sursa="02E", program="PRG"):
    """One Clasificatii-joined-Unitati row, in the column names the SQL selects."""
    return {"IdUnitate": id_unitate, "Detalii": detalii,
            "SursaSector": sursa, "CodProgram": program, "Cnt": 1}


def indicator(cod="AAB", ss="02E", clsf_e="200101",
              clsf_sal="650402200101", raw="02E- 65. 04. 02. 20. 01. 01"):
    return {"cod_indicator": cod, "ss": ss, "clsf_e": clsf_e,
            "clsf_sal": clsf_sal, "clsf_raw": raw}


# ---------------------------------------------------------------------------
# find_unit_candidates
# ---------------------------------------------------------------------------
def test_candidates_are_reshaped_into_ascii_wire_keys():
    cur = FakeCursor(candidates={("02E", "200101"): [
        unit(75, "SC29 LOCAL", "02A", "P75"),
        unit(76, "ENERGETIC ISJ", "02E", "P76"),
    ]})
    got = find_unit_candidates(cur, "02E", "200101")
    assert got == [
        {"id_unitate": 75, "detalii": "SC29 LOCAL",
         "sursa_sector": "02A", "cod_program": "P75"},
        {"id_unitate": 76, "detalii": "ENERGETIC ISJ",
         "sursa_sector": "02E", "cod_program": "P76"},
    ]


def test_candidate_nulls_become_empty_strings_not_none():
    # The operator reads these; None would render as "Nothing" on the client.
    cur = FakeCursor(candidates={("02E", "200101"): [
        {"IdUnitate": 75, "Detalii": None, "SursaSector": None,
         "CodProgram": None, "Cnt": 1},
    ]})
    got = find_unit_candidates(cur, "02E", "200101")
    assert got[0]["detalii"] == "" and got[0]["cod_program"] == ""


# ---------------------------------------------------------------------------
# normalize_supplied_choices
# ---------------------------------------------------------------------------
def test_supplied_choices_are_keyed_by_the_pair():
    got = normalize_supplied_choices([
        {"ss": "02E", "clsfe": "200101", "id_unitate": 76, "retine": True},
        {"ss": "02A", "clsfe": "200301", "id_unitate": 75},
    ])
    assert got == {("02E", "200101"): (76, True), ("02A", "200301"): (75, False)}


def test_no_choices_is_an_empty_map_not_an_error():
    assert normalize_supplied_choices(None) == {}
    assert normalize_supplied_choices([]) == {}


@pytest.mark.parametrize("bad", [
    {"clsfe": "200101", "id_unitate": 76},                       # no ss
    {"ss": "02E", "id_unitate": 76},                             # no clsfe
    {"ss": "02E", "clsfe": "200101"},                            # no id_unitate
    {"ss": "02E", "clsfe": "200101", "id_unitate": "nu"},        # id not a number
    {"ss": "02E", "clsfe": "200101", "id_unitate": 76,
     "retine": "true"},                                          # retine as text
])
def test_a_malformed_choice_raises_rather_than_being_skipped(bad):
    # Skipping would re-ask a question the operator already answered.
    with pytest.raises(ValueError):
        normalize_supplied_choices([bad])


def test_choices_must_be_a_list():
    with pytest.raises(ValueError):
        normalize_supplied_choices({"ss": "02E"})


# ---------------------------------------------------------------------------
# resolve_units -- the unambiguous paths
# ---------------------------------------------------------------------------
def test_one_candidate_resolves_and_never_touches_the_memory_table():
    # A database that never hits an ambiguity must keep working even if
    # sql/0048_alegeri_unitate.sql was never run on it.
    cur = FakeCursor(candidates={("02E", "200101"): [unit(76, "ENERGETIC ISJ")]},
                     remembered="missing")
    warnings = []
    got = resolve_units(cur, [indicator()], {}, "op@x.ro", warnings)
    assert got == {("02E", "200101"): 76}
    assert cur.memory_reads() == 0
    assert warnings == []


def test_no_candidate_is_a_blocking_error_naming_the_indicator():
    cur = FakeCursor(candidates={})
    with pytest.raises(ValueError) as err:
        resolve_units(cur, [indicator(cod="AAC")], {}, "op@x.ro", [])
    assert "AAC" in str(err.value)
    assert "200101" in str(err.value)


# ---------------------------------------------------------------------------
# resolve_units -- the question
# ---------------------------------------------------------------------------
def test_two_candidates_and_no_answer_asks_the_operator():
    cur = FakeCursor(candidates={("02E", "200101"): [
        unit(75, "SC29 LOCAL"), unit(76, "ENERGETIC ISJ")]})
    with pytest.raises(UnitChoiceRequired) as err:
        resolve_units(cur, [indicator()], {}, "op@x.ro", [])
    pending = err.value.pending
    assert len(pending) == 1
    q = pending[0]
    assert q["ss"] == "02E" and q["clsfe"] == "200101"
    assert q["cod_indicator"] == "AAB"
    assert q["clsf"] == "02E- 65. 04. 02. 20. 01. 01"
    # Names, not just numbers -- the whole point of the round trip.
    assert [u["detalii"] for u in q["unitati"]] == ["SC29 LOCAL", "ENERGETIC ISJ"]


def test_two_indicators_sharing_a_pair_are_one_question_that_names_both():
    cur = FakeCursor(candidates={("02E", "200101"): [
        unit(75, "SC29 LOCAL"), unit(76, "ENERGETIC ISJ")]})
    rows = [indicator(cod="AAB"), indicator(cod="AAC")]
    with pytest.raises(UnitChoiceRequired) as err:
        resolve_units(cur, rows, {}, "op@x.ro", [])
    assert len(err.value.pending) == 1
    assert err.value.pending[0]["indicatori"] == ["AAB", "AAC"]


def test_every_ambiguous_pair_is_collected_before_asking():
    # One round trip for the whole angajament, not one per question.
    cur = FakeCursor(candidates={
        ("02E", "200101"): [unit(75, "SC29 LOCAL"), unit(76, "ENERGETIC ISJ")],
        ("02A", "200301"): [unit(80, "VENITURI"), unit(81, "REPUBLICAN")],
    })
    rows = [indicator(cod="AAB"),
            indicator(cod="AAD", ss="02A", clsf_e="200301",
                      clsf_sal="650402200301", raw="02A- 65. 04. 02. 20. 03. 01")]
    with pytest.raises(UnitChoiceRequired) as err:
        resolve_units(cur, rows, {}, "op@x.ro", [])
    assert [q["clsfe"] for q in err.value.pending] == ["200101", "200301"]


# ---------------------------------------------------------------------------
# resolve_units -- the answer
# ---------------------------------------------------------------------------
def test_a_supplied_choice_resolves_and_is_not_stored_when_the_box_is_unticked():
    cur = FakeCursor(candidates={("02E", "200101"): [
        unit(75, "SC29 LOCAL"), unit(76, "ENERGETIC ISJ")]})
    got = resolve_units(cur, [indicator()],
                        {("02E", "200101"): (76, False)}, "op@x.ro", [])
    assert got == {("02E", "200101"): 76}
    assert cur.writes_to_memory() == 0


def test_a_ticked_choice_is_stored_for_next_time():
    cur = FakeCursor(candidates={("02E", "200101"): [
        unit(75, "SC29 LOCAL"), unit(76, "ENERGETIC ISJ")]})
    resolve_units(cur, [indicator()],
                  {("02E", "200101"): (76, True)}, "op@x.ro", [])
    assert cur.writes_to_memory() == 1
    assert cur.remembered[("02E", "200101")] == 76


def test_a_stored_choice_answers_silently_the_next_run():
    cur = FakeCursor(
        candidates={("02E", "200101"): [unit(75, "SC29 LOCAL"),
                                        unit(76, "ENERGETIC ISJ")]},
        remembered={("02E", "200101"): 76})
    warnings = []
    got = resolve_units(cur, [indicator()], {}, "op@x.ro", warnings)
    assert got == {("02E", "200101"): 76}
    assert warnings == []
    assert cur.writes_to_memory() == 0


def test_a_different_pair_is_still_asked_even_though_another_one_is_stored():
    # "if a new combo comes up - ask again for that combo" -- the operator's rule.
    cur = FakeCursor(
        candidates={
            ("02E", "200101"): [unit(75, "a"), unit(76, "b")],
            ("02A", "200301"): [unit(80, "c"), unit(81, "d")],
        },
        remembered={("02E", "200101"): 76})
    rows = [indicator(cod="AAB"),
            indicator(cod="AAD", ss="02A", clsf_e="200301",
                      clsf_sal="650402200301", raw="02A- 65. 04. 02. 20. 03. 01")]
    with pytest.raises(UnitChoiceRequired) as err:
        resolve_units(cur, rows, {}, "op@x.ro", [])
    assert [q["clsfe"] for q in err.value.pending] == ["200301"]


def test_a_request_choice_beats_a_stored_one():
    cur = FakeCursor(
        candidates={("02E", "200101"): [unit(75, "a"), unit(76, "b")]},
        remembered={("02E", "200101"): 76})
    got = resolve_units(cur, [indicator()],
                        {("02E", "200101"): (75, False)}, "op@x.ro", [])
    assert got == {("02E", "200101"): 75}


def test_a_stored_choice_that_no_longer_matches_warns_and_asks_again():
    # The nomenclator moved under a remembered answer. Asking again beats writing
    # a unit the pair cannot mean any more.
    cur = FakeCursor(
        candidates={("02E", "200101"): [unit(75, "a"), unit(77, "c")]},
        remembered={("02E", "200101"): 76})
    warnings = []
    with pytest.raises(UnitChoiceRequired):
        resolve_units(cur, [indicator()], {}, "op@x.ro", warnings)
    assert len(warnings) == 1
    assert "76" in warnings[0]


def test_a_choice_naming_an_impossible_unit_is_refused():
    cur = FakeCursor(candidates={("02E", "200101"): [unit(75, "a"), unit(76, "b")]})
    with pytest.raises(ValueError) as err:
        resolve_units(cur, [indicator()], {("02E", "200101"): (99, False)},
                      "op@x.ro", [])
    assert "99" in str(err.value)


def test_the_missing_memory_table_is_loud_and_names_the_ddl_file():
    cur = FakeCursor(candidates={("02E", "200101"): [unit(75, "a"), unit(76, "b")]},
                     remembered="missing")
    with pytest.raises(UnitChoiceTableMissing) as err:
        resolve_units(cur, [indicator()], {}, "op@x.ro", [])
    assert "0048_alegeri_unitate.sql" in str(err.value)


def test_storing_a_choice_into_a_missing_table_is_loud_too():
    cur = FakeCursor(remembered="missing")
    with pytest.raises(UnitChoiceTableMissing):
        save_remembered_choice(cur, "02E", "200101", 76, "op@x.ro")


def test_load_remembered_rethrows_other_driver_errors_unchanged():
    class Boom(FakeCursor):
        def execute(self, sql, params=None):
            raise mysql.connector.Error(msg="gone", errno=2006)

    with pytest.raises(mysql.connector.Error):
        load_remembered_choices(Boom())


# ---------------------------------------------------------------------------
# find_id_clsf_acc
# ---------------------------------------------------------------------------
def test_classification_found_returns_the_access_id():
    cur = FakeCursor(clsf={(76, "650402200101"): [{"IdClsfAcc": 1204}]})
    warnings = []
    got = find_id_clsf_acc(cur, 76, "650402200101", "raw", "AAB", warnings)
    assert got == 1204 and warnings == []


def test_classification_not_found_is_none_plus_a_warning_not_an_error():
    # Decision D19: `If Not IsNull(IdClsf) Then RC!IdClsf = IdClsf` -- Access wrote
    # the row anyway. Ported, but the operator is told.
    cur = FakeCursor(clsf={})
    warnings = []
    got = find_id_clsf_acc(cur, 76, "650402200101", "02E- 65...", "AAB", warnings)
    assert got is None
    assert len(warnings) == 1 and "AAB" in warnings[0]


def test_conflicting_classification_ids_raise_instead_of_picking_one():
    cur = FakeCursor(clsf={(76, "650402200101"): [{"IdClsfAcc": 1204},
                                                  {"IdClsfAcc": 1301}]})
    with pytest.raises(ValueError) as err:
        find_id_clsf_acc(cur, 76, "650402200101", "raw", "AAB", [])
    assert "1204" in str(err.value) and "1301" in str(err.value)
