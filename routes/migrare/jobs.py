# routes/migrare/jobs.py
# -----------------------------------------------------------------------------
# Reading 29 MB of Access and writing sixteen tables takes minutes, which is far
# past what an HTTP request should hold open. So the two long routes start a job
# and hand back an id; the migrator polls it and shows the log as it grows.
#
# In-memory registry, one background thread per job. Safe here and only here
# because the deployment is locked to a single Gunicorn worker.
# -----------------------------------------------------------------------------

import logging
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# Cate randuri de jurnal pastram pentru un job.
MAX_LOG_LINES = 2000

# Un job incheiat se sterge dupa atat, ca sa nu creasca la nesfarsit.
JOB_TTL_SECONDS = 2 * 3600

IN_LUCRU = "în lucru"
GATA = "gata"
EROARE = "eroare"

_jobs = {}
_lock = threading.Lock()


class Job(object):

    def __init__(self, kind):
        self.id = str(uuid.uuid4())
        self.kind = kind
        self.state = IN_LUCRU
        self.started = time.time()
        self.finished = None
        self.error = None
        self.result = None
        # Pastrate pentru pasul de scriere: raportul analizei aceluiasi fisier
        # si planul de rutare pe care l-a folosit. Planul NU se rezolva din nou
        # la scriere -- ramura aleasa trebuie sa fie aceeasi cu cea masurata.
        self.report = None
        self.plan = None
        self.lines = []
        self._lock = threading.Lock()

    def say(self, text):
        with self._lock:
            self.lines.append(text)
            if len(self.lines) > MAX_LOG_LINES:
                del self.lines[0:len(self.lines) - MAX_LOG_LINES]

    def snapshot(self, since=0):
        with self._lock:
            lines = self.lines[since:]
            total = len(self.lines)
        return {
            "id": self.id,
            "fel": self.kind,
            "stare": self.state,
            "eroare": self.error,
            "rezultat": self.result,
            "jurnal": lines,
            "jurnal_total": total,
        }


def start(kind, work):
    """
    `work(job)` ruleaza pe firul de fundal. Ce intoarce ajunge in job.result.
    Orice exceptie devine job.error si NU se pierde: se scrie si in jurnalul
    serverului, cu urma completa.
    """
    _prune()
    job = Job(kind)
    with _lock:
        _jobs[job.id] = job

    def runner():
        try:
            job.result = work(job)
            job.state = GATA
        except Exception as exc:
            logger.exception("migrare: jobul %s (%s) a eșuat", job.id, kind)
            job.error = str(exc)
            job.state = EROARE
            job.say("EROARE: %s" % exc)
        finally:
            job.finished = time.time()

    thread = threading.Thread(target=runner, name="migrare-%s" % kind, daemon=True)
    thread.start()
    return job


def get(job_id):
    with _lock:
        return _jobs.get(job_id)


def _prune():
    now = time.time()
    with _lock:
        for job_id in [k for k, v in _jobs.items()
                       if v.finished and now - v.finished > JOB_TTL_SECONDS]:
            _jobs.pop(job_id, None)
