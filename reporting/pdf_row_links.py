"""Opt-in PDF row annotations, positioned after table layout and pagination."""


def mark(paragraph, href, columns=None):
    if isinstance(href, str) and href.startswith('/') and not href.startswith('//'):
        paragraph._report_product_row = (href, tuple(columns) if columns is not None else None)
    return paragraph


def table_type(base):
    class ProductRowTable(base):
        def draw(self):
            super().draw()
            # ReportLab preserves this subclass and its cell flowables when it
            # splits tables. Positions here belong to this rendered page part.
            for index, row in enumerate(self._cellvalues):
                links = set()
                for cell in row:
                    for item in (cell if isinstance(cell, (list, tuple)) else (cell,)):
                        link = getattr(item, '_report_product_row', None)
                        if link:
                            links.add(link)
                for href, columns in links:
                    start, stop = columns if columns is not None else (0, len(self._colWidths))
                    if not 0 <= start < stop <= len(self._colWidths):
                        continue
                    rect = (self._colpositions[start], self._rowpositions[index + 1],
                            self._colpositions[stop], self._rowpositions[index])
                    self.canv.linkURL(href, rect, relative=1, thickness=0)

    return ProductRowTable
