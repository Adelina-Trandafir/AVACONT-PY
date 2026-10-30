# routes/forexe/receptii_cumulat.py
"""
Receptiile unui angajament comparate cu rezervarile lui (slice 0122-10).

DE CE. O versiune de receptie (`FX_Receptii_H`) pusa pe antetul gresit nu da nicio eroare: totalul
final iese bun, dar `DIF` al ei se calculeaza fata de alt lant, si ordonantarile (care aduna `DIF`
pana la data platii) vad receptiile prea mici pentru toate zilele dintre data versiunii si urmatoarea
versiune corecta. Cazul AAB2FH3R3FF (29.10.2026): versiunea din 12.10.2026 sta pe receptia din
10.06.2026; plata din 12-17.10 ar fi vazut 137.865,00 mai putin.

REGULA. Rezervarile vin separat de la FOREXE si au un total cumulat (`FX_Rezervari.R_Definitiva`). Pentru
un indicator care se comporta «rezervare = receptie», suma `DIF` a receptiilor pana la ziua unei
rezervari (inclusiv) trebuie sa fie egala cu `R_Definitiva` de la sfarsitul zilei. Daca nu e, e aratat
(arborele din K-BOT: tooltip + randul rosu; la salvarea unei descarcari: avertisment).

CAND SE APLICA (ca sa nu strige degeaba la un contract rezervat integral dinainte):
  * ziua rezervarii are si receptii in acea zi pentru acelasi indicator (rezervarea s-a nascut dintr-o receptie);
  * ziua are un singur capat de lant de rezervari (`R_Anterioara` -> `R_Definitiva`); altfel e sarita;
  * indicatorul are cel putin DOUA zile de felul asta in care cifrele se potrivesc exact -- adica se vede
    ca la el rezervarea chiar urmeaza receptiile. Fara asta, nimic nu e raportat.
`DIF` se aduna ca in ordonantare (`ord_edit._SQL_REC_ANT`): fara versiunile sterse.

NEVERIFICAT: nu a rulat pe MariaDB si nu a trecut prin niciun test. Pe jurnalul AAB2FH3R3FF (un singur
angajament) da exact cele cinci randuri gresite si nicio alarma falsa din 60 de verificari; pe angajamente
de alt fel nu s-a incercat.
"""
import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TOLERANTA = 0.01
MIN_ZILE_POTRIVITE = 2
_MAX_DETALII = 60

_SQL_REZERVARI = (
    "SELECT CodAngajament, CodIndicator, DATE(DataRezervare) AS Zi, R_Anterioara, R_Definitiva "
    "  FROM FX_Rezervari "
    " WHERE CodIndicator IS NOT NULL AND DataRezervare IS NOT NULL AND R_Definitiva IS NOT NULL{filtru}"
)
# Same filter as the ordonantare (`COALESCE(H.Sters, 0) = 0`); `DIF` NULL (a snapshot not placed) adds nothing.
_SQL_RECEPTII = (
    "SELECT R.CodAngajament, R.CodIndicator, DATE(R.Data) AS Zi, SUM(R.DIF) AS Dif "
    "  FROM FX_Receptii R JOIN FX_Receptii_H H ON R.IDRH = H.IDRH "
    " WHERE COALESCE(H.Sters, 0) = 0 AND R.CodIndicator IS NOT NULL AND R.Data IS NOT NULL{filtru} "
    " GROUP BY R.CodAngajament, R.CodIndicator, DATE(R.Data)"
)


def _zi(valoare) -> Optional[date]:
    if isinstance(valoare, datetime):
        return valoare.date()
    if isinstance(valoare, date):
        return valoare
    try:
        return date.fromisoformat(str(valoare)[:10])
    except ValueError:
        return None


def _num(valoare) -> float:
    return round(float(valoare or 0), 2)


def _capat_de_zi(randuri: List[dict]) -> Optional[dict]:
    """The row a day's reservation chain ends on: the only one whose `R_Definitiva` no other row of that day
    starts from. None when the day is ambiguous (zero or several such rows) -- nothing is guessed."""
    if len(randuri) == 1:
        return randuri[0]
    capete = [r for r in randuri
              if not any(o is not r and abs(o["ant"] - r["def"]) < TOLERANTA for o in randuri)]
    return capete[0] if len(capete) == 1 else None


