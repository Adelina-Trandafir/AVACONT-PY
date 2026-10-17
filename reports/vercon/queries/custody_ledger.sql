SELECT IdProdus AS p,DATE(DATABP) AS d,Cant AS q,'bp' AS kind,IDBP AS document_id,
ID AS line_id,NRBP AS number,IDC_BP AS holder,IdMasina AS category
FROM {{TABLE:VC_OI_Predare}} p WHERE Anulat=0
UNION ALL
SELECT IdProdus,DATE(DATABT),Cant,'bt_in',IDBT,ID,NRBT,IDC_BT,IdMasina
FROM {{TABLE:VC_OI_Transfer}} t WHERE Anulat=0
UNION ALL
SELECT IdProdus,DATE(DATABT),-Cant,'bt_out',IDBT,ID,NRBT,IDC_OLD,IdMasina
FROM {{TABLE:VC_OI_Transfer}} t WHERE Anulat=0
UNION ALL
SELECT IdProdus,DATE(DATABC),-Cant,'bs',IDBC,ID,NRBC,IDC_BC,IdMasina
FROM {{TABLE:VC_OI_Casare}} c WHERE Anulat=0
