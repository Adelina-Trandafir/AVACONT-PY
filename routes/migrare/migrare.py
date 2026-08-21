# routes/migrare/migrare.py
# -----------------------------------------------------------------------------
# The routes KBot.Migrator drives, in the order the operator meets them:
#
#   GET  /api/migrare/baze          -> bazele de unitate de pe MariaDB
#   GET  /api/migrare/fisiere       -> ce fisiere Access sunt deja pe server
#   POST /api/migrare/push/init     -> deschide o incarcare in bucati
#   POST /api/migrare/push/bucata   -> o bucata (multipart), cu amprenta
#   POST /api/migrare/push/final    -> lipeste, verifica amprenta, muta in loc
#   POST /api/migrare/analiza       -> porneste analiza; intoarce un id de lucrare
#   POST /api/migrare/rulare        -> porneste scrierea; cere id-ul analizei
#   GET  /api/migrare/stare/<id>    -> starea unei lucrari + jurnalul ei
#
# Garda e X-Api-Key, ca pe rutele de seed pe care le inlocuieste. Migratorul este
# un utilitar de administrare, nu aplicatia operatorului: nu are token bearer.
# -----------------------------------------------------------------------------

import json
import logging
import re

from flask import Blueprint, Response, request

from utils.database import get_db_connection
from utils.security import require_api_key

from . import accdb, execute, jobs, routing, storage, tables, validate

migrare_bp = Blueprint("migrare", __name__)
logger = logging.getLogger(__name__)

# Numele unei baze de unitate. Aceeasi forma ca in routes/forexe/seed.py.
_DBNAME_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9]+$")

# Bazele de serviciu, care nu sunt baze de unitate si nu se ofera ca tinta.
_NOT_UNIT_DBS = {"information_schema", "mysql", "performance_schema", "sys",
                 "AVACONT_COMUN", "AVACONT_SURSA"}


def _json(payload, status=200):
    return Response(json.dumps(payload, ensure_ascii=False), status=status,
                    mimetype="application/json; charset=utf-8")


def _err(message, status=400):
    return _json({"ok": False, "error": message}, status)


def _tabele_cerute(body):
    """
    Tabelele bifate de operator, sau None pentru toate.

    O lista goala NU inseamna „toate": inseamna ca nu s-a bifat nimic, si atunci
    nu e nimic de facut. Un nume strain opreste cu exceptie, niciodata tacut.
    Ordinea trimisa e ORDINEA DE SCRIERE.
    """
    names = body.get("tabele")
    if names is None:
        return None
    if not isinstance(names, list):
        raise ValueError("Lista de tabele trimisă nu este o listă.")
    if not names:
        raise ValueError("Nu e bifat niciun tabel, deci nu e nimic de făcut.")
    chosen = []
    for name in names:
        name = str(name)
        try:
            tables.by_name(name)
        except KeyError:
            raise ValueError("Tabelul «%s» nu face parte din setul migrat." % name)
        chosen.append(name)
    return chosen


def _coloane_cerute(body):
    """
    Coloanele alese de operator: {tabel: [coloane]}, sau None pentru «toate».

    Un tabel din afara setului migrat opreste cu exceptie. O lista goala pentru
    un tabel e refuzata la fel: un tabel fara nicio coloana nu se poate scrie,
    iar cheile primare se adauga oricum pe server — deci lista goala nu poate fi
    ce a vrut operatorul.
    """
    chosen = body.get("coloane")
    if chosen is None:
        return None
    if not isinstance(chosen, dict):
        raise ValueError("Câmpul «coloane» trimis nu este un dicționar.")
    out = {}
    for name, cols in chosen.items():
        tables.by_name(str(name))
        if not isinstance(cols, list) or not cols:
            raise ValueError(
                "Tabelul «%s» a venit fără nicio coloană aleasă." % name)
        out[str(name)] = [str(c) for c in cols]
    return out


