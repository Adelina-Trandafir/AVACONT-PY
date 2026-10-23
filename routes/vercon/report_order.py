"""Typed sort metadata for labels that contain both document numbers and dates."""


def document_order(day, number, identity):
    day = day.date() if hasattr(day, 'hour') else day
    value = day.isoformat() if hasattr(day, 'isoformat') else str(day) if day is not None else None
    return {'sortType': 'date', 'sortValue': value,
            'sortDirection': 'desc', 'sortThen': [{'type': 'number', 'value': number},
                                               {'type': 'number', 'value': identity}]}
