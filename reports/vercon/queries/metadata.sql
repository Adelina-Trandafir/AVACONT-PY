SELECT pp.IdProdus AS variant,pp.ID AS product,pp.COD AS code,pp.PUTVA AS price,
pr.DenumireP AS name,pr.UM AS um,pr.CPV AS cpv,pr.IdMasina AS category,m.Masina AS account
FROM {{TABLE:VC_ProduseP}} pp
LEFT JOIN {{TABLE:VC_Produse}} pr ON pr.ID=pp.ID
LEFT JOIN {{TABLE:VC_Masini}} m ON m.IdMasina=pr.IdMasina
ORDER BY pr.DenumireP,pp.IdProdus
