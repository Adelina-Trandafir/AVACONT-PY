# scripts/aproba_cerere.py
"""
Approving a registration request from the command line (slice 0075-03).

The approval PAGE is slice 0075-05. Until it exists, this is how the operator runs
the provisioning job of plan 5.6 on the VPS. It calls exactly what the page will
call (routes/inregistrare/provizionare.py), so nothing here is a second copy of the
job -- only a way to start it and read what it says.

Usage (from the PYTHON folder, with the venv):
    python -m scripts.aproba_cerere --lista                     # waiting and failed requests
    python -m scripts.aproba_cerere 7 --plan                    # every check, writes nothing
    python -m scripts.aproba_cerere 7                           # approve (asks for DA)
    python -m scripts.aproba_cerere 7 --cod-program 01A=0000002510 --cod-program 02E=0000000000
    python -m scripts.aproba_cerere 7 --nume-baza 115_TRND      # the operator's own database name
    python -m scripts.aproba_cerere 7 --link-nou                # a fresh password link

Exit code 0 = done, 1 = refused or failed (the reason is printed and, for a failed
approval, stored in FX_Inregistrari.Motiv).
"""
import argparse
import getpass
import logging
import sys

import mysql.connector

from routes.inregistrare import provizionare
from utils.database import get_kbot_provisioning_connection


def _say(line):
    print(line, flush=True)


def _parse_cod_program(values):
    out = {}
    for item in values or []:
        ss, sep, value = item.partition("=")
        if not sep or not ss.strip() or not value.strip():
            raise SystemExit(f"--cod-program vrea forma SS=VALOARE, nu «{item}».")
        out[ss.strip().upper()] = value.strip()
    return out


def _list():
    conn = get_kbot_provisioning_connection()
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(
            "SELECT IdCerere, DataCerere, Stare, CF, Email, Denumire, Motiv "
            "FROM FX_Inregistrari WHERE Stare IN ('InAsteptare', 'Esuata') "
            "ORDER BY IdCerere"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        _say("Nicio cerere în așteptare sau eșuată.")
        return
    for r in rows:
        _say(f"{r['IdCerere']:>5}  {r['DataCerere']:%Y-%m-%d %H:%M}  {r['Stare']:<11}  "
             f"CF {r['CF']:<12} {r['Email']:<35} {r['Denumire']}")
        if r["Motiv"]:
            _say(f"       motiv: {r['Motiv']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aprobă o cerere de înregistrare.")
    parser.add_argument("id_cerere", nargs="?", type=int)
    parser.add_argument("--lista", action="store_true", help="Cererile în așteptare și eșuate.")
    parser.add_argument("--plan", action="store_true", help="Doar verifică. Nu scrie nimic.")
    parser.add_argument("--link-nou", action="store_true",
                        help="Trimite un link nou de parolă pentru o cerere aprobată.")
    parser.add_argument("--cod-program", action="append", metavar="SS=VALOARE",
                        help="CodProgram pentru o sursă-sector (D25). Se poate repeta.")
    parser.add_argument("--nume-baza", metavar="1nn_SSSS",
                        help="Alt nume pentru baza nouă decât cel calculat.")
    parser.add_argument("--da", action="store_true", help="Nu mai întreba.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        if args.lista:
            _list()
            return 0
        if args.id_cerere is None:
            parser.error("lipsește numărul cererii")

        if args.link_nou:
            result = provizionare.link_nou(args.id_cerere, progress=_say)
            if result["link"]:
                _say(f"Linkul, de transmis de mână: {result['link']}")
            return 0

        codes = _parse_cod_program(args.cod_program)
        preview = provizionare.plan(args.id_cerere, codes, progress=_say,
                                    db_name=args.nume_baza)
        if args.plan:
            return 0

        if not args.da:
            answer = input(f"Se creează baza {preview['db_name']} pentru «{preview['denumire']}» "
                           f"și contul {preview['email']}. Scrieți DA: ").strip()
            if answer != "DA":
                _say("Anulat. Nu s-a scris nimic.")
                return 1

        result = provizionare.approve(
            args.id_cerere, f"cli:{getpass.getuser()}", codes, progress=_say,
            db_name=args.nume_baza)
        _say(f"GATA: {result['db_name']}, {result['randuri']} clasificații, "
             f"cont {result['email']}.")
        if result["link"]:
            _say(f"Linkul de parolă, de transmis de mână: {result['link']}")
        return 0

    except provizionare.ProvizionareRefuzata as err:
        _say(f"REFUZAT: {err}")
        return 1
    except provizionare.ProvizionareEsuata as err:
        _say(f"EȘUAT: {err.message}")
        if err.ramas:
            _say("De curățat de mână:")
            for item in err.ramas:
                _say(f"  - {item}")
        return 1
    except mysql.connector.Error as err:
        _say(f"Eroare MariaDB: [{getattr(err, 'errno', '?')}] {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
