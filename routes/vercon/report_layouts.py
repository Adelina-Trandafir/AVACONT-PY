"""Declarative defaults matching CAPTURI_RAPOARTE/Magazie-Alimente.

They are data, not drawing code: an admin can change columns, bindings, groups,
fonts, sizes and band elements without editing Python or a JSON file by hand.
"""
from copy import deepcopy


def col(key, label, width=1, kind='text'):
    return {'key': key, 'label': label, 'width': width, 'type': kind}


def money(key, label, width=1):
    return col(key, label, width, 'decimal')


NAME = col('name', 'Denumire', 3)
UM = col('um', 'UM', .65)
PRICE = money('price', 'Preț unitar')
QUANTITY = money('quantity', 'Cantitate')
VALUE = money('value', 'Valoare')
BALANCE = [NAME, UM, PRICE, money('opening', 'Cant.\nInițială'), money('opening_value', 'Valoare\nInițială'),
           money('incoming', 'Cant.\nIntrată'), money('incoming_value', 'Valoare\nIntrată'),
           money('outgoing', 'Cant.\nIeșită'), money('outgoing_value', 'Valoare\nIeșită'),
           money('closing', 'Cant.\nFinală'), money('closing_value', 'Valoare\nFinală')]
FINAL = [NAME, UM, PRICE, money('closing', 'Cant.\nFinală'), money('closing_value', 'Valoare\nFinală')]
DOCUMENT = [col('ordinal', 'Nr.\ncrt.', .45), col('code', 'COD ARTICOL', 1), col('name', 'DENUMIRE ARTICOL', 3),
            UM, money('quantity', 'Cant. cnf.\nDocum.'), money('quantity', 'Cantitate'), PRICE, VALUE]
INVENTORY = [col('name', 'DENUMIREA BUNURILOR ÎN INVENTAR', 3.4), col('inventory', 'Număr de\ninventar', .8),
             UM, money('actual', 'Faptice'), money('closing', 'Scriptice'), money('plus', 'Plus', .6),
             money('minus', 'Minus', .6), PRICE, money('closing_value', 'Valoare'),
             money('value_plus', 'Plus', .6), money('value_minus', 'Minus', .6),
             money('inventory_value', 'Valoare de\ninventar'), money('loss_value', 'Valoare'), money('loss_quantity', 'Cantitate')]
STATEMENT = [col('date', 'Data'), col('number', 'Numărul', 2), money('incoming', 'Intrări'),
             money('outgoing', 'Ieșiri'), money('closing', 'Stoc'), col('control', 'Număr\ncontrol'), col('signature', 'Data\nsemnătura')]
TITLES = {'Balanta_produse': 'Balanță stocuri produse', 'Fisa_Magazie': 'FIȘĂ DE MAGAZIE',
          'Fisa_Magazie_BF': 'FIȘĂ DE MAGAZIE', 'Stoc_Final': 'Stoc final',
          'LISTA_INVENTAR': 'LISTĂ DE INVENTARIERE\nMAGAZIE', 'NIR': 'NOTĂ DE RECEPȚIE ȘI CONSTATARE DE DIFERENȚE',
          'NIR_SFP': 'NOTĂ DE RECEPȚIE ȘI CONSTATARE DE DIFERENȚE', 'NIR_Storno': 'NOTĂ DE RECEPȚIE — STORNO',
          'Centralizator_BC': 'Centralizator Bonuri de Consum', 'Centralizator_Conturi': 'Centralizator conturi',
          'Centralizator_Conturi_Furnizori': 'Situații furnizori', 'Centralizator_Furnizori': 'Situații furnizori',
          'Consumuri_Produs': 'Situație consumuri / conturi', 'Consumuri_Unitati': 'Situație consumuri / gestionari',
          'Nota_Comanda': 'NOTĂ DE COMANDĂ', 'Referat_periodic_produs': 'REFERAT DE ACHIZIȚIE',
          'Referat_periodic_Intrari': 'REFERAT DE ACHIZIȚIE', 'Plan_Achizitii': 'Situație intrări produse în perioada',
          'Situatie_Intrari': 'Situație intrări produse în perioada', 'ListaAlimente2015': 'LISTĂ ZILNICĂ DE ALIMENTE'}


