"""Bank rows are shared by the DC and explicitly assigned to one scoped ADE child.

The caller owns AD_Lock(0), the subunit lock and the transaction. No DDL.
AD_EXTRASE_ASOCIERI reserves each IDFXE across ALL subunits of the current DC.
"""
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from .domain import DomainError, require
from . import service
from .bank_targets import reviewed_target, ensure_payment_row


SOURCE_FIELDS = ('IDFXE IDFXH IDOP CodAI DataBanca DataDoc NrDoc Referinta ReferintaDest '
                 'platitor_nume platitor_cui platitor_iban suma_debit suma_credit Explicatii').split()
SOURCE_SELECT = ','.join('e.`' + field + '`' for field in SOURCE_FIELDS)


def fingerprint(row):
    return hashlib.sha256(json.dumps({key: row.get(key) for key in SOURCE_FIELDS},
                                    sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def schema_error(error):
    if getattr(error, 'errno', None) in (1146, 1054):
        raise DomainError('BANK_SCHEMA_REQUIRED',
                          'Asocierea extraselor necesită instalarea manuală a SQL-ului AD_11_extrase_asocieri.sql '
                          'în baza unității.', 409) from error
    raise error


def date_filter(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError) as error:
        raise DomainError('DATE', 'Intervalul extraselor conține o dată invalidă.', 400) from error


def available(repo, args):
    start, end = date_filter(args.get('start')), date_filter(args.get('end'))
    require(not start or not end or start <= end, 'DATE', 'Data de început depășește data de sfârșit.', 400)
    cursor = args.get('before', '')
    require(not cursor or str(cursor).isdigit(), 'FILTER', 'Pagina extraselor este invalidă.', 400)
    search = (args.get('search') or '').strip()
    require(len(search) <= 255, 'FILTER', 'Textul căutat este prea lung.', 400)
    clauses = ['a.IDFXE IS NULL', 'e.suma_credit>0', 'COALESCE(e.suma_debit,0)=0']
    params = []
    period = args.get('period', '')
    if period:
        try:
            first = date.fromisoformat(period + '-01')
        except (ValueError, TypeError) as error:
            raise DomainError('FILTER', 'Luna extraselor este invalidă.', 400) from error
        following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        clauses.extend(['e.DataDoc>=%s', 'e.DataDoc<%s'])
        params.extend([first.isoformat(), following.isoformat()])
    for clause, value in [('e.DataDoc>=%s', start), ('e.DataDoc<=%s', end), ('e.IDFXE<%s', int(cursor) if cursor else None)]:
        if value is not None:
            clauses.append(clause)
            params.append(value)
    if len(search) >= 2:
        # The bank importer stores the segment before the first '-' as Referinta.
        # Exclude that leading reference from searches, keeping the source itself intact.
        explanation = ("CASE WHEN LOCATE('-',e.Explicatii)>0 "
                       "AND TRIM(SUBSTRING_INDEX(e.Explicatii,'-',1))=TRIM(e.Referinta) "
                       "THEN SUBSTRING(e.Explicatii,LOCATE('-',e.Explicatii)+1) "
                       "ELSE e.Explicatii END")
        # Literal, case-insensitive substring: 'gr' finds both 'Gr40' and 'Grădinița 40'.
        clauses.append(f"LOWER({explanation}) LIKE %s ESCAPE '='")
        params.append('%' + search.lower().replace('=', '==').replace('%', '=%').replace('_', '=_') + '%')
    try:
        rows = repo.query('SELECT ' + SOURCE_SELECT + ' FROM FX_Extrase e '
                          'LEFT JOIN AD_EXTRASE_ASOCIERI a ON a.IDFXE=e.IDFXE WHERE '
                          + ' AND '.join(clauses) + ' ORDER BY e.IDFXE DESC LIMIT 151', tuple(params))
        # AD_07 removed FelDoc; the requested correspondence restores it via manual SQL.
        repo.query('SELECT FelDoc FROM AD_AlteDoc LIMIT 0')
    except Exception as error:
        schema_error(error)
    return {'rows': [{**row, 'fingerprint': fingerprint(row)} for row in rows[:150]],
            'next': rows[149]['IDFXE'] if len(rows) > 150 else None}


def available_periods(repo):
    rows = repo.query('SELECT DISTINCT YEAR(DataDoc) AS year, MONTH(DataDoc) AS month FROM FX_Extrase '
                     'WHERE DataDoc IS NOT NULL AND YEAR(DataDoc)>0 AND MONTH(DataDoc) BETWEEN 1 AND 12 '
                     'AND suma_credit>0 AND COALESCE(suma_debit,0)=0 '
                     'ORDER BY year DESC, month DESC')
    return {'periods': [{'year': int(row['year']), 'month': int(row['month']),
                         'value': f"{row['year']:04d}-{row['month']:02d}"} for row in rows]}


def payer_history(repo, child_id):
    """Scoped payer history with a display-only bank derived as in K-BOT Parteneri."""
    from utils.database import get_kbot_comun_connection

    repo.get('Platitori', child_id)
    rows = repo.rows('Platitori_sub', IDP=child_id)
    bic = {}
    if any(row.get('Cont') for row in rows):
        connection = get_kbot_comun_connection()
        try:
            cursor = connection.cursor(dictionary=True, buffered=True)
            try:
                cursor.execute('SELECT Cod, Banca FROM BIC')
                bic = {str(row['Cod'] or '').strip().upper(): str(row['Banca'] or '').strip()
                       for row in cursor.fetchall() if row['Cod']}
            finally:
                cursor.close()
        finally:
            connection.close()
    for row in rows:
        # Match Parteneri's _bank_from_iban: remove whitespace, uppercase, take characters 5–8.
        iban = ''.join(str(row.get('Cont') or '').split()).upper()
        row['BancaCont'] = bic.get(iban[4:8], '') if len(iban) >= 8 else ''
    return rows


def amount_of(source):
    try:
        credit = Decimal(str(source.get('suma_credit') or 0))
        debit = Decimal(str(source.get('suma_debit') or 0))
    except InvalidOperation as error:
        raise DomainError('AMOUNT', 'Suma extrasului este invalidă.', 400) from error
    require(credit.is_finite() and debit.is_finite() and credit > 0 and debit == 0,
            'AMOUNT', 'Selectați o încasare cu sumă credit pozitivă și debit zero.', 400)
    # AD_Plati.Plata and existing calculations use Access Long. Never silently round bank money.
    require(credit == credit.to_integral_value() and credit <= 2147483647,
            'AMOUNT_PRECISION', 'Suma extrasului nu poate fi păstrată exact în câmpul Plata '
            'actual (număr întreg). Asocierea nu a fost salvată.', 400)
    return int(credit)


def default_explanation(repo, month_id):
    month = repo.get('LunaD', month_id)
    config = repo.receipt_config()
    configured = service.receipt_explanation(config, month) if config and config.get('Explicatie') else ''
    return configured.strip() or f"Contravaloare hrană pentru {service.MONTHS[month['Luna'] - 1]} {month['Anul']}"


def editable_source(source, body):
    """Corrections affect the ADE document / payer only; the K-BOT bank source is immutable."""
    fields = {'name': 'platitor_nume', 'cui': 'platitor_cui', 'iban': 'platitor_iban',
              'date': 'DataDoc', 'number': 'NrDoc', 'amount': 'suma_credit'}
    changes = body.get('values', {})
    require(isinstance(changes, dict) and set(changes) <= set(fields), 'FIELDS', 'Date de asociere invalide.', 400)
    result = dict(source)
    for key, value in changes.items():
        if key == 'amount':
            require(type(value) in (int, float), 'AMOUNT', 'Suma este invalidă.', 400)
        else:
            require(isinstance(value, str) and len(value) <= 255, 'TEXT', 'Text prea lung sau invalid.', 400)
            value = value.strip()
        result[fields[key]] = value
    return result


def associate(repo, db_name, body, email):
    for key in ('extract_id', 'payer_id'):
        require(type(body.get(key)) is int and body[key] > 0, 'REQUEST', 'Selecție incompletă sau invalidă.', 400)
    try:
        assigned = repo.query('SELECT IDFXE FROM AD_EXTRASE_ASOCIERI WHERE IDFXE=%s', (body['extract_id'],))
        require(not assigned, 'BANK_ALREADY_ASSIGNED',
                'Extrasul a fost deja asociat în această unitate. Reîncărcați lista.', 409)
        sources = repo.query('SELECT ' + SOURCE_SELECT + ' FROM FX_Extrase e WHERE e.IDFXE=%s FOR UPDATE',
                             (body['extract_id'],))
        repo.query('SELECT FelDoc FROM AD_AlteDoc LIMIT 0')
    except DomainError:
        raise
    except Exception as error:
        schema_error(error)
    require(bool(sources), 'NOT_FOUND', 'Rândul de extras nu mai există.', 404)
    original = sources[0]
    require(Decimal(str(original.get('suma_credit') or 0)) > 0
            and Decimal(str(original.get('suma_debit') or 0)) == 0, 'AMOUNT',
            'Rândul de extras nu reprezintă o încasare.', 400)
    require(body.get('fingerprint') == fingerprint(original), 'BANK_CHANGED',
            'Datele extrasului s-au schimbat. Reîncărcați lista înainte de asociere.', 409)
    source = editable_source(original, body)
    child, month, previous, attendance = reviewed_target(repo, body)
    service.temporal(repo, month['IDL'])
    day = service.document_date(str(source.get('DataDoc') or '')[:10], month)
    amount = amount_of(source)
    explanation = body.get('explanation')
    require(explanation is None or isinstance(explanation, str), 'EXPLANATION', 'Explicație invalidă.', 400)
    explanation = (explanation or '').strip() or default_explanation(repo, month['IDL'])
    require(len(explanation) <= 255, 'EXPLANATION', 'Explicația poate avea maximum 255 de caractere.', 400)
    name = str(source.get('platitor_nume') or '').strip()
    number = str(source.get('NrDoc') or '').strip()
    require(bool(name) and bool(number), 'BANK_DOCUMENT', 'Extrasul trebuie să aibă nume plătitor și număr document.', 400)
    attendance = ensure_payment_row(repo, child, month, attendance)
    # Preserve the selected parent's contacts / portal identity, not a guessed identity from CUI.
    values = {key: previous.get(key) for key in
              ('Adresa', 'CNP_Platitor', 'Banca', 'Telefon', 'EMail', 'TrimiteMail')}
    values.update(IDP=child['IDP'], Nume=name, CUI=source.get('platitor_cui'), Cont=source.get('platitor_iban'), Activ=True)
    payer = service.save_catalog(repo, 'Platitori_sub', {'id': None, 'values': values})
    document = service.emit_document(repo, db_name, {
        'kind': 'other', 'attendance_id': attendance['IDZ'], 'payer_id': payer['IDS'],
        'date': day, 'amount': amount, 'number': number,
        'explanation': explanation,
    })
    document = repo.update('AlteDoc', document, {'FelDoc': source.get('Referinta')})
    repo.execute('INSERT INTO AD_EXTRASE_ASOCIERI '
                 '(IDFXE,SUBUNIT_ID,IDP,IDS_ANTERIOR,IDS,IDZ,IDL,IDPL,IDA,SURSA_HASH,ASOCIAT_DE) '
                 'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                 (source['IDFXE'], repo.scope(), child['IDP'], previous['IDS'], payer['IDS'],
                  attendance['IDZ'], month['IDL'], document['IDPL'], document['IDA'], fingerprint(original), email))
    return {'extract_id': source['IDFXE'], 'payer': payer, 'document': document, 'subunit_id': repo.scope()}
