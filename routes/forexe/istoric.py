# routes/forexe/istoric.py
"""
Ruta Istoric pentru frmFX_ISTORIC (felia 0022, vederea Istoric).

Contract (GET /api/forexe/istoric?cod=<CodAngajament>):
    { "cod": "<CodAngajament>",
      "randuri":      [ {...}, ... ],   # FX_Istoric — un rand per inregistrare
      "clasificatii": [ {...}, ... ] }  # ierarhia de filtrare (nomenclator), deduplicata

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree, /sumar, /rezervari, /receptii, /plati si /ddf. Un token nu
poate tinti alta baza decat cea pe care s-a logat.

Un singur drum dus-intors pentru tot CodAngajament-ul: clientul (IstoricView) modeleaza
grila, cele trei meniuri de filtrare si filtrarea LOCAL, fara alte cereri (decizia din
0020-01 §7, nu se re-litiga aici).

Sursa Access (verificata in export, NU reghicita):
  - frmFX_ISTORIC : RecordSource = `SELECT ... FROM FX_Istoric WHERE CodAngajament Like ...
                    ORDER BY DataFX, Clsf`. ORDER BY-ul de mai jos este al ei, verbatim.
  - mdl_FX_Popups : cele trei constructoare de meniu (Show_Popup_Clasificatii /
                    Show_Popup_TipRand / Show_Popup_DataFx) + ApplyColumnFilter. Meniurile
                    TipRand si DataFX se construiesc pe client din randurile deja incarcate.

`Clsf` (§2.4) — coloana TEXT DENORMALIZATA in FX_Istoric, scrisa la parsare de
`FX_Istoric_Prelucreaza_Observatii`. Se citeste DIRECT: vederea NU e expusa problemei de
duplicate din nomenclator pentru afisare — doar ierarhia de filtrare atinge nomenclatorul.

`DataFX` (§2.3) — este `datetime`, iar randurile de Istoric sunt EVENIMENTE cu ora. Se
trimite valoarea ISO COMPLETA (cu ora), NU trunchiata la zi. `_iso_dt` de aici pastreaza
ora; NU se copiaza `_iso`-ul din ddf.py (care taie ora — corect acolo, gresit aici).

Cheia de clasificatie (§2.5) — INVERS fata de DDF. `FX_Istoric_Prelucreaza_Observatii`
scrie `Rs!IdClsf = rcInd!IdClsf` direct din FX_Indicatori, care tine id-ul ACCESS. Deci
`FX_Istoric.IdClsf` se potriveste cu `Clasificatii.IdClsfAcc` (NU cu IDClsf, ca in DDF), si
are nevoie de predicatul IdUnitate — pe care FX_Istoric nu-l are ca si coloana, deci vine
prin FX_Indicatori pe CodAngajament, exact ca filtrul Access (bFilter_Click). Gresit
inversata, cheia da un meniu de filtrare GOL, nu o eroare — de aceea e prinsa cu test.

Ierarhia `clasificatii` (§2.2) — asamblata din DOUA baze (nu exista echivalent MariaDB al
lui `qFX_Clsf2026_Structura`, a carui definitie e pierduta):
  - captionul Subcapitol = AVACONT_COMUN.DefaClsfF.Denumire (cheiat pe Clasificatii.ClsfF);
  - captionul Articol    = AVACONT_COMUN.DefaArticol.Denumire (cheiat pe Clasificatii.Articol);
  - captionul Alineat    = Clasificatii.Denumire (per-unitate, fara join — DECIZIE de operator
                           peste alternativa AVACONT_COMUN.DefaClsfE.Denumire; ambele erau
                           disponibile, niciuna nu se putea dovedi fidela structurii pierdute).
  ATENTIE (host-verification, felia 0022): exportul Access (qFX_DDF_SA_CLSF) foloseste
  `DefaClsfF.Explicatie` (nu `.Denumire`) si `DefaTitlu2` (nu `DefaArticol`). Numele de mai
  jos urmeaza SCHEMA MariaDB migrata asa cum o cere planul §2.2; daca un nume nu se
  potriveste in baza reala, endpoint-ul PICA ZGOMOTOS (500) — asta e comportamentul cerut
  (§2.2: „fail loudly rather than return empty captions"), nu se mascheaza cu captiuni goale.
  Ambele predicate (IdClsf + IdUnitate) sunt din interogarea Access bFilter_Click si sunt
  OBLIGATORII. `IdUnitate` se PASTREAZA: Clasificatii e un nomenclator partajat, nu o tabela
  `FX_` (regula drop-IdUnitate nu i se aplica — 0011-03; Status_migrare_5 §8).

`clasificatii` se DEDUPLICA pe IdClsfAcc (§2.2): nomenclatorul are duplicate reale (0011-03
a masurat perechi la (75,79/84/90/92/93)). E o LISTA, nu un lookup scalar, deci `LIMIT 1` NU
se aplica — se grupeaza pe IdClsfAcc si se emite exact o intrare per valoare distincta.

Coloane FX_Istoric DELIBERAT ABSENTE de pe fir (§2.1): Utilizator, HASH, Prelucrat, DTQ,
Val_Receptie_T, Rez_Ord. Niciuna nu apare pe formularul Access; ultimele doua exista in DDL
dar pe niciun control — a le adauga ar inventa o vedere.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Un rand per inregistrare FX_Istoric a angajamentului. ORDER BY DataFX, Clsf = al
# RecordSource-ului Access, verbatim (§2.1). `cod` e match EXACT (Access folosea Like —
# ABATERE §9.3), parametrizat, niciodata interpolat in text.
_SQL_RANDURI = (
    "SELECT "
    "ID, DataFX, Clsf, IdClsf, TipRand, CodIndicator, CodAI, "
    "Descriere, Observatii, "
    "Val_Rezervare_I, Val_Rezervare_D, Val_Rezervare_Ant, Val_Rezervare_Dif, "
    "Val_AngLeg, Val_Receptie, Val_Plata, "
    "IdTrezor, Doc, IDREV "
    "FROM FX_Istoric "
    "WHERE CodAngajament = %s "
    "ORDER BY DataFX, Clsf"
)

# Ierarhia de filtrare (§2.2). Domeniul e din interogarea Access bFilter_Click: doar
# clasificatiile prezente pe angajament (IdClsf din FX_Istoric = id ACCESS -> IdClsfAcc)
# SI ale unitatii angajamentului (IdUnitate prin FX_Indicatori). GROUP BY c.IdClsfAcc
# deduplica (§2.2): exact o intrare per valoare distincta, chiar cand nomenclatorul are
# duplicate. Captiunile Subcapitol/Articol vin din AVACONT_COMUN prin nume calificat;
# Alineat din Clasificatii.Denumire (decizie de operator). LEFT JOIN ca o captiune lipsa
# sa nu stearga intrarea (dar o TABELA lipsa PICA — comportament cerut).
_SQL_CLASIFICATII = (
    "SELECT "
    "c.IdClsfAcc AS id_clsf, "
    "MAX(c.Clsf) AS clsf, "
    "MAX(c.Capitol) AS capitol, "
    "MAX(c.Subcapitol) AS subcapitol, "
    "MAX(c.Articol) AS articol, "
    "MAX(c.Alineat) AS alineat, "
    "MAX(df.Denumire) AS den_subcapitol, "
    "MAX(da.Denumire) AS den_articol, "
    "MAX(c.Denumire) AS den_alineat "
    "FROM Clasificatii c "
    "LEFT JOIN AVACONT_COMUN.DefaClsfF df ON df.ClsfF = c.ClsfF "
    "LEFT JOIN AVACONT_COMUN.DefaArticol da ON da.Articol = c.Articol "
    "WHERE c.IdClsfAcc IN (SELECT IdClsf FROM FX_Istoric WHERE CodAngajament = %s) "
    "  AND c.IdUnitate IN (SELECT IdUnitate FROM FX_Indicatori WHERE CodAngajament = %s) "
    "GROUP BY c.IdClsfAcc "
    "ORDER BY MAX(c.Capitol), MAX(c.Subcapitol), MAX(c.Articol), MAX(c.Alineat)"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): Descriere / Observatii /
    Denumire contin text romanesc si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso_dt(value):
    """DateTime -> ISO 8601 COMPLET (cu ora) sau None.

    DELIBERAT diferit de `_iso`-ul din ddf.py: `DataFX` e un `datetime` si randurile de
    Istoric sunt evenimente cu ora — se pastreaza componenta de timp (§2.3). Clientul
    formateaza pe `dd.MM.yyyy` in grila, dar ora ramane pe fir pentru filtrul pe zi si ca
    o eventuala trunchiere accidentala sa fie prinsa de test.
    """
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. DOUBLE vine ca float; None devine 0.0 (server-side), ca
    grila si totalurile sa arate «0,00», nu gol. Valorile negative isi pastreaza semnul."""
    return float(value) if value is not None else 0.0


