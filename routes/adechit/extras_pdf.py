"""Reads the XML embedded in a Treasury account-statement PDF (ADECHIT, SLICE-AD13-01).

Port of KBot.Forexe.Services.PdfXmlExtractor (the reader K-BOT uses for the same files), with the
same method: no PDF library, just the /EF /F reference to the EmbeddedFile object, its stream, and
the FlateDecode that goes with it. File names look like
    TREZ521_ExtrasEP_PDFCLI_2842919_XML_SIGNED_31122025h1327.pdf
and the part after _SIGNED_ is the day and minute the statement was produced (ddMMyyyyhHHmm).

Pure functions, no database, no network.
"""
import re
import zlib
from datetime import datetime
from xml.etree import ElementTree

NAME_DATE = re.compile(r'_SIGNED_(\d{2})(\d{2})(\d{4})h(\d{2})(\d{2})\.pdf$', re.IGNORECASE)

# The reference to the embedded file: the Filespec form first, then any /EF dictionary.
_EF_PATTERNS = (
    re.compile(r'/Type\s*/Filespec[\s\S]{0,400}?/EF\s*<<[\s\S]{0,200}?/F\s+(\d+)\s+0\s+R', re.IGNORECASE),
    re.compile(r'/EF\s*<<[\s\S]{0,200}?/F\s+(\d+)\s+0\s+R', re.IGNORECASE),
)
_XML_DECLARED = re.compile(rb'<\?xml[^>]*encoding\s*=\s*["\']([A-Za-z0-9_.:-]+)["\']', re.IGNORECASE)


def date_from_name(name):
    """The production date in the file name, or None when the name does not carry one."""
    match = NAME_DATE.search(name or '')
    if not match:
        return None
    day, month, year, hour, minute = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def date_text(value):
    """The `DataFisier` text the statement import expects: dd.MM.yyyy HH:mm:ss."""
    return value.strftime('%d.%m.%Y %H:%M:%S') if value else ''


def _embedded_objects(text):
    """Object numbers of the embedded files, in order of appearance, without repeats."""
    found = []
    for pattern in _EF_PATTERNS:
        for match in pattern.finditer(text):
            number = int(match.group(1))
            if number not in found:
                found.append(number)
    return found


def _object_stream(raw, text, number):
    """(dictionary text, stream bytes) of object `number`, or None when it has no stream."""
    start = re.search(r'(?m)(^|\r|\n)' + str(number) + r'\s+0\s+obj\b', text)
    if not start:
        return None
    begin = start.start() + len(start.group(1))
    word = text.find('stream', begin)
    end_object = text.find('endobj', begin)
    if word < 0 or end_object < 0 or word > end_object:
        return None
    dictionary = text[begin:word]
    data_start = word + len('stream')
    if raw[data_start:data_start + 2] == b'\r\n':
        data_start += 2
    elif raw[data_start:data_start + 1] in (b'\n', b'\r'):
        data_start += 1
    data_end = None
    # A direct /Length is exact; the first 'endstream' is only a guess because compressed
    # bytes can contain that word by accident.
    length = re.search(r'/Length\s+(\d+)\b(?!\s+\d+\s+R)', dictionary)
    if length:
        candidate = data_start + int(length.group(1))
        if raw[candidate:candidate + 12].lstrip(b'\r\n').startswith(b'endstream'):
            data_end = candidate
    if data_end is None:
        data_end = text.find('endstream', word)
        if data_end < 0 or data_end > end_object:
            return None
        while data_end > data_start and raw[data_end - 1:data_end] in (b'\n', b'\r'):
            data_end -= 1
    if data_end <= data_start:
        return None
    return dictionary, raw[data_start:data_end]


def _xml_text(data):
    """The XML in `data` as text, or None when the bytes are not a well-formed XML document."""
    data = data.strip(b'\x00 \t\r\n')
    if not data:
        return None
    try:
        ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError, LookupError):
        return None
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    declared = _XML_DECLARED.search(data[:200])
    for encoding in (declared.group(1).decode('ascii') if declared else 'utf-8', 'utf-8', 'cp1252'):
        try:
            return data.decode(encoding).lstrip('﻿')
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def _decompressed(data):
    """Zlib first, then bare deflate; None when neither opens the bytes."""
    for window in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompressobj(window).decompress(data)
        except zlib.error:
            continue
    return None


def extract_xml(pdf_bytes):
    """(xml text, None) on success, (None, reason) when the PDF carries no readable XML."""
    if not pdf_bytes or not pdf_bytes.lstrip()[:5] == b'%PDF-':
        return None, 'Fișierul nu este un PDF.'
    text = pdf_bytes.decode('latin-1')    # one character per byte, so offsets are the same in both
    numbers = _embedded_objects(text)
    if not numbers:
        return None, 'PDF-ul nu conține un fișier atașat (XML).'
    reason = 'Atașamentul PDF nu conține un XML valid.'
    for number in numbers:
        found = _object_stream(pdf_bytes, text, number)
        if not found:
            continue
        dictionary, stream = found
        xml = _xml_text(stream)
        if xml is None and 'FlateDecode' in dictionary:
            unpacked = _decompressed(stream)
            xml = _xml_text(unpacked) if unpacked is not None else None
        if xml is not None:
            return xml, None
    return None, reason
