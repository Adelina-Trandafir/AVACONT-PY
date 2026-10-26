"""Bringing bank statements into the unit database: manual upload and the mailbox (ADECHIT, SLICE-AD13-01).

Both ways end in the SAME reader K-BOT uses (routes.forexe.extrase.importa_extrase), so a statement
loaded here, by the FOREXE robot or from the mailbox is one and the same row set in FX_Extrase_* and the
existing association window (bank_associations) sees it with no further step.

Three layers keep a statement from being stored twice:
  1. the SHA-256 of the PDF (AD_EXTRASE_INCARCARI) - the very same file;
  2. file name + statement date, then every operation key (DataDoc, NrDoc, CUI, debit, credit) - what
     the FOREXE import has always used;
  3. a file whose operations ALL exist already is a duplicate even under another name.

Connections are opened here, one short transaction per call. The ADE repository (AD_Lock + subunit lock)
is deliberately NOT used: the unit-wide lock must not be held while a mailbox answers slowly, and these
rows belong to the whole unit, not to a subunit. Concurrent imports in the same unit queue on a
named lock instead.

The mailbox password is encrypted with Fernet (routes.efactura.cripto) under ADE_MAIL_KEY, or
EF_TOKEN_KEY when the server has only that one. It never leaves this module in clear text: the settings
view says only whether a password is saved.
"""
import hashlib
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from .domain import DomainError, require
from . import extras_mail, extras_pdf

logger = logging.getLogger(__name__)

MAX_FILES = 40
MAX_FILE_BYTES = 12 * 1024 * 1024
HISTORY_ROWS = 200
DEFAULT_FOLDER = 'INBOX'
DEFAULT_NAME_PART = 'ExtrasEP'
FIRST_RUN_DAYS = 30
SCHEMA_ERRORS = (1146, 1054)
SOURCES = {'manual': 'manual', 'email': 'e-mail'}

_INSERT_LOG = ('INSERT INTO AD_EXTRASE_INCARCARI (SURSA,FISIER,SHA256,STARE,MESAJ,RANDURI,IDEXF,'
               'EMAIL_UID,EMAIL_DE,EMAIL_SUBIECT,EMAIL_DATA,INCARCAT_DE) '
               'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)')


def _schema_error(error):
    if getattr(error, 'errno', None) in SCHEMA_ERRORS:
        raise DomainError('EXTRAS_SCHEMA_REQUIRED',
                          'Încărcarea extraselor necesită instalarea manuală a SQL-ului '
                          'AD_14_extrase_incarcare.sql în baza unității.', 409) from error
    raise error


@contextmanager
def _connection(db_name):
    from utils.database import get_kbot_connection
    connection = get_kbot_connection(db_name)
    try:
        yield connection
    finally:
        connection.close()


def _rows(connection, sql, params=()):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def _bmp(text):
    """The columns are utf8mb3: characters outside the basic plane (emoji in a subject) cannot be stored."""
    return ''.join(char for char in (text or '') if ord(char) < 0x10000)


def _plain(row):
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else value for key, value in row.items()}


# ---- password -------------------------------------------------------------------------------------------------

def _key():
    import config    # the real one exists only on the server; read, never written
    for name in ('ADE_MAIL_KEY', 'EF_TOKEN_KEY'):
        value = str(os.environ.get(name) or getattr(config, name, '') or '').strip()
        if value:
            return value
    raise DomainError('MAIL_KEY', 'Serverul nu are cheia de criptare pentru parola e-mailului (ADE_MAIL_KEY). '
                      'Cereți administratorului serverului să o configureze.', 503)


def _encrypt(password):
    from routes.efactura import cripto
    try:
        return cripto.encrypt(password, _key())
    except cripto.CriptoError as error:
        raise DomainError('MAIL_KEY', str(error), 503) from error


def _decrypt(blob):
    from routes.efactura import cripto
    try:
        return cripto.decrypt(blob, _key())
    except cripto.CriptoError as error:
        raise DomainError('MAIL_KEY', 'Parola salvată nu mai poate fi citită (cheia serverului s-a schimbat). '
                          'Introduceți din nou parola în setările e-mailului.', 409) from error


# ---- mailbox settings -----------------------------------------------------------------------------------------

def _saved(connection):
    try:
        rows = _rows(connection, 'SELECT * FROM AD_EXTRASE_EMAIL WHERE ID=1')
    except Exception as error:
        _schema_error(error)
    return rows[0] if rows else None


