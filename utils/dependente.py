# utils/dependente.py
"""
«Who depends on this row?» -- one answer for every table that may point at a parent row (a classification, an account, ...).

A route that deletes a parent row asks `Dependencies.usage(cursor, key)` and refuses when the list it gets back is not empty. The list comes from TWO sources,
so a table added later needs no change in the delete route:

  1. FOREIGN KEYS, found by themselves: every table of the CURRENT database that has a foreign key to the parent column (information_schema). A future
     table with `FOREIGN KEY (IdClsfV) REFERENCES Clasificatii_Venituri (IdClsfV)` is covered the day it exists, with no line of code.
  2. SUBSCRIPTIONS, for the tables that point at the parent WITHOUT a foreign key (an old table, a column that cannot have one): one line next to the
     parent's other subscriptions, `DEPENDENCIES.register("FX_Extrase_H", "IdClsfV", "extrase de cont")`. The label is what the operator reads.

A subscribed table that is missing in a database (an old one not synchronised yet) counts as «not used there», never as an error. A table found by foreign key and
not subscribed is shown under its own name; subscribing it only gives it a readable label.

The tables and columns come from the code (subscriptions) and from the server's own catalogue (foreign keys), never from a request.
"""
import logging

import mysql.connector
from mysql.connector import errorcode

logger = logging.getLogger(__name__)

_SQL_FOREIGN_KEYS = (
    "SELECT TABLE_NAME AS T, COLUMN_NAME AS C FROM information_schema.KEY_COLUMN_USAGE "
    " WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_SCHEMA = DATABASE() "
    "   AND REFERENCED_TABLE_NAME = %s AND REFERENCED_COLUMN_NAME = %s "
    " ORDER BY TABLE_NAME, COLUMN_NAME"
)


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


class Dependencies:
    """The dependents of one parent column (`parent_table`.`parent_key`)."""

    def __init__(self, parent_table: str, parent_key: str):
        self._parent_table = parent_table
        self._parent_key = parent_key
        self._subscribed = []  # [(table, column, label)] in subscription order

    def register(self, table: str, column: str, label: str) -> None:
        """Subscribes a table whose `column` points at the parent key; `label` is the Romanian name the refusal message uses."""
        if not table or not column or not label:
            raise ValueError("table, column and label are all required")
        if self._find(table, column) is not None:
            raise ValueError(f"{table}.{column} is already subscribed to {self._parent_table}.{self._parent_key}")
        self._subscribed.append((table, column, label))

    def _find(self, table: str, column: str):
        for entry in self._subscribed:
            if entry[0].lower() == table.lower() and entry[1].lower() == column.lower():
                return entry
        return None

    def _targets(self, cursor) -> list:
        """Subscribed tables first (their labels), then the foreign keys nobody subscribed, under the table's own name."""
        targets = list(self._subscribed)
        cursor.execute(_SQL_FOREIGN_KEYS, (self._parent_table, self._parent_key))
        for row in cursor.fetchall():
            table, column = row["T"], row["C"]
            if self._find(table, column) is None:
                targets.append((table, column, table))
        return targets

    def usage(self, cursor, key) -> list:
        """
        Romanian fragments «label (n)» for every dependent that has at least one row pointing at `key`; empty = nothing depends on it.
        `cursor` must be a dictionary cursor of the database that holds the parent table.
        """
        used = []
        for table, column, label in self._targets(cursor):
            try:
                cursor.execute(f"SELECT COUNT(*) AS N FROM {_quote(table)} WHERE {_quote(column)} = %s", (key,))
            except mysql.connector.Error as e:
                if e.errno == errorcode.ER_NO_SUCH_TABLE:
                    continue
                logger.error("[dependente] %s.%s: %s", table, column, e)
                raise
            n = int(cursor.fetchone()["N"])
            if n:
                used.append(f"{label} ({n})")
        return used
