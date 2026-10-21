# Tooltip pe noduri exclusiv cand textul este trunchiat

Data: 2026-10-21.

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

Stare: modificari scrise in surse, pentru componentele comune folosite de K-BOT, ADECHIT si VERCON. Au fost analizate sursele si proiectarea; nu s-au rulat teste, verificari de sintaxa sau probe in browser/server, conform instructiunilor. Nicio modificare de schema sau configuratie.
