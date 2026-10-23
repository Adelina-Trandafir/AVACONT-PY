"""VC08-03: explicit disposition of the 61 Access exports (N06 exclusions).

Aliases are case insensitive, as in Access. Subreports are also available as
independent sections with the parent's explicit document/variant selection.
"""
from .nomenclatoare import NomError
from .report_mf import MODES as MF_SELECTION_MODES

ALL = ('MATERIALE', 'ALIMENTE', 'OI', 'MF')
G = ('MATERIALE', 'ALIMENTE')
CATALOG = {}

# The OI branch follows the Access reference; Magazie remains a separate root.
# Reuse the MF selection protocol and its coupled account/holder/document/product
# identities, rather than the old independent use-account and holder filters.
OI_REPORTS = {
    'OI_Bon_BS': ('BONURI', 'Bonuri casare', 'bons'),
    'OI_Bon_BP': ('BONURI', 'Bonuri predare', 'bons'),
    'OI_Bon_BT': ('BONURI', 'Bonuri transfer', 'bons'),
    'OI_Fisa_Evidenta_Produse': ('PRODUSE', 'Situație produse', 'balance'),
    'OI_Fisa_Evidenta_Produse_STOC': ('PRODUSE', 'Stoc final produse', 'balance'),
    'OI_Balanta': ('PRODUSE', 'Balanță obiecte inventar', 'balance'),
    'OI_LISTA_INVENTAR_TOTAL': ('PRODUSE', 'Lista inventar', 'balance'),
    'OI_Fisa_Evidenta_Primitori': ('PRIMITORI', 'Situație primitor', 'inventory'),
    'OI_Fisa_Evidenta_Primitori_STOC': ('PRIMITORI', 'Stoc final primitor', 'inventory'),
    'OI_LISTA_INVENTAR': ('PRIMITORI', 'Lista inventar', 'inventory'),
    'OI_Fisa_Evidenta': ('PRIMITORI', 'Fișă de evidență / spații O.I.', 'inventory'),
}


def add(names, kind, types=ALL, group='Magazie', **options):
    for name in names.split('|'):
        CATALOG[name.casefold()] = {'id': name, 'label': name.replace('_', ' '),
                                   'kind': kind, 'types': types, 'group': group, **options}


add('Balanta_produse', 'balance')
add('Centralizator_Conturi', 'accounts', group='Centralizatoare')
add('Fisa_Produsului', 'product')
add('Stoc_Final', 'final')
add('LISTA_INVENTAR', 'inventory')
add('Fisa_Magazie|FM1-u|FM2', 'statement')
add('Fisa_Magazie_BF', 'statement')
add('OI_Balanta', 'balance', ('OI',), 'Obiecte de inventar', ledger='custody')
add('MF_Balanta|MF_Balanta_Subinv', 'balance', ('MF',), 'Mijloace fixe', ledger='custody')
add('OI_LISTA_INVENTAR|MF_LISTA_INVENTAR', 'inventory', ('OI', 'MF'),
    'Inventar deținători', ledger='custody')
# The exported names constrain the two inventory variants separately.
CATALOG['oi_lista_inventar']['types'] = ('OI',)
CATALOG['mf_lista_inventar']['types'] = ('MF',)
add('OI_LISTA_INVENTAR_TOTAL', 'inventory_total', ('OI',), 'Obiecte de inventar', ledger='custody')
add('OI_Fisa_Evidenta_Primitori_STOC', 'final', ('OI',), 'Obiecte de inventar', ledger='custody')
add('OI_Fisa_Evidenta_Produse_STOC', 'final_total', ('OI',), 'Obiecte de inventar', ledger='custody')
add('OI_Fisa_Evidenta|OI_Fisa_Evidenta_Primitori|OI_Fisa_Evidenta_Produse',
    'custody_statement', ('OI',), 'Obiecte de inventar', ledger='custody')
add('Amortizare', 'depreciation', ('MF',), 'Mijloace fixe')
add('NIR|NIR_SFP', 'documents', family='nir', group='Recepții')
CATALOG['nir_sfp']['types'] = G
CATALOG['nir']['include_storno'] = True
add('NIR_Storno', 'documents', family='storno', group='Recepții', document_required=True)
add('Bon_Consum', 'documents', G, 'Consumuri', family='bc', outgoing_selection=True, include_returns=True, document_options=True)
add('BUConsum|BUConsum_F|BUConsum_F1', 'documents', G, 'Consumuri', family='bc', document_required=True)
add('BonRetur', 'documents', G, 'Consumuri', family='br', document_required=True)
for prefix, types in (('OI', ('OI',)), ('MF', ('MF',))):
    for suffix, family in (('BP', 'bp'), ('BT', 'bt'), ('BS', 'bs')):
        add(f'{prefix}_Bon_{suffix}', 'documents', types, 'Bonuri OI / MF', family=family)
for name, family in (('OI_Predare', 'bp'), ('OI_Transfer', 'bt'), ('OI_Casare', 'bs')):
    add(name, 'documents', ('OI', 'MF'), 'Bonuri OI / MF', family=family, document_required=True)
for suffix, family in (('BP', 'bp'), ('BT', 'bt'), ('BS', 'bs')):
    add(f'Centralizator_OI_Bon_{suffix}', 'document_summary', ('OI',), 'Centralizatoare OI', family=family)
