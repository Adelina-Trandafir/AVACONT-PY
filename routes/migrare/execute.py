# routes/migrare/execute.py
# -----------------------------------------------------------------------------
# The write pass. Runs only after an analysis of the SAME file said it may.
#
# UPSERT everywhere: `ON DUPLICATE KEY UPDATE <every column> = VALUES(...)`.
# A row that is already on the server is BROUGHT UP TO DATE with the one in the
# Access file, not left as it was -- the Access file is the source of truth of
# the migration, and a half-migrated row that never gets corrected is worse than
# no row at all. Deliberately not `INSERT IGNORE`, which would also degrade type
# errors, truncations and constraint violations to warnings, i.e. swallow real
# failures.
#
# The primary-key columns stay OUT of the UPDATE list: they are what identifies
# the row, and assigning them to themselves is noise. Under this form MariaDB
# reports rowcount 1 for an inserted row, 2 for one it actually changed and 0
# for one that was already identical, so the three counts are exact.
#
# The Access primary keys are copied VERBATIM: none of the sixteen tables is
# AUTO_INCREMENT on MariaDB, and the intra-family FK columns (IDRH, IDRR, IDRZ,
# IDEXF, IDR, IDH ...) only stay valid if the ids do.
# -----------------------------------------------------------------------------

import binascii
import logging

from . import accdb, parser, tables, validate

# The driver's OWN escaping, so the SQL written into the dump folder is not a
# hand-rolled guess at what MariaDB received. `MySQLConverter` is pure Python and
# needs no connection, so it works whatever connection type the deployment uses
# (the server runs the C extension, whose connection does not expose a converter
# the same way). Guarded like `storage.config`: the pure modules stay importable
# on a workstation without the driver, and the write path fails loudly there.
try:
    from mysql.connector.conversion import MySQLConverter
except ImportError:                                  # pragma: no cover - off-host
    MySQLConverter = None

logger = logging.getLogger(__name__)

# Cate randuri intr-un singur executemany.
BATCH_ROWS = 500

# Cate nume de coloana scriem in jurnal inainte de „… si inca N".
MAX_LOGGED_COLUMNS = 30

_CONVERTER = MySQLConverter() if MySQLConverter is not None else None


class ExecuteError(Exception):
    """Scrierea nu poate continua — mesaj in romana."""