def settings_view(db_name):
    """The saved mailbox settings without the password, or {'configured': False}."""
    with _connection(db_name) as connection:
        row = _saved(connection)
        if row is None:
            return {'configured': False, 'folder': DEFAULT_FOLDER, 'name_part': DEFAULT_NAME_PART,
                    'since': (date.today() - timedelta(days=FIRST_RUN_DAYS)).isoformat()}
        return {'configured': True, 'host': row['HOST'], 'user': row['UTILIZATOR'], 'has_password': bool(row['PAROLA']),
                'folder': row['DOSAR'], 'sender': row['EXPEDITOR'] or '', 'name_part': row['NUME_ATASAMENT'],
                'since': row['DE_LA_DATA'].isoformat(), 'last_check': _plain(row)['ULTIMA_VERIFICARE'],
                'last_result': row['ULTIMUL_REZULTAT'] or '', 'changed_by': row['MODIFICAT_DE']}


def _text(body, key, label, limit, required=True, default=''):
    value = body.get(key, default)
    require(value is None or isinstance(value, str), 'MAIL_SETTINGS', f'{label}: valoare invalidă.', 400)
    value = (value or default).strip()
    require(not required or bool(value), 'MAIL_SETTINGS', f'{label} este obligatoriu.', 400)
    require(len(value) <= limit, 'MAIL_SETTINGS', f'{label} este prea lung.', 400)
    return value


def save_settings(db_name, body, email):
    require(isinstance(body, dict), 'MAIL_SETTINGS', 'Cerere invalidă.', 400)
    host = _text(body, 'host', 'Serverul de e-mail', 253).lower()
    user = _text(body, 'user', 'Adresa de e-mail', 255)
    folder = _text(body, 'folder', 'Dosarul', 255, default=DEFAULT_FOLDER)
    sender = _text(body, 'sender', 'Expeditorul', 255, required=False)
    name_part = _text(body, 'name_part', 'Numele atașamentului', 100, default=DEFAULT_NAME_PART)
    try:
        since = date.fromisoformat(str(body.get('since') or ''))
    except ValueError as error:
        raise DomainError('MAIL_SETTINGS', 'Data de la care se citesc mesajele este invalidă.', 400) from error
    require(since <= date.today(), 'MAIL_SETTINGS', 'Data de început nu poate fi în viitor.', 400)
    password = body.get('password')
    require(password is None or isinstance(password, str) and len(password) <= 255,
            'MAIL_SETTINGS', 'Parola este invalidă.', 400)
    extras_mail.check_host(host)
    if sender:
        require(sender.isascii(), 'MAIL_SETTINGS', 'Expeditorul trebuie să conțină doar litere ASCII.', 400)
    with _connection(db_name) as connection:
        row = _saved(connection)
        # The stored password goes only to the server and account it was saved for.
        moved = row is None or (row['HOST'], row['UTILIZATOR']) != (host, user)
        require(bool(password) or not moved, 'MAIL_PASSWORD',
                'Introduceți parola contului: ați schimbat serverul sau adresa de e-mail.', 400)
        blob = _encrypt(password) if password else row['PAROLA']
        restart = row is None or (row['HOST'], row['UTILIZATOR'], row['DOSAR'], row['EXPEDITOR'] or '',
                                  row['DE_LA_DATA']) != (host, user, folder, sender, since)
        cursor = connection.cursor()
        try:
            cursor.execute(
                'INSERT INTO AD_EXTRASE_EMAIL (ID,HOST,UTILIZATOR,PAROLA,DOSAR,EXPEDITOR,NUME_ATASAMENT,DE_LA_DATA,'
                'MODIFICAT_DE,MODIFICAT_LA) VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) '
                'ON DUPLICATE KEY UPDATE HOST=VALUES(HOST),UTILIZATOR=VALUES(UTILIZATOR),PAROLA=VALUES(PAROLA),'
                'DOSAR=VALUES(DOSAR),EXPEDITOR=VALUES(EXPEDITOR),NUME_ATASAMENT=VALUES(NUME_ATASAMENT),'
                'DE_LA_DATA=VALUES(DE_LA_DATA),MODIFICAT_DE=VALUES(MODIFICAT_DE),MODIFICAT_LA=VALUES(MODIFICAT_LA)',
                (host, user, blob, folder, sender or None, name_part, since, email))
            if restart:
                # Another account / folder / start date: the saved place in the old mailbox means nothing.
                cursor.execute('UPDATE AD_EXTRASE_EMAIL SET UIDVALIDITY=NULL,ULTIMUL_UID=NULL WHERE ID=1')
        finally:
            cursor.close()
        connection.commit()
    logger.info('ADE extras mailbox settings saved db=%s by=%s', db_name, email)
    return settings_view(db_name)


