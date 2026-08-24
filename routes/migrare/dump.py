# routes/migrare/dump.py
# -----------------------------------------------------------------------------
# What SQL did this run send? One folder per unit database, one file per table,
# plain text, written as the migration goes.
#
# It is a RECORD. Nothing in the code ever reads it back, and it must never
# change what the migration does: every method here is guarded, and a failure to
# write disables the dump (with a full trace in the server log and a line in the
# operator's job log) instead of stopping the write.
#
# Two things about it that have to be said out loud, and are said again inside
# the files themselves:
#
#   * The driver sends PARAMETERS, not text. The statements here are
#     reconstructions -- faithful ones, because the values go through the
#     driver's own escaping (see execute._literal) -- but not a transcript of
#     the bytes on the wire.
#   * File writes are NOT part of the transaction. On a failed «Inlocuieste tot»
#     run, _99_final.txt says ROLLBACK and the .sql files above it describe work
#     that no longer exists in the database. That is the design: the folder
#     records what was ATTEMPTED.
#
# No pruning: the migration runs once per unit database, and the failed run is
# the only one worth reading.
# -----------------------------------------------------------------------------

import logging
import os
import time

from . import storage, validate

logger = logging.getLogger(__name__)


class SqlDump(object):
    """The statements of one write run, on disk. See the module comment."""

    def __init__(self, db_name, an, fx_path, job_id, replace, force,
                 progress=None):
        # Deliberately NOT guarded: a missing MIGRARE_SQL_DIR must stop the write
        # with the name of the key, not fall back to some other path.
        self.db_name = db_name
        self.an = an
        self.fx_path = fx_path
        self.job_id = job_id
        self.replace = replace
        self.force = force
        self._progress = progress
        self.disabled = False
        self._finished = False
        self._handle = None
        self._table = None
        self._notes = []
        # Jurnalul de parsare isi tine mainerul lui, deschis pe toata rularea:
        # conversiile vin amestecate de la toate tabelele, in ordinea scrierii.
        self._parse_handle = None
        self._parse_table = None
        self._parse_totals = {}
        self._parsed_count = 0
        self._ambiguous_count = 0
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        # The run subfolder is what keeps the failed run -- the interesting one --
        # from being overwritten by the next attempt.
        self.dir = os.path.join(storage.sql_dir(), db_name, "%s_scriere" % stamp)
        os.makedirs(self.dir, exist_ok=True)

    # --- ce scrie -------------------------------------------------------------

    def info(self, table_order):
        """_00_info.txt — o singura data, la inceput."""
        def body():
            lines = [
                "Bază:      %s" % self.db_name,
                "An:        %s" % (self.an or ""),
                "Fișier:    %s" % self.fx_path,
                "Lucrare:   %s" % self.job_id,
                "Pornit:    %s" % _now(),
                "Mod:       %s" % ("Înlocuiește tot" if self.replace
                                   else "Adaugă/actualizează"),
                "Forțat:    %s" % ("da" if self.force else "nu"),
                "Ordinea de scriere: %s" % ", ".join(table_order),
            ]
            self._write_file("_00_info.txt", "\n".join(lines) + "\n")
        self._guard("info", body)

    def delete(self, statement, rowcount):
        """_01_stergeri.sql — doar in «Inlocuieste tot», in ordinea rularii."""
        def body():
            self._append_file("_01_stergeri.sql",
                              "%s;   -- %d rânduri\n" % (statement, rowcount))
        self._guard("delete", body)

    def open_table(self, table, columns, skipped):
        """Deschide <table>.sql si ii scrie antetul."""
        def body():
            self._close_handle()
            self._table = table
            path = os.path.join(self.dir, "%s.sql" % table)
            self._handle = open(path, "a", encoding="utf-8", newline="\n")
            head = [
                "-- `%s`.`%s`" % (self.db_name, table),
                "-- lucrare %s  |  %s" % (self.job_id, _now()),
                "-- coloane (%d): %s" % (len(columns), ", ".join(columns)),
            ]
            if skipped:
                head.append("-- coloane Access sărite: %s"
                            % validate.describe_skipped(skipped))
            head += [
                "--",
                "-- RECONSTRUCȚIE: driverul trimite parametri, nu text. Valorile de mai jos sunt",
                "-- scrise cu aceeași funcție de escape pe care o folosește driverul, deci sunt",
                "-- fidele — dar fișierul nu este o transcriere a octeților de pe fir.",
                "",
                "",
            ]
            self._handle.write("\n".join(head))
            self._handle.flush()
        self._guard("open_table", body)

    def row(self, statement):
        """O instructiune, gata terminata cu ';'."""
        def body():
            if self._handle is None:
                return
            self._handle.write(statement + "\n")
        self._guard("row", body)

    def flush(self):
        """Chemat dupa fiecare lot: un proces omorat lasa tot ce s-a incheiat."""
        def body():
            if self._handle is not None:
                self._handle.flush()
        self._guard("flush", body)

    def close_table(self, stats):
        def body():
            if self._handle is not None:
                self._handle.write(
                    "\n-- %d scrise, %d actualizate, %d deja identice, %d sărite\n"
                    % (stats.get("scrise", 0), stats.get("actualizate", 0),
                       stats.get("neschimbate", 0), stats.get("sarite", 0)))
            self._close_handle()
        self._guard("close_table", body)

    def parsed(self, table, key, changes):
        """
        _02_parsare.log — fiecare valoare pe care parserul a schimbat-o, cu
        valoarea dinainte, cea de după și motivul.

        Numai ce s-a SCHIMBAT. O valoare care a trecut neatinsă n-are ce spune,
        iar scrisul tuturor celulelor ar face fișierul de neconsultat exact
        pentru cel care caută o conversie greșită. Câte au trecut neatinse se
        vede din numărul de rânduri scrise, care e în `_99_final.txt`.
        """
        def body():
            if not changes:
                return
            if self._parse_handle is None:
                path = os.path.join(self.dir, "_02_parsare.log")
                self._parse_handle = open(path, "a", encoding="utf-8",
                                          newline="\n")
                self._parse_handle.write(
                    "Conversii Access ▸ MariaDB — lucrare %s, %s\n"
                    "Se scrie DOAR ce s-a schimbat.\n\n" % (self.job_id, _now()))
            if table != self._parse_table:
                self._parse_handle.write("\n=== %s ===\n" % table)
                self._parse_table = table
            for change in changes:
                self._parsed_count += 1
                if change.ambiguous:
                    self._ambiguous_count += 1
                slot = (table, change.column)
                self._parse_totals[slot] = self._parse_totals.get(slot, 0) + 1
                self._parse_handle.write(
                    "%s | %s | %s\n" % (key or "—", change.column, change.note))
        self._guard("parsed", body)

    def parse_flush(self):
        def body():
            if self._parse_handle is not None:
                self._parse_handle.flush()
        self._guard("parse_flush", body)

    def _close_parse_handle(self):
        """Inchide jurnalul de parsare, dupa ce ii scrie totalurile."""
        if self._parse_handle is None:
            return
        try:
            self._parse_handle.write("\n\n--- Totaluri ---\n")
            self._parse_handle.write(
                "%d conversii, dintre care %d cu zi/lună ambiguă.\n"
                % (self._parsed_count, self._ambiguous_count))
            for (table, column), count in sorted(self._parse_totals.items()):
                self._parse_handle.write("  %s.%s: %d\n" % (table, column, count))
            self._parse_handle.close()
        finally:
            self._parse_handle = None

    def note(self, text):
        """O observatie care ajunge in _99_final.txt (valori nereprezentabile)."""
        def body():
            if len(self._notes) < 200:
                self._notes.append(text)
        self._guard("note", body)

    def finish(self, outcome, totals):
        """_99_final.txt, calea buna."""
        def body():
            self._close_handle()
            self._close_parse_handle()
            scrise = sum(s.get("scrise", 0) for s in totals.values())
            actualizate = sum(s.get("actualizate", 0) for s in totals.values())
            sarite = sum(s.get("sarite", 0) for s in totals.values())
            lines = [
                "Încheiat:  %s" % _now(),
                "Rezultat:  %s" % outcome,
                "Totaluri:  %d scrise, %d actualizate, %d sărite"
                % (scrise, actualizate, sarite),
                "Pe tabel:  %s" % ", ".join(
                    "%s %d/%d/%d" % (name, s.get("scrise", 0),
                                     s.get("actualizate", 0), s.get("sarite", 0))
                    for name, s in totals.items()),
                "Conversii: %d (vezi _02_parsare.log), %d cu zi/lună ambiguă"
                % (self._parsed_count, self._ambiguous_count),
            ]
            self._write_file("_99_final.txt",
                             "\n".join(lines + self._note_lines()) + "\n")
            self._finished = True
        self._guard("finish", body)

    def failure(self, table, statement, exc):
        """
        _99_final.txt, calea proasta — fisierul pentru care exista tot dosarul.

        Chemat de doua ori pe acelasi esec (o data din `_write`, care stie
        instructiunea, o data din `run`, care nu o stie): a doua oara nu face
        nimic, ca sa nu inlocuiasca instructiunea cu un mesaj mai sarac.
        """
        def body():
            if self._finished:
                return
            self._close_handle()
            self._close_parse_handle()
            lines = [
                "Încheiat:  %s" % _now(),
                "Rezultat:  ROLLBACK",
                "Tabel:     %s" % (table or "—"),
                "Eroare:    %s" % exc,
            ]
            if statement:
                lines += ["Ultima instrucțiune trimisă:", statement]
            self._write_file("_99_final.txt",
                             "\n".join(lines + self._note_lines()) + "\n")
            self._finished = True
        self._guard("failure", body)

    # --- masinaria ------------------------------------------------------------

    def _note_lines(self):
        if not self._notes:
            return []
        return ["", "Observații:"] + ["  " + n for n in self._notes]

    def _write_file(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)

    def _append_file(self, name, text):
        with open(os.path.join(self.dir, name), "a", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)

    def _close_handle(self):
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None
                self._table = None

    def _guard(self, where, body):
        """
        The dump NEVER breaks the migration. A write that fails is logged with a
        full trace, said once in the operator's job log, and turns the dump off
        for the rest of the run -- it is not re-raised and it is not hidden.
        """
        if self.disabled:
            return
        try:
            body()
        except Exception as exc:
            self.disabled = True
            logger.exception("migrare/consemnare SQL: %s a eșuat", where)
            self._handle = None
            self._parse_handle = None
            self._say("ATENȚIE: consemnarea SQL în «%s» a eșuat (%s). Migrarea "
                      "continuă; de la acest punct nu se mai scrie nimic în "
                      "dosarul de instrucțiuni." % (self.dir, exc))

    def _say(self, text):
        if self._progress is None:
            return
        try:
            self._progress(text)
        except Exception:
            logger.exception("migrare/consemnare SQL: jurnalul lucrării a refuzat "
                             "o linie")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
