# routes/avacont/configurari/clasificatii_venituri/dependente.py
"""
What depends on the existence of a revenue classification (`Clasificatii_Venituri`.`IdClsfV`).

`clasificatii_venituri.py` refuses to delete a classification while anything here points at it. A table that depends on a classification, now or later:
  * with a FOREIGN KEY to `Clasificatii_Venituri (IdClsfV)` -- nothing to do, it is found by itself (utils/dependente.py);
  * without one -- subscribe it with one line below (or, from the module of the new table, `from ...clasificatii_venituri.dependente import CLASIFICATII_VENITURI`
    and `CLASIFICATII_VENITURI.register("TZ_Tabel", "IdClsfV", "ce citeste operatorul")`).
"""
from utils.dependente import Dependencies

CLASIFICATII_VENITURI = Dependencies("Clasificatii_Venituri", "IdClsfV")

# Foreign keys to it (found by themselves; subscribed here only for a readable label).
CLASIFICATII_VENITURI.register("Clasificatii_Venituri_Buget", "IdClsfV", "buget")
CLASIFICATII_VENITURI.register("Clasificatii_Venituri_Rectificari", "IdClsfV", "rectificări")

# No foreign key: the bank-statement headers keep the classification they were matched to.
CLASIFICATII_VENITURI.register("FX_Extrase_H", "IdClsfV", "extrase de cont")