def run(conn, db_name, fx_path, plan, report, force, only=None, replace=False,
        progress=None, dump=None):
    """
    Write the rows that passed the analysis.

    `plan` is the SAME UnitPlan the analysis used, not a fresh one: otherwise the
    selection could shift between measuring and writing.

    `only` are the tables the operator ticked, in the operator's order — that
    order IS the write order. They must be among the ANALYSED ones -- an
    unmeasured table is not written.

    `report` is the Report of the analysis of the same file: the foreign-key
    values missing on the target come from there, so the server is not asked
    twice — and the CHOSEN COLUMNS come from there too, so the write uses
    exactly what the analysis measured.

    force=False -> any finding stops the write (the caller should not have got
                   here; we check anyway rather than trusting the UI).
    force=True  -> the offending rows are SKIPPED and counted; blocking findings
                   still stop the write here too, not only in the UI.

    replace=True -> «Inlocuieste tot pe server»: the chosen tables are EMPTIED
                    first (children before parents, i.e. the reverse of the
                    write order), then filled from the file — everything in ONE
                    transaction, committed only at the very end. Any error rolls
                    the whole thing back: no half-deleted, half-written state
                    survives.

    `dump` is an optional `dump.SqlDump`: it records on disk the statements this
    run sent. None means record nothing, which is what every caller that only
    wants the write does. It never influences the migration -- see dump.py.
    """
    def say(msg):
        logger.info("migrare/scriere: %s", msg)
        if progress:
            progress(msg)

    if report.has_blocking():
        raise ExecuteError(
            "Analiza a găsit probleme blocante (tip, dimensiune, coloană sau tabel "
            "lipsă). Scrierea nu pornește nici forțat — acelea strică date.")
    if not force and not report.is_clean():
        raise ExecuteError(
            "Analiza a găsit probleme de integritate. Pornirea normală nu este "
            "permisă; folosiți «Forțează rularea» dacă acceptați ca rândurile "
            "vinovate să fie sărite.")

    chosen = tables.selected(only)
    unmeasured = [t.name for t in chosen if report.tables and t.name not in report.tables]
    if unmeasured:
        raise ExecuteError(
            "Tabelele %s nu au fost analizate, deci nu se pot scrie. Rulați din nou "
            "analiza cu ele bifate." % ", ".join(unmeasured))

    schema = validate.TargetSchema(conn, db_name, [t.name for t in chosen])
    chosen_names = [t.name for t in chosen]
    # The same rule the analysis reported, checked again here on the order THIS
    # request sent: the analysis measured an arrangement, and nothing else stops
    # a different one arriving at `rulare`. A foreign key needs the referenced
    # row present at INSERT time, so a child before its parent cannot succeed.
    disorder = validate.order_findings(schema, chosen_names, db_name)
    if disorder:
        raise ExecuteError(" ".join(
            validate.order_message(*pair) for pair in disorder))
    # A table absent from the target is SKIPPED when nothing ticked depends on
    # it (the analysis applied the same rule); it stops the run only when a
    # ticked table's foreign key points at it. Writing cannot create tables.
    absent = []
    for table in chosen:
        if schema.has(table.name):
            continue
        dependents = validate.missing_table_dependents(schema, chosen_names,
                                                       table.name, db_name)
        if dependents:
            raise ExecuteError(
                "Tabelul «%s» lipsește din baza «%s», iar %s arată spre el prin "
                "cheie străină. Migrarea nu creează tabele."
                % (table.name, db_name, ", ".join(dependents)))
        absent.append(table.name)
    chosen = [t for t in chosen if t.name not in absent]

    totals = {}
    current = None
    if dump is not None:
        dump.info([t.name for t in chosen])
    try:
        if replace:
            say("Mod «Înlocuiește tot»: se golesc întâi tabelele alese, într-o "
                "singură tranzacție — la orice eroare totul se întoarce la loc.")
            _empty_tables(conn, db_name, [t.name for t in reversed(chosen)], say,
                          dump=dump)

        for name in absent:
            say("«%s» lipsește din baza «%s» și niciun tabel bifat nu depinde "
                "de el — sărit." % (name, db_name))

        for table in chosen:
            current = table.name
            stats = {"citite": 0, "ale_unitatii": 0, "scrise": 0,
                     "actualizate": 0, "neschimbate": 0, "sarite": 0}
            totals[table.name] = stats

            target_columns = schema.columns[table.name]
            pk_columns = schema.primary_key.get(table.name) or [table.primary_key]
            # The columns MariaDB will not let out of the INSERT. They are what
            # an «(nu se scrie)» correlation may NOT delete, so they are resolved
            # before the rename map, and from the TARGET alone.
            protected = validate.required_columns_of(
                target_columns, schema.primary_key.get(table.name))
            # The SAME correlations the analysis measured against -- they travel
            # on the report, not in the run's own request.
            rename = validate.column_rename_map(table.name, target_columns,
                                                report.mappings, protected)
            selector = plan.selector_for(table)
            chosen_cols = validate.chosen_columns_of(table.name, report.columns,
                                                    pk_columns, rename)
            seen_keys = set()
            missing = dict((column, values)
                           for (t, column), values in report.missing_fk.items()
                           if t == table.name)
            # Orphan values on columns that ACCEPT NULL: the row is written and
            # only the link is lost. Measured by the analysis, applied here --
            # the same two dictionaries, and they never overlap.
            nullable = dict((column, values)
                            for (t, column), values in report.null_fk.items()
                            if t == table.name)
            nulled = {}

            # Only the columns BOTH sides have -- correlated by `rename` (the
            # by-name match, plus the operator's «Corelatii coloane») -- narrowed
            # to what he ticked, and written under the TARGET's exact spelling. A
            # column that stays out keeps the target's default; one that exists
            # only in Access was either unticked (fine) or already reported as
            # missing by the analysis, so it never gets here. Built by the SAME
            # function the analysis used -- see validate.insert_columns.
            access_columns = [c["nume"] for c in accdb.columns(fx_path, table.name)]
            try:
                columns, skipped = validate.insert_columns(
                    table.name, access_columns, rename, chosen_cols)
            except validate.ValidationError as exc:
                raise ExecuteError(str(exc))
            if not columns:
                raise ExecuteError(
                    "Tabelul «%s» nu are nicio coloană comună între Access și «%s»."
                    % (table.name, db_name))
            # The third check of the same rule (interface, analysis, here): a
            # required target column left out of the statement would come back as
            # MariaDB 1364 twenty tables later, about a column list nobody logged.
            lipsa = validate.missing_required(target_columns, columns)
            if lipsa:
                raise ExecuteError(
                    validate.required_columns_message(table.name, lipsa))

            say("«%s»: %d coloane — %s."
                % (table.name, len(columns), _name_list(columns)))
            if skipped:
                say("«%s»: coloane Access sărite — %s."
                    % (table.name, validate.describe_skipped(skipped)))
            if dump is not None:
                dump.open_table(table.name, columns, skipped)
            batch = []

            for row in accdb.iter_rows(fx_path, table.name):
                stats["citite"] += 1
                keep, reject = selector.keep(row)
                if reject:
                    stats["sarite"] += 1
                    continue
                if not keep:
                    # The row belongs to another unit in the same file: it is not
                    # this database's, and it does not count as skipped.
                    continue
                stats["ale_unitatii"] += 1

                # The row under the target's exact column names; the selector
                # above already read it with the Access ones.
                vrow = validate.with_target_names(row, rename)
                # Shaped for the target -- the SAME call the analysis made, so
                # what was measured is what travels. Every change is recorded in
                # _02_parsare.log.
                vrow, changes = parser.parse_row(vrow, target_columns)
                if dump is not None and changes:
                    dump.parsed(table.name, _row_key(vrow, pk_columns), changes)

                # BEFORE the row is judged: the column accepts NULL, so the
                # emptied value passes `check_value` on its own terms.
                emptied = _null_orphans(vrow, nullable)

                if _row_is_blocked(vrow, target_columns, pk_columns, seen_keys,
                                   missing, chosen_cols):
                    stats["sarite"] += 1
                    continue

                # Counted only now: a row skipped for some OTHER reason was not
                # «written with the column emptied», and the log line says it was.
                for column in emptied:
                    nulled[column] = nulled.get(column, 0) + 1

                batch.append(tuple(vrow.get(c) for c in columns))
                if len(batch) >= BATCH_ROWS:
                    _write(conn, db_name, table.name, columns, pk_columns, batch,
                           stats, dump=dump)
                    batch = []
                    if dump is not None:
                        dump.parse_flush()

            if batch:
                _write(conn, db_name, table.name, columns, pk_columns, batch,
                       stats, dump=dump)

            for column in sorted(nulled):
                line = ("«%s»: %d valori %s fără corespondent, scrise ca NULL."
                        % (table.name, nulled[column], column))
                say(line)
                if dump is not None:
                    # In _99_final.txt too: the folder has to say what the rows
                    # in the .sql files above it are missing.
                    dump.note(line)

            if not replace:
                # Un tabel incheiat ramane scris chiar daca urmatorul pica.
                conn.commit()
            if dump is not None:
                dump.close_table(stats)
            say("«%s»: %d scrise, %d actualizate, %d deja identice, %d sărite."
                % (table.name, stats["scrise"], stats["actualizate"],
                   stats["neschimbate"], stats["sarite"]))

        current = None
        if replace:
            conn.commit()
            say("Tranzacția «Înlocuiește tot» a fost încheiată (commit).")
        if dump is not None:
            dump.finish("COMMIT", totals)
    except Exception as exc:
        # The dump records what was ATTEMPTED; `_write` already named the exact
        # statement when the failure came from a row, and the dump ignores the
        # second call. Written before the rollback, deliberately: the file is the
        # only thing that survives it.
        if dump is not None:
            dump.failure(current, None, exc)
        # In replace mode NOTHING must survive a half-run; in normal mode only
        # the current table's uncommitted rows are open. Either way: rollback.
        try:
            conn.rollback()
        except Exception:
            logger.exception("migrare/scriere: rollback-ul însuși a eșuat")
        raise

    return totals


