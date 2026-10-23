SELECT i.IdProdus AS p,DATE(COALESCE(n.DataFc,n.Data)) AS d,i.Cant AS q,
'nir' AS kind,n.IdIntrare AS document_id,i.ID AS line_id,n.NrFact AS number,n.CodPartener AS supplier
FROM {{TABLE:VC_Intrari}} i JOIN {{TABLE:VC_NIR}} n ON n.IdIntrare=i.IdIntrare
WHERE n.Anulat=0 AND COALESCE(n.Storno,0)=0
UNION ALL
SELECT i.IdProdus,DATE(h.DataBC),i.Cant,'br',h.IdBonConsum,i.ID,h.NumarBC,NULL
FROM {{TABLE:VC_IesiriRetur}} i JOIN {{TABLE:VC_Iesiri_H}} h ON h.IdBonConsum=i.IdBonConsum WHERE h.Anulat=0
UNION ALL
SELECT i.IdProdus,DATE(h.DataBC),-i.Cant,'bc',h.IdBonConsum,i.ID,h.NumarBC,NULL
FROM {{TABLE:VC_Iesiri}} i JOIN {{TABLE:VC_Iesiri_H}} h ON h.IdBonConsum=i.IdBonConsum WHERE h.Anulat=0
UNION ALL
SELECT i.IdProdus,DATE(COALESCE(n.DataFc,n.Data)),-i.Cant,'storno',n.IdIntrare,i.ID,n.NrFact,n.CodPartener
FROM {{TABLE:VC_IntrariStorno}} i JOIN {{TABLE:VC_NIR}} n ON n.IdIntrare=i.IdIntrare WHERE n.Anulat=0 AND i.Anulat=0
