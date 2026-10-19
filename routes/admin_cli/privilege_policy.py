"""MariaDB data privileges for human accounts; no DDL, routines or shared registry access.

SQL permissions are table-level. The API remains responsible for rows, subunits,
closed periods, signatures and validation of individual business operations.
"""
from routes.admin_cli.admin_common import AdminError, _q, _rows
from routes.schema_sync.schema_common import COMMON_DB, FORBIDDEN_TARGETS, UNIT_DB_RE

DML = frozenset({'SELECT', 'INSERT', 'UPDATE', 'DELETE'})
READ = frozenset({'SELECT'})

# No direct access to authentication challenges, access lists or import machinery
# from a module right. Those operations run through the service account in the API.
PRIVATE_TABLES = frozenset(name.upper() for name in (
    'AD_PortalParents', 'AD_PortalChallenges', 'AD_SubunitAccess',
    'AD_IdMap', 'AD_Imports', 'VC_Imports', 'VC_ImportSources', 'VC_IdMap',
    'VC_MigrationIssues',
))

AD_WRITES = {
    'attendance': 'AD_Prezenta AD_SS_Buget',
    'edit_closed': 'AD_Prezenta AD_SS_Buget',
    'catalog': ('AD_Grupe AD_Grupe_Educator AD_Platitori AD_Platitori_sub '
                'AD_Platitori_Istoric AD_ValoriTaxe AD_Prezenta AD_REPORT_CONFIG'),
    'transfer': 'AD_Platitori AD_Platitori_Istoric AD_Prezenta',
    'collect': ('AD_Prezenta AD_SS_Buget AD_Plati AD_Chitante AD_AlteDoc AD_Retur '
                'AD_ReceiptConfig AD_EXTRASE_ASOCIERI'),
    'cancel': 'AD_Plati AD_Chitante AD_AlteDoc AD_Retur AD_SS_Buget',
    'close': ('AD_LunaD AD_Prezenta AD_SS_Buget AD_Grupe AD_Plati AD_Retur '
              'AD_Compensare AD_Chitante AD_AlteDoc AD_Grupe_Educator AD_Platitori AD_Platitori_Istoric'),
    'reopen': ('AD_LunaD AD_Prezenta AD_SS_Buget AD_Plati AD_Retur '
               'AD_Compensare AD_Chitante AD_AlteDoc'),
    # Imports use the API / migrator service, never grant DDL or access-list writes.
    'import': 'AD_*',
}

VR_NOM = ('VC_Masini VC_Produse VC_ProduseP VC_ProduseM VC_Clienti '
          'VC_ComisieNIR VC_ComisieInventar VC_TVA VC_FeluriMancare VC_Retetar VC_OI_Produse')
VR_RECEIPT = ('VC_NIR VC_Intrari VC_Produse VC_ProduseP VC_ProduseM VC_OI_Produse '
              'VC_FACTURI VC_FACTURI_LINII VC_NIR_FACTURI VC_FACTURI_REPARTIZARI VC_NIR_CALCULE')
VR_WRITES = {
    'nomenclatoare': VR_NOM,
    'receptie': VR_RECEIPT,
    'storno': 'VC_NIR VC_IntrariStorno VC_OI_Produse',
    'consum': 'VC_Iesiri_H VC_Iesiri VC_LegBCLA',
    'retur': 'VC_IesiriRetur',
    'alimente': ('VC_LABC VC_ListaAlimente VC_LegBCLA VC_MicDejun VC_Dejun VC_Cina '
                 'VC_Iesiri_H VC_Iesiri'),
    'oi_predare': 'VC_OI_Predare',
    'oi_transfer': 'VC_OI_Transfer',
    'oi_casare': 'VC_OI_Casare',
    'mf': 'VC_ProduseM VC_ProduseP',
    'configure': 'VC_Settings VC_Numbering VC_Gestiuni VC_Exercitii',
    'close': 'VC_Exercitii VC_Settings',
    'reopen': 'VC_Exercitii VC_Settings',
    'import': 'VC_*',
}


