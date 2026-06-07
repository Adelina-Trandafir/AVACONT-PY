import re
from unidecode import unidecode

def sanitize_header(text):
    if not isinstance(text, str):
        return str(text)
    
    # 1. Eliminare Diacritice
    text = unidecode(text)
    # 2. Curatare caractere speciale (pastreaza doar litere, cifre, underscore)
    text = re.sub(r'[^a-zA-Z0-9_]+', '_', text)
    # 3. Trim underscore
    return text.strip('_')