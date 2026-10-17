SELECT n.IdIntrare AS document,n.Numar AS number,n.NumarSub AS sub_number,n.NrFact AS invoice,
DATE(n.Data) AS date,DATE(COALESCE(n.DataFc,n.Data)) AS movement_date,n.Valoare AS header_value,
n.IdPartener AS supplier,COALESCE(p.DenumirePartener,n.FurnizorV,'Stoc inițial') AS party,
p.CodFiscal AS fiscal_code,p.Adresa AS address,n.IdMasina AS header_category,n.IdComisie AS committee,
n.TipDoc AS doc_type,n.Anulat AS annulled,i.ID AS line_id,i.IdProdus AS variant,
i.Cant AS quantity,i.PUTVA AS price,i.ValoareCUTVA AS value,i.TVA AS vat,'nir' AS family
FROM {{TABLE:VC_NIR}} n JOIN {{TABLE:VC_Intrari}} i ON i.IdIntrare=n.IdIntrare
LEFT JOIN {{TABLE:Parteneri}} p ON p.IdPartener=n.IdPartener WHERE COALESCE(n.Storno,0)=0
UNION ALL
SELECT n.IdIntrare,n.Numar,n.NumarSub,n.NrFact,DATE(n.Data),DATE(COALESCE(n.DataFc,n.Data)),n.Valoare,
n.IdPartener,COALESCE(p.DenumirePartener,n.FurnizorV),p.CodFiscal,p.Adresa,n.IdMasina,n.IdComisie,
n.TipDoc,n.Anulat,i.ID,i.IdProdus,i.Cant,i.PUTVA,NULL,NULL,'storno'
FROM {{TABLE:VC_NIR}} n JOIN {{TABLE:VC_IntrariStorno}} i ON i.IdIntrare=n.IdIntrare
LEFT JOIN {{TABLE:Parteneri}} p ON p.IdPartener=n.IdPartener WHERE COALESCE(n.Storno,0)=1 AND i.Anulat=0
