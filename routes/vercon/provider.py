"""SLICE-VC06-01: the real context provider of VERCON, over the VC_* tables of one DC database.

It answers the one question `context.resolve` asks -- "what may this unit show?" -- with the tree
    [{id, name, gestiuni: [{id, name, type, years, closed_years, populated, occupied_by, zec, scara}]}]
read from VC_Subunitati / VC_Gestiuni / VC_Exercitii. Only active rows appear. On a DC where the VC_* tables do not
exist yet (schema not synchronised) the answer is an empty tree, which the page shows as "in pregatire".
The lock of a gestiune is NOT read here: the blueprint asks the lock service, so the two cannot disagree.
"""
import logging

import mysql.connector

from .connection import connection
from .context import SelectionInvalid

logger = logging.getLogger(__name__)

_NO_SUCH_TABLE = 1146


class SqlProvider:
    def __init__(self, connect=None):
        self._connect = connect or connection

    def tree(self, unit, email):
        try:
            with self._connect(unit) as conn:
                cur = conn.cursor(dictionary=True)
                cur.execute('SELECT SubunitId, Name FROM VC_Subunitati WHERE Active=1 ORDER BY Name')
                subunits = cur.fetchall()
                cur.execute('SELECT GestiuneId, SubunitId, Name, Type, Zec, Scara, Populated FROM VC_Gestiuni WHERE Active=1 ORDER BY Name')
                gestiuni = cur.fetchall()
                cur.execute('SELECT GestiuneId, An, Closed FROM VC_Exercitii ORDER BY An')
                exercitii = cur.fetchall()
        except mysql.connector.Error as error:
            if getattr(error, 'errno', None) == _NO_SUCH_TABLE:
                logger.warning('VERCON: tabelele VC_* nu exista in %s; arbore gol.', unit)
                return []
            raise
        years, closed = {}, {}
        for row in exercitii:
            years.setdefault(row['GestiuneId'], []).append(int(row['An']))
            if row['Closed']:
                closed.setdefault(row['GestiuneId'], []).append(int(row['An']))
        tree = []
        for sub in subunits:
            tree.append({
                'id': sub['SubunitId'], 'name': sub['Name'],
                'gestiuni': [{
                    'id': g['GestiuneId'], 'name': g['Name'], 'type': g['Type'],
                    'years': years.get(g['GestiuneId'], []), 'closed_years': closed.get(g['GestiuneId'], []),
                    'populated': bool(g['Populated']), 'occupied_by': None,
                    'zec': int(g['Zec']), 'scara': int(g['Scara']),
                } for g in gestiuni if g['SubunitId'] == sub['SubunitId']],
            })
        return tree

    def set_type(self, unit, subunit_id, gestiune_id, new_type):
        """The type changes only while the gestiune holds no data (the same rule the route checks first)."""
        with self._connect(unit) as conn:
            try:
                cur = conn.cursor()
                cur.execute('UPDATE VC_Gestiuni SET Type=%s, Version=Version+1 '
                            'WHERE SubunitId=%s AND GestiuneId=%s AND Populated=0', (new_type, subunit_id, gestiune_id))
                changed = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if not changed:
            raise SelectionInvalid('Tipul nu mai poate fi schimbat sau gestiunea nu mai există.')
