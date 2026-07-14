# routes/auth/session_store.py
"""
Registrul token -> sesiune al login-ului aplicatiei K-BOT.

Doua implementari cu ACELASI contract public (create / validate_and_touch /
validate_and_touch_ex / revoke / sweep / size):

  * SessionStore       — in-memory, in procesul curent. SAFE ONLY sub gunicorn
                         single-process (1 worker / 4 thread-uri), la fel ca
                         _upload_sessions (vezi gunicorn.conf.py).
  * RedisSessionStore  — stare partajata intr-un Redis (acelasi de pe VPS). Ridica
                         constrangerea single-worker si supravietuieste unui restart
                         de proces. Cheile sunt namespace-uite (prefix + DB dedicat)
                         ca sa NU se ciocneasca cu alte proiecte de pe acelasi Redis.

Backend-ul se alege din config (SESSION_BACKEND = "memory" | "redis"); implicit
"memory". `STORE` de la finalul modulului e singleton-ul folosit de guard.py si de
auth.py — a fost, istoric, sursa unui bug de tip "split-brain" (login scria intr-un
dict privat din auth.py, iar rutele de date validau in ACEST STORE, mereu gol).
Acum login-ul minteste AICI, deci exista o singura sursa de adevar.

SECURITY (R1): token-ul e credential bearer — nu se logheaza niciodata valoarea.
SECURITY (R2): parola operatorului NU se persista in Redis. Modelul de login
(option A) nu tine parola dupa verificarea MariaDB, deci `Session.password` e gol
in productie; in plus, RedisSessionStore refuza explicit sa serializeze parola.
"""
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TTL_SECONDS = 20 * 60        # fereastra glisanta de inactivitate
_ABS_TTL_SECONDS = 30 * 60    # plafon absolut de la emitere (R6) — re-auth fortat
_TOKEN_NBYTES = 32            # token opac de 256 biti (CSPRNG)

# ---------------------------------------------------------------------------
# Coduri-motiv stabile pentru fiecare 401 (impartite cu guard.py). Scop: un 401
# se explica singur in log si in corpul raspunsului, ca operatorul si dezvoltatorul
# sa vada IMEDIAT de ce a picat o cerere autentificata (vezi guard.py).
# ---------------------------------------------------------------------------
REASON_OK = "OK"
REASON_TOKEN_ABSENT = "TOKEN_ABSENT"          # fara antet Authorization
REASON_TOKEN_MALFORMED = "TOKEN_MALFORMED"    # antet prezent, dar nu "Bearer <token>"
REASON_TOKEN_UNKNOWN = "TOKEN_UNKNOWN"        # token inexistent in store
REASON_EXPIRED_IDLE = "EXPIRED_IDLE"          # fereastra de inactivitate depasita
REASON_EXPIRED_ABSOLUTE = "EXPIRED_ABSOLUTE"  # plafonul absolut de la emitere depasit
REASON_CONTEXT_MISMATCH = "CONTEXT_MISMATCH"  # token viu, dar pe alt context (ex. alt db_name)


@dataclass
class Session:
    username: str
    password: str          # parola MariaDB a operatorului (DOAR in memorie; gol in option A)
    id_unitate: int
    db_name: str           # baza unitatii rezolvata, ex. "000_DEMO"
    ctx: dict              # payload-ul SessionContext intors clientului
    issued_at: float
    expires_at: float
    pcname: str = ""
    last_seen: float = 0.0


# Rezultatul unei validari, cu destul context pentru un log de 401 lamuritor
# (issued_at / last_seen / now scot la iveala pe loc un bug de fus orar sau ceas).
@dataclass
class Validation:
    session: object = None          # Session viu, sau None
    reason: str = REASON_OK
    issued_at: float = None
    last_seen: float = None
    now: float = None


