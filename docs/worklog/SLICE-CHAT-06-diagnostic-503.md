# SLICE-CHAT-06 — Diagnostic pentru raspunsurile 503

Data: 2026-10-20.

Utilizatorul a raportat raspunsuri 503 pentru `/api/chat/messages` si `/api/chat/state`. Analiza surselor: absenta mesajelor este permisa; lista goala si numarul zero de necitite nu constituie eroare. Absenta conversatiei COMUN, adaugata de seed, produce insa explicit 503. Din consola browserului nu se poate determina daca aceasta este cauza concreta sau daca exista o alta eroare SQL/runtime.

Schimbari:
- `routes/chat/repository.py`: motiv distinct `CHAT_CONVERSATIE_LIPSA` pentru lipsa conversatiei initiale.
- `routes/chat/api.py`: logarea motivului pentru indisponibilitatile explicite si a operatiei/codului SQL/pozitiei din cod pentru exceptii. Fara text SQL, parametri, continut de mesaje, conturi sau valorile locale din traceback.

Stare: diagnostic imbunatatit in surse. Cauza pe server ramane neconfirmata; s-a cerut confirmarea seed-ului si, daca acesta este executat, linia relevanta din api_server.log. Nu s-au rulat teste, interogari sau alte verificari si nu s-au modificat scheme.

Actualizare: utilizatorul a confirmat ulterior ca nu executase interogarea de seed. Aceasta explica indisponibilitatea initiala; nu era necesar sa existe mesaje in tabel.
