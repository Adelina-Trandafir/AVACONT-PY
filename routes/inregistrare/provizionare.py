# routes/inregistrare/provizionare.py
"""
Approving a registration request: the provisioning job (slice 0075-03, plan 5.6).

One call turns one `FX_Inregistrari` row into a working unit:

    1   the database name `1nn_SSSS`, computed again now (the preview the applicant
        was never shown is not a promise)
    2   the database, cloned from AVACONT_SURSA -- every table and view (D22)
    3   `IdUnitate` = MAX(CAI.IdUnitate) + 1, one per sector-source, under a lock
    4   the `CAI` rows (schema_sync finds unit databases there)
    4a  `AVACONT_COMUN.Unitati` + one `Unitati_Ani` row per sector-source (D23, D25)
    5   the unit database's own `Unitati` rows (same IdUnitate as in CAI)
    6   the `Clasificatii` rows (plan 6), in one transaction
    7   the MariaDB account named by the e-mail, rights on THIS database only (D18),
        and its `Unitati_Utilizatori` row, role `Contabil` (D24)
    8   the request marked `Aprobata`, and a one-time link (24 h) mailed to the user
        to choose a password (D13)

EVERY STEP THAT LEAVES SOMETHING BEHIND REGISTERS HOW TO TAKE IT BACK, right after it
succeeds. Any failure unwinds the list in reverse order, marks the request `Esuata`
with the reason in `Motiv`, and says what, if anything, could NOT be taken back. A
failed undo is never swallowed: it goes into `Motiv` and into the log, word for word,
because it is exactly what the operator has to clean by hand. A failed run can be
approved again once the cause is fixed (plan 7).

The mail of step 8 is the one thing that does NOT unwind: by then the unit exists and
is correct, and a mail server that is down is no reason to demolish it. The answer says
`link_trimis: false` and `link_nou()` sends a fresh link later.

WHO RUNS IT. The provisioning account (`DB_CONFIG_PROVIZIONARE`), never the service
account (D18). What that account needs is in `sql/0075_03_provizionare.sql`.
One run at a time on the whole server: a named MariaDB lock, so the command line on
the VPS and the approval page of 0075-05 cannot race each other either.

`plan()` runs every check and computes every value, and writes nothing -- the only way
to look at a request's consequences before they happen, since this pass has no tests
(operator decision, 22.09.2026) and the developer has no access to the server.
"""
import hashlib
import json
import logging
import re
import secrets

import config
from routes.auth import mailer
from routes.inregistrare import cerere as cerere_mod
from routes.inregistrare import nume, randuri
from utils.database import get_kbot_provisioning_connection

logger = logging.getLogger(__name__)

TEMPLATE_DB = "AVACONT_SURSA"

# D24: the Romanian word already stored on the live rows. Administrator and Director
# come later; they are values of this one column, not new code paths.
ROL_CONTABIL = "Contabil"

# D18: what a unit user needs on their own database, and nothing else. The GRANT
# itself is written inside the procedure below; this is the text reported on screen.
USER_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, EXECUTE"
_GRANT_PROCEDURE = "`AVACONT_COMUN`.`proc_Provizionare_Grant`"

# D25: CodProgram prefilled per sector. A sector outside these has no known value.
COD_PROGRAM_BY_SECTOR = {"01": "0000002510", "02": "0000000000"}
COD_PROGRAM_DEFAULT = "0000000000"
# `CAI.CodProgram` is varchar(32), the narrowest of the three columns it lands in.
COD_PROGRAM_MAX_LENGTH = 32

# D13: life of the password link.
LINK_TTL_HOURS = 24
_LINK_TOKEN_NBYTES = 32

# Server-wide: one provisioning at a time. And the lock D6 needs, because nothing on
# `CAI.IdUnitate` is unique -- the column has no index at all (plan 0.0).
_RUN_LOCK = "kbot_provizionare"
_ID_LOCK = "cai_idunitate"
_ID_LOCK_WAIT = 10

# What a request must be in to be approved: waiting, or failed and being retried.
_APPROVABLE = (cerere_mod.STARE_IN_ASTEPTARE, cerere_mod.STARE_ESUATA)

# The work columns 0075-00 left on the template (plan 0.0). A unit cloned while they
# are there would carry three dead columns no other unit database has.
_TEMPLATE_LEFTOVERS = ("Sector_w", "Sursa_w", "SS_w")

# The names this job builds, and the only ones it will ever DROP.
_NEW_DB_NAME = re.compile(r"^1[1-9]{2}_[A-Z]{4}$")
_CHARSET_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_]+$")

# The e-mail becomes a MariaDB account name. Parameters quote it, but an account name
# is not the place to find out how well: anything beyond this is refused.
_SAFE_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

