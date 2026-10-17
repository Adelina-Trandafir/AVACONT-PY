SELECT 'md' AS meal,i.Id,i.IdFel,i.Explicatie FROM {{TABLE:VC_MicDejun}} i WHERE i.IdLista=%(document)s
UNION ALL
SELECT 'd',i.Id,i.IdFel,i.Explicatie FROM {{TABLE:VC_Dejun}} i WHERE i.IdLista=%(document)s
UNION ALL
SELECT 'c',i.Id,i.IdFel,i.Explicatie FROM {{TABLE:VC_Cina}} i WHERE i.IdLista=%(document)s
