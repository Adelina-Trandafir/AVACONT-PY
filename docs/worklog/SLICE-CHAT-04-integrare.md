# SLICE-CHAT-04 — Integrare K-BOT, ADECHIT si VERCON

Data: 2026-10-20.

Entry point comun in cele trei pagini reale. Modulul identifica aplicatia gazda, reutilizeaza tokenul portalului, gestioneaza polling-ul, istoricul, preferintele si confirmarea citirii. Nu monteaza chat in preview-uri sau in pagina de autentificare. In K-BOT urmareste vizibilitatea zonei autentificate; in ADECHIT/VERCON foloseste sesiunea deja prezenta.

Actualizare la 5 secunde in panou, 20 de secunde inchis, pauza pe fila ascunsa. Schimbarile filtrului din alta fila sunt preluate la actualizare. O trimitere esuata poate fi reluata fara duplicare. Cererile si controalele se distrug la expirarea sesiunii sau iesirea din pagina.

Fisiere:
- `static/js/chat/client.js`
- `static/js/chat/bootstrap.js`
- `static/css/portal.css`
- `static/portal.html`
- `static/adechit.html`
- `static/vercon.html`

Stare: integrare scrisa in surse, nepublicata. SQL-ul nu a fost executat. Nu s-au rulat teste, verificari de sintaxa sau probe in browser/server. Progresul acestei integrari VERCON este consemnat aici, in PYTHON, conform cerintei explicite de localizare a proiectului.