_AUTO_INCREMENT = re.compile(r"\sAUTO_INCREMENT=\d+")
_DEFINER = re.compile(r"\sDEFINER=`[^`]*`@`[^`]*`")

# `Motiv` is TEXT (65 535 bytes) in utf8mb3, three bytes a character at worst.
_MOTIV_MAX_CHARS = 20000

# Rows per INSERT batch at step 6.
_BATCH = 1000


class ProvizionareRefuzata(RuntimeError):
    """The request cannot be approved as it stands. Carries the Romanian sentence.
    Raised before anything is created, so there is nothing to unwind."""


class ProvizionareEsuata(RuntimeError):
    """A step failed after something was created. Everything registered has been
    unwound (or `ramas` says what was not), and the request is `Esuata`."""

    def __init__(self, message, ramas):
        super().__init__(message)
        self.message = message
        self.ramas = ramas


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def plan(id_cerere, cod_program=None, progress=None, db_name=None) -> dict:
    """Every check and every computed value of an approval. Writes nothing.
    `db_name` is the operator's own choice of name (0075-05), checked as at approval."""
    say = _sayer(progress)
    conn = get_kbot_provisioning_connection()
    try:
        row = _load(conn, id_cerere)
        _require_approvable(row)
        prepared = _prepare(conn, row, cod_program, say)
        proposed = nume.db_name(nume.first_free_number(nume.used_prefixes(conn)),
                                prepared["key"])
        dc = _choose_name(conn, proposed, db_name)
        first_id = _max_id_unitate(conn) + 1
        tables, views = _template_objects(conn)
        conn.rollback()
    finally:
        _close(conn)

    ids = {ss: first_id + i for i, ss in enumerate(prepared["ss"])}
    if dc == proposed:
        say(f"Baza propusă acum: {dc} (se recalculează la aprobare).")
    else:
        say(f"Baza aleasă de operator: {dc} (propunerea calculată era {proposed}).")
    say(f"Șablon: {len(tables)} tabele, {len(views)} view-uri.")
    for ss in prepared["ss"]:
        say(f"  {ss}: IdUnitate ≈ {ids[ss]}, CodProgram {prepared['cod_program'][ss]}")
    say(f"Clasificații: {prepared['randuri']} rânduri.")
    say("Doar verificare: nu s-a scris nimic.")
    return {
        "id_cerere": id_cerere,
        "db_name": dc,
        "db_name_propus": proposed,
        "id_unitate": ids,
        "cod_program": prepared["cod_program"],
        "randuri": prepared["randuri"],
        "tabele": len(tables),
        "viewuri": len(views),
        "email": prepared["email"],
        "denumire": prepared["denumire"],
        "an": prepared["an"],
        "scris": False,
    }


def approve(id_cerere, decided_by, cod_program=None, progress=None, db_name=None) -> dict:
    """
    Runs the whole job for one request. See the note at the top of the file.

    `db_name` None = the name computed now (plan 5.4). Otherwise the operator's own
    choice (0075-05), held to the same rules: shape `1nn_SSSS`, a number no other
    database uses, a database that does not exist yet. A bad name is refused before
    anything is created and does NOT mark the request failed -- it is the operator's
    input, not the request's fault.

    Raises ProvizionareRefuzata when the request cannot be approved as it stands
    (nothing created; the request is marked `Esuata` unless it was not approvable to
    begin with), and ProvizionareEsuata when a step failed after something was built
    (everything unwound, the request `Esuata`).
    """
    say = _sayer(progress)
    decided_by = (decided_by or "").strip()[:80] or "necunoscut"

    lock_conn = get_kbot_provisioning_connection()
    try:
        if not _get_lock(lock_conn, _RUN_LOCK, 0):
            raise ProvizionareRefuzata(
                "O altă aprobare rulează chiar acum pe server. Reîncercați după ce se încheie."
            )
        try:
            return _approve_locked(id_cerere, decided_by, cod_program, say, db_name)
        finally:
            _release_lock(lock_conn, _RUN_LOCK)
    finally:
        _close(lock_conn)


def link_nou(id_cerere, progress=None) -> dict:
    """
    A fresh password link for an approved request whose link was lost, expired or
    never mailed. The old link stops working: only one hash is kept per request.
    """
    say = _sayer(progress)
    conn = get_kbot_provisioning_connection()
    try:
        row = _load(conn, id_cerere)
        if row["Stare"] != cerere_mod.STARE_APROBATA:
            raise ProvizionareRefuzata(
                f"Cererea {id_cerere} nu este aprobată (stare: {row['Stare']})."
            )
        email = row["Email"]
        if not _account_exists(conn, email):
            raise ProvizionareRefuzata(
                f"Contul {email} nu există pe server; un link de parolă nu ar folosi la nimic."
            )
        token = _store_link(conn, id_cerere)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _close(conn)

    say("Link nou de parolă generat; cel vechi nu mai funcționează.")
    sent = _send_link(email, row["Denumire"], token, say)
    return {"id_cerere": id_cerere, "link_trimis": sent,
            "link": None if sent else password_link(token)}