def _name_list(names):
    """Numele, plafonate: un tabel cu 60 de coloane nu inunda jurnalul."""
    if len(names) <= MAX_LOGGED_COLUMNS:
        return ", ".join(names)
    return "%s … și încă %d" % (", ".join(names[:MAX_LOGGED_COLUMNS]),
                                len(names) - MAX_LOGGED_COLUMNS)


def _empty_tables(conn, db_name, table_names, say, dump=None):
    """
    DELETE, deliberately not TRUNCATE: TRUNCATE is DDL and commits implicitly,
    which would make the rollback promise a lie. Children first (the caller
    passes the reverse of the write order), so the intra-family foreign keys do
    not object.
    """
    cur = conn.cursor()
    try:
        for name in table_names:
            statement = "DELETE FROM `%s`.`%s`" % (db_name, name)
            cur.execute(statement)
            if dump is not None:
                dump.delete(statement, cur.rowcount)
            say("«%s»: %d rânduri șterse (netrimis încă — se confirmă la final)."
                % (name, cur.rowcount))
    except Exception as exc:
        raise ExecuteError("Golirea tabelului a eșuat: %s" % exc)
    finally:
        cur.close()


def _null_orphans(row, nullable):
    """
    Empty every foreign-key value the analysis found no parent for, on the
    columns that accept NULL. The row is KEPT: only the link is lost.

    Returns the columns it emptied, so the caller can count them AFTER the row
    is known to survive -- a row skipped for another reason was not written with
    anything emptied.

    The values are matched the same way `_row_is_blocked` matches its own --
    as they came and as an int -- because that is how the analysis collected
    them.
    """
    emptied = []
    for column, bad_values in nullable.items():
        value = row.get(column)
        if value is None:
            continue
        if value in bad_values or validate.as_int(value) in bad_values:
            row[column] = None
            emptied.append(column)
    return emptied