@forexe_bp.route("/api/forexe/istoric", methods=["GET"])
@require_session
def get_istoric():
    """Istoricul unui angajament: un rand per inregistrare FX_Istoric + ierarhia de filtrare.

    Query: cod (obligatoriu) = CodAngajament.

    Un `cod` necunoscut / fara istoric NU este 404: un angajament fara randuri de istoric
    este legitim, deci raspunsul este 200 cu ambele liste goale. Apelantul trebuie sa poata
    distinge „nu are istoric" de „a cazut transportul" — un 404 ar amesteca cele doua.
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    # Scope: baza sesiunii, niciodata din cerere (o baza = o unitate).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()

        # --- randuri: FX_Istoric --------------------------------------------------------
        # SQL parametrizat — `cod` nu se interpoleaza NICIODATA in text.
        cursor.execute(_SQL_RANDURI, (cod,))
        randuri = []
        for (rid, data_fx, clsf, id_clsf, tip_rand, cod_indicator, cod_ai,
             descriere, observatii,
             val_rez_i, val_rez_d, val_rez_ant, val_rez_dif,
             val_ang_leg, val_receptie, val_plata,
             id_trezor, doc, idrev) in cursor.fetchall():
            randuri.append({
                "id": int(rid) if rid is not None else None,
                # §2.3 — ISO COMPLET (cu ora), nu trunchiat la zi.
                "data_fx": _iso_dt(data_fx),
                # §2.4 — coloana denormalizata, citita direct (fara join).
                "clsf": clsf,
                # §2.5 — id ACCESS (= Clasificatii.IdClsfAcc); cheia meniului de filtrare.
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "tip_rand": tip_rand,
                "cod_indicator": cod_indicator,
                "cod_ai": cod_ai,
                "descriere": descriere,
                "observatii": observatii,
                "val_rezervare_i": _num(val_rez_i),
                "val_rezervare_d": _num(val_rez_d),
                "val_rezervare_ant": _num(val_rez_ant),
                "val_rezervare_dif": _num(val_rez_dif),
                "val_ang_leg": _num(val_ang_leg),
                "val_receptie": _num(val_receptie),
                "val_plata": _num(val_plata),
                "id_trezor": id_trezor,
                "doc": doc,
                "idrev": int(idrev) if idrev is not None else None,
            })

        # --- clasificatii: ierarhia de filtrare, deduplicata pe IdClsfAcc ---------------
        cursor.execute(_SQL_CLASIFICATII, (cod, cod))
        clasificatii = []
        for (id_clsf, clsf, capitol, subcapitol, articol, alineat,
             den_subcapitol, den_articol, den_alineat) in cursor.fetchall():
            clasificatii.append({
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "clsf": clsf,
                "capitol": capitol,
                "subcapitol": subcapitol,
                "articol": articol,
                "alineat": alineat,
                "den_subcapitol": den_subcapitol,
                "den_articol": den_articol,
                "den_alineat": den_alineat,
            })

        logger.info("[forexe.istoric] %s: cod=%s -> randuri=%s clasificatii=%s",
                    db_name, cod, len(randuri), len(clasificatii))
        return _json_utf8({"cod": cod, "randuri": randuri, "clasificatii": clasificatii}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU liste goale — listele goale
        # ar minti operatorul ca angajamentul nu are istoric.
        logger.error(f"[forexe.istoric] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea istoricului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