def reject(id_cerere, decided_by, motiv) -> dict:
    """
    Plan 7: the request becomes `Respinsa`, with the operator's reason in `Motiv`.
    Only a waiting or failed request; nothing was built for either, so there is nothing
    to take down. Under the same server-wide lock as approve(), so a rejection cannot
    land in the middle of an approval of the same request from the command line.
    Answers the applicant's address and unit name, for the mail the caller sends.
    """
    decided_by = (decided_by or "").strip()[:80] or "necunoscut"
    lock_conn = get_kbot_provisioning_connection()
    try:
        if not _get_lock(lock_conn, _RUN_LOCK, _ID_LOCK_WAIT):
            raise ProvizionareRefuzata(
                "O aprobare rulează chiar acum pe server. Reîncercați după ce se încheie."
            )
        try:
            conn = get_kbot_provisioning_connection()
            try:
                row = _load(conn, id_cerere)
                if row["Stare"] not in _APPROVABLE:
                    raise ProvizionareRefuzata(
                        f"Cererea {id_cerere} are starea {row['Stare']}; se pot respinge doar "
                        f"cererile {' sau '.join(_APPROVABLE)}."
                    )
                cur = conn.cursor(buffered=True)
                cur.execute(
                    "UPDATE FX_Inregistrari SET Stare = %s, Motiv = %s, DataDecizie = NOW(), "
                    "Decis = %s, ParolaHash = NULL, ParolaExpira = NULL WHERE IdCerere = %s",
                    (cerere_mod.STARE_RESPINSA, motiv[:_MOTIV_MAX_CHARS], decided_by,
                     int(id_cerere)),
                )
                conn.commit()
            except Exception:
                _safe_rollback(conn)
                raise
            finally:
                _close(conn)
        finally:
            _release_lock(lock_conn, _RUN_LOCK)
    finally:
        _close(lock_conn)
    return {"id_cerere": int(id_cerere), "email": row["Email"], "denumire": row["Denumire"]}


def password_link(token) -> str:
    """The address the user opens. The token rides in the fragment (`#`), which
    browsers never send to a server -- so it stays out of every access log and every
    Referer header on the way."""
    base = str(getattr(config, "PUBLIC_BASE_URL", "") or "https://kbot.avatarsoft.ro")
    return f"{base.rstrip('/')}/parola#{token}"