add('Centralizator_BC', 'document_summary', G, 'Centralizatoare', family='bc', outgoing_selection=True, receiver_options=True)
add('Consumuri_Produs', 'consumption', G, 'Centralizatoare', grouping=('name', 'variant', 'receiver'))
add('Consumuri_Unitati', 'consumption', G, 'Centralizatoare', grouping=('receiver', 'name', 'variant'))
add('Fisa_Evidenta', 'consumption_sheet', G, 'Centralizatoare')
add('Centralizator_Conturi_Furnizori', 'receipts_summary', group='Centralizatoare', grouping=('account', 'supplier'))
add('Centralizator_Furnizori', 'receipts_summary', G, 'Centralizatoare', grouping=('supplier', 'account'))
add('nou_Furnizori', 'suppliers', group='Recepții')
add('Nota_Comanda', 'purchases', G, 'Achiziții', grouping=('supplier', 'name', 'um'), document_options=True)
add('Referat_periodic_Intrari', 'purchases', G, 'Achiziții', grouping=('supplier', 'name', 'um'), document_options=True)
add('Referat_periodic_produs', 'purchases', group='Achiziții', grouping=('name', 'um'), document_options=True)
add('Plan_Achizitii|Situatie_Intrari', 'purchases', ('MATERIALE', 'ALIMENTE', 'OI'), 'Achiziții', grouping=('name', 'um', 'cpv'), document_options=True)
add('ListaAlimente2015|BC subreport 2013|MicDejun subreport|Dejun subreport|Cina subreport',
    'food', ('ALIMENTE',), 'Alimente', family='lista', document_required=True)
EXCLUDED = ('Balanta_produse_AFB', 'Balanta_Produse_BF', 'Balanta_produse_furnizor')

for name, label in {
    'Referat_periodic_produs': 'Referat Periodic',
    'Referat_periodic_Intrari': 'Referat Periodic',
    'Centralizator_Furnizori': 'Situație Furnizori',
    'nou_Furnizori': 'Centralizator Furnizori',
    'Bon_Consum': 'BONURI',
    'Centralizator_BC': 'CENTRALIZATOR',
    'OI_Balanta': 'Balanță obiecte de inventar',
    'MF_Balanta': 'Balanta',
    'MF_LISTA_INVENTAR': 'Lista Inventar',
    'MF_Bon_BP': 'Bon Predare',
    'MF_Bon_BT': 'Bon Transfer',
    'MF_Bon_BS': 'Bon Casare',
    'OI_LISTA_INVENTAR': 'Listă de inventariere pe deținător',
    'OI_LISTA_INVENTAR_TOTAL': 'Listă de inventariere — total produse',
    'OI_Fisa_Evidenta_Primitori_STOC': 'Stoc final pe deținător',
    'OI_Fisa_Evidenta_Produse_STOC': 'Stoc final pe produs',
    'OI_Fisa_Evidenta': 'Fișă de evidență în folosință',
    'OI_Fisa_Evidenta_Primitori': 'Situație pe deținător',
    'OI_Fisa_Evidenta_Produse': 'Situație pe produs',
    'OI_Bon_BP': 'Bonuri de predare', 'OI_Bon_BT': 'Bonuri de transfer', 'OI_Bon_BS': 'Bonuri de casare',
    'Centralizator_OI_Bon_BP': 'Centralizator predări',
    'Centralizator_OI_Bon_BT': 'Centralizator transferuri',
    'Centralizator_OI_Bon_BS': 'Centralizator casări',
}.items():
    CATALOG[name.casefold()]['label'] = label

for name in ('NIR', 'NIR_SFP', 'NIR_Storno', 'Centralizator_Conturi_Furnizori',
             'Centralizator_Furnizori', 'nou_Furnizori', 'Nota_Comanda', 'Referat_periodic_Intrari'):
    CATALOG[name.casefold()]['incoming_selection'] = True


def spec_of(name, gest_type):
    spec = CATALOG.get(str(name).casefold())
    if not spec:
        raise NomError('REPORT_UNKNOWN', 'Raportul nu există sau este exclus din portare.', 404)
    if gest_type not in spec['types']:
        raise NomError('FORM_NOT_AVAILABLE', 'Raportul nu este disponibil pentru acest tip de gestiune.', 403)
    selection_mode = MF_SELECTION_MODES.get(spec['id']) if gest_type == 'MF' else None
    if gest_type == 'OI' and spec['id'] in OI_REPORTS:
        group, label, selection_mode = OI_REPORTS[spec['id']]
        spec = {**spec, 'group': group, 'tree_group': group, 'label': label}
    if spec['kind'] == 'depreciation':
        filters = ['variant']
    elif spec.get('ledger') == 'custody':
        filters = ['category', 'variant', 'receiver']
    elif spec.get('family') in ('bp', 'bt', 'bs'):
        filters = ['category', 'variant', 'receiver', 'document']
    elif spec['kind'] == 'food':
        filters = ['document']
    elif spec['kind'] in ('balance', 'product', 'accounts', 'final', 'inventory', 'statement'):
        filters = ['category', 'variant', 'supplier']
    elif spec.get('family') in ('bc', 'br') or spec['kind'].startswith('consumption'):
        filters = ['category', 'variant', 'receiver', 'document']
    else:
        filters = ['category', 'variant', 'supplier', 'document']
    if spec['id'] == 'OI_Balanta':
        filters = ['category', 'variant']
    return {**spec, 'filters': filters,
            'mf_selection': selection_mode,
            'outgoing_filters': spec.get('family') in ('bc', 'br') or spec['kind'].startswith('consumption')}


def catalog(gest_type):
    if gest_type == 'OI':
        warehouse = [spec_of(spec['id'], gest_type) for spec in CATALOG.values()
                     if gest_type in spec['types'] and spec.get('ledger') != 'custody'
                     and spec.get('family') not in ('bp', 'bt', 'bs')
                     and spec['kind'] != 'depreciation']
        return warehouse + [spec_of(name, gest_type) for name in OI_REPORTS]
    return [spec_of(spec['id'], gest_type) for spec in CATALOG.values() if gest_type in spec['types']]
