"""Balance display options, applied before the shared screen/PDF/Excel model."""
from decimal import Decimal
from unicodedata import normalize

VERSION = 2


def alphabetical(value):
    # Romanian letters follow their base letter; support legacy cedilla spelling.
    text = normalize('NFC', str(value or '')).casefold().replace('ş', 'ș').replace('ţ', 'ț')
    special = {'ă': ord('a') * 10 + 1, 'â': ord('a') * 10 + 2,
               'î': ord('i') * 10 + 1, 'ș': ord('s') * 10 + 1, 'ț': ord('t') * 10 + 1}
    return tuple(special.get(letter, ord(letter) * 10) for letter in text)


def prepare(rows, params):
    if not params.get('show_without_movements', False):
        # Compare net turnover and balances; fully offset documents do not keep an empty row.
        rows = [row for row in rows if any(
            Decimal(str(row[key])) != 0 for key in ('opening', 'incoming', 'outgoing', 'closing'))]
    return sorted(rows, key=lambda row: (
        alphabetical(row.get('account')), row.get('category') or 0,
        alphabetical(row.get('name')), row.get('product') or 0, row['variant']))


def display_layout(layout, params):
    if not params.get('page_break_accounts', False):
        return layout
    # Account must be the outer group, including in customized report layouts.
    return {**layout, 'groups': ['account'] + [key for key in layout.get('groups', []) if key != 'account']}