def hash_link_token(token) -> str:
    """SHA-256 hex of a link token: what `FX_Inregistrari.ParolaHash` stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def _approve_locked(id_cerere, decided_by, cod_program, say, wanted_name=None):
    conn = get_kbot_provisioning_connection()
    undo = []
    step = "verificări"
    dc = None
    try:
        row = _load(conn, id_cerere)
        _require_approvable(row)
        say(f"Cererea {id_cerere}: {row['Denumire']} (CF {row['CF']}), {row['Email']}.")

        try:
            prepared = _prepare(conn, row, cod_program, say)
        except ProvizionareRefuzata as refused:
            _mark_failed(id_cerere, refused.args[0], decided_by)
            raise

        step = "1 · numele bazei"
        number = nume.first_free_number(nume.used_prefixes(conn))
        proposed = nume.db_name(number, prepared["key"])
        if not _NEW_DB_NAME.match(proposed):
            raise RuntimeError(f"numele calculat {proposed!r} nu are forma 1nn_SSSS")
        dc = _choose_name(conn, proposed, wanted_name)
        if _schema_exists(conn, dc):
            raise RuntimeError(f"baza {dc} există deja pe server")
        conn.rollback()
        say(f"Pas 1: baza nouă se va numi {dc}.")

        step = "2 · crearea bazei"
        _create_database(conn, dc, undo, say)

        step = "3-4a · registrul (CAI, Unitati, Unitati_Ani)"
        ids = _register_unit(conn, dc, prepared, undo, say)

        step = "5-6 · Unitati și Clasificatii în baza nouă"
        _fill_unit_database(dc, prepared, ids, say)

        step = "7 · contul utilizatorului"
        _create_account(conn, dc, prepared["email"], undo, say)

        step = "8 · marcarea cererii"
        token = _store_link(conn, id_cerere)
        cur = conn.cursor(buffered=True)
        cur.execute(
            "UPDATE FX_Inregistrari SET Stare = %s, DbName = %s, Motiv = NULL, "
            "DataDecizie = NOW(), Decis = %s WHERE IdCerere = %s",
            (cerere_mod.STARE_APROBATA, dc, decided_by, id_cerere),
        )
        conn.commit()
        say(f"Pas 8: cererea {id_cerere} este aprobată.")
    except ProvizionareRefuzata:
        conn.rollback()
        raise
    except Exception as exc:
        _safe_rollback(conn)
        logger.exception("provisioning of request %s failed at step %s", id_cerere, step)
        say(f"EROARE la pasul {step}: {exc}")
        left = _unwind(undo, say)
        message = f"Eșuat la pasul {step}: {exc}"
        if left:
            message += " | NU s-a putut anula: " + " ; ".join(left)
        _mark_failed(id_cerere, message, decided_by)
        raise ProvizionareEsuata(message, left) from exc
    finally:
        _close(conn)

    # Past this point the unit exists and stays. See the note at the top of the file.
    sent = _send_link(prepared["email"], prepared["denumire"], token, say)
    return {
        "id_cerere": id_cerere,
        "db_name": dc,
        "id_unitate": ids,
        "randuri": prepared["randuri"],
        "email": prepared["email"],
        "link_trimis": sent,
        "link": None if sent else password_link(token),
        "scris": True,
    }


def _prepare(conn, row, cod_program, say) -> dict:
    """Every check that can be made before anything is created. Pure reads."""
    try:
        payload = json.loads(row["Payload"] or "{}")
    except ValueError:
        raise ProvizionareRefuzata("Conținutul cererii (Payload) nu este JSON valid.") from None
    ss_list = [str(v) for v in payload.get("ss") or []]
    f_list = [str(v) for v in payload.get("f") or []]
    e_list = [str(v) for v in payload.get("e") or []]
    if not ss_list or not f_list or not e_list:
        raise ProvizionareRefuzata("Cererea nu are surse-sector sau clasificații.")

    email = (row["Email"] or "").strip().lower()
    if not _SAFE_EMAIL.match(email) or len(email) > 80:
        raise ProvizionareRefuzata(
            f"Adresa {email!r} conține caractere care nu pot forma un cont MariaDB."
        )
    if _email_taken(conn, email):
        raise ProvizionareRefuzata(
            f"Adresa {email} este deja un cont MariaDB sau un utilizator K-BOT."
        )

    cf = (row["CF"] or "").strip()
    if _cf_taken(conn, cf):
        raise ProvizionareRefuzata(f"Pentru codul fiscal {cf} există deja o bază (D11).")

    denumire = nume.normalize_name(row["Denumire"])
    try:
        key = nume.letter_key(denumire)
    except nume.NameTooShort:
        raise ProvizionareRefuzata(
            "Denumirea unității are sub patru litere; numele bazei nu se poate forma."
        ) from None

    _check_template(conn)

    dictionaries = randuri.read_dictionaries(conn)
    placeholder_ids = {ss: 0 for ss in ss_list}
    try:
        rows = randuri.build(ss_list, f_list, e_list, placeholder_ids, dictionaries)
    except randuri.RanduriInvalide as err:
        raise ProvizionareRefuzata(str(err)) from None
    if len(rows) > cerere_mod.MAX_ROWS:
        raise ProvizionareRefuzata(
            f"Cererea ar produce {len(rows)} clasificații, peste limita de "
            f"{cerere_mod.MAX_ROWS}."
        )

    codes = _cod_program(ss_list, cod_program)
    say(f"Verificări trecute: {len(ss_list)} surse-sector, {len(f_list)} coduri F, "
        f"{len(e_list)} coduri E.")
    return {
        "email": email,
        "cf": cf,
        "denumire": denumire,
        "an": int(row["An"]),
        "key": key,
        "ss": ss_list,
        "f": f_list,
        "e": e_list,
        "dictionaries": dictionaries,
        "randuri": len(rows),
        "cod_program": codes,
    }


def _cod_program(ss_list, overrides) -> dict:
    """
    D25: CodProgram per sector-source, prefilled from the sector; the operator's
    value wins. An override for a sector-source the request does not have is refused
    (house rule: an unknown key is an error, never a quiet no-op).
    """
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(ss_list))
    if unknown:
        raise ProvizionareRefuzata(
            "CodProgram dat pentru surse-sector care nu sunt în cerere: " + ", ".join(unknown)
        )
    out = {}
    for ss in ss_list:
        value = overrides.get(ss)
        if value is None:
            value = COD_PROGRAM_BY_SECTOR.get(ss[:2], COD_PROGRAM_DEFAULT)
        value = str(value).strip()
        if not value or len(value) > COD_PROGRAM_MAX_LENGTH or not value.isalnum():
            raise ProvizionareRefuzata(
                f"CodProgram pentru {ss} trebuie să aibă 1-{COD_PROGRAM_MAX_LENGTH} "
                "litere sau cifre."
            )
        out[ss] = value
    return out


def _choose_name(conn, proposed, wanted) -> str:
    """
    The computed name, or the operator's (0075-05) after three checks. The shape is not
    a style rule: the provisioning account may create only databases matching the
    pattern `1__\\_____`, and proc_Provizionare_Grant refuses any other name.
    """
    if wanted is None or not str(wanted).strip():
        return proposed
    dc = str(wanted).strip().upper()
    if dc == proposed:
        return dc
    if not _NEW_DB_NAME.match(dc):
        raise ProvizionareRefuzata(
            f"Numele bazei «{dc}» nu are forma 1nn_SSSS: cifra 1, două cifre de la 1 la 9, "
            "liniuță jos, patru litere A-Z (ex. 111_VTRS)."
        )
    if dc[:3] in nume.used_prefixes(conn):
        raise ProvizionareRefuzata(
            f"Numărul {dc[:3]} este deja folosit de altă bază. Alegeți alt număr."
        )
    if _schema_exists(conn, dc):
        raise ProvizionareRefuzata(f"Baza {dc} există deja pe server.")
    return dc


# ---------------------------------------------------------------------------
# Step 2 -- the database
# ---------------------------------------------------------------------------
def _create_database(conn, dc, undo, say):
    """CREATE DATABASE with the template's defaults, then every table and view."""
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
        (TEMPLATE_DB,),
    )
    found = cur.fetchone()
    if found is None:
        raise RuntimeError(f"șablonul {TEMPLATE_DB} nu există pe server")
    charset, collation = str(found[0]), str(found[1])
    if not _CHARSET_NAME.match(charset) or not _CHARSET_NAME.match(collation):
        raise RuntimeError(f"setul de caractere al șablonului arată ciudat: {charset}/{collation}")

    cur.execute(f"CREATE DATABASE {_q(dc)} CHARACTER SET {charset} COLLATE {collation}")
    undo.append((f"DROP DATABASE {dc}", lambda: _drop_database(dc)))
    say(f"Pas 2: baza {dc} creată ({charset}/{collation}).")

    tables, views = _template_objects(conn)
    unit = get_kbot_provisioning_connection(dc)
    try:
        ucur = unit.cursor(buffered=True)
        # Tables arrive in alphabetical order, not in the order their foreign keys
        # need. Off for this session only, back on before anything is inserted.
        ucur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tables:
            ucur.execute(_clone_table_sql(cur, table, dc))
        ucur.execute("SET FOREIGN_KEY_CHECKS = 1")
        _clone_views(cur, ucur, views, dc)
    finally:
        _close(unit)

    made_tables, made_views = _objects_of(conn, dc)
    if len(made_tables) != len(tables) or len(made_views) != len(views):
        raise RuntimeError(
            f"clona are {len(made_tables)} tabele / {len(made_views)} view-uri, "
            f"șablonul {len(tables)} / {len(views)}"
        )
    say(f"Pas 2: {len(tables)} tabele și {len(views)} view-uri copiate din {TEMPLATE_DB}.")


