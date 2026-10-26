# Intervenții web comune și specifice — 21.10.2026

Consolidare documentară D001-04 (24.10.2026) a patru jurnale locale din PYTHON. Nu atribuie un număr nou intervențiilor de produs și nu schimbă stările lor. Toate căile tehnice sunt relative la PYTHON.

Stare comună la predare: modificări scrise în surse, numai analiză de proiectare; fără teste, verificări de sintaxă, interogări, probe browser/server sau modificări de schemă/configurație. Căutarea copiilor era explicit nepublicată. Capturile și serverul nu au fost accesate la consolidare. Localizarea în PYTHON cerută pentru acest lucru se păstrează.

## ADECHIT — Cautarea copiilor in toate grupele lunii selectate

Sursă absorbită: `ADECHIT-cautare-copii-in-luna.md`.

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

## Editor tooltip — Familia, inaltimea in PT si culoarea textului

Sursă absorbită: `UI-tooltip-font.md`.

Adaugate trei proprietati in Setari tooltip:

- Familia fontului, prin Combobox custom: implicit aplicatie sau Calibri, Carlito, Segoe UI, Arial, Tahoma, Verdana, Times New Roman, Georgia, Consolas.
- Inaltimea fontului in puncte, prin TextBox custom numeric cu virgula/punct zecimal; interval 6–48 PT. Implicit 9 PT, echivalentul celor 12 px folositi anterior la dimensiunea de baza a aplicatiei.
- Culoarea textului, prin componenta custom ColorPicker cu selector de culoare si bifa Automat. Automat pastreaza culoarea corespunzatoare aspectului/paletei/temei tooltipului.

Proprietatile sunt incluse in normalizare, previzualizare, revenirea la valorile implicite si salvarea existenta in localStorage. Setarile vechi fara aceste proprietati primesc valorile implicite; cheia de stocare este pastrata. Datele din stocare sunt limitate la familii cunoscute, puncte in interval si culori hex valide.

Tooltipul aplica valorile prin variabile CSS gestionate de API-ul componentei. Familia selectata este respectata si de textul/antetul interior, inclusiv in paginile care impun global fontul aplicatiei. Culoarea explicita este folosita si in tema intunecata sau aspectul Colorat. Pentru culoarea automata raman valabile regulile existente de aspect/tema. Greutatea si stilul fontului nu au fost schimbate.

Fisiere:

- `static/js/components/tooltip/tooltip-config.js`
- `static/js/components/tooltip/tooltip-settings.js`
- `static/js/components/tooltip/tooltip.js`
- `static/js/components/tooltip/tooltip.css`
- `static/js/components/colorpicker/colorpicker.js`
- `static/js/components/colorpicker/colorpicker.css`

## Tooltip pe noduri exclusiv cand textul este trunchiat

Sursă absorbită: `UI-tooltip-noduri-doar-text-trunchiat.md`.

Cerinta reiterata: pe ramurile/nodurile copacilor si TreeCombo, tooltip numai daca textul nu este vizibil integral. Daca incape, niciun tooltip de nod.

Cauza identificata in surse: randurile cu node.tooltip si etichetele copacilor cu bife primeau title neconditionat; mecanismul comun de tooltip colecta acele titluri fara sa verifice decuparea textului. Parintii din breadcrumb aveau de asemenea titlu permanent.

Schimbari:

- Politica comuna bazata pe geometria textului efectiv afisat: overflow propriu al etichetei/textului din dreapta si decupare de catre parinti/viewport. Masuratorile folosesc acelasi sistem de coordonate vizuale, inclusiv zoom-ul aplicatiei.
- Mecanismul comun refuza tooltip-ul unui nod al carui text incape. Nu continua cautarea unui titlu pe stramosii nodului, evitand mostenirea unui tooltip al containerului.
- Nodurile cu text trunchiat pot afisa textul complet chiar daca nu au un tooltip configurat. Un tooltip explicit al nodului ramane conditionat de aceeasi regula.
- Titlurile neconditionate ale nodurilor si etichetelor au fost eliminate din randarea TreeView. Metadatele optionale sunt pastrate separat. Breadcrumb pastreaza numele accesibil, fara tooltip nativ permanent.
- TreeView include mecanismul comun; ComboTree il primeste prin componenta de baza. Nu sunt necesare corectii separate in fiecare aplicatie.
- Redimensionarea, schimbarea latimii parintilor, scrollul si deplasarea mouse-ului reevalueaza eligibilitatea. Un tooltip deschis se inchide daca textul a devenit complet vizibil. Observarea dimensiunilor este activa numai pentru tooltip-ul curent; miscarile mouse-ului sunt limitate la un calcul pe cadru.
- Pictogramele de actiune din rand pastreaza propriile explicatii, fara sa mosteneasca tooltip-ul nodului. Acestea sunt ajutoare pentru actiuni distincte de textul ramurii.

Fisiere:

- `static/js/components/treeview/treeview-tooltip.js`
- `static/js/components/treeview/treeview-render.js`
- `static/js/components/treeview/treeview-breadcrumb.js`
- `static/js/components/treeview/treeview.js`
- `static/js/components/tooltip/tooltip-titles.js`

## VERCON — Preluare e-Factura din meniul Intrari

Sursă absorbită: `VERCON-meniu-intrari-efactura.md`.

La cererea utilizatorului, eliminate complet cele doua butoane de preluare e-Factura din barele NIR: NIR din e-Factura in consultare si Din e-Factura la adaugare.

In meniul custom Intrari este adaugata comanda NIR din e-Factura, dupa Factura Stornare, precedata de separator. Refoloseste disponibilitatea optiunii NIR din contextul serverului si actiunea existenta de selectare/completare din efactura-intake. Cand formularul curent nu este NIR, navigheaza prin fluxul normal de deschidere, cu protectia lucrului nesalvat. Comanda asteapta incarcarea editorului, verifica contextul si dreptul de scriere si nu porneste simultan mai multe selectii. In NIR ramane confirmarea existenta pentru inlocuirea unei ciorne.

Fisiere:

- `static/js/vercon/app.js` — pozitia in meniu si deschiderea actiunii.
- `static/js/vercon/docs.js` — eliminarea butoanelor si metoda publica a editorului.