def _mail_settings(row):
    return {'host': row['HOST'], 'user': row['UTILIZATOR'], 'folder': row['DOSAR'], 'sender': row['EXPEDITOR'] or '',
            'name_part': row['NUME_ATASAMENT'], 'since': row['DE_LA_DATA'],
            'uidvalidity': row['UIDVALIDITY'], 'last_uid': row['ULTIMUL_UID']}


def _configured_row(connection):
    row = _saved(connection)
    require(row is not None and bool(row['PAROLA']), 'MAIL_NOT_CONFIGURED',
            'Căsuța de e-mail nu este configurată. Completați setările e-mailului.', 409)
    return row


def test_mailbox(db_name):
    with _connection(db_name) as connection:
        row = _configured_row(connection)
    password = _decrypt(row['PAROLA'])
    count = extras_mail.test_login(_mail_settings(row), password)
    return {'message': f'Conexiune reușită. Dosarul «{row["DOSAR"]}» conține {count} mesaje.'}


# ---- ingestion ------------------------------------------------------------------------------------------------

@contextmanager
def _import_lock(connection, db_name):
    """Imports of one unit run one at a time; the transaction starts only after the lock is held, so it sees
    what the import that held the lock before it has committed."""
    name = 'ade_extras:' + db_name[:50]
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT GET_LOCK(%s, 60)', (name,))
        require(cursor.fetchone()[0] == 1, 'BUSY', 'Un alt import de extrase este în curs. Reîncercați peste puțin.', 503)
        connection.commit()
        connection.start_transaction()
        try:
            yield
        finally:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (name,))
            cursor.fetchall()
    finally:
        cursor.close()


def _outcome(name, state, message, rows=0):
    return {'fisier': name, 'stare': state, 'randuri': rows, 'mesaj': message}


def ingest(connection, db_name, files, source, email, after=None):
    """Store `files` ([{name, data, file_date, mail: {uid, sender, subject, date} | None}]) in one transaction.

    `after(cursor, outcomes)` runs in the same transaction, after the files (the mailbox uses it to move its saved
    place only together with the statements it found). Returns one outcome per file, in order."""
    from routes.forexe.extrase import importa_extrase
    outcomes = [None] * len(files)
    hashes = [hashlib.sha256(item['data']).hexdigest() for item in files]
    pending = []
    with _import_lock(connection, db_name):
        try:
            seen = {row['SHA256'] for row in _rows(
                connection, "SELECT DISTINCT SHA256 FROM AD_EXTRASE_INCARCARI WHERE STARE IN ('importat','duplicat') "
                            "AND SHA256 IN (" + ','.join(['%s'] * len(hashes)) + ')', tuple(hashes))} if hashes else set()
        except Exception as error:
            _schema_error(error)
        for index, item in enumerate(files):
            name = item['name']
            if hashes[index] in seen or any(hashes[index] == hashes[other] for other in range(index)):
                outcomes[index] = _outcome(name, 'duplicat', 'Același fișier a mai fost încărcat.')
                continue
            xml, reason = extras_pdf.extract_xml(item['data'])
            if xml is None:
                outcomes[index] = _outcome(name, 'fara_xml', reason)
                continue
            produced = extras_pdf.date_from_name(name) or item.get('file_date')
            pending.append((index, {'PdfFisier': name, 'DataFisier': extras_pdf.date_text(produced), 'XmlContent': xml,
                                    'CaleLocala': f'ADECHIT/{SOURCES[source]}'}))
        warnings = []
        ids = {}
        if pending:
            cursor = connection.cursor()
            try:
                result = importa_extrase(cursor, [entry for _, entry in pending], sari_continut_cunoscut=True)
            finally:
                cursor.close()
            warnings = result['avertismente']
            for (index, _), outcome in zip(pending, result['rezultate']):
                outcomes[index] = _outcome(files[index]['name'], outcome['stare'], outcome['mesaj'], outcome['randuri'])
                ids[index] = outcome.get('idexf')
        cursor = connection.cursor()
        try:
            for index, item in enumerate(files):
                mail = item.get('mail') or {}
                outcome = outcomes[index]
                cursor.execute(_INSERT_LOG, (SOURCES[source], _bmp(item['name'])[:255], hashes[index], outcome['stare'],
                                             _bmp(outcome['mesaj'])[:500], outcome['randuri'], ids.get(index),
                                             mail.get('uid'), _bmp(mail.get('sender')) or None,
                                             _bmp(mail.get('subject')) or None, mail.get('date'), email))
            if after is not None:
                after(cursor, outcomes)
        finally:
            cursor.close()
        connection.commit()
    return outcomes, warnings


