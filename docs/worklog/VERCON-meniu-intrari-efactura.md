# VERCON — Preluare e-Factura din meniul Intrari

Data: 2026-10-21.

La cererea utilizatorului, eliminate complet cele doua butoane de preluare e-Factura din barele NIR: NIR din e-Factura in consultare si Din e-Factura la adaugare.

In meniul custom Intrari este adaugata comanda NIR din e-Factura, dupa Factura Stornare, precedata de separator. Refoloseste disponibilitatea optiunii NIR din contextul serverului si actiunea existenta de selectare/completare din efactura-intake. Cand formularul curent nu este NIR, navigheaza prin fluxul normal de deschidere, cu protectia lucrului nesalvat. Comanda asteapta incarcarea editorului, verifica contextul si dreptul de scriere si nu porneste simultan mai multe selectii. In NIR ramane confirmarea existenta pentru inlocuirea unei ciorne.

Fisiere:

- `static/js/vercon/app.js` — pozitia in meniu si deschiderea actiunii.
- `static/js/vercon/docs.js` — eliminarea butoanelor si metoda publica a editorului.

Stare: modificari scrise in surse. Nu s-au rulat teste, verificari de sintaxa sau probe in browser/server, conform instructiunilor. Nicio modificare de schema sau configuratie. Progresul este consemnat in PYTHON, conform localizarii cerute pentru lucrul din acest chat.
