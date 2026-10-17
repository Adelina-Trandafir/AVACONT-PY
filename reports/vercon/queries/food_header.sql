SELECT h.*,l.MicDejun_P,l.VMD,l.MicDejun_V,l.MicDejun_D,l.Dejun_P,l.VD,l.Dejun_V,l.Dejun_D,
l.Cina_P,l.VC,l.Cina_V,l.Cina_D,l.Comentarii
FROM {{TABLE:VC_LABC}} h LEFT JOIN {{TABLE:VC_ListaAlimente}} l ON l.IDLISTA=h.IdLista
WHERE h.IdLista=%(document)s