def _operations(conn, username, db, section):
    if section == 'KB':
        sql = ("SELECT DISTINCT ro.Operatie FROM `%s`.`Unitati_Utilizatori` uu "
               "JOIN `%s`.`Roluri` r ON r.Cod=CONCAT('KB_',UPPER(uu.Rol)) AND r.Sectiune='KB' "
               "JOIN `%s`.`Roluri_Operatii` ro ON ro.IdRol=r.IdRol WHERE uu.UN=%%s AND uu.DC=%%s")
    else:
        sql = ("SELECT DISTINCT ro.Operatie FROM `%s`.`Utilizatori_Roluri` ur "
               "JOIN `%s`.`Roluri` r ON r.IdRol=ur.IdRol AND r.Sectiune='" + section + "' "
               "JOIN `%s`.`Roluri_Operatii` ro ON ro.IdRol=r.IdRol WHERE ur.UN=%%s AND ur.DC=%%s")
    return {r['Operatie'] for r in _rows(conn, sql % (COMMON_DB, COMMON_DB, COMMON_DB), (username, db))}


def _module_permissions(tables, prefix, operations, writes):
    unknown = operations - {'read', 'raport'} - writes.keys()
    if unknown:
        raise AdminError('Politica MariaDB nu cunoaste operatiile %s: %s.' % (prefix, ', '.join(sorted(unknown))))
    if 'read' not in operations:
        return {}
    allowed = [t for t in tables if t.upper().startswith(prefix) and t.upper() not in PRIVATE_TABLES]
    permissions = {t: set(READ) for t in allowed}
    for op in operations & writes.keys():
        names = {n.upper() for n in writes[op].split()}
        for table in allowed:
            if prefix + '*' in names or table.upper() in names:
                permissions[table].update(DML)
        # Infrastructure shared by all business writes. Audit is append-only here.
        for table in allowed:
            upper = table.upper()
            if upper in {prefix + 'OPERATIONS', prefix + 'AUDIT'}:
                permissions[table].add('INSERT')
            if upper in {prefix + 'LOCK', prefix + 'CONTEXTLOCK', prefix + 'NUMBERING'}:
                permissions[table].update(DML)
    return permissions


def build_plan(conn, privilege_conn, username):
    """Read ALL memberships, including tentative edits in conn's transaction.

    Grant concrete base tables only: no database patterns (_ and %), no definer
    views/routines, no automatic access to tables created in the future.
    """
    links = _rows(conn, 'SELECT DC, Rol FROM `%s`.`Unitati_Utilizatori` WHERE UN=%%s ORDER BY DC' % COMMON_DB,
                  (username,))
    registered = {r['DC'] for r in _rows(conn, 'SELECT DC FROM `%s`.`Unitati`' % COMMON_DB)}
    grants = []
    for link in links:
        db = link['DC']
        if db not in registered or db.upper() in {n.upper() for n in FORBIDDEN_TARGETS} or not UNIT_DB_RE.match(db):
            raise AdminError('Legatura contului cu baza %s nu este o unitate valida.' % db)
        tables = [r['TABLE_NAME'] for r in _rows(
            privilege_conn, "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (db,))]
        if not tables:
            raise AdminError('Baza %s nu exista sau tabelele ei nu sunt vizibile contului de drepturi.' % db)
        role = (link['Rol'] or '').upper()
        kb_ops = _operations(conn, username, db, 'KB')
        if role == 'CONTABIL':
            # All business data in assigned units; never server or schema administration.
            permissions = {t: set(DML) for t in tables}
        else:
            permissions = {}
            # K-BOT data is separate from the optional web modules. Finer KB writes
            # (including signatures) remain API operations through the service account.
            for table in tables:
                upper = table.upper()
                if upper.startswith(('AD_', 'VC_')):
                    continue
                if 'CITIRE' in kb_ops:
                    permissions[table] = set(DML if 'EDITARE' in kb_ops else READ)
            for section, prefix, write_map in (('AD', 'AD_', AD_WRITES), ('VR', 'VC_', VR_WRITES)):
                ops = _operations(conn, username, db, section)
                for table, rights in _module_permissions(tables, prefix, ops, write_map).items():
                    permissions.setdefault(table, set()).update(rights)
                if section == 'VR' and 'read' in ops and 'Parteneri' in tables:
                    permissions.setdefault('Parteneri', set()).update(READ)
        for table, rights in sorted(permissions.items()):
            grants.append((db, table, tuple(sorted(rights))))
    return grants


def grant_sql(db, table, rights):
    return 'GRANT %s ON %s.%s TO %%s@%%s' % (', '.join(rights), _q(db), _q(table))
