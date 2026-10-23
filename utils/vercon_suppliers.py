"""One supplier identity for online VERCON: the normalized fiscal code."""
import re
import unicodedata


def code(value):
    clean = ''.join(ch for ch in str(value or '') if ch.isalnum()).upper()
    return clean[2:] if re.match(r'^RO[0-9]', clean) else clean


def name_key(value):
    text = unicodedata.normalize('NFC', str(value or '').casefold()).translate(str.maketrans('şţ', 'șț'))
    accents = {'ă': ('a', 1), 'â': ('a', 2), 'î': ('i', 1), 'ș': ('s', 1), 'ț': ('t', 1)}
    return tuple(accents.get(char, (char, 0)) for char in text)


_PREFIXES = (('sc',), ('societatea', 'comerciala'))
_SUFFIXES = (('srl', 'd'), ('srl',), ('sa',), ('snc',), ('scs',), ('sca',), ('pfa',), ('ii',))


def duplicate_key(value):
    """Name without spaces, accents, punctuation, leading SC / S.C. and trailing SRL / S.R.L. / SA ...: two partners
    with the same key and different fiscal codes are probably the same firm typed twice."""
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch)).replace('.', '')
    tokens = re.sub(r'[\W_]+', ' ', text).split()
    kept, changed = tokens, True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if len(kept) > len(prefix) and tuple(kept[:len(prefix)]) == prefix:
                kept, changed = kept[len(prefix):], True
        for suffix in _SUFFIXES:
            if len(kept) > len(suffix) and tuple(kept[-len(suffix):]) == suffix:
                kept, changed = kept[:-len(suffix)], True
    return ''.join(kept or tokens)


def sql(expression):
    clean = "UPPER(REGEXP_REPLACE(COALESCE(" + expression + ",''),'[^[:alnum:]]',''))"
    return "(CASE WHEN " + clean + " REGEXP '^RO[0-9]' THEN SUBSTRING(" + clean + ',3) ELSE ' + clean + ' END)'


def relation(active_only=False):
    """One complete display row per CF, independent of unit and internal partner IDs."""
    fiscal = sql('p.CodFiscal')
    return ('(SELECT CodFiscal,DenumirePartener,Adresa,ContIBAN,Banca,Ascuns,Tip FROM ('
            'SELECT ' + fiscal + ' AS CodFiscal,p.DenumirePartener,p.Adresa,p.ContIBAN,p.Banca,p.Ascuns,p.Tip,'
            'ROW_NUMBER() OVER (PARTITION BY ' + fiscal + ' ORDER BY (COALESCE(p.Ascuns,0)<>0),'
            "COALESCE(p.DenumirePartener,''),COALESCE(p.Adresa,''),COALESCE(p.ContIBAN,''),"
            "COALESCE(p.Banca,''),COALESCE(p.Tip,'')) AS CF_ROW FROM Parteneri p WHERE " + fiscal + "<>''"
            + (' AND COALESCE(p.Ascuns,0)=0' if active_only else '')
            + ') fiscal_partners WHERE CF_ROW=1)')


def find(cursor, value, active_only=False):
    fiscal = code(value)
    if not fiscal or len(fiscal) > 50:
        return None
    cursor.execute('SELECT * FROM ' + relation(active_only) + ' p WHERE p.CodFiscal=%s', (fiscal,))
    return cursor.fetchone()


def schema_ready(cursor):
    cursor.execute('SELECT TABLE_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                   "AND TABLE_NAME IN ('VC_NIR','VC_FACTURI') AND COLUMN_NAME='CodPartener'")
    return {row['TABLE_NAME'].upper() for row in cursor.fetchall()} == {'VC_NIR', 'VC_FACTURI'}
