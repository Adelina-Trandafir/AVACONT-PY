# SLICE-CHAT-02 — API comun, anonimat si necitite

Data: 2026-10-20.

Implementate endpoint-urile autentificate `/api/chat/state`, `/messages` (GET/POST), `/preferences` (POST), `/read` (POST). Sesiunea portalului este verificata fara prelungire prin polling. Pseudonimele sunt aleatoare si stabile, autorii reali nu apar in proiectia publica a mesajelor sau a citatelor. Numele afisat se completeaza explicit; mesajele cu nume pastreaza instantaneul acestuia.

Istoric cu cursoare, filtru persistent, necitite pe aplicatie, retrimitere idempotenta si limita intre trimiteri. Conversatia comuna se rezolva exclusiv pe server. DML tranzactional pe serverul K-BOT; lipsa tabelelor/seed-ului produce 503, fara DDL automat.

Fisiere:
- `routes/chat/__init__.py`
- `routes/chat/contract.py`
- `routes/chat/repository.py`
- `routes/chat/api.py`
- `main.py`

Stare: cod implementat, nepublicat. Nu s-au rulat teste, verificari de sintaxa sau interogari de verificare. Activarea depinde de SQL-ul manual din SLICE-CHAT-01.
