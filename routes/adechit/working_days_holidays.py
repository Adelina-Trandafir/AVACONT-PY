"""Explicit holiday download; returns editable proposals and never saves them."""
import json
from datetime import date
import requests
from .domain import DomainError, require
from .service import MONTHS, work_days
from .working_days import year_of


def download(year):
    year = year_of(year)
    url = f'https://zilelibere.webventure.ro/api/{year}'
    try:
        with requests.get(url, headers={'Accept': 'application/json'}, timeout=(3, 10),
                          allow_redirects=False, stream=True) as response:
            require(response.status_code == 200, 'HOLIDAYS_API',
                    'Serviciul de zile libere nu este disponibil. Puteți completa manual zilele.', 502)
            payload = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                payload.extend(chunk)
                require(len(payload) <= 512000, 'HOLIDAYS_API', 'Răspunsul serviciului de zile libere este prea mare.', 502)
        events = json.loads(payload.decode('utf-8-sig'))
    except requests.RequestException as error:
        raise DomainError('HOLIDAYS_API', 'Zilele libere nu au putut fi descărcate. Puteți edita manual zilele.', 502) from error
    except (UnicodeError, ValueError) as error:
        if isinstance(error, DomainError):
            raise
        raise DomainError('HOLIDAYS_API', 'Serviciul de zile libere nu a returnat JSON valid.', 502) from error
    require(isinstance(events, list) and 1 <= len(events) <= 100,
            'HOLIDAYS_API', 'Structura răspunsului cu zile libere nu este validă.', 502)
    holidays = {}
    for event in events:
        require(isinstance(event, dict) and isinstance(event.get('name'), str)
                and 0 < len(event['name']) <= 250 and isinstance(event.get('date'), list)
                and 1 <= len(event['date']) <= 31, 'HOLIDAYS_API', 'O sărbătoare are un format invalid.', 502)
        for item in event['date']:
            require(isinstance(item, dict) and isinstance(item.get('date'), str),
                    'HOLIDAYS_API', 'O dată a sărbătorilor este invalidă.', 502)
            try:
                day = date.fromisoformat(item['date'].replace('/', '-'))
            except ValueError as error:
                raise DomainError('HOLIDAYS_API', 'O dată a sărbătorilor este invalidă.', 502) from error
            require(day.year == year, 'HOLIDAYS_YEAR', 'Serviciul a returnat sărbători pentru alt an.', 502)
            holidays.setdefault(day, set()).add(event['name'].strip())
    months = []
    for month in range(1, 13):
        days = [{'date': day.isoformat(), 'name': ' / '.join(sorted(names)), 'working_day': day.weekday() < 5}
                for day, names in sorted(holidays.items()) if day.month == month]
        deducted = sum(day['working_day'] for day in days)
        months.append({'month': month, 'name': MONTHS[month - 1].capitalize(), 'holidays': days,
                       'deducted': deducted, 'days': work_days(month, year) - deducted})
    return {'year': year, 'source': url, 'months': months, 'holiday_count': len(holidays),
            'deducted': sum(month['deducted'] for month in months)}