def _row_is_blocked(row, target_columns, pk_columns, seen_keys, missing,
                    chosen_cols):
    """
    The same rules as the analysis, applied row by row. Deliberately re-evaluated
    here rather than read from a saved list of keys: the list could no longer match
    the file, and a mismatch would write exactly the wrong row.
    """
    for name, meta in target_columns.items():
        if meta["auto"] and name not in row:
            continue
        value = row.get(name)
        if chosen_cols is not None and name not in chosen_cols:
            # An unticked column is not written; the target sees NULL/default.
            value = None
        if validate.check_value(meta, value):
            return True

    pk_value = tuple(row.get(c) for c in pk_columns)
    if pk_value in seen_keys:
        return True
    seen_keys.add(pk_value)

    for column, bad_values in missing.items():
        value = row.get(column)
        if value is None:
            continue
        if value in bad_values or validate.as_int(value) in bad_values:
            return True

    return False


def _write(conn, db_name, table, columns, pk_columns, rows, stats, dump=None):
    """
    A row that does not exist is INSERTED; one that does is BROUGHT UP TO DATE
    from the Access file. The primary-key columns stay out of the update list --
    they identify the row.
    """
    quoted = ",".join("`%s`" % c for c in columns)
    placeholders = ",".join(["%s"] * len(columns))
    keys = set(c.lower() for c in pk_columns)
    updatable = [c for c in columns if c.lower() not in keys]
    if not updatable:
        # A table where nothing can be updated (every shared column is part of the
        # key): self-assignment, so a duplicate does not fail.
        updatable = [columns[0]]
    updates = ",".join("`%s` = VALUES(`%s`)" % (c, c) for c in updatable)

    sql = ("INSERT INTO `%s`.`%s` (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s"
           % (db_name, table, quoted, placeholders, updates))

    last = None
    cur = conn.cursor()
    try:
        for row in rows:
            if dump is not None:
                # BEFORE the execute, never after: the row that fails is the one
                # worth reading, and it only reaches the file if it is written
                # first.
                last = _statement_text(db_name, table, quoted, updates, columns,
                                       pk_columns, row, dump)
                dump.row(last)
            cur.execute(sql, row)
            # MariaDB: 1 = inserted, 2 = updated, 0 = already identical.
            if cur.rowcount == 1:
                stats["scrise"] += 1
            elif cur.rowcount == 2:
                stats["actualizate"] += 1
            else:
                stats["neschimbate"] += 1
        if dump is not None:
            # Per batch, not at the end: a killed job leaves on disk everything
            # up to the last completed batch.
            dump.flush()
    except Exception as exc:
        if dump is not None:
            dump.failure(table, last, exc)
        conn.rollback()
        raise ExecuteError("Scrierea în «%s» a eșuat: %s" % (table, exc))
    finally:
        cur.close()