def _clone_table_sql(cur, table, dc) -> str:
    """
    The template's own CREATE TABLE, pointed at the new database: references to the
    template by name become references to the new one, and the AUTO_INCREMENT counter
    starts afresh. References to AVACONT_COMUN (the four dictionary foreign keys) are
    left exactly as they are.
    """
    cur.execute(f"SHOW CREATE TABLE {_q(TEMPLATE_DB)}.{_q(table)}")
    ddl = _text(cur.fetchone()[1])
    ddl = ddl.replace(f"`{TEMPLATE_DB}`", f"`{dc}`")
    return _AUTO_INCREMENT.sub("", ddl)


def _clone_views(cur, ucur, views, dc):
    """
    Views in rounds: one that reads another view fails until that one exists, so
    every round retries what failed and stops only when a round makes no progress.

    The DEFINER goes: creating a view for another account needs SUPER, which the
    provisioning account does not have, and a view owned by the account that made
    the database is what a fresh clone should have anyway.
    """
    pending = []
    for view in views:
        cur.execute(f"SHOW CREATE VIEW {_q(TEMPLATE_DB)}.{_q(view)}")
        ddl = _text(cur.fetchone()[1]).replace(f"`{TEMPLATE_DB}`", f"`{dc}`")
        pending.append((view, _DEFINER.sub("", ddl)))

    while pending:
        failed = []
        last_error = None
        for view, ddl in pending:
            try:
                ucur.execute(ddl)
            except Exception as err:    # retried next round; the last one is raised
                failed.append((view, ddl))
                last_error = err
        if len(failed) == len(pending):
            names = ", ".join(v for v, _ in failed)
            raise RuntimeError(f"view-urile {names} nu au putut fi create: {last_error}")
        pending = failed


