# ADECHIT — Cautarea copiilor in toate grupele lunii selectate

Data: 2026-10-21.

Cerinta: cautarea din antet incepe dupa minimum trei caractere, afiseaza rezultatele sub camp ca la combobox si pozitioneaza grupa/copilul ales. Se cauta exclusiv copiii lunii selectate, nu intreg nomenclatorul si nici alte luni.

Implementare:

- Campul nativ din antet a fost inlocuit cu Combobox-ul custom din components. Prag de trei caractere utile, debounce 300 ms; nicio cerere sub prag sau fara luna/subunitate selectata.
- Endpoint GET `/api/adechit/child-search?IDL=...&q=...`, cu aceleasi drepturi read si delimitare pe unitate/subunitate ca restul ADECHIT.
- Selectia porneste din AD_Prezenta, restrictionata la IDL si SubunitId, cu legaturi la copil si grupa istorica a prezentei. Nu incarca toti platitorii, nu recalculeaza situatia tuturor grupelor si nu cauta in alte luni. Numele si CNP-ul raman criterii de cautare, ca in campul anterior.
- Rezultatele includ fiecare inregistrare lunara potrivita, numele, grupa si numai ultimele patru cifre CNP pentru deosebirea omonimelor. Sunt returnate maximum 100 de rezultate; un mesaj cere restrangerea cautarii cand sunt mai multe. Numele sunt afisate ca text prin controlul comun, fara HTML din date.
- Selectarea confirma editarea celulei curente daca este cazul, deschide grupa si selecteaza/deplaseaza grila la IDZ-ul copilului, inclusiv documentele acestuia prin onSelect existent. Daca grupa este ascunsa, activeaza afisarea grupelor ascunse. Filtrele din subsol sunt golite pentru ca rezultatul ales explicit sa fie vizibil.
- Cautarea din antet nu mai filtreaza grila grupei. Schimbarea lunii/unitatii/subunitatii invalideaza rezultatele; cererile vechi sunt anulate sau ignorate. O alegere invechita nu poate schimba luna curenta sau selecta copilul in alta luna.
- Logica de cautare este separata in module proprii; CSS-ul specific este importat prin stylesheet-ul de intrare ADECHIT. Resursele se elibereaza la iesirea din pagina sau expirarea sesiunii.

Fisiere:

- `routes/adechit/child_search.py`
- `routes/adechit/__init__.py`
- `static/js/adechit/child-search.js`
- `static/js/adechit/app.js`
- `static/adechit.html`
- `static/css/adechit-child-search.css`
- `static/css/adechit.css`

Stare: cod implementat in surse, fara publicare. Analiza de proiectare pe surse; nu s-au rulat teste, verificari de sintaxa, interogari sau probe in browser/server, conform instructiunilor. Nicio schema sau configuratie modificata.
