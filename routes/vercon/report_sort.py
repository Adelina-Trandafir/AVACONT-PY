"""Product order for every report that prints product lines.

Rows are ordered by the layout groups from the outermost inwards, then by
product name. Documents keep the order the report already gives them (newest
first); only their lines are sorted. Sheets (chronological) and aggregates
that carry no product of their own are left as they are.
"""
from .report_balance import alphabetical

# Aggregates keep the first product of each group, so a name sort would be meaningless.
NO_PRODUCT_KINDS = ('statement', 'custody_statement', 'accounts', 'document_summary',
                    'receipts_summary', 'suppliers', 'food')


def group_identity(row, key):
    # Same identity rule as report_body.build, so equal labels of different groups stay apart.
    if key == 'account':
        return row.get('category')
    if key == 'party':
        return row.get('supplier') if 'supplier' in row else row.get('receiver')
    return row.get(key)


def by_name(rows, spec, groups):
    if spec['kind'] in NO_PRODUCT_KINDS or len(rows) < 2:
        return rows
    # A document group keeps its current position: first appearance in the incoming rows.
    first_seen = {}
    for row in rows:
        first_seen.setdefault(row.get('document'), len(first_seen))

    def key(row):
        parts = []
        for group in groups:
            if group == 'document':
                parts.append(first_seen[row.get('document')])
            elif group == 'variant':
                parts.extend((alphabetical(row.get('name')), row.get('product') or 0, row.get('variant') or 0))
            else:
                parts.extend((alphabetical(row.get(group)), str(group_identity(row, group))))
        parts.extend((alphabetical(row.get('name')), row.get('product') or 0, row.get('variant') or 0))
        return tuple(parts)
    return sorted(rows, key=key)
