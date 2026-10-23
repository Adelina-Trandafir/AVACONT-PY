"""Add the selected subunit to every report section when its name differs."""
import unicodedata


def normalized(value):
    text = unicodedata.normalize('NFC', str(value or ''))
    return ' '.join(text.split()).casefold()


def add_subunit(model, unit_name, subunit_name):
    name = str(subunit_name or '').strip()
    if not name or normalized(name) == normalized(unit_name):
        return model
    caption = 'Subunitate: ' + name
    blocks, page = [], []

    def finish():
        if any(block['t'] == 'letterhead' for block in page):
            # A builder may put the SAME letterhead dict on several pages (one per group); a copy is changed, never the shared one,
            # or the caption would pile up once per page.
            page[:] = [{**block, 'left': [*block['left'], caption]} if block['t'] == 'letterhead' else block for block in page]
        elif page:
            blocks.append({'t': 'text', 'text': caption, 'align': 'center'})
        blocks.extend(page)
        page.clear()

    for block in model['blocks']:
        if block['t'] == 'break':
            finish()
            blocks.append(block)
        else:
            page.append(block)
    finish()
    model['blocks'] = blocks
    return model