def _corelatii_cerute(body):
    """
    Corelatiile de coloane ale operatorului: {tabel: {coloana_access:
    coloana_tinta}}, sau None daca n-a trimis niciuna (atunci se aplica
    corelatia implicita — vezi validate.column_rename_map).

    O tinta vida inseamna «coloana asta nu calatoreste». Doua coloane din Access
    corelate cu ACEEASI coloana de pe MariaDB se refuza aici, nu la scriere: una
    dintre cele doua valori s-ar pierde, si nu se poate spune care.
    """
    chosen = body.get("corelatii") or body.get("corelatii")
    if chosen is None:
        return None
    if not isinstance(chosen, dict):
        raise ValueError("Câmpul «corelatii» trimis nu este un dicționar.")
    out = {}
    for name, pairs in chosen.items():
        tables.by_name(str(name))
        if not isinstance(pairs, dict):
            raise ValueError(
                "Corelațiile tabelului «%s» nu sunt un dicționar." % name)
        table_map = {}
        taken = {}
        for access_name, target_name in pairs.items():
            target = "" if target_name is None else str(target_name)
            if target:
                previous = taken.get(target.lower())
                if previous is not None:
                    raise ValueError(
                        "În «%s», coloanele «%s» și «%s» sunt corelate amândouă "
                        "cu «%s» de pe MariaDB. O coloană a țintei poate primi o "
                        "singură coloană din Access."
                        % (name, previous, access_name, target))
                taken[target.lower()] = str(access_name)
            table_map[str(access_name)] = target
        out[str(name)] = table_map
    return out


def _fail(where, exc):
    """Nicio exceptie nu se pierde: jurnal cu urma completa, mesaj in romana afara."""
    logger.exception("migrare/%s a eșuat", where)
    return _err(str(exc), 500)


