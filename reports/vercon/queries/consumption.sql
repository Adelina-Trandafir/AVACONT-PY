SELECT h.IdBonConsum AS document,h.NumarBC AS number,DATE(h.DataBC) AS date,
h.ValoareBC AS header_value,h.IDC AS receiver,c.Denumire AS party,h.IdMasina AS header_category,
h.Expl AS explanation,h.Anulat AS annulled,i.ID AS line_id,i.IdProdus AS variant,
i.Cant AS quantity,i.PUTVA AS price,i.ValoareCUTVA AS value,'bc' AS family
FROM {{TABLE:VC_Iesiri_H}} h JOIN {{TABLE:VC_Iesiri}} i ON i.IdBonConsum=h.IdBonConsum
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=h.IDC WHERE COALESCE(h.Retur,0)=0
UNION ALL
SELECT h.IdBonConsum,h.NumarBC,DATE(h.DataBC),h.ValoareBC,h.IDC,c.Denumire,h.IdMasina,
h.Expl,h.Anulat,i.ID,i.IdProdus,i.Cant,i.PUTVA,NULL,'br'
FROM {{TABLE:VC_Iesiri_H}} h JOIN {{TABLE:VC_IesiriRetur}} i ON i.IdBonConsum=h.IdBonConsum
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=h.IDC WHERE COALESCE(h.Retur,0)=1
