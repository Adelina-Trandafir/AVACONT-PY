# SLICE-CHAT-08 — Bula DISCUȚII mobila

Data: 2026-10-21.

Cerinta: bula fixa acopera butoane ale aplicatiei pe ecrane cu inaltime redusa; utilizatorul trebuie sa o poata muta cu mouse-ul.

Schimbari:

- Tragere de pe butonul custom existent, cu pointer capture si prag de 6 pixeli pentru separarea de clicul normal.
- Clicul de dupa tragere este oprit inainte de actiunea de deschidere/inchidere. Activarea cu tastatura ramane disponibila.
- Pozitia bulei este limitata la viewport si salvata in localStorage, ca proportii ale spatiului disponibil. Se reutilizeaza in cele trei aplicatii si dupa reincarcarea paginii, fara modificari in baza de date sau cereri suplimentare.
- Redimensionarea ferestrei, zoom-ul aplicatiei si latimea variabila a butonului cu necitite readapteaza pozitia.
- Panoul se pozitioneaza in viewport fata de noua pozitie a bulei; prefera deasupra, apoi dedesubt sau lateral daca spatiul nu permite pozitia initiala.
- Coordonatele si dimensiunile dinamice sunt gestionate exclusiv de componenta. Ascultatorii, ResizeObserver si cadrul de repozitionare se elibereaza la distrugerea chatului.

Fisiere:

- `static/js/components/chat/chat-position.js` — gesturi, pozitionare si persistenta locala.
- `static/js/components/chat/chat-panel.js` — integrarea comportamentului in componenta comuna.
- `static/js/components/chat/chat.css` — pozitii parametrizate si cursoare de tragere.
- `docs/chat/PROIECT.md` — comportamentul actualizat.

Stare: cod implementat in surse pentru K-BOT, ADECHIT si VERCON. Au fost analizate numai sursele si proiectarea; nu s-au rulat teste, verificari de sintaxa, probe in browser sau pe server. Nicio schema sau configuratie modificata. Controalele raman exclusiv componentele custom existente.
