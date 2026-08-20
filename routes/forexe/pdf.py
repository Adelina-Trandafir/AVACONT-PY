# routes/forexe/pdf.py
"""
Stocarea pe server a PDF-urilor SEMNATE (felia 0041).

Rute:
    GET  /api/forexe/ddf/pdf/<idrev>    -> octetii PDF-ului semnat al reviziei
    PUT  /api/forexe/ddf/pdf/<idrev>    -> inlocuieste-sau-insereaza randul
    GET  /api/forexe/ord/pdf/<idordp>   -> octetii PDF-ului semnat al ordonantarii
    PUT  /api/forexe/ord/pdf/<idordp>   -> inlocuieste-sau-insereaza randul

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista parametru
db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact ca la toate
celelalte rute /api/forexe/*. Un token nu poate tinti alta baza decat cea pe care s-a logat.

CE SE STOCHEAZA: DOAR PDF-uri SEMNATE. Un PDF nesemnat e un artefact DERIVAT — clientul il
regenereaza prin XfaWriter ori de cate ori operatorul cere sa-l vada — si NU se incarca
niciodata aici. Existenta randului INSEAMNA «exista PDF semnat»; nu exista coloana `Semnat`.
Fara istoric: cheia unica pe IDREV / IDORDP face ca o re-semnare sa INLOCUIASCA randul.

CELE TREI REGULI BIT-CU-BIT (miezul feliei — protejeaza semnaturile digitale):

1. OCTETI BRUTI PE FIR. Corpurile sunt `application/octet-stream` / `application/pdf` —
   niciodata JSON, niciodata base64. Nicaieri pe drum nu se trece prin mod TEXT.

2. SHA-256 VERIFICAT LA AMANDOUA CAPETELE, IN AMANDOUA SENSURILE.
   * Incarcare: clientul calculeaza suma INAINTE de a trimite si o pune in `X-Sha256`;
     serverul o RECALCULEAZA peste corpul primit si respinge cu 400 la nepotrivire —
     nu se scrie nimic.
   * Descarcare: serverul trimite suma stocata ca `ETag`; clientul o recalculeaza peste
     octetii primiti si refuza sa scrie fisierul de cache la nepotrivire.

3. CONCURENTA OPTIMISTA. Incarcarea poarta in `X-Sha-Precedent` suma pe care clientul a
   vazut-o ultima data pentru documentul respectiv («-» cand crede ca nu exista rand).
   Daca suma randului curent difera, raspunsul e 409 si NU se scrie nimic. Nicio semnatura
   a altcuiva nu se suprascrie in tacere.

NUMELE FISIERULUI se deriva pe SERVER (sursa unica, fara incredere in client), reproducand
exact conventiile Access deja portate pe client (DdfPdfLocator / OrdPdfLocator):
    DDF: DDF_NR_{CUAL}_REV_{NumarRev}_{CodAngajament}.PDF
    ORD: ORD_NR_{NrORD}_{CodAngajament}.PDF
La DDF se tine cont ca PK-ul lui FX_DDF e COMPUS (IDDF, CUAL) — se ia randul de antet prin
`LIMIT 1` cu ordine stabila, ca in routes/forexe/ddf.py, niciodata un join care ar da fan-out.
La ORD se foloseste `NrORD`-ul REAL — defectul Access «ORD_NR_0_…» (dictionar gol pe ramura
«un singur document») NU se reproduce, aceeasi decizie ca in OrdPdfLocator.

Aceasta ruta NU atinge `FX_DDF_REV.Semnatura` — scrierea inapoi a semnaturii apartine feliei
de semnare (0021), care va chema PUT-ul de aici DUPA o semnare reusita si abia apoi isi va
face propriul UPDATE.

NU se logheaza niciodata continutul blobului — doar dimensiuni si sume de control.
"""
import hashlib
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Plafonul practic al unui PDF stocat. Coloana e LONGBLOB (nu se opreste aici), dar peste
# atat raspundem 413 cu un mesaj care SPUNE limita — vezi si MAX_CONTENT_LENGTH din main.py,
# care taie cererea mai devreme, inainte ca octetii sa ajunga in memorie.
MAX_PDF_BYTES = 17 * 1024 * 1024

