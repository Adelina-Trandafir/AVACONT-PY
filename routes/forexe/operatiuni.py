# routes/forexe/operatiuni.py
"""
The «Operatiuni necorectate» of the FOREXE landing page (slice 0084).

    POST /api/forexe/operatiuni/necorectate
        { "operatiuni": [ { "program": "0000000000",
                            "ssi": "02A-65.04.01.20.01.03",
                            "referinta_trezor": "TZ521102457328",
                            "nr_doc": "964887316/280",
                            "data_plata": "2026-09-24",
                            "tip": "Incasare",          (as the page wrote it)
                            "suma": -368.00,
                            "probleme": null | "..." }, ... ] }
        -> 200 { "primite", "inserate", "existente", "avertismente": [...] }

K-BOT reads the table right after every FOREXE login and sends every row it saw. Only rows
that are NOT in FX_Operatiuni yet are inserted; the key is (ReferintaTrezor, NrDoc) --
operator's choice, 26.09.2026: "only the new ones", nothing is deleted or updated.

MAPPING (MariaDB_Schema/AVACONT_SURSA.sql, FX_Operatiuni)
  * Program         <- program; FK to AVACONT_COMUN.DefaProgram(Program), checked first so an
                       unknown program is a warning, not a 500.
  * CodSSI          <- SS + ClsfSal ("02A" + "650401200103"), the Access CodSSI shape (see the
                       ddf_edit.py docstring: CONCAT(SS, ClsfSal)).
  * IdClsf          <- Clasificatii.IDClsf: the unit comes from SS through Unitati.SursaSector
                       (as extrase.py does), then ClsfSal inside that unit. NOT NULL in the
                       table, so a row whose classification does not resolve is skipped with
                       a warning.
  * ReferintaTrezor, NrDoc, DataPlata, Tip, Probleme, Suma <- as sent.
  * Suma            -- the column is added by the operator (26.09.2026: "Adaug eu Suma"); the
                       live schema dump of 22.09 does not have it yet.

NEXT (not here): correlating these operations with the angajamente in the database.
"""
import json
import logging
import re
from datetime import date

from flask import g, current_app, request

from routes.auth.guard import require_session
from utils.database import COMMON_DB, get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# FX_Operatiuni.ReferintaTrezor is varchar(15).
_MAX_REFERINTA = 15

_SSI_RE = re.compile(r"^([0-9]{2}[A-Z])-([0-9.]+)$")