# -----------------------------------------------------------------------------
# 1. Bazele de pe MariaDB
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/baze", methods=["GET"])
@require_api_key
def baze():
    """
    Bazele de unitate existente pe server, cu numarul de tabele FX_ pe care le au
    deja. Numaratoarea e acolo ca operatorul sa vada dintr-o privire daca schema
    e instalata -- migrarea NU creeaza tabele.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SHOW DATABASES")
        names = []
        for (name,) in cur.fetchall():
            text = name if isinstance(name, str) else name.decode("utf-8", "replace")
            if text in _NOT_UNIT_DBS or not _DBNAME_RE.match(text):
                continue
            names.append(text)

        wanted = [t.name for t in tables.ALL]
        out = []
        if names:
            placeholders = ",".join(["%s"] * len(names))
            table_slots = ",".join(["%s"] * len(wanted))
            cur.execute(
                "SELECT TABLE_SCHEMA, COUNT(*) FROM information_schema.TABLES "
                " WHERE TABLE_SCHEMA IN (" + placeholders + ") "
                "   AND TABLE_NAME IN (" + table_slots + ") "
                " GROUP BY TABLE_SCHEMA",
                tuple(names + wanted))
            counts = dict((k if isinstance(k, str) else k.decode("utf-8", "replace"), int(v))
                          for k, v in cur.fetchall())
            for name in sorted(names):
                out.append({
                    "nume": name,
                    "tabele_fx": counts.get(name, 0),
                    "complet": counts.get(name, 0) == len(wanted),
                })
        cur.close()
        return _json({"ok": True, "baze": out, "tabele_asteptate": len(wanted)})

    except Exception as exc:
        return _fail("baze", exc)
    finally:
        if conn is not None:
            conn.close()


# -----------------------------------------------------------------------------
# 2. Fisierele Access de pe server
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/fisiere", methods=["GET"])
@require_api_key
def fisiere():
    try:
        return _json({"ok": True, "fisiere": storage.list_pushed()})
    except storage.StorageError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("fisiere", exc)


# -----------------------------------------------------------------------------
# 3. Impingerea fisierului, in bucati
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/push/init", methods=["POST"])
@require_api_key
def push_init():
    body = request.get_json(silent=True) or {}
    try:
        upload_id, name = storage.begin_upload(
            body.get("fel"), body.get("an"), body.get("dc"),
            body.get("octeti") or body.get("total_size"),
            body.get("sha256"))
        return _json({"ok": True, "id": upload_id, "nume": name,
                      "bucata_maxima": storage.MAX_CHUNK_BYTES})
    except storage.StorageError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("push/init", exc)


@migrare_bp.route("/api/migrare/push/bucata", methods=["POST"])
@require_api_key
def push_bucata():
    try:
        upload_id = (request.form.get("id") or "").strip()
        index = request.form.get("index")
        sha = request.form.get("sha256")
        # „file" e numele adevarat, ASCII, ca pe toate celelalte rute de incarcare:
        # numele campului merge intr-un antet, iar un client care scrie antetele in
        # Latin-1 (HttpClient) stalceste orice diacritica. „fisier" ramane acceptat
        # pentru clientii care il trimit corect.
        if "file" in request.files:
            payload = request.files["file"].read()
        elif "fisier" in request.files:
            payload = request.files["fisier"].read()
        else:
            # Mesajul spune si CE a venit: daca numele a ajuns stalcit, se vede aici.
            primite = ", ".join("«%s»" % k for k in request.files.keys()) or "niciun fișier"
            return _err(
                "Bucata de fișier lipsește din cerere. Se aștepta câmpul «file»; "
                "au venit: %s." % primite, 400)

        storage.store_chunk(upload_id, index, payload, sha)
        return _json({"ok": True})
    except storage.StorageError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("push/bucata", exc)


@migrare_bp.route("/api/migrare/push/final", methods=["POST"])
@require_api_key
def push_final():
    body = request.get_json(silent=True) or {}
    try:
        info = storage.finish_upload(body.get("id"), body.get("bucati") or body.get("total_chunks"))
        return _json({"ok": True, "fisier": info})
    except storage.StorageError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("push/final", exc)


# -----------------------------------------------------------------------------
# 4. Inventarul -- ce tabele are fisierul si cate randuri fiecare
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/tabele", methods=["POST"])
@require_api_key
def tabele_fisier():
    """
    Lista tabelelor migrate, cu numarul de randuri din fisier si cu unitatea aleasa
    deja rezolvata. Migratorul o arata cu bife: un tabel FARA randuri se ofera
    NEBIFAT, ca operatorul sa nu porneasca scrierea pentru nimic.

    Numararea nu interpreteaza randurile (accdb.count_rows), deci e cu mult mai
    ieftina decat analiza. Cate dintre randuri sunt ale unitatii alese se afla
    abia la analiza -- pentru asta chiar trebuie citit fiecare rand.
    """
    body = request.get_json(silent=True) or {}
    db_name = body.get("baza")
    an = body.get("an")
    dc = body.get("dc") or db_name

    if not db_name or not _DBNAME_RE.match(db_name):
        return _err("Numele bazei de unitate este invalid: «%s»." % (db_name or ""), 400)

    try:
        accdb.ensure_tools()
        fx_path = storage.pushed_path(storage.fx_file_name(an, dc))
    except (storage.StorageError, accdb.AccdbError) as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("tabele/pregătire", exc)

    def work(job):
        job.say("Se inventariază «%s»." % fx_path)
        present = accdb.list_tables(fx_path)

        plan = routing.build_plan(fx_path, db_name, progress=job.say)
        job.plan = plan

        afara = [t for t in tables.OUT_OF_SCOPE if t in present]
        if afara:
            job.say("ATENȚIE: tabele în afara domeniului există în fișier și NU se "
                    "migrează: %s." % ", ".join(afara))

        # Schema tintei, ca lista de coloane sa spuna si care exista pe MariaDB
        # si care e cheia primara -- din ele isi face migratorul bifele.
        conn = get_db_connection(db_name)
        try:
            schema = validate.TargetSchema(conn, db_name,
                                           [t.name for t in tables.ALL])
        finally:
            conn.close()

        out = []
        for table in tables.ALL:
            if table.name not in present:
                job.say("«%s» lipsește din fișier." % table.name)
                out.append({"nume": table.name, "exista": False, "randuri": 0,
                            "coloane": [],
                            "coloane_tinta": sorted(
                                schema.columns.get(table.name, {}))})
                continue
            rows = accdb.count_rows(fx_path, table.name)
            job.say("«%s»: %d rânduri în fișier." % (table.name, rows))

            # Potrivirea numelor e FARA litere mari/mici: Access e
            # case-insensitive («Cual» si «CUAL» sunt aceeasi coloana acolo).
            target_names = list(schema.columns.get(table.name, {}))
            target_lower = set(n.lower() for n in target_names)
            pk_lower = set(p.lower() for p in
                           (schema.primary_key.get(table.name) or [table.primary_key]))
            access_names = [c["nume"] for c in accdb.columns(fx_path, table.name)]
            # Corelatia propusa: unu-la-unu dupa nume, cu exceptiile din
            # tables.COLUMN_RENAMES (perechea IdClsf / IdClsfPY). Migratorul o
            # arata in «Corelatii coloane» si o poate schimba rand cu rand.
            proposed = tables.default_correlations(access_names, target_names)
            cols = [{"nume": name,
                     "in_baza": name.lower() in target_lower,
                     "cheie": name.lower() in pk_lower,
                     "tinta": proposed.get(name) or ""}
                    for name in access_names]
            out.append({"nume": table.name, "exista": True, "randuri": rows,
                        "coloane": cols,
                        "coloane_tinta": sorted(target_names)})

        return {"baza": db_name, "unitati": plan.units,
                "toate_unitatile": plan.all_units, "tabele": out}

    job = jobs.start("inventar", work)
    return _json({"ok": True, "lucrare": job.id})


# -----------------------------------------------------------------------------
# 5. Analiza -- citeste, selecteaza, masoara. Nu scrie nimic.
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/analiza", methods=["POST"])
@require_api_key
def analiza():
    body = request.get_json(silent=True) or {}
    db_name = body.get("baza")
    an = body.get("an")
    dc = body.get("dc") or db_name

    if not db_name or not _DBNAME_RE.match(db_name):
        return _err("Numele bazei de unitate este invalid: «%s»." % (db_name or ""), 400)

    try:
        alese = _tabele_cerute(body)
        coloane = _coloane_cerute(body)
        corelatii = _corelatii_cerute(body)
    except ValueError as exc:
        return _err(str(exc), 400)
    except KeyError as exc:
        return _err(str(exc.args[0] if exc.args else exc), 400)

    try:
        accdb.ensure_tools()
        fx_path = storage.pushed_path(storage.fx_file_name(an, dc))
    except (storage.StorageError, accdb.AccdbError) as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _fail("analiza/pregătire", exc)

    def work(job):
        conn = None
        try:
            job.say("Se citește «%s»." % fx_path)
            present = accdb.list_tables(fx_path)
            lipsa = [t.name for t in tables.selected(alese) if t.name not in present]
            if lipsa:
                raise validate.ValidationError(
                    "Fișierul Access nu conține tabelele: %s." % ", ".join(lipsa))
            afara = [t for t in tables.OUT_OF_SCOPE if t in present]
            if afara:
                job.say("ATENȚIE: tabele în afara domeniului există în fișier și NU se "
                        "migrează: %s." % ", ".join(afara))

            # Fisierul poate purta mai multe unitati; se scrie DOAR unitatea
            # bazei alese. Cine e unitatea aia se afla din fisierul insusi
            # (FX_Angajamente poarta si IdUnitate, si DC), nu dintr-un fisier de
            # rutare pe langa.
            plan = routing.build_plan(fx_path, db_name, progress=job.say)
            job.plan = plan

            conn = get_db_connection(db_name)
            report = validate.analyze(conn, db_name, fx_path, plan, only=alese,
                                      columns=coloane, mappings=corelatii,
                                      progress=job.say)
            job.report = report

            data = report.to_dict()
            job.say("Analiză încheiată: %s." %
                    ("nicio problemă" if data["curat"] else
                     "%d feluri de constatări" % len(data["pe_fel"])))
            return data
        finally:
            if conn is not None:
                conn.close()

    job = jobs.start("analiza", work)
    return _json({"ok": True, "lucrare": job.id})


# -----------------------------------------------------------------------------
# 6. Rularea -- scrie. Cere id-ul analizei care a aprobat-o.
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/rulare", methods=["POST"])
@require_api_key
def rulare():
    body = request.get_json(silent=True) or {}
    analiza_id = body.get("analiza") or body.get("analiza")
    force = bool(body.get("fortat") or body.get("force"))
    replace = bool(body.get("inlocuieste") or body.get("inlocuieste"))

    try:
        alese = _tabele_cerute(body)
    except ValueError as exc:
        return _err(str(exc), 400)

    sursa = jobs.get(analiza_id) if analiza_id else None
    if sursa is None or sursa.report is None:
        return _err(
            "Analiza indicată nu mai există pe server. Rulați din nou analiza "
            "înainte de scriere.", 400)
    if sursa.state != jobs.GATA:
        return _err("Analiza indicată nu s-a încheiat cu bine.", 400)

    report = sursa.report
    db_name = report.db_name
    an = body.get("an")
    dc = body.get("dc") or db_name

    if report.has_blocking():
        return _err(
            "Analiza a găsit probleme blocante (tip, dimensiune, coloană sau tabel "
            "lipsă). Nici «Forțează rularea» nu trece peste ele.", 409)
    if not force and not report.is_clean():
        return _err(
            "Analiza a găsit probleme de integritate. Folosiți «Forțează rularea» "
            "dacă acceptați ca rândurile vinovate să fie sărite.", 409)

    # Planul unitatii vine de la analiza, nu se rezolva din nou: altfel selectia
    # s-ar putea schimba intre masurare si scriere.
    plan = sursa.plan
    if plan is None:
        return _err(
            "Analiza indicată nu a lăsat un plan al unității. Rulați din nou analiza "
            "înainte de scriere.", 400)
    if alese is None:
        alese = list(report.tables) or None

    try:
        fx_path = storage.pushed_path(storage.fx_file_name(an, dc))
    except storage.StorageError as exc:
        return _err(str(exc), 400)

    def work(job):
        conn = None
        try:
            job.say(plan.describe())
            conn = get_db_connection(db_name)
            totals = execute.run(conn, db_name, fx_path, plan, report, force,
                                 only=alese, replace=replace, progress=job.say)
            scrise = sum(s["scrise"] for s in totals.values())
            actualizate = sum(s["actualizate"] for s in totals.values())
            sarite = sum(s["sarite"] for s in totals.values())
            job.say("Scriere încheiată: %d rânduri scrise, %d actualizate, %d sărite."
                    % (scrise, actualizate, sarite))
            return {"baza": db_name, "fortat": force, "inlocuit": replace,
                    "pe_tabel": totals, "scrise": scrise,
                    "actualizate": actualizate, "sarite": sarite}
        finally:
            if conn is not None:
                conn.close()

    job = jobs.start("scriere", work)
    return _json({"ok": True, "lucrare": job.id})


# -----------------------------------------------------------------------------
# 7. Starea unei lucrari
# -----------------------------------------------------------------------------

@migrare_bp.route("/api/migrare/stare/<job_id>", methods=["GET"])
@require_api_key
def stare(job_id):
    job = jobs.get(job_id)
    if job is None:
        return _err("Lucrarea «%s» nu există sau a expirat." % job_id, 404)
    try:
        since = int(request.args.get("de_la", 0))
    except (TypeError, ValueError):
        since = 0
    return _json({"ok": True, "lucrare": job.snapshot(since)})