class SessionStore:
    """Registru in-memory (single-process). Contract identic cu RedisSessionStore."""

    def __init__(self):
        self._lock = threading.Lock()          # 4 thread-uri impart procesul
        self._by_token: dict[str, Session] = {}

    def create(self, username, password, id_unitate, db_name, ctx, pcname):
        token = secrets.token_urlsafe(_TOKEN_NBYTES)
        now = time.time()
        s = Session(username=username, password=password,
                    id_unitate=id_unitate, db_name=db_name, ctx=ctx,
                    issued_at=now,
                    expires_at=min(now + _TTL_SECONDS, now + _ABS_TTL_SECONDS),
                    pcname=pcname, last_seen=now)
        with self._lock:
            self._by_token[token] = s
        return token, s

    def validate_and_touch_ex(self, token) -> Validation:
        """Ca validate_and_touch, dar intoarce si MOTIVUL (pentru logul de 401) si
        marcajele de timp. Distinge token necunoscut / expirat-idle / expirat-absolut."""
        now = time.time()
        if not token:
            return Validation(reason=REASON_TOKEN_UNKNOWN, now=now)
        with self._lock:
            s = self._by_token.get(token)
            if s is None:
                return Validation(reason=REASON_TOKEN_UNKNOWN, now=now)
            # Plafonul absolut are prioritate (chiar sub folosire continua).
            if now >= s.issued_at + _ABS_TTL_SECONDS:
                del self._by_token[token]
                return Validation(reason=REASON_EXPIRED_ABSOLUTE,
                                  issued_at=s.issued_at, last_seen=s.last_seen, now=now)
            if s.expires_at <= now:
                del self._by_token[token]
                return Validation(reason=REASON_EXPIRED_IDLE,
                                  issued_at=s.issued_at, last_seen=s.last_seen, now=now)
            # Fereastra glisanta, dar niciodata dincolo de plafonul absolut.
            s.expires_at = min(now + _TTL_SECONDS, s.issued_at + _ABS_TTL_SECONDS)
            s.last_seen = now
            return Validation(session=s, reason=REASON_OK,
                              issued_at=s.issued_at, last_seen=s.last_seen, now=now)

    def validate_and_touch(self, token):
        """Intoarce sesiunea vie (si ii gliseaza expirarea) sau None. Pastreaza
        contractul istoric folosit de teste; motivul se ia din validate_and_touch_ex."""
        return self.validate_and_touch_ex(token).session

    def revoke(self, token):
        with self._lock:
            self._by_token.pop(token, None)

    def sweep(self):
        """Curatare periodica optionala a sesiunilor expirate."""
        now = time.time()
        with self._lock:
            dead = [t for t, s in self._by_token.items()
                    if s.expires_at <= now or now >= s.issued_at + _ABS_TTL_SECONDS]
            for t in dead:
                del self._by_token[t]

    def size(self) -> int:
        with self._lock:
            return len(self._by_token)


class RedisSessionStore:
    """
    Registru token -> sesiune intr-un Redis partajat. Contract identic cu SessionStore.

    Namespacing: fiecare cheie e `<prefix><token>` intr-un DB Redis dedicat, deci
    K-BOT nu poate atinge cheile altui proiect de pe acelasi server.

    Expirare: idle (fereastra glisanta) si plafonul absolut sunt aplicate IN-APP,
    din issued_at/expires_at pastrate in blob, ca sub SessionStore — asa motivul
    (EXPIRED_IDLE vs EXPIRED_ABSOLUTE) ramane exact. TTL-ul nativ Redis e setat pe
    timpul ramas pana la plafonul absolut, ca backstop care matura cheia oricum.
    """

    def __init__(self, client, key_prefix="kbot:sess:", clock=time.time):
        # `client` e un redis.Redis (decode_responses=True) sau un fake compatibil.
        # `clock` = ceasul LOGIC al expirarii (implicit time.time). Injectabil ca sa
        # fie independent de ceasul reaper-ului Redis (util la teste si la skew de ceas).
        if client is None:
            raise ValueError("RedisSessionStore necesita un client Redis.")
        self._r = client
        self._prefix = key_prefix
        self._now = clock

    def _key(self, token):
        return self._prefix + token

    def create(self, username, password, id_unitate, db_name, ctx, pcname):
        token = secrets.token_urlsafe(_TOKEN_NBYTES)
        now = self._now()
        expires_at = min(now + _TTL_SECONDS, now + _ABS_TTL_SECONDS)
        s = Session(username=username, password=password,
                    id_unitate=id_unitate, db_name=db_name, ctx=ctx,
                    issued_at=now, expires_at=expires_at,
                    pcname=pcname, last_seen=now)
        # SECURITY (R2): parola NU pleaca niciodata spre stocarea partajata.
        blob = json.dumps({
            "username": username, "password": "", "id_unitate": id_unitate,
            "db_name": db_name, "ctx": ctx, "issued_at": now,
            "expires_at": expires_at, "pcname": pcname, "last_seen": now,
        })
        ttl = max(1, int(now + _ABS_TTL_SECONDS - now))   # = _ABS_TTL_SECONDS
        self._r.set(self._key(token), blob, ex=ttl)
        return token, s

    def _load(self, token):
        raw = self._r.get(self._key(token))
        if raw is None:
            return None
        d = json.loads(raw)
        return Session(username=d["username"], password=d.get("password", ""),
                       id_unitate=d["id_unitate"], db_name=d["db_name"],
                       ctx=d["ctx"], issued_at=d["issued_at"],
                       expires_at=d["expires_at"], pcname=d.get("pcname", ""),
                       last_seen=d.get("last_seen", d["issued_at"]))

    def validate_and_touch_ex(self, token) -> Validation:
        now = self._now()
        if not token:
            return Validation(reason=REASON_TOKEN_UNKNOWN, now=now)
        s = self._load(token)
        if s is None:
            # Redis a maturat cheia (idle/absolut) SAU tokenul e necunoscut — sub Redis
            # cele doua nu se pot distinge dupa reap; raportam TOKEN_UNKNOWN.
            return Validation(reason=REASON_TOKEN_UNKNOWN, now=now)
        if now >= s.issued_at + _ABS_TTL_SECONDS:
            self._r.delete(self._key(token))
            return Validation(reason=REASON_EXPIRED_ABSOLUTE,
                              issued_at=s.issued_at, last_seen=s.last_seen, now=now)
        if s.expires_at <= now:
            self._r.delete(self._key(token))
            return Validation(reason=REASON_EXPIRED_IDLE,
                              issued_at=s.issued_at, last_seen=s.last_seen, now=now)
        # Gliseaza fereastra de idle, taiata la plafonul absolut; rescrie blobul si TTL-ul.
        s.expires_at = min(now + _TTL_SECONDS, s.issued_at + _ABS_TTL_SECONDS)
        s.last_seen = now
        blob = json.dumps({
            "username": s.username, "password": "", "id_unitate": s.id_unitate,
            "db_name": s.db_name, "ctx": s.ctx, "issued_at": s.issued_at,
            "expires_at": s.expires_at, "pcname": s.pcname, "last_seen": s.last_seen,
        })
        ttl = max(1, int(s.issued_at + _ABS_TTL_SECONDS - now))
        self._r.set(self._key(token), blob, ex=ttl)
        return Validation(session=s, reason=REASON_OK,
                          issued_at=s.issued_at, last_seen=s.last_seen, now=now)

    def validate_and_touch(self, token):
        return self.validate_and_touch_ex(token).session

    def revoke(self, token):
        if token:
            self._r.delete(self._key(token))

    def sweep(self):
        # Redis matura singur cheile la expirarea TTL-ului; nimic de facut.
        return None

    def size(self) -> int:
        n = 0
        for _ in self._r.scan_iter(match=self._prefix + "*", count=500):
            n += 1
        return n