# ---------------------------------------------------------------------------
# Steps 3, 4, 4a -- the registry in AVACONT_COMUN
# ---------------------------------------------------------------------------
def _register_unit(conn, dc, p, undo, say) -> dict:
    """
    One transaction, under the IdUnitate lock: CAI (one row per sector-source),
    Unitati, Unitati_Ani. The undo steps are registered only after the commit --
    before it, a failure rolls the three back by itself.
    """
    if not _get_lock(conn, _ID_LOCK, _ID_LOCK_WAIT):
        raise RuntimeError("blocarea «cai_idunitate» nu a putut fi obținută")
    try:
        # A fresh transaction AFTER the lock: an older snapshot on this connection
        # would read a MAX that another run has already moved past.
        conn.rollback()
        first = _max_id_unitate(conn) + 1
        ids = {ss: first + i for i, ss in enumerate(p["ss"])}
        cur = conn.cursor(buffered=True)
        id_cai = []
        for ss in p["ss"]:
            cur.execute(
                "INSERT INTO CAI (IdUnitate, DbName, NumeUnitate, Sursa, CF, CodProgram, "
                "AnDate, DC) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (ids[ss], dc, p["denumire"], ss, p["cf"], p["cod_program"][ss], p["an"], dc),
            )
            id_cai.append(cur.lastrowid)
        cur.execute(
            "INSERT INTO Unitati (DC, NumeUnitate, CF) VALUES (%s, %s, %s)",
            (dc, p["denumire"], p["cf"]),
        )
        for ss in p["ss"]:
            cur.execute(
                "INSERT INTO Unitati_Ani (DC, AN, SS, CodProgram) VALUES (%s, %s, %s, %s)",
                (dc, p["an"], ss, p["cod_program"][ss]),
            )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _release_lock(conn, _ID_LOCK)

    undo.append((f"CAI IdCai {id_cai}", lambda: _delete_where(
        "DELETE FROM CAI WHERE DbName = %s AND IdCai IN (" + ", ".join(["%s"] * len(id_cai)) + ")",
        (dc, *id_cai))))
    undo.append((f"Unitati {dc}", lambda: _delete_where(
        "DELETE FROM Unitati WHERE DC = %s", (dc,))))
    undo.append((f"Unitati_Ani {dc}", lambda: _delete_where(
        "DELETE FROM Unitati_Ani WHERE DC = %s", (dc,))))
    say("Pas 3-4a: " + ", ".join(f"{ss} ▸ IdUnitate {ids[ss]}" for ss in p["ss"])
        + f"; Unitati și {len(p['ss'])} rânduri Unitati_Ani scrise.")
    return ids


# ---------------------------------------------------------------------------
# Steps 5, 6 -- inside the new database
# ---------------------------------------------------------------------------
def _fill_unit_database(dc, p, ids, say):
    """
    Unitati first (Clasificatii points at it, F7), then every Clasificatii row, in one
    transaction. No undo of their own: both go with DROP DATABASE.
    """
    rows = randuri.build(p["ss"], p["f"], p["e"], ids, p["dictionaries"])
    unit = get_kbot_provisioning_connection(dc)
    try:
        cur = unit.cursor(buffered=True)
        for ss in p["ss"]:
            cur.execute(
                "INSERT INTO Unitati (IdUnitate, Detalii, SursaSector, An, CodProgram) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ids[ss], p["denumire"], ss, p["an"], p["cod_program"][ss]),
            )
        for start in range(0, len(rows), _BATCH):
            cur.executemany(randuri.INSERT_SQL, rows[start:start + _BATCH])
        cur.execute("SELECT COUNT(*) FROM Clasificatii")
        written = int(cur.fetchone()[0])
        if written != len(rows):
            raise RuntimeError(f"s-au scris {written} clasificații din {len(rows)}")
        unit.commit()
    except Exception:
        _safe_rollback(unit)
        raise
    finally:
        _close(unit)
    say(f"Pas 5-6: {len(p['ss'])} rânduri Unitati și {len(rows)} clasificații scrise în {dc}.")


