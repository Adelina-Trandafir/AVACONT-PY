# SLICE-CHAT-07 — Inchidere, tastatura, polling si antet DISCUTII

Data: 2026-10-20.

Cerintele utilizatorului implementate in surse:

1. Apasarea in exterior inchide panoul. Evenimentul nu este anulat, iar controlul exterior isi pastreaza focalizarea. Butonul plutitor comuta deschiderea/inchiderea. Ciorna se pastreaza.
2. Enter trimite; Ctrl+Enter (si Cmd+Enter pe Mac) insereaza rand nou la selectie/cursor si actualizeaza contorul prin evenimentul input al TextBox. Se respecta limita de lungime si compozitia IME; repetitia tastei Enter nu retrimite mesajul.
3. Polling la 60 secunde inchis, 5 secunde deschis. Panoul foloseste separat vechimea listei si a starii. Redeschiderea reincarca daca lista nu a fost incarcata sau are cel putin 5 secunde; redeschiderea rapida refoloseste lista. Revenirea in fila respecta aceleasi praguri. Inchiderea reprogrameaza temporizatorul si anuleaza marcarea citirii amanate. Polling-ul este oprit pe fila ascunsa. Recuperarea unui backlog continua la interval scurt numai cu panoul deschis.
4. Antet albastru #3b5a85, ca antetele aplicatiilor, cu text si butoane albe.
5. Titlu simplu DISCUTII cu 💬, identic cu butonul plutitor; fara subtitlu. Contorul de necitite si marcajele V/A/K sunt pastrate.

Fisiere:
- `static/js/components/chat/chat-panel.js`
- `static/js/components/chat/chat.css`
- `static/js/chat/client.js`
- `docs/chat/PROIECT.md`
- `docs/worklog/SLICE-CHAT-06-diagnostic-503.md`

Stare: modificari scrise in surse. Nu s-au rulat teste, verificari de sintaxa sau probe in browser/server, conform instructiunilor. Nicio modificare de schema sau configuratie. Toate controalele interactive raman componentele custom existente.
