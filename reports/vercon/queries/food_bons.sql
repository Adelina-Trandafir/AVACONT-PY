SELECT h.IdBonConsum AS document,h.NumarBC AS number,DATE(h.DataBC) AS date,h.ValoareBC AS value,
h.Anulat AS annulled,c.Denumire AS receiver
FROM {{TABLE:VC_LegBCLA}} l JOIN {{TABLE:VC_Iesiri_H}} h ON h.IdBonConsum=l.IdBonConsum
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=h.IDC WHERE l.IdLista=%(document)s ORDER BY h.DataBC,h.IdBonConsum