# ---------------------------------------------------------------------------
# Step 7 -- the account
# ---------------------------------------------------------------------------
def _create_account(conn, dc, email, undo, say):
    """
    The e-mail becomes a MariaDB account with a random password nobody knows (the user
    chooses their own through the link of step 8). Rights on the new database only --
    never the grants today's accounts carry (D18, D26, plan 13).
    """
    cur = conn.cursor(buffered=True)
    cur.execute("CREATE USER %s@'%' IDENTIFIED BY %s", (email, secrets.token_urlsafe(32)))
    undo.append((f"DROP USER {email}", lambda: _drop_user(email)))
    # Not a GRANT of our own: in `GRANT ... ON db.*` MariaDB treats the name as a
    # pattern and counts the grantor's rights only when granted on that exact string,
    # so rights held on the pattern `1__\_____` can never be passed on (1044 on the
    # first real run, 23.09.2026). A root-owned procedure does this one GRANT after
    # checking both arguments -- sql/0075_03_02_grant_procedura.sql.
    cur.execute(f"CALL {_GRANT_PROCEDURE}(%s, %s)", (dc, email))
    say(f"Pas 7: contul {email} creat, drepturi {USER_PRIVILEGES} numai pe {dc}.")

    try:
        cur.execute(
            "INSERT INTO Unitati_Utilizatori (UN, DC, Rol) VALUES (%s, %s, %s)",
            (email, dc, ROL_CONTABIL),
        )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    undo.append((f"Unitati_Utilizatori {email}/{dc}", lambda: _delete_where(
        "DELETE FROM Unitati_Utilizatori WHERE UN = %s AND DC = %s", (email, dc))))
    say(f"Pas 7: {email} are rolul {ROL_CONTABIL} pe {dc}.")


# ---------------------------------------------------------------------------
# Step 8 -- the link
# ---------------------------------------------------------------------------
def _store_link(conn, id_cerere) -> str:
    """A new link token; only its hash is stored. The caller commits."""
    token = secrets.token_urlsafe(_LINK_TOKEN_NBYTES)
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE FX_Inregistrari SET ParolaHash = %s, "
        f"ParolaExpira = NOW() + INTERVAL {int(LINK_TTL_HOURS)} HOUR "
        "WHERE IdCerere = %s",
        (hash_link_token(token), id_cerere),
    )
    return token


def _send_link(email, denumire, token, say) -> bool:
    """True when the mail went out. A failure is logged and reported, never raised:
    the unit is built either way, and link_nou() can send another."""
    try:
        mailer.send_account_ready(email, denumire, password_link(token), LINK_TTL_HOURS)
        say(f"Linkul de parolă a fost trimis la {mailer.mask_address(email)}.")
        return True
    except Exception as err:
        logger.error("password link mail to %s failed: %s", mailer.mask_address(email), err)
        say(f"ATENȚIE: e-mailul cu linkul de parolă NU a plecat ({err}). "
            "Trimiteți un link nou după ce e-mailul funcționează.")
        return False


# ---------------------------------------------------------------------------
# Unwinding
# ---------------------------------------------------------------------------
def _unwind(undo, say) -> list:
    """Runs the registered undo steps, last first. Answers what could NOT be undone."""
    left = []
    for label, action in reversed(undo):
        try:
            action()
            say(f"Anulat: {label}.")
        except Exception as err:
            logger.error("undo step '%s' failed: %s", label, err)
            say(f"NU s-a putut anula: {label} ({err}).")
            left.append(f"{label} ({err})")
    return left


def _drop_database(dc):
    # The regex is the last guard: this job drops only a name of the shape it builds.
    if not _NEW_DB_NAME.match(dc):
        raise RuntimeError(f"refuz să șterg baza {dc!r}: nu are forma 1nn_SSSS")
    conn = get_kbot_provisioning_connection()
    try:
        conn.cursor(buffered=True).execute(f"DROP DATABASE IF EXISTS {_q(dc)}")
    finally:
        _close(conn)


def _drop_user(email):
    conn = get_kbot_provisioning_connection()
    try:
        conn.cursor(buffered=True).execute("DROP USER IF EXISTS %s@'%'", (email,))
    finally:
        _close(conn)


def _delete_where(sql, params):
    conn = get_kbot_provisioning_connection()
    try:
        conn.cursor(buffered=True).execute(sql, params)
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _close(conn)


def _mark_failed(id_cerere, message, decided_by):
    """
    `Esuata` + the reason, on a connection of its own (the run's may be the thing that
    broke). If even this fails the reason is still in the log and in the exception.
    """
    conn = None
    try:
        conn = get_kbot_provisioning_connection()
        conn.cursor(buffered=True).execute(
            "UPDATE FX_Inregistrari SET Stare = %s, Motiv = %s, DataDecizie = NOW(), "
            "Decis = %s, ParolaHash = NULL, ParolaExpira = NULL WHERE IdCerere = %s",
            (cerere_mod.STARE_ESUATA, message[:_MOTIV_MAX_CHARS], decided_by, id_cerere),
        )
        conn.commit()
    except Exception as err:
        logger.error("could not mark request %s as failed (%s); reason was: %s",
                     id_cerere, err, message)
    finally:
        if conn is not None:
            _close(conn)


