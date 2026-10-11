# routes/efactura/adresa_anaf.py
"""
Splits the one-line address ANAF gives («JUD. PRAHOVA, MUN. PLOIESTI, STR. X NR. 1») into the county CODE the invoice XML
needs (ISO 3166-2:RO, written after «RO-») and the city, for the «Date Unitate» window (slice 00EF-13).

Pure text handling, no I/O. A part it cannot read comes back as None: the operator then types it, nothing is guessed.
"""
import re
import unicodedata

# Upper case, no diacritics -> ISO code. Same 41 counties + Bucharest as validare.COUNTIES.
_COUNTY_CODES = {
    "ALBA": "AB", "ARAD": "AR", "ARGES": "AG", "BACAU": "BC", "BIHOR": "BH", "BISTRITA-NASAUD": "BN", "BOTOSANI": "BT",
    "BRAILA": "BR", "BRASOV": "BV", "BUZAU": "BZ", "CALARASI": "CL", "CARAS-SEVERIN": "CS", "CLUJ": "CJ",
    "CONSTANTA": "CT", "COVASNA": "CV", "DAMBOVITA": "DB", "DOLJ": "DJ", "GALATI": "GL", "GIURGIU": "GR", "GORJ": "GJ",
    "HARGHITA": "HR", "HUNEDOARA": "HD", "IALOMITA": "IL", "IASI": "IS", "ILFOV": "IF", "MARAMURES": "MM",
    "MEHEDINTI": "MH", "MURES": "MS", "NEAMT": "NT", "OLT": "OT", "PRAHOVA": "PH", "SALAJ": "SJ", "SATU MARE": "SM",
    "SIBIU": "SB", "SUCEAVA": "SV", "TELEORMAN": "TR", "TIMIS": "TM", "TULCEA": "TL", "VALCEA": "VL", "VASLUI": "VS",
    "VRANCEA": "VN", "BUCURESTI": "B",
}
_COUNTY_PREFIX = re.compile(r"^(JUDETUL|JUD)\.?\s+")
_CITY_PREFIX = re.compile(r"^(MUNICIPIUL|MUN|ORASUL|ORS|COMUNA|COM|SATUL|SAT|LOCALITATEA|LOC)\.?\s+")
_SECTOR = re.compile(r"^SECTOR(UL)?\s*([1-6])$")


def _plain(text):
    """Upper case without diacritics, one space between words."""
    decomposed = unicodedata.normalize("NFD", text or "")
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", ascii_text.upper()).strip()


def split_adresa(adresa):
    """(county code, city) read from ANAF's address; either is None when it is not there."""
    county = None
    city = None
    sector = None
    for part in (_plain(p) for p in (adresa or "").split(",")):
        if not part:
            continue
        county_name = _COUNTY_PREFIX.sub("", part) if _COUNTY_PREFIX.match(part) else None
        if county_name is not None:
            county = _COUNTY_CODES.get(county_name)
            continue
        match = _SECTOR.match(part)
        if match:
            sector = "SECTOR" + match.group(2)
            continue
        if city is None and _CITY_PREFIX.match(part):
            name = _CITY_PREFIX.sub("", part)
            if name == "BUCURESTI":
                county = county or "B"
            city = name
    if county == "B" and sector:
        city = sector                       # the XML writes the sector as the city of Bucharest
    return county, (city.title() if city and not city.startswith("SECTOR") else city)


_RAW_PREFIX = re.compile(r"^(municipiul|mun|ora[s\u015f\u0219]ul|ors|comuna|com|satul|sat|localitatea|loc)\.?\s+", re.IGNORECASE)
_COMMA_BELOW = str.maketrans("\u015f\u0163\u015e\u0162", "\u0219\u021b\u0218\u021a")


def _keep_text(text):
    """The city as ANAF wrote it, without «Mun.» / «Com.» in front; the cedilla letters become the comma-below ones."""
    return _RAW_PREFIX.sub("", text.strip()).translate(_COMMA_BELOW) or None


def county_and_city(found):
    """(county code, city) from an `anaf.lookup` answer: the separate seat fields ANAF gives first, the one-line address as
    the fallback when ANAF left them empty. Either is None when it cannot be read."""
    county = None
    code = _plain(found.get("judet_cod"))
    if code in _COUNTY_CODES.values():
        county = code
    elif found.get("judet"):
        name = _plain(found["judet"])
        name = _COUNTY_PREFIX.sub("", name)
        county = _COUNTY_CODES.get("BUCURESTI" if "BUCURESTI" in name else name)
    city = None
    place = _plain(found.get("localitate"))
    sector = re.search(r"SECTOR(?:UL)?\s*([1-6])", place)
    if sector:
        county = county or "B"
        city = "SECTOR" + sector.group(1)
    elif place:
        city = _keep_text(found["localitate"])
    fallback_county, fallback_city = split_adresa(found.get("adresa"))
    return county or fallback_county, city or fallback_city
