SELECT h.IdLista,h.NumarLA,DATE(h.DATALA) AS date,h.DEP AS difference,h.ValoareLista AS value
FROM {{TABLE:VC_LABC}} h WHERE h.Anulat=0 ORDER BY h.DATALA,h.IdLista
