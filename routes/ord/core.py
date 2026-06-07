# routes/ord/core.py
"""
Endpoint-uri principale ORD (Sectiunea 10 din monolit, fara patch).

  - save_staging  : staging ADD
  - update_staging: staging MOD
  - confirm       : commit ADD/MOD sau anulare (FAIL)
  - cleanup_staging: expira tokenuri PENDING vechi
  - delete        : sterge un ORD complet (cascade FK)

Conexiune + retry: refolosite din utils.db_retry (_run_with_retry).
Validare/staging/commit: importate din modulele pachetului.
"""
import uuid

from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry
from utils.parsing import _strict_pos_int, _strict_str_nonempty

from . import ord_bp, logger
from .validation import _check_content_length, _validate_payload
from .staging import _stg_insert_all
from .commit import _commit_add, _commit_mod, _cleanup_stg_children


@ord_bp.route("/api/ord/save_staging", methods=["POST"])
@require_api_key
def save_staging():
    """
    Staging ADD (ordonantare noua).

    Validari:
      ord.IDORDP < 0 (rand nou — nu exista inca in DB)
      ord.IDORD  > 0 (pre-calculat de VBA)
      toate ...P < 0 (toti copiii sunt noi)
      ord.IDRR   nullable (NULL = flux 2)
      ord.IDRH   nullable (NULL = flux 2, v6)

    Returneaza: {"token": "<uuid>"}
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "ADD")

        token = str(uuid.uuid4())
        logger.info(
            f"[save_staging] ADD token={token} "
            f"IDORD={data['ord'].get('IDORD')} "
            f"IDRR={data['ord'].get('IDRR')} "
            f"IDRH={data['ord'].get('IDRH')}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "ADD", data)
            return {"token": token}

        return jsonify(_run_with_retry(operation, data)), 200

    except ValueError as e:
        logger.warning(f"[save_staging] Validare eronata: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[save_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/update_staging", methods=["POST"])
@require_api_key
def update_staging():
    """
    Staging MOD (ordonantare existenta).

    Validari:
      ord.IDORDP > 0 (ORD existent in DB)
      ord.IDORD  > 0
      ...P < 0 = rand nou in MOD, ...P > 0 = rand existent
      ord.IDRR   nullable
      ord.IDRH   nullable (v6)

    Returneaza: {"token": "<uuid>"}
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "MOD")

        token = str(uuid.uuid4())
        logger.info(
            f"[update_staging] MOD token={token} "
            f"IDORDP={data['ord'].get('IDORDP')} "
            f"IDRR={data['ord'].get('IDRR')} "
            f"IDRH={data['ord'].get('IDRH')}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "MOD", data)
            return {"token": token}

        return jsonify(_run_with_retry(operation, data)), 200

    except ValueError as e:
        logger.warning(f"[update_staging] Validare eronata: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[update_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/confirm", methods=["POST"])
@require_api_key
def confirm():
    """
    Confirma sau anuleaza o operatie staged.

    Payload: {db_name, token, status: "OK" | "FAIL"}

    status="OK":
      - Verifica Status=PENDING (dublu submit = eroare, nu re-executa).
      - Executa _commit_add sau _commit_mod in functie de TipOperatie.
      - Seteaza Status=CONFIRMED + DataConfirm.
      - Sterge copiii din staging (stg_Ord ramane pentru audit).
      - Returneaza {IDORDP, Part_Map, TBL_Map, ATT_Map, DOC_Map, REC_Map}.

    status="FAIL":
      - Seteaza Status=FAIL pe stg_Ord.
      - Sterge copiii din staging.
      - Returneaza {ok: True}.
    """
    data  = request.json
    token = None
    try:
        token  = _strict_str_nonempty(data.get("token"),  "token")
        status = _strict_str_nonempty(data.get("status"), "status")

        if status not in ("OK", "FAIL"):
            return jsonify(
                {"error": f"status='{status}' invalid — acceptat: OK | FAIL"}
            ), 400

        if status == "FAIL":
            def operation_fail(cursor):
                cursor.execute("""
                    UPDATE stg_Ord
                    SET Status='FAIL', DataConfirm=NOW()
                    WHERE Token=%s AND Status='PENDING'
                """, (token,))
                if cursor.rowcount > 0:
                    _cleanup_stg_children(cursor, token)
                else:
                    # Token deja procesat (CONFIRMED/FAIL) sau inexistent
                    logger.warning(
                        f"[confirm] FAIL pe token={token} care nu mai e PENDING "
                        f"(rowcount=0) — ignorat"
                    )
                return {"ok": True}

            result = _run_with_retry(operation_fail, data)
            logger.info(f"[confirm] FAIL token={token}")
            return jsonify(result), 200

        # status == OK
        def operation_ok(cursor):
            cursor.execute(
                "SELECT TipOperatie, Status FROM stg_Ord "
                "WHERE Token=%s FOR UPDATE",
                (token,),
            )
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Token necunoscut: {token}")

            if row["Status"] != "PENDING":
                raise ValueError(
                    f"Token {token} are Status={row['Status']} (nu PENDING). "
                    "Dublu submit detectat — operatia nu va fi reexecutata."
                )

            tip = row["TipOperatie"]
            logger.info(f"[confirm] token={token} TipOperatie={tip}")

            if tip == "ADD":
                result = _commit_add(cursor, token)
            elif tip == "MOD":
                result = _commit_mod(cursor, token)
            else:
                raise ValueError(
                    f"TipOperatie necunoscut in stg_Ord: '{tip}'"
                )

            cursor.execute(
                "UPDATE stg_Ord SET Status='CONFIRMED', DataConfirm=NOW() "
                "WHERE Token=%s",
                (token,),
            )
            _cleanup_stg_children(cursor, token)
            return result

        result = _run_with_retry(operation_ok, data)
        logger.info(f"[confirm] DONE token={token} IDORDP={result['IDORDP']}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[confirm] ERROR token={token}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/cleanup_staging", methods=["POST"])
@require_api_key
def cleanup_staging():
    """
    Sterge tokenuri PENDING mai vechi de 60 minute (si copiii lor).
    Apelat de VBA la inceputul fiecarui flux de salvare.
    Returneaza {"deleted": N} — numarul de tokenuri expirate sterse.
    """
    data = request.json
    try:
        def operation(cursor):
            cursor.execute("""
                SELECT Token FROM stg_Ord
                WHERE Status = 'PENDING'
                  AND DataInsert < DATE_SUB(NOW(), INTERVAL 60 MINUTE)
            """)
            tokens = [r["Token"] for r in cursor.fetchall()]
            if tokens:
                ph = ",".join(["%s"] * len(tokens))
                # Sterge copiii mai intai (explicit, nu numai prin CASCADE)
                for tabel in ("stg_OrdPart", "stg_OrdTbl",
                              "stg_OrdAtt",  "stg_OrdDoc", "stg_OrdTblRec"):
                    cursor.execute(
                        f"DELETE FROM {tabel} WHERE Token IN ({ph})", tokens
                    )
                cursor.execute(
                    f"DELETE FROM stg_Ord WHERE Token IN ({ph})", tokens
                )
            return {"deleted": len(tokens)}

        result = _run_with_retry(operation, data)
        logger.info(f"[cleanup_staging] deleted={result['deleted']}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[cleanup_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/delete", methods=["POST"])
@require_api_key
def delete_ord():
    """
    Sterge un ORD complet din DB.
    Cascade FK MariaDB sterge automat: PART → TBL / ATT / DOC.
    Payload:  {db_name, IDORDP}
    Response: {ok: True, IDORDP: N}

    NOTA: ORD NU modifica FX_Receptii_Plati la stergere —
    IDRP din TBL este read-only, RecPl ramane intact.
    """
    data = request.json

    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")

        def operation(cursor):
            cursor.execute(
                "SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE",
                (idordp,)
            )

            if not cursor.fetchone():
                raise LookupError(f"FX_ORD cu IDORDP={idordp} nu exista")

            cursor.execute(
                "DELETE FROM FX_ORD WHERE IDORDP=%s",
                (idordp,)
            )

            return {"ok": True, "IDORDP": idordp}

        result = _run_with_retry(operation, data)
        return jsonify(result), 200

    except LookupError as e:
        return jsonify({"error": str(e)}), 404

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        logger.error(f"[delete] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500