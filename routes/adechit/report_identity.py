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
        headers = [block for block in page if block['t'] == 'letterhead']
        if headers:
            for block in headers:
                block['left'] = [*block['left'], caption]
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
