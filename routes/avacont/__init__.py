# routes/avacont/__init__.py
"""
Blueprint AVACONT -- the accounting part of the main application (slice family AV, stage 1).

Exposes `avacont_bp`. The routes live in submodules and register on it through the imports at
the end (same shape as routes/forexe/__init__.py: the blueprint must exist BEFORE the import,
because a submodule does `from . import avacont_bp`).

Auth: the bearer session of K-BOT (`require_session`), the unit database comes from the session
(`g.session.db_name`); the client never sends it.
"""
import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

avacont_bp = Blueprint("avacont", __name__)

# configurari/plan_cont/  (slice AV01-04; sql/AVACONT/Configurari/PlanCont/AV01_04_conturi_sursa.sql)
#   subunitati.py -> GET/POST /api/avacont/subunitati, DELETE /api/avacont/subunitati/<id>  (TZ_Subunitati)
#   conturi.py    -> GET /api/avacont/conturi, GET /api/avacont/conturi/nomenclator,
#                    POST /api/avacont/conturi, DELETE /api/avacont/conturi/<id>            (TZ_PlanCont)
#   solduri.py    -> GET/POST /api/avacont/conturi/solduri (opening balances + turnovers)   (TZ_SoldInitial)
# Layout rule (operator, 24.10.2026): routes/avacont/<category>/<module>/ ; only what several modules share stays here (common.py).
from .configurari.plan_cont import subunitati  # noqa: E402,F401
from .configurari.plan_cont import conturi  # noqa: E402,F401
from .configurari.plan_cont import solduri  # noqa: E402,F401
#   solduri_parteneri.py -> GET /api/avacont/parteneri/conturi, GET/POST /api/avacont/parteneri/solduri  (TZ_SoldInitial with a partner; slice AV01-08)
from .configurari.plan_cont import solduri_parteneri  # noqa: E402,F401
# configurari/jurnale/  (slice AV01-09; sql/AVACONT/Configurari/Jurnale/AV01_09_jurnale.sql)
#   jurnale.py    -> GET/POST /api/avacont/jurnale                                          (TZ_Jurnale)
from .configurari.jurnale import jurnale  # noqa: E402,F401
# configurari/clasificatii_venituri/  (slice AV01-11; sql/AVACONT/Configurari/ClasificatiiVenituri/AV01_11_clasificatii_venituri_buget.sql)
#   clasificatii_venituri.py -> GET /api/avacont/clasificatii-venituri (+ /nomenclator, /sumar, /<id>/buget), POST /adauga, POST /<id>/buget
#                               (Clasificatii_Venituri, Clasificatii_Venituri_Buget, Clasificatii_Venituri_Rectificari)
from .configurari.clasificatii_venituri import clasificatii_venituri  # noqa: E402,F401
# configurari/sablon_operatii/  (slice AV01-14; sql/AVACONT/Configurari/SablonOperatii/AV01_14_sablon_operatii.sql)
#   sablon_operatii.py -> GET/POST /api/avacont/sabloane-operatii                          (TZ_SablonOperatii, TZ_SablonOperatiiLinie)
from .configurari.sablon_operatii import sablon_operatii  # noqa: E402,F401
# configurari/sablon_salarii/  (slice AV01-15; sql/AVACONT/Configurari/SablonSalarii/AV01_15_sablon_salarii.sql)
#   sablon_salarii.py -> GET/POST /api/avacont/sabloane-salarii                           (TZ_SablonSalarii, TZ_SablonSalariiLinie)
from .configurari.sablon_salarii import sablon_salarii  # noqa: E402,F401
