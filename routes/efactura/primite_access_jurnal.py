# routes/efactura/primite_access_jurnal.py
"""
What the journal (AVACONT_COMUN.Jurnal) records about «Preia din Access» (slice 00EF-44), so a run that brought less than it should can be
explained afterwards from the database alone, without the log of the client's PC.

Two kinds of rows, both written on EVERY call (also when nothing new was added, which used to leave no trace):
    EF_PRIMITE_ACCESS_PREIA   one per batch the PC sent. Rezultat: OK | PROBLEME (operations left out) | ERORI (messages not written) | EROARE (the call failed).
    EF_PRIMITE_ACCESS_FINAL   one at the end of a run: the references, the automatic link to receptions, and the PC's totals.
A run that has PREIA rows and no FINAL row was cut off on the PC (an error box, the window closed, the network).

`Detalii` is a line of `key=value` pairs. From the server: what the batch held (messages, the first and last id, the months it covers) and what
was done with it (new / already here, invoices, lines, notes, operations, errors). From the PC (the optional `context` of the body, every value
checked here): the year and file, the tax code used, how many ids Access gave, how many messages were read so far, and the ids that were asked
for and NOT returned. An old client sends no context: the row then holds the server's part only.
"""

MAX_DETALII = 3000
MAX_NECITITE = 20
MAX_ERORI_LISTATE = 5

_INT_KEYS = ("an", "lot", "loturi", "cerute", "citite_lot", "citite", "acces_total", "necitite_total")
_TEXT_KEYS = (("fisier", 64), ("cui", 32))


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cut(value, limit):
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text[:limit]


def context(raw):
    """The PC's account of the run, reduced to the known keys with checked values: {key: value}. Anything else is dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key in _INT_KEYS:
        number = _int(raw.get(key))
        if number is not None:
            out[key] = number
    for key, limit in _TEXT_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _cut(value, limit)
    ids = raw.get("necitite")
    if isinstance(ids, list):
        out["necitite"] = [_cut(i, 32) for i in ids[:MAX_NECITITE] if isinstance(i, (str, int)) and str(i).strip()]
    return out


def _pairs(pairs):
    text = " ".join(f"{key}={value}" for key, value in pairs if value is not None and value != "")
    return text[:MAX_DETALII]


def _context_pairs(ctx):
    pairs = [(key, ctx.get(key)) for key in ("an", "fisier", "cui", "lot", "loturi", "cerute", "citite_lot", "citite", "acces_total",
                                             "necitite_total")]
    if ctx.get("necitite"):
        pairs.append(("necitite", ",".join(ctx["necitite"])))
    return pairs


def rezumat_mesaje(mesaje):
    """What a batch holds, from the request itself: count, first / last id, the months it covers, messages without invoices or without archive."""
    if not isinstance(mesaje, list):
        return [("mesaje_primite", "?")]
    items = [m for m in mesaje if isinstance(m, dict)]
    dates = sorted(str(m.get("DataMesaj"))[:7] for m in items if m.get("DataMesaj"))
    return [
        ("mesaje_primite", len(mesaje)),
        ("prima", _cut(items[0].get("IdSol"), 32) if items else None),
        ("ultima", _cut(items[-1].get("IdSol"), 32) if items else None),
        ("luni", f"{dates[0]}..{dates[-1]}" if dates else None),
        ("fara_factura", sum(1 for m in items if not m.get("facturi"))),
        ("fara_arhiva", sum(1 for m in items if not m.get("Xml"))),
    ]


def rezultat_preia(raspuns):
    """The journal's `Rezultat` for a batch that was answered."""
    if raspuns["erori"]:
        return "ERORI"
    if raspuns["probleme"]:
        return "PROBLEME"
    return "OK"


def detalii_preia(raspuns, mesaje, ctx, ms):
    pairs = _context_pairs(ctx) + rezumat_mesaje(mesaje) + [
        ("mesaje_noi", raspuns["mesaje_noi"]), ("mesaje_sarite", raspuns["mesaje_sarite"]),
        ("facturi_noi", raspuns["facturi_noi"]), ("facturi_sarite", raspuns["facturi_sarite"]), ("linii", raspuns["linii"]),
        ("note", raspuns["note"]), ("mesaje_anaf", raspuns["mesaje_factura"]), ("operatii", raspuns["operatii"]),
        ("operatii_sarite", raspuns["operatii_sarite"]), ("erori", len(raspuns["erori"])), ("probleme", len(raspuns["probleme"]))]
    if raspuns["erori"]:
        listed = "; ".join(f"{e['id_sol']}: {_cut(e['motiv'], 80)}" for e in raspuns["erori"][:MAX_ERORI_LISTATE])
        pairs.append(("erori_lista", f"[{listed}]"))
    pairs.append(("ms", ms))
    return _pairs(pairs)


def detalii_eroare(mesaje, ctx, err, ms):
    """A batch the server could not answer (a refusal or a failure): what it held and why it stopped."""
    reason = getattr(err, "reason", None)
    return _pairs(_context_pairs(ctx) + rezumat_mesaje(mesaje) + [
        ("eroare", type(err).__name__), ("motiv", reason), ("text", _cut(err, 120)), ("ms", ms)])


def detalii_final(raspuns, n_id_primite, n_referinte, ctx, ms):
    legare = raspuns["legare"]
    return _pairs(_context_pairs(ctx) + [
        ("facturi_adaugate", n_id_primite), ("referinte_primite", n_referinte), ("referinte_setate", raspuns["referinte_setate"]),
        ("legare_citite", legare.get("citite")), ("legate", legare.get("legate")), ("fara_furnizor", legare.get("fara_furnizor")),
        ("fara_receptie", legare.get("fara_receptie")), ("nesigure", legare.get("nesigure")),
        ("mai_multe_clasificatii", legare.get("mai_multe_clasificatii")), ("conflicte", legare.get("conflicte")),
        ("legare_omisa", _cut(legare["omisa"], 120) if legare.get("omisa") else None), ("ms", ms)])