_SQL_EXISTA = (
    "SELECT 1 FROM FX_Operatiuni WHERE ReferintaTrezor = %s AND NrDoc = %s LIMIT 1"
)
_SQL_PROGRAM = f"SELECT 1 FROM {COMMON_DB}.DefaProgram WHERE Program = %s LIMIT 1"
_SQL_UNITATE = "SELECT IdUnitate FROM Unitati WHERE SursaSector = %s"
_SQL_CLSF = "SELECT IDClsf FROM Clasificatii WHERE IdUnitate = %s AND ClsfSal = %s"
_SQL_INSERT = (
    "INSERT INTO FX_Operatiuni "
    "(Program, IdClsf, CodSSI, ReferintaTrezor, NrDoc, DataPlata, Tip, Suma, Probleme) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def split_ssi(ssi: str):
    """
    "02A-65.04.01.20.01.03" -> ("02A", "650401200103"), or None when the text is not in
    that shape. The ClsfSal is the classification without dots, which is exactly how the
    generated Clasificatii.ClsfSal column is built.
    """
    m = _SSI_RE.match((ssi or "").replace(" ", "").strip())
    if m is None:
        return None
    return m.group(1), m.group(2).replace(".", "")


def _text(rand: dict, cheie: str) -> str:
    return str(rand.get(cheie) or "").strip()


def _descrie(rand: dict) -> str:
    return f"{_text(rand, 'referinta_trezor')} / nr. {_text(rand, 'nr_doc')}"


def _unitate(cursor, cache: dict, ss: str):
    if ss not in cache:
        cursor.execute(_SQL_UNITATE, (ss,))
        rows = cursor.fetchall()
        cache[ss] = int(rows[0]["IdUnitate"]) if len(rows) == 1 else (None if not rows else -len(rows))
    return cache[ss]


def _program_exista(cursor, cache: dict, program: str) -> bool:
    if program not in cache:
        cursor.execute(_SQL_PROGRAM, (program,))
        cache[program] = cursor.fetchone() is not None
    return cache[program]


def _id_clsf(cursor, id_unitate: int, clsf_sal: str):
    cursor.execute(_SQL_CLSF, (id_unitate, clsf_sal))
    return [int(r["IDClsf"]) for r in cursor.fetchall()]


def _prelucreaza(cursor, operatiuni: list) -> dict:
    inserate = 0
    existente = 0
    avertismente = []
    unitati = {}
    programe = {}

    for rand in operatiuni:
        if not isinstance(rand, dict):
            avertismente.append("Un rând primit nu are forma așteptată; a fost sărit.")
            continue
        referinta = _text(rand, "referinta_trezor")
        nr_doc = _text(rand, "nr_doc")
        if not referinta:
            avertismente.append("Un rând fără «Referință TREZOR» a fost sărit.")
            continue
        if len(referinta) > _MAX_REFERINTA:
            avertismente.append(f"«{referinta}»: Referința TREZOR are peste {_MAX_REFERINTA} "
                                "caractere; rândul a fost sărit.")
            continue

        cursor.execute(_SQL_EXISTA, (referinta, nr_doc))
        if cursor.fetchone() is not None:
            existente += 1
            continue

        program = _text(rand, "program")
        if not _program_exista(cursor, programe, program):
            avertismente.append(f"{_descrie(rand)}: programul «{program}» nu există în "
                                "nomenclatorul de programe; rândul nu a fost salvat.")
            continue

        ssi = _text(rand, "ssi")
        parti = split_ssi(ssi)
        if parti is None:
            avertismente.append(f"{_descrie(rand)}: «Sector - Sursa - Indicator» «{ssi}» nu are "
                                "forma așteptată (02A-65.04.01.20.01.03); rândul nu a fost salvat.")
            continue
        ss, clsf_sal = parti

        id_unitate = _unitate(cursor, unitati, ss)
        if id_unitate is None or id_unitate < 0:
            motiv = "nu corespunde niciunei unități" if id_unitate is None \
                else "corespunde mai multor unități"
            avertismente.append(f"{_descrie(rand)}: sursa-sectorul «{ss}» {motiv} din baza "
                                "curentă; rândul nu a fost salvat.")
            continue

        ids = _id_clsf(cursor, id_unitate, clsf_sal)
        if len(ids) != 1:
            motiv = "nu există" if not ids else "apare de mai multe ori"
            avertismente.append(f"{_descrie(rand)}: clasificația «{ssi}» {motiv} la unitatea "
                                f"{id_unitate}; rândul nu a fost salvat.")
            continue

        data_text = _text(rand, "data_plata")
        try:
            data_plata = date.fromisoformat(data_text)
        except ValueError:
            avertismente.append(f"{_descrie(rand)}: data plății «{data_text}» nu este o dată; "
                                "rândul nu a fost salvat.")
            continue

        suma = rand.get("suma")
        if suma is not None and not isinstance(suma, (int, float)):
            avertismente.append(f"{_descrie(rand)}: suma «{suma}» nu este un număr; "
                                "rândul nu a fost salvat.")
            continue

        probleme = _text(rand, "probleme") or None
        cursor.execute(_SQL_INSERT, (program, ids[0], ss + clsf_sal, referinta, nr_doc,
                                     data_plata, _text(rand, "tip"), suma,
                                     probleme[:255] if probleme else None))
        inserate += 1

    return {"primite": len(operatiuni), "inserate": inserate, "existente": existente,
            "avertismente": avertismente}


@forexe_bp.route("/api/forexe/operatiuni/necorectate", methods=["POST"])
@require_session
def post_operatiuni_necorectate():
    """Save the uncorrected operations the FOREXE landing page showed; only new ones."""
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict) or not isinstance(sarcina.get("operatiuni"), list):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid (se așteaptă «operatiuni»)."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()
        rezultat = _prelucreaza(cursor, sarcina["operatiuni"])
        conn.commit()
        logger.info("[forexe.operatiuni] %s: primite %s, inserate %s, existente %s, avertismente %s",
                    db_name, rezultat["primite"], rezultat["inserate"], rezultat["existente"],
                    len(rezultat["avertismente"]))
        return _json_utf8(rezultat, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.operatiuni] rollback esuat", exc_info=True)
        logger.error("[forexe.operatiuni] %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea operațiunilor necorectate: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
