# routes/auth/ratelimit.py
# Limita anti-forta-bruta pentru rutele de login (units/login). In-process, sigur
# doar la un singur worker (ca session_store; vezi gunicorn.conf.py). Cheie: IP si
# (IP, utilizator). request.remote_addr = IP-ul real al clientului DOAR pentru ca
# ProxyFix e activ in main.py; fara el toti clientii ar imparti bucket-ul IP.
#
# Praguri (confirmate cu operatorul):
#   - per (IP, utilizator): 5 esecuri / 15 min  -> blocare 15 min (parola gresita repetat)
#   - per IP:              30 esecuri / 1 min   -> blocare 15 min (~30 incercari/minut)
# Un login reusit sterge ambele contoare pentru acel IP/utilizator.
import threading
import time

_WINDOW_USER = 15 * 60      # fereastra pentru contorul (IP, utilizator)
_MAX_PER_USER = 5           # esecuri (IP+utilizator) pana la blocare
_WINDOW_IP = 60             # fereastra pentru contorul per IP (~/minut)
_MAX_PER_IP = 30            # esecuri per IP pana la blocare
_LOCKOUT = 15 * 60          # cat dureaza blocarea (ambele bucket-uri)


class _Bucket:
    __slots__ = ("fails", "blocked_until")

    def __init__(self):
        self.fails = []          # timestamps ale esecurilor recente
        self.blocked_until = 0.0


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_ip = {}
        self._by_pair = {}

    @staticmethod
    def _prune(bucket, now, window):
        cutoff = now - window
        bucket.fails = [t for t in bucket.fails if t >= cutoff]

    def is_blocked(self, ip, username):
        now = time.time()
        with self._lock:
            b = self._by_ip.get(ip)
            if b and b.blocked_until > now:
                return True
            b = self._by_pair.get((ip, username))
            if b and b.blocked_until > now:
                return True
            return False

    def record_failure(self, ip, username):
        now = time.time()
        with self._lock:
            ib = self._by_ip.setdefault(ip, _Bucket())
            ib.fails.append(now)
            self._prune(ib, now, _WINDOW_IP)
            if len(ib.fails) >= _MAX_PER_IP:
                ib.blocked_until = now + _LOCKOUT

            pb = self._by_pair.setdefault((ip, username), _Bucket())
            pb.fails.append(now)
            self._prune(pb, now, _WINDOW_USER)
            if len(pb.fails) >= _MAX_PER_USER:
                pb.blocked_until = now + _LOCKOUT

    def record_success(self, ip, username):
        with self._lock:
            self._by_ip.pop(ip, None)
            self._by_pair.pop((ip, username), None)


# STARE IN-PROCESS (verificat 2026-07-15). Contoarele traiesc in dict-urile
# _by_ip / _by_pair ale acestei instante, in memoria procesului. Consecinte:
#   1) Blocarile se pierd la fiecare restart al serviciului — un atacator blocat
#      isi recapata cele 5/30 incercari daca prinde o repornire.
#   2) Al DOILEA lucru (dupa _upload_sessions din routes/ftp.py) care blocheaza
#      multi-worker: cu >1 worker fiecare proces ar avea contoarele lui, deci
#      pragul efectiv s-ar inmulti cu numarul de workeri. Vezi garda din
#      gunicorn.conf.py (workers = 1) — NU o ridicati inainte de a muta pe Redis
#      SI _upload_sessions, SI acest limitator.
# Nu e un bug de reparat acum, e o limita cunoscuta si acceptata la un worker.
LIMITER = RateLimiter()
