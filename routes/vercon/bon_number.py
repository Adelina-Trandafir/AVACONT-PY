"""The text of a bon number: the prefix of its kind, ONE space, the number (SLICE-VC06-19).

    BC 13  consumption bon (VC_Iesiri_H.NumarBC)      BX 13  return of a consumption bon (VC_Iesiri_H.NumarBC)
    BP 13  handover (VC_OI_Predare.NRBP, and the bon it creates in VC_Iesiri_H)
    BT 13  transfer (VC_OI_Transfer.NRBT)             BS 13  casare (VC_OI_Casare.NRBC)   (BS = Bon Casare; retur is BX)

The prefix is how a bon is told from another kind, so it is always written. Reading is tolerant: older rows may hold the
prefix glued to the number (BC13) or only the digits (13); they are read as the same number and shown in the new form.
"""
import re

PREFIX = {'bc': 'BC', 'br': 'BX', 'bp': 'BP', 'bt': 'BT', 'bs': 'BS'}   # keys are the internal families: br = retur (prefix BX), bs = casare (prefix BS)


def text(family, number):
    """The stored text: BC 13."""
    return '%s %d' % (PREFIX[family], number)


def variants(family, number):
    """Every spelling of one number a row can hold: the new one, the glued one, the bare digits."""
    return [text(family, number), '%s%d' % (PREFIX[family], number), str(number)]


def parse(family, raw):
    """The number of a stored text of THIS kind; None for another kind (a BP bon in a BC list) or for no number."""
    found = re.fullmatch(r'\s*(?:%s\s*)?(\d+)\s*' % PREFIX[family], str(raw if raw is not None else ''), re.IGNORECASE)
    return int(found.group(1)) if found else None


def display(family, raw):
    """What the operator reads: the number in the new form whatever the row holds; an unreadable text stays as it is."""
    number = parse(family, raw)
    return text(family, number) if number is not None else raw