# ---------------------------------------------------------------------------
# Reads and checks
# ---------------------------------------------------------------------------
def _load(conn, id_cerere) -> dict:
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute(
        "SELECT IdCerere, Email, CF, DenumireAnaf, Denumire, An, Payload, Stare, DbName "
        "FROM FX_Inregistrari WHERE IdCerere = %s",
        (int(id_cerere),),
    )
    row = cur.fetchone()
    if row is None:
        raise ProvizionareRefuzata(f"Cererea {id_cerere} nu există.")
    return row


def _require_approvable(row):
    if row["Stare"] not in _APPROVABLE:
        raise ProvizionareRefuzata(
            f"Cererea {row['IdCerere']} are starea {row['Stare']}; se pot aproba doar "
            f"cererile {' sau '.join(_APPROVABLE)}."
        )


def _check_template(conn):
    """
    The template must be something this job knows how to copy completely. Triggers,
    routines and events are NOT copied, so their presence stops the job rather than
    producing a unit that is quietly missing them.
    """
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'Clasificatii' AND COLUMN_NAME IN "
        "(%s, %s, %s)",
        (TEMPLATE_DB, *_TEMPLATE_LEFTOVERS),
    )
    leftovers = [str(r[0]) for r in cur.fetchall()]
    if leftovers:
        raise ProvizionareRefuzata(
            f"Șablonul {TEMPLATE_DB}.Clasificatii mai are coloanele de lucru "
            f"{', '.join(leftovers)} de la 0075-00. Ștergeți-le întâi (planul, 0.0)."
        )
    for what, sql in (
        ("declanșatori", "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = %s"),
        ("proceduri/funcții", "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = %s"),
        ("evenimente", "SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA = %s"),
    ):
        cur.execute(sql, (TEMPLATE_DB,))
        if int(cur.fetchone()[0]):
            raise ProvizionareRefuzata(
                f"Șablonul {TEMPLATE_DB} are {what}, pe care provizionarea nu le copiază. "
                "Baza nouă ar ieși incompletă."
            )


def _template_objects(conn):
    return _objects_of(conn, TEMPLATE_DB)


def _objects_of(conn, schema):
    """(tables, views) of one schema, names checked before they are ever quoted."""
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
        (schema,),
    )
    tables, views = [], []
    for name, kind in cur.fetchall():
        name = _text(name)
        if not _TABLE_NAME.match(name):
            raise RuntimeError(f"{schema} conține un obiect cu nume neobișnuit: {name!r}")
        (views if _text(kind) == "VIEW" else tables).append(name)
    return tables, views


def _schema_exists(conn, name) -> bool:
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (name,))
    return cur.fetchone() is not None


def _max_id_unitate(conn) -> int:
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT COALESCE(MAX(IdUnitate), 0) FROM CAI")
    return int(cur.fetchone()[0])


def _email_taken(conn, email) -> bool:
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT 1 FROM Unitati_Utilizatori WHERE UN = %s LIMIT 1", (email,))
    if cur.fetchone() is not None:
        return True
    return _account_exists(conn, email)


def _account_exists(conn, email) -> bool:
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT 1 FROM mysql.user WHERE User = %s LIMIT 1", (email,))
    return cur.fetchone() is not None


def _cf_taken(conn, cf) -> bool:
    """Same two tables and the same two spellings as `/anaf` and `/cerere` (D11)."""
    spellings = (cf, "RO" + cf)
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT 1 FROM CAI WHERE CF IN (%s, %s) LIMIT 1", spellings)
    if cur.fetchone() is not None:
        return True
    cur.execute("SELECT 1 FROM Unitati WHERE CF IN (%s, %s) LIMIT 1", spellings)
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _get_lock(conn, name, wait) -> bool:
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT GET_LOCK(%s, %s)", (name, int(wait)))
    return cur.fetchone()[0] == 1


def _release_lock(conn, name):
    """Released explicitly; a lock left behind would also go with the connection."""
    try:
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
        cur.fetchall()
    except Exception as err:
        logger.warning("RELEASE_LOCK(%s) failed, the lock goes with the connection: %s",
                       name, err)


def _q(name) -> str:
    """Backtick-quotes an identifier that has already been checked by a regex."""
    if "`" in name:
        raise ValueError(f"identifier with a backtick: {name!r}")
    return f"`{name}`"


def _sayer(progress):
    def say(line):
        logger.info("provizionare: %s", line)
        if callable(progress):
            progress(line)
    return say


def _safe_rollback(conn):
    try:
        conn.rollback()
    except Exception as err:
        logger.warning("rollback failed: %s", err)


def _close(conn):
    try:
        if conn.is_connected():
            conn.close()
    except Exception as err:
        logger.warning("closing a provisioning connection failed: %s", err)


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    return "" if value is None else str(value)