def gaseste_receptii_nepotrivite(cursor, cod: Optional[str] = None) -> Dict[str, List[dict]]:
    """
    Angajamente whose receptions (sum of `DIF`) do not add up to their reservations on a day when both exist.
    `cod` None = the whole database (the tree). Returns {CodAngajament: [{zi, indicator, rezervat, receptionat}]}
    in date order, only for the days that disagree. The cursor is a DICTIONARY one.
    """
    filtru_r = " AND CodAngajament = %s" if cod else ""
    filtru_c = " AND R.CodAngajament = %s" if cod else ""
    parametri = (cod,) if cod else ()

    cursor.execute(_SQL_REZERVARI.format(filtru=filtru_r), parametri)
    pe_zi: Dict[tuple, List[dict]] = defaultdict(list)
    for r in cursor.fetchall():
        zi = _zi(r["Zi"])
        if zi is None:
            continue
        pe_zi[(str(r["CodAngajament"]), str(r["CodIndicator"]), zi)].append(
            {"ant": _num(r["R_Anterioara"]), "def": _num(r["R_Definitiva"])})
    if not pe_zi:
        return {}

    cursor.execute(_SQL_RECEPTII.format(filtru=filtru_c), parametri)
    receptii: Dict[tuple, Dict[date, float]] = defaultdict(dict)
    for r in cursor.fetchall():
        zi = _zi(r["Zi"])
        if zi is not None:
            receptii[(str(r["CodAngajament"]), str(r["CodIndicator"]))][zi] = _num(r["Dif"])

    puncte: Dict[tuple, List[tuple]] = defaultdict(list)
    for (cod_ang, indicator, zi), randuri in pe_zi.items():
        puncte[(cod_ang, indicator)].append((zi, randuri))

    rezultat: Dict[str, List[dict]] = defaultdict(list)
    for (cod_ang, indicator), lista in puncte.items():
        zile_rec = receptii.get((cod_ang, indicator), {})
        verificari = []
        for zi, randuri in sorted(lista, key=lambda x: x[0]):
            if zi not in zile_rec:
                continue
            capat = _capat_de_zi(randuri)
            if capat is None:
                continue
            cumulat = round(sum(v for z, v in zile_rec.items() if z <= zi), 2)
            verificari.append((zi, capat["def"], cumulat, abs(cumulat - capat["def"]) < TOLERANTA))
        if sum(1 for v in verificari if v[3]) < MIN_ZILE_POTRIVITE:
            continue
        for zi, rezervat, cumulat, potrivit in verificari:
            if not potrivit:
                rezultat[cod_ang].append(
                    {"zi": zi, "indicator": indicator, "rezervat": rezervat, "receptionat": cumulat})
    for lista in rezultat.values():
        lista.sort(key=lambda x: (x["zi"], x["indicator"]))
    return dict(rezultat)


def sir_pentru_arbore(nepotrivite: Optional[List[dict]]) -> Optional[str]:
    """«yyyy-MM-dd~indicator~rezervat~receptionat|...», None = everything adds up. No «~» or «|» can be in a piece."""
    if not nepotrivite:
        return None

    def curat(v):
        return str(v).replace("~", " ").replace("|", " ")
    return "|".join("~".join((d["zi"].isoformat(), curat(d["indicator"]),
                              f"{d['rezervat']:.2f}", f"{d['receptionat']:.2f}"))
                    for d in nepotrivite[:_MAX_DETALII])


def _ro(valoare: float) -> str:
    return f"{valoare:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def text_avertisment(nepotrivite: List[dict], maxim: int = 3) -> str:
    """The warning for the operator after a saved download (Romanian, read on screen)."""
    bucati = [f"{d['zi']:%d.%m.%Y} {d['indicator']}: rezervat {_ro(d['rezervat'])}, recepționat {_ro(d['receptionat'])}"
              for d in nepotrivite[:maxim]]
    rest = len(nepotrivite) - maxim
    mai = f" (și încă {rest})" if rest > 0 else ""
    return ("Recepțiile nu se potrivesc cu rezervările, deci ordonanțările din zilele respective ar vedea recepții "
            "greșite: " + "; ".join(bucati) + mai + ". Verificați așezarea versiunilor de recepție în fereastra de asocieri.")
