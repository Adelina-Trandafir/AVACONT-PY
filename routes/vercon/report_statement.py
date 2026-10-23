"""One independent warehouse card per selected product/price variant."""
from decimal import Decimal
from . import report_body
from .report_balance import alphabetical


def build(spec, layout, rows, ctx, unit, committees=None, **options):
    # A saved customized layout must not merge the balances of several variants.
    # Keep its columns and formatting, but make every variant an independent card.
    layout = {**layout, 'groups': ['variant']}
    if not rows:
        return report_body.build(spec, layout, rows, ctx, unit, committees, **options)
    cards = {}
    for row in rows:
        cards.setdefault(row['variant'], []).append(row)

    def order(items):
        row = items[0]
        return (alphabetical(row.get('name')), row.get('product') or 0,
                Decimal(str(row['price'])), row['variant'])

    blocks = []
    for index, items in enumerate(sorted(cards.values(), key=order)):
        if index:
            blocks.append({'t': 'break', 'space_after': 0})
        blocks.extend(report_body.build(spec, layout, items, ctx, unit, committees, **options))
    return blocks