# ---------------------------------------------------------------------------
# Selectia backend-ului din config (host-only). Implicit "memory": codul se poate
# livra fara sa comute pe Redis, deci un deploy NU poate cadea din cauza unui Redis
# indisponibil. Comutarea pe Redis e o singura linie in config.py de pe VPS:
#   SESSION_BACKEND = "redis"
#   REDIS_URL = "redis://127.0.0.1:6379/3"     # DB dedicat K-BOT
#   # optional: SESSION_KEY_PREFIX = "kbot:sess:"
# ---------------------------------------------------------------------------
def _read_config():
    try:
        import config as _config
    except Exception:
        return None
    return _config


def _build_store():
    cfg = _read_config()

    def _get(name, default):
        return getattr(cfg, name, default) if cfg is not None else default

    backend = str(_get("SESSION_BACKEND", "memory")).strip().lower()
    if backend != "redis":
        return SessionStore()

    # Backend Redis cerut EXPLICIT: construim clientul si dam PING acum, ca o
    # configurare gresita sa pice zgomotos la pornire (nu tacut, nu la prima cerere).
    import redis   # dependenta prezenta doar cand Redis e chiar folosit

    prefix = str(_get("SESSION_KEY_PREFIX", "kbot:sess:"))
    url = _get("REDIS_URL", None)
    if url:
        client = redis.Redis.from_url(url, decode_responses=True)
    else:
        client = redis.Redis(
            host=str(_get("REDIS_HOST", "127.0.0.1")),
            port=int(_get("REDIS_PORT", 6379)),
            db=int(_get("REDIS_DB", 0)),
            password=_get("REDIS_PASSWORD", None),
            decode_responses=True,
        )
    try:
        client.ping()
    except Exception as err:
        raise RuntimeError(
            "SESSION_BACKEND='redis' dar Redis nu raspunde la PING. Verificati "
            "REDIS_URL/REDIS_HOST din config.py si daca serverul Redis ruleaza. "
            f"Detaliu: {err}"
        ) from err
    logger.info("Session store: Redis (prefix=%s).", prefix)
    return RedisSessionStore(client, key_prefix=prefix)


STORE = _build_store()   # singleton la nivel de modul, ca _upload_sessions