def _statement_text(db_name, table, quoted, updates, columns, pk_columns, row,
                    dump):
    """
    The statement as SQL TEXT, for the dump folder. The driver sends parameters,
    not text, so this is a reconstruction -- an honest one, because the values go
    through the driver's own escaping (see `_literal`), but a reconstruction. The
    header of every dumped table file says so.
    """
    parts = []
    for name, value in zip(columns, row):
        text, ok = _literal(value)
        if not ok:
            dump.note("«%s».«%s»: valoare nereprezentabilă (%s) pe rândul cu "
                      "cheia %s — instrucțiunea trimisă nu e afectată, doar "
                      "consemnarea ei."
                      % (table, name, type(value).__name__,
                         _key_text(columns, pk_columns, row)))
        parts.append(text)
    return "\n".join([
        "INSERT INTO `%s`.`%s` (%s)" % (db_name, table, quoted),
        "VALUES (%s)" % ",".join(parts),
        "ON DUPLICATE KEY UPDATE %s;" % updates,
    ])


def _row_key(vrow, pk_columns):
    """The row's primary key, from a dict keyed by the target's column names."""
    return ", ".join("%s=%s" % (c, vrow.get(c)) for c in pk_columns)


def _key_text(columns, pk_columns, row):
    """The row's primary key, for a note that has to say WHICH row."""
    values = dict(zip(columns, row))
    return ", ".join("%s=%s" % (c, values.get(c)) for c in pk_columns) or "necunoscută"


def _literal(value):
    """
    The value as MariaDB would receive it, written as SQL text. Returns
    (text, ok); `ok` is False when nothing honest could be produced, and the
    caller records which column and row that was.

    The escaping is the DRIVER's own (`MySQLConverter`), so the reconstruction is
    not a hand-rolled guess. `bytes` are the one thing written by hand, as an
    `0x…` literal: the driver's quoting of them is raw octets, which have no
    business in a UTF-8 text file.
    """
    if value is None:
        return "NULL", True
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if not raw:
            return "''", True
        return "0x" + binascii.hexlify(raw).decode("ascii"), True
    if _CONVERTER is None:
        return "/* VALOARE NEREPREZENTABILĂ: driverul MySQL lipsește */", False
    try:
        out = _CONVERTER.quote(_CONVERTER.escape(_CONVERTER.to_mysql(value)))
    except Exception:
        # Not swallowed: the caller turns this into a line in _99_final.txt, and
        # the server log gets the trace. Inventing an escape here would be worse
        # than saying nothing.
        logger.exception("migrare/scriere: valoare nereprezentabilă în SQL (%s)",
                         type(value).__name__)
        return "/* VALOARE NEREPREZENTABILĂ: %s */" % type(value).__name__, False
    if isinstance(out, (bytes, bytearray)):
        out = bytes(out).decode("utf-8", "replace")
    return out, True
