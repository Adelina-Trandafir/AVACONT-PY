SELECT i.ID AS line_id,i.IdProdus AS variant,i.Cant AS quantity,i.PUTVA AS price,i.ValoareCUTVA AS value
FROM {{TABLE:VC_LegBCLA}} l JOIN {{TABLE:VC_Iesiri_H}} h ON h.IdBonConsum=l.IdBonConsum
JOIN {{TABLE:VC_Iesiri}} i ON i.IdBonConsum=h.IdBonConsum
WHERE l.IdLista=%(document)s AND h.Anulat=0 ORDER BY i.IdProdus,i.ID
