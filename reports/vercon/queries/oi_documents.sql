SELECT o.IDBP AS document,o.NRBP AS number,DATE(o.DATABP) AS date,
o.IDC_BP AS receiver,c.Denumire AS party,NULL AS giver,NULL AS giver_name,
o.IdMasina AS category,m.Masina AS account,o.IdMasinaP AS warehouse_category,w.Masina AS warehouse_account,
o.Expl AS explanation,o.Anulat AS annulled,o.ID AS line_id,o.IdProdus AS variant,
o.Cant AS quantity,o.PUTVA AS price,o.ValoareCUTVA AS value,'bp' AS family
FROM {{TABLE:VC_OI_Predare}} o
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=o.IDC_BP
LEFT JOIN {{TABLE:VC_Masini}} m ON m.IdMasina=o.IdMasina
LEFT JOIN {{TABLE:VC_Masini}} w ON w.IdMasina=o.IdMasinaP
UNION ALL
SELECT o.IDBT,o.NRBT,DATE(o.DATABT),o.IDC_BT,c.Denumire,o.IDC_OLD,g.Denumire,
o.IdMasina,m.Masina,NULL,NULL,o.Expl,o.Anulat,o.ID,o.IdProdus,o.Cant,o.PUTVA,o.ValoareCUTVA,'bt'
FROM {{TABLE:VC_OI_Transfer}} o
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=o.IDC_BT
LEFT JOIN {{TABLE:VC_Clienti}} g ON g.IDC=o.IDC_OLD
LEFT JOIN {{TABLE:VC_Masini}} m ON m.IdMasina=o.IdMasina
UNION ALL
SELECT o.IDBC,o.NRBC,DATE(o.DATABC),o.IDC_BC,c.Denumire,o.IDC_BC,c.Denumire,
o.IdMasina,m.Masina,NULL,NULL,o.Expl,o.Anulat,o.ID,o.IdProdus,o.Cant,o.PUTVA,o.ValoareCUTVA,'bs'
FROM {{TABLE:VC_OI_Casare}} o
LEFT JOIN {{TABLE:VC_Clienti}} c ON c.IDC=o.IDC_BC
LEFT JOIN {{TABLE:VC_Masini}} m ON m.IdMasina=o.IdMasina