def _summary(outcomes):
    count = {state: sum(1 for item in outcomes if item['stare'] == state) for state in
             ('importat', 'duplicat', 'fara_xml', 'invalid')}
    return {'received': len(outcomes), 'imported': count['importat'], 'duplicates': count['duplicat'],
            'unreadable': count['fara_xml'] + count['invalid'],
            'rows': sum(item['randuri'] for item in outcomes)}


def upload(db_name, uploads, email):
    """`uploads`: [(file name, bytes)] from the page."""
    require(0 < len(uploads) <= MAX_FILES, 'UPLOAD', f'Trimiteți între 1 și {MAX_FILES} fișiere deodată.', 400)
    files = []
    for name, data in uploads:
        name = os.path.basename((name or '').replace('\\', '/')).strip()
        require(bool(name) and len(data) <= MAX_FILE_BYTES, 'UPLOAD',
                f'Fișierul «{name or "fără nume"}» este gol sau depășește {MAX_FILE_BYTES // (1024 * 1024)} MB.', 400)
        files.append({'name': name, 'data': data, 'file_date': None})
    with _connection(db_name) as connection:
        try:
            outcomes, warnings = ingest(connection, db_name, files, 'manual', email)
        except Exception:
            connection.rollback()
            raise
    logger.info('ADE extras upload db=%s by=%s files=%s', db_name, email, len(files))
    return {'files': outcomes, 'summary': _summary(outcomes), 'warnings': warnings}


def fetch_mailbox(db_name, email):
    with _connection(db_name) as connection:
        row = _configured_row(connection)
    password = _decrypt(row['PAROLA'])
    try:
        found = extras_mail.fetch_new(_mail_settings(row), password)
    except DomainError as error:
        _remember_check(db_name, error.args[0])
        raise
    files = [{'name': item['file'], 'data': item['data'], 'file_date': item['mail_date'],
              'mail': {'uid': item['uid'], 'sender': item['sender'], 'subject': item['subject'], 'date': item['mail_date']}}
             for item in found['files']]
    outcomes, warnings = [], []
    with _connection(db_name) as connection:
        def remember(cursor, outcomes):
            summary = _summary(outcomes)
            text = (f"{found['scanned']} mesaje noi verificate; {summary['received']} extrase găsite, "
                    f"{summary['imported']} importate, {summary['duplicates']} deja existente"
                    + (f", {summary['unreadable']} nelizibile" if summary['unreadable'] else '')
                    + ('. Mai sunt mesaje de verificat.' if found['more'] else '.'))
            cursor.execute('UPDATE AD_EXTRASE_EMAIL SET UIDVALIDITY=%s,ULTIMUL_UID=%s,ULTIMA_VERIFICARE=NOW(),'
                           'ULTIMUL_REZULTAT=%s WHERE ID=1', (found['uidvalidity'], found['last_uid'], text[:500]))
        try:
            outcomes, warnings = ingest(connection, db_name, files, 'email', email, after=remember)
        except Exception:
            connection.rollback()
            raise
    logger.info('ADE extras mailbox db=%s by=%s scanned=%s files=%s', db_name, email, found['scanned'], len(files))
    return {'files': outcomes, 'summary': _summary(outcomes), 'warnings': warnings,
            'scanned': found['scanned'], 'more': found['more']}


def _remember_check(db_name, text):
    """Best effort: the settings page shows why the last check failed. Never hides the original error."""
    try:
        with _connection(db_name) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute('UPDATE AD_EXTRASE_EMAIL SET ULTIMA_VERIFICARE=NOW(),ULTIMUL_REZULTAT=%s WHERE ID=1',
                               (('Verificare eșuată: ' + str(text))[:500],))
            finally:
                cursor.close()
            connection.commit()
    except Exception:
        logger.exception('ADE extras mailbox: last result not saved')


def history(db_name):
    with _connection(db_name) as connection:
        try:
            rows = _rows(connection, 'SELECT ID,SURSA,FISIER,STARE,MESAJ,RANDURI,EMAIL_DE,EMAIL_DATA,INCARCAT_DE,INCARCAT_LA '
                                     'FROM AD_EXTRASE_INCARCARI ORDER BY ID DESC LIMIT %s', (HISTORY_ROWS,))
        except Exception as error:
            _schema_error(error)
    return {'rows': [_plain(row) for row in rows]}