def food_sections():
    def section(columns, totals=()):
        return {'columns': columns, 'totals': list(totals), 'headers': [], 'table_width': 1}
    return {'drepturi': section([col('meal', 'Masa', 1.6), col('portions', 'Drepturi\nla masă', .9, 'int'),
                money('allocation', 'Alocație\nLEI', .9), money('value', 'Efectiv\nLEI')], ('value',)),
            'recapitulatie': section([col('period', 'Recapitulația cheltuielilor pentru hrană', 2.8),
                money('economy', 'Economii'), money('overrun', 'Depășiri')]),
            'portii': section([col('meal', 'Mese servite', 1.5), col('portions', 'Porții preparate', .9, 'int'),
                col('distributed', 'Distribuite', .9, 'int')]),
            'feluri': section([col('Explicatie', 'Feluri de mâncare', 4)]),
            'alimente': section([NAME, col('um', 'U/M', .6), money('quantity', 'Cant.\nsolic.', .9),
                money('quantity', 'Cant.\nelib.', .9), PRICE, VALUE]),
            'bonuri': section([col('number', 'BC', .9), col('date', 'Data', 1, 'date'),
                col('receiver', 'Primitor', 2.4), VALUE], ('value',))}


def default(spec):
    kind, name = spec['kind'], spec['id']
    columns, groups, totals, headers = FINAL, ['account'], ['closing_value'], []
    signs = []
    if kind in ('balance', 'product'):
        columns, totals = BALANCE, ['opening_value', 'incoming_value', 'outgoing_value', 'closing_value']
    elif kind == 'accounts':
        columns = [col('account', 'Denumire cont', 4)] + [money(k, label) for k, label in (
            ('opening_value', 'Valoare\nInițială'), ('incoming_value', 'Valoare\nIntrată'),
            ('outgoing_value', 'Valoare\nIeșită'), ('closing_value', 'Valoare\nFinală'))]
        groups, totals = [], [c['key'] for c in columns[1:]]
    elif kind == 'inventory':
        columns = INVENTORY
        headers = [[{'label': 'CANTITĂȚI', 'start': 3, 'span': 4}, {'label': 'Valoare contabilă', 'start': 8, 'span': 3},
                    {'label': 'Deprecierea', 'start': 12, 'span': 2}],
                   [{'label': 'Stocuri', 'start': 3, 'span': 2}, {'label': 'Diferențe', 'start': 5, 'span': 2},
                    {'label': 'Diferențe', 'start': 9, 'span': 2}]]
        signs = ['Comisia de inventariere', 'Gestionar (responsabil I.C.)', 'Contabilitate']
    elif kind == 'statement':
        columns, groups, totals = STATEMENT, ['variant'], ['incoming', 'outgoing']
        headers = [[{'label': 'Document', 'start': 0, 'span': 2}]]
    elif kind == 'documents':
        columns, groups, totals = DOCUMENT, ['document'], ['value']
        if spec['family'] in ('nir', 'storno'):
            if spec['family'] == 'nir':
                columns = DOCUMENT + [money('vat', 'din care\nT.V.A.', .9)]
                totals = ['value', 'vat']
            headers = [[{'label': 'RECEPȚIONAT', 'start': 5, 'span': 3}]]
            signs = ['CERTIFICARE COMISIA DE RECEPȚIE', 'Gestionar\nData primirii / Semnătura']
        else:
            columns = [col('ordinal', 'Nr.\ncrt.', .4), col('name', 'Denumirea materialelor (inclusiv sort,\nmarcă, profil, dimensiune)', 3.2),
                       money('quantity', 'Cant.\nnecesară'), col('code', 'Cod'), UM,
                       money('quantity', 'Cantitatea\neliberată'), PRICE, VALUE]
            signs = ['Data și semnătura', 'Șef compartiment', 'Gestionar', 'Primitor']
    elif kind == 'document_summary':
        columns = [col('number', 'Număr bon consum', 1.4), col('date', 'Data'), VALUE, col('party', 'Primitor', 2.7)]
        groups, totals = ['account'], ['value']
    elif kind == 'receipts_summary':
        columns = [col('invoice', 'Serie/Număr fact.', 2), col('date', 'Dată'), col('number', 'NIR', .6), col('account', 'Cont', 2), VALUE]
        groups, totals = list(spec.get('grouping', ('party',))), ['value']
        groups = ['party' if k == 'supplier' else k for k in groups]
    elif kind == 'suppliers':
        columns = [col('party', 'Furnizor', 3), col('fiscal_code', 'Cod fiscal'), col('address', 'Adresă', 3), VALUE]
        groups, totals = [], ['value']
    elif kind in ('consumption', 'consumption_sheet'):
        columns, totals = [NAME, UM, QUANTITY, PRICE, VALUE], ['quantity', 'value']
        groups = ['account', 'name', 'um'] if name == 'Consumuri_Produs' else ['party', 'name', 'um']
        if kind == 'consumption_sheet':
            columns = [NAME, UM, col('date', 'Data'), col('number', 'Bon'), QUANTITY, PRICE, VALUE,
                       col('signature', 'Semnătura')]
            groups = ['party']
    elif kind == 'purchases':
        columns, groups, totals = [col('ordinal', 'NR. CRT.', .6), NAME, UM, QUANTITY], ['account'], []
        if name == 'Plan_Achizitii':
            columns, totals = [NAME, col('cpv', 'CPV', 1.4), QUANTITY, VALUE], ['value']
        elif name == 'Situatie_Intrari':
            columns, totals = [NAME, UM, QUANTITY, VALUE], ['value']
        else:
            groups = ['party'] if name != 'Referat_periodic_produs' else []
            signs = ['ÎNTOCMIT,\nResponsabil achiziții', 'VIZĂ CFP']
    elif kind == 'food':
        columns, groups, totals = [], [], []
    return deepcopy({'schema': 1, 'title': TITLES.get(name, 'BON DE RETUR' if spec.get('family') == 'br'
                       else 'BON DE CONSUM (COLECTIV)' if spec.get('family') == 'bc' else spec['label']),
        'font_size': 8, 'title_size': 15, 'landscape': kind in ('balance', 'inventory', 'product'),
        'table_width': 1, 'columns': columns, 'groups': groups, 'totals': totals, 'headers': headers,
        'signatures': signs, 'elements': [], 'sections': food_sections() if kind == 'food' else {},
        'reference': 'CAPTURI_RAPOARTE/Magazie-Alimente',
        'fields': ['ordinal', 'document', 'number', 'date', 'invoice', 'name', 'um', 'code', 'variant',
                   'cpv', 'account', 'party', 'fiscal_code', 'address', 'quantity', 'price', 'value', 'vat',
                   'opening', 'incoming', 'outgoing', 'closing', 'opening_value', 'incoming_value', 'outgoing_value', 'closing_value',
                   'inventory', 'actual', 'plus', 'minus', 'value_plus', 'value_minus', 'inventory_value', 'loss_value', 'loss_quantity',
                   'control', 'signature', 'meal', 'portions', 'allocation', 'distributed', 'period', 'economy', 'overrun', 'Explicatie', 'receiver']})


def unit_default(unit):
    return {'name': unit, 'fiscal_code': '', 'address': '', 'manager': '',
            'header': '{UNITATE}', 'footer': 'Raport generat în VERCON',
            'elements': [{'id': 'unit-name', 'band': 'header', 'text': '{ANTET}', 'x': 0, 'y': 0, 'w': .72, 'h': 12, 'size': 10},
                         {'id': 'footer-text', 'band': 'footer', 'text': '{SUBSOL}', 'x': 0, 'y': 0, 'w': .75, 'h': 12, 'size': 7},
                         {'id': 'page-count', 'band': 'footer', 'text': 'Pagina {PAGINA} din {PAGINI}', 'x': .77, 'y': 0, 'w': .23, 'h': 12, 'size': 7}]}