# Antetul care poarta suma clientului peste octetii trimisi.
H_SHA = "X-Sha256"
# Antetul care poarta suma pe care clientul a vazut-o ULTIMA DATA pentru document.
# «-» inseamna «cred ca nu exista rand».
H_SHA_PREC = "X-Sha-Precedent"
NO_ROW = "-"

# Cele doua familii de documente, intr-o singura descriere: tabela de PDF-uri, coloana-cheie
# si tabela parinte. Rutele sunt identice in afara acestor trei nume, deci logica sta o
# singura data (regula casei: fara al doilea exemplar care se poate desincroniza).
_DDF = {
    "tabela": "FX_DDF_PDF",
    "cheie": "IDREV",
    "parinte": "FX_DDF_REV",
    "eticheta": "ddf",
}
_ORD = {
    "tabela": "FX_ORD_PDF",
    "cheie": "IDORDP",
    "parinte": "FX_ORD",
    "eticheta": "ord",
}


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): mesajele de eroare sunt
    romanesti si trebuie sa ajunga la operator ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _sha256(data: bytes) -> str:
    """SHA-256 peste octetii dati, hex MINUSCULE — acelasi format ca pe client (PdfHash)."""
    return hashlib.sha256(data).hexdigest()


def _parinte_exista(cursor, spec, cheie: int) -> bool:
    """Exista documentul parinte (revizia / ordonantarea)? Fara el nu are ce PDF sa poarte."""
    cursor.execute(
        f"SELECT 1 FROM {spec['parinte']} WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
    return cursor.fetchone() is not None


def _sha_curent(cursor, spec, cheie: int):
    """Suma randului stocat acum, sau None cand nu exista rand."""
    cursor.execute(
        f"SELECT Sha256 FROM {spec['tabela']} WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
    row = cursor.fetchone()
    return row[0] if row else None


def _nume_fisier_ddf(cursor, idrev: int) -> str:
    """DDF_NR_{CUAL}_REV_{NumarRev}_{CodAngajament}.PDF — conventia din mdl_FX_DDF_PDF.

    `FX_DDF` are PK COMPUS (IDDF, CUAL) si nicio constrangere unica pe CodAngajament, deci
    acelasi IDDF poate purta mai multe randuri: se ia UNUL, cu ordine stabila (`ORDER BY
    IDDF, CUAL LIMIT 1`), exact ca alegerea deterministica din routes/forexe/ddf.py. Un join
    fara LIMIT ar multiplica revizia.
    """
    cursor.execute(
        "SELECT r.NumarRev, d.CUAL, d.CodAngajament "
        "  FROM FX_DDF_REV r "
        "  JOIN FX_DDF d ON d.IDDF = r.IDDF "
        " WHERE r.IDREV = %s "
        " ORDER BY d.IDDF, d.CUAL LIMIT 1", (idrev,))
    row = cursor.fetchone()
    if not row:
        return None
    numar_rev, cual, cod = row
    return f"DDF_NR_{cual}_REV_{numar_rev}_{cod}.PDF"


def _nume_fisier_ord(cursor, idordp: int) -> str:
    """ORD_NR_{NrORD}_{CodAngajament}.PDF — conventia din mdl_FX_ORD_PDF.

    Se foloseste `NrORD`-ul REAL. Access ia numarul dintr-un dictionar populat DOAR pe ramura
    «toate documentele lunii»; pe ramura «un singur document» dictionarul e gol si fisierul se
    naste «ORD_NR_0_…». Defectul NU se reproduce aici (aceeasi decizie ca in OrdPdfLocator).
    """
    cursor.execute(
        "SELECT NrORD, CodAngajament FROM FX_ORD WHERE IDORDP = %s LIMIT 1", (idordp,))
    row = cursor.fetchone()
    if not row:
        return None
    nr_ord, cod = row
    return f"ORD_NR_{nr_ord}_{cod}.PDF"


# ---------------------------------------------------------------------------------------
# DESCARCARE
# ---------------------------------------------------------------------------------------
def _descarca(spec, cheie: int):
    """Octetii PDF-ului semnat, VERBATIM.

    `If-None-Match` egal cu suma stocata -> 304 cu corp gol: asa evita cache-ul validat prin
    sha o descarcare inutila. Ruta de octeti NU poarta numele fisierului — metadatele calatoresc
    pe rutele de lista (GET /api/forexe/ddf, /ord).
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT Sha256, Dimensiune, Continut FROM {spec['tabela']} "
            f" WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
        row = cursor.fetchone()
        if row is None:
            return _json_utf8({"error": "Nu există PDF semnat pentru acest document."}, 404)

        sha, dimensiune, continut = row

        # ETag-ul se compara ca valoare goala de ghilimele, cum il trimitem mai jos.
        if (request.headers.get("If-None-Match", "").strip().strip('"')) == sha:
            resp = current_app.response_class(b"", status=304)
            resp.headers["ETag"] = f'"{sha}"'
            logger.info("[forexe.pdf] %s: %s %s=%s -> 304 (cache valid)",
                        db_name, spec["eticheta"], spec["cheie"], cheie)
            return resp

        # `bytes(continut)` — conectorul poate intoarce bytearray; octetii raman identici.
        octeti = bytes(continut)
        resp = current_app.response_class(octeti, status=200, mimetype="application/pdf")
        resp.headers["Content-Length"] = str(len(octeti))
        resp.headers["ETag"] = f'"{sha}"'
        logger.info("[forexe.pdf] %s: %s %s=%s -> 200 (%s octeti, sha=%s…)",
                    db_name, spec["eticheta"], spec["cheie"], cheie, dimensiune, sha[:8])
        return resp
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un 404 — un 404 ar minti
        # clientul ca documentul nu are PDF semnat si l-ar trimite sa regenereze degeaba.
        logger.error(f"[forexe.pdf] descarcare {spec['eticheta']}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea PDF-ului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------------------
# INCARCARE (inlocuieste-sau-insereaza)
# ---------------------------------------------------------------------------------------
def _incarca(spec, cheie: int, nume_fisier_fn):
    """Scrie PDF-ul semnat, intr-o SINGURA tranzactie, dupa verificarile din nota de modul.

    Ordinea pasilor conteaza: parinte -> PDF valid -> suma de control -> concurenta -> scriere.
    Fiecare pas care esueaza raspunde SI NU SCRIE NIMIC.
    """
    db_name = g.session.db_name

    octeti = request.get_data()
    if not octeti:
        return _json_utf8({"error": "Corpul cererii este gol: nu s-a primit niciun fișier."}, 400)
    if len(octeti) > MAX_PDF_BYTES:
        return _json_utf8(
            {"error": f"Fișierul depășește limita de {MAX_PDF_BYTES // (1024 * 1024)} MB "
                      f"acceptată de server ({len(octeti)} octeți). "
                      f"Reduceți dimensiunea capturilor de ecran atașate."}, 413)

    sha_client = (request.headers.get(H_SHA) or "").strip().lower()
    if not sha_client:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA}."}, 400)
    sha_precedent = (request.headers.get(H_SHA_PREC) or "").strip().lower()
    if not sha_precedent:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA_PREC}."}, 400)

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()

        # 1. Documentul parinte trebuie sa existe.
        if not _parinte_exista(cursor, spec, cheie):
            return _json_utf8({"error": "Documentul pentru care s-a trimis PDF-ul nu există."}, 404)

        # 2. Sanity: chiar e un PDF?
        if not octeti.startswith(b"%PDF-"):
            return _json_utf8({"error": "Conținutul trimis nu este un fișier PDF valid."}, 400)

        # 3. Suma de control peste octetii CHIAR PRIMITI (regula 2 din nota de modul).
        sha_server = _sha256(octeti)
        if sha_server != sha_client:
            logger.warning("[forexe.pdf] %s: %s %s=%s sumă nepotrivită (client=%s… server=%s…)",
                           db_name, spec["eticheta"], spec["cheie"], cheie,
                           sha_client[:8], sha_server[:8])
            return _json_utf8(
                {"error": "Fișierul a sosit corupt: suma de control nu corespunde."}, 400)

        # 4. Concurenta optimista (regula 3). Nicio suprascriere tacuta.
        sha_stocat = _sha_curent(cursor, spec, cheie)
        asteptat = NO_ROW if sha_stocat is None else sha_stocat
        if sha_precedent != asteptat:
            logger.warning("[forexe.pdf] %s: %s %s=%s conflict (precedent=%s asteptat=%s)",
                           db_name, spec["eticheta"], spec["cheie"], cheie,
                           sha_precedent[:8], asteptat[:8])
            return _json_utf8(
                {"error": "Documentul a fost modificat de altcineva între timp."}, 409)

        # 5. Numele fisierului — derivat pe SERVER, sursa unica.
        nume = nume_fisier_fn(cursor, cheie)
        if not nume:
            # Parintele exista (pasul 1), dar antetul din care se compune numele lipseste.
            # Zgomotos, nu tacut: un nume inventat ar strica regasirea documentului.
            return _json_utf8(
                {"error": "Nu s-a putut compune numele fișierului: lipsesc datele de antet."}, 409)

        # 6. Scrierea propriu-zisa, atomica sub cheia unica.
        cursor.execute(
            f"INSERT INTO {spec['tabela']} "
            f"       ({spec['cheie']}, NumeFisier, Dimensiune, Sha256, Continut, DataModif) "
            f"VALUES (%s, %s, %s, %s, %s, NOW()) "
            f"ON DUPLICATE KEY UPDATE "
            f"       NumeFisier = VALUES(NumeFisier), "
            f"       Dimensiune = VALUES(Dimensiune), "
            f"       Sha256     = VALUES(Sha256), "
            f"       Continut   = VALUES(Continut), "
            f"       DataModif  = NOW()",
            (cheie, nume, len(octeti), sha_server, octeti))
        conn.commit()

        logger.info("[forexe.pdf] %s: %s %s=%s salvat (%s octeti, sha=%s…, nume=%s)",
                    db_name, spec["eticheta"], spec["cheie"], cheie,
                    len(octeti), sha_server[:8], nume)
        return _json_utf8(
            {"sha256": sha_server, "nume_fisier": nume, "dimensiune": len(octeti)}, 200)
    except Exception as e:
        # Fara inghitire: se anuleaza tranzactia si se intoarce motivul.
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                # Rollback-ul pe o conexiune deja cazuta nu are ce sa mai salveze; eroarea
                # REALA e cea de mai jos si ea trebuie sa ajunga la client.
                logger.warning("[forexe.pdf] rollback esuat dupa eroarea de mai jos", exc_info=True)
        logger.error(f"[forexe.pdf] incarcare {spec['eticheta']}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea PDF-ului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------------------
# RUTELE
# ---------------------------------------------------------------------------------------
@forexe_bp.route("/api/forexe/ddf/pdf/<int:idrev>", methods=["GET"])
@require_session
def get_ddf_pdf(idrev):
    """PDF-ul semnat al unei revizii DDF (octeti bruti)."""
    return _descarca(_DDF, idrev)


@forexe_bp.route("/api/forexe/ddf/pdf/<int:idrev>", methods=["PUT"])
@require_session
def put_ddf_pdf(idrev):
    """Inlocuieste-sau-insereaza PDF-ul semnat al unei revizii DDF."""
    return _incarca(_DDF, idrev, _nume_fisier_ddf)


@forexe_bp.route("/api/forexe/ord/pdf/<int:idordp>", methods=["GET"])
@require_session
def get_ord_pdf(idordp):
    """PDF-ul semnat al unei ordonantari (octeti bruti)."""
    return _descarca(_ORD, idordp)


@forexe_bp.route("/api/forexe/ord/pdf/<int:idordp>", methods=["PUT"])
@require_session
def put_ord_pdf(idordp):
    """Inlocuieste-sau-insereaza PDF-ul semnat al unei ordonantari."""
    return _incarca(_ORD, idordp, _nume_fisier_ord)
