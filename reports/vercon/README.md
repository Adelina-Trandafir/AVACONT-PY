# Resursele rapoartelor VERCON

<!-- slice: ADE9-07 -->
La generarea unui raport/document sau la export, fereastra apare imediat cu
indicator de încărcare. Cât timp operația este în pregătire nu pornește alt
raport în aceeași pagină. Indicatorul dispare după afișarea primei pagini PDF;
la descărcare, fereastra se închide după pregătirea fișierului. Erorile apar în
aceeași fereastră. Implementare locală, fără probe funcționale/vizuale.

Directorul `queries/` conține interogările SELECT folosite la generarea
rapoartelor. Aceste fișiere sunt resurse ale aplicației: se copiază pe server,
nu se execută separat și nu instalează/modifică schema bazei de date.

Fiecare marker de tabel trebuie urmat de un alias, de exemplu
`FROM {{TABLE:VC_ComisieNIR}} c`. La rulare, markerul devine un subquery
limitat la contextul utilizatorului, iar MariaDB cere aliasul acestuia.
Pentru configurări personalizate salvate anterior, aliasurile se corectează
în editorul de rapoarte; publicarea nu suprascrie acele configurări.

La publicarea arborelui `PYTHON` către `/root/AVACONT` se păstrează:

- `reports/vercon/queries/*.sql` — toate interogările, inclusiv `metadata.sql` și sursele OI;
- `static/fonts/DejaVuSans.ttf` și `static/fonts/LICENSE-DejaVu.txt` — fontul PDF și licența;
- `static/fonts/DejaVuSansMono.ttf`, `DejaVuSansMono-Bold.ttf` și
  `DejaVuSansMono-Oblique.ttf` — fonturile machetei NIR;
- modulele backend/frontend modificate, urmate de repornirea serviciului pentru codul Python.

`PYTHON/.pushignore` include explicit aceste resurse peste regula generală
`*.sql`. Versiunile actualizate ale KBotAdmin și AvacontPush aplică includerea
explicită și peste filtrul extensiilor. Dacă se folosește un executabil vechi,
extensiile `sql`, `ttf` și `txt` trebuie bifate înaintea scanării; regula generală
de ignorare SQL rămâne activă pentru celelalte directoare. Scriptul
`tools/Push-Changed.ps1` include aceleași resurse, cu protecția fișierelor de
configurare păstrată. Nu folosiți `-Baseline` pentru fișiere care încă lipsesc
de pe server: acesta doar memorează starea locală, fără să trimită fișiere.

Configurările proprietarului din `instance/vercon-reports/` rămân în afara
publicării și nu trebuie suprascrise cu valorile implicite.

Un răspuns `503 / REPORT_RESOURCE_MISSING` indică fișierul care lipsește din
instalare. `401 / SESIUNE_EXPIRATA` este separat: portalul nu mai acceptă
sesiunea transmisă și este necesară autentificarea. Nu se ocolește verificarea
sesiunii pentru generarea sau descărcarea rapoartelor.

## Obiecte de inventar

Sursele OI suplimentare sunt `oi_documents.sql`, `custody_ledger.sql`,
`oi_accounts.sql` și `oi_holders.sql`. Sunt SELECT-uri contextualizate,
editabile în KBotAdmin, cu aceeași versionare ca sursele Magazie/Alimente.
Nu sunt scripturi de instalare și nu se execută manual.

Contul din nomenclatorul produsului reprezintă proveniența din magazie.
Contul în folosință provine din fiecare linie BP/BT/BS; nu se deduce din
contul produsului și nu există un singur cont implicit pentru gestiune.
În pagina Rapoarte OI, MAGAZIE rămâne rădăcină separată, cu toate rapoartele
și grupările existente, ca în celelalte gestiuni. Numai ramura OBIECTE DE
INVENTAR cuprinde cele 11 rapoarte din referința Access, în ordinea dată.
Cele două niveluri de grupare sunt Root (OBIECTE DE INVENTAR) și Niv1
(BONURI, PRODUSE, PRIMITORI), urmate de frunzele rapoartelor.
Selecția datelor reutilizează mecanismul MF: primitor → bon → produs pentru
bonuri, cont → produs pentru PRODUSE și primitor → cont → produs pentru
PRIMITORI. Nu apar ramurile independente „Conturi în folosință” și
„Deținători”. Identitatea bifelor păstrează legătura cont–primitor–produs,
respectiv primitor–bon–produs; nu combină independent filtrele bifate.
Cheia internă `mf_selection` și identificatorii `mf-item`/`mf-branch` sunt
protocolul comun reutilizat de OI și MF; sursele și calculele OI se păstrează.
Aliasurile folosite direct de documente rămân disponibile prin API.
Listele totale însumează deținătorii numai în aceeași pereche cont–variantă.
Transferurile apar cu minus la predător și plus la primitor, pe același cont.

Balanța, soldurile și fișele evaluează la prețul curent al variantei, conform
contractului comun. Bonurile și centralizatoarele păstrează prețurile și
valorile liniilor documentelor. Intervalele OI pot traversa anii; soldul
inițial include mișcările anterioare începutului intervalului.

Ecranul, PDF-ul și Excel-ul folosesc același snapshot. Lățimile Excel
continuă să fie citite de modulul comun din configurarea existentă.
Machetele implicite OI sunt funcționale în surse, dar fidelitatea lor
vizuală și integrarea pe date reale nu sunt încă validate.

Corecții 2026-10-23: Balanța OI folosește aceeași machetă ca balanța de
magazie (`Balanta_produse`), inclusiv configurarea existentă, cu titlul OI.
Are un rând pe cont–variantă, fără grupare pe deținător. Soldul inițial
include predările minus casările anterioare perioadei; Intrări reprezintă
numai predările din perioada aleasă, Ieșiri numai casările din perioadă,
iar soldul final este SI + Intrări − Ieșiri. Transferurile nu participă la
calcul și nu au coloane în această balanță. Fișele pe primitor păstrează
istoricul transferurilor necesar stabilirii deținerii.

În PRIMITORI, primul nivel din Selectare date este primitorul, apoi contul
și produsele sale. Rapoartele MF își păstrează gruparea existentă.

Generarea rapoartelor din browser trimite acum filtrele prin POST cu corp
form-url-encoded; URL-ul păstrează numai subunitatea, gestiunea și anul.
Același contract este folosit la schimbarea perioadei în vizualizator.
Exporturile snapshotului nu retrimit filtrele în URL. GET rămâne disponibil
pentru clienții existenți. Schimbarea elimină cererile de generare cu URL-uri
foarte lungi; eroarea HTTP 400 raportată nu a fost reprodusă și cauza exactă
de pe server nu este confirmată fără jurnalul cererii.

## Corecții BC în luna activă și istoricul recalculărilor (2026-10-23)

BC-urile pot fi modificate în orice ordine în luna activă, cu exercițiul
deschis, dreptul Consum, blocarea gestiunii și versiunea curentă. În Alimente
se folosește ultima lună cu liste active, cu fallback la ultima lună cu BC
active dacă nu există liste. În Materiale se folosește ultima lună cu BC
active. Se caută pe toate exercițiile; documentele de stoc dintr-un exercițiu
ulterior blochează modificarea anului anterior. Bonurile cu retur activ ori
cu listă asociată din afara lunii active sunt protejate. Anularea păstrează
regula ultimului BC și interdicția anulării BC asociat unei liste.

La salvare se verifică toate datele din istoricul variantelor vechi și noi
prin `protocol.check_timeline`, după înlocuirea provizorie a bonului în
tranzacție. Intrările preced ieșirile aceleiași zile. Orice sold negativ
respinge întreaga tranzacție, inclusiv recalcularea listei și auditul.
Mișcările fără dată/cantitate produc eroare explicită, nu cantitate zero.

Lista asociată se recalculează în aceeași tranzacție, chiar dacă nu este
ultima din luna activă. Bonurile ulterioare își păstrează liniile și prețurile;
recalcularea stocului lor la sfârșit de zi este consemnată în istoricul BC
afectat cu înainte/după și cauza (BC sursă, număr, dată, ID și versiune).
Soldurile DEP/EC și cumulative ale listelor afectate primesc înregistrări
similare, inclusiv după modificarea porțiilor unei liste. Nu se rescriu
artificial antetele sau meniurile listelor ulterioare.

`recalculation_audit.py` citește soldurile înainte/după în tranzacția
blocată, folosind aceleași surse și calcule ca rapoartele. Istoricul folosește
VC_Audit existent; nu necesită DDL. Interfața comună de istoric arată motivul
și valorile înainte/după în înregistrări expandabile, cu acces și din pagina
listelor de alimente. Helpul descrie ambele reguli de modificare în ultima
lună activă și protecția stocului.

Schimbări scrise local, fără teste, verificări de execuție sau probe pe server.

## Antetul bonurilor P/T/C și Consum (2026-10-23)

Bonurile de predare, transfer și casare OI/MF și bonurile de consum folosesc
același antet tabelar după referința furnizată. Retururile de consum folosesc
aceeași structură, cu titlul propriu. Antetul cuprinde denumirea gestiunii
în celula superioară stângă, numărul documentului, Data cu Ziua/Luna/Anul,
tipul gestiunii, Predător, Primitor și Nr. comandă. Titlul bonului ocupă
celula din dreapta, unită pe înălțimea celor patru rânduri.

Datele provin din bonul și gestiunea curente. La predare/consum, predătorul
este Magazie; la transfer/casare se folosește predătorul documentului. La
casare, Primitor rămâne gol. La retur, primitorul este Magazie. Nr. comandă
se afișează numai dacă sursa îl furnizează; nu se înlocuiește cu numărul
bonului. Explicațiile se păstrează sub antet. Datele din imaginea de referință
nu sunt introduse ca valori fixe.

Structura se aplică la generare și machetelor personalizate existente,
numai în memorie, păstrând configurația salvată. Fiecare bon primește propriul
antet, iar titlul și perioada nu se mai repetă separat deasupra lui.
Snapshot-urile deja generate rămân cu structura lor; generați din nou bonul
după publicare.

`routes/vercon/report_voucher.py` construiește antetul comun; banda
`form_grid` din `reporting/form_grid.py` gestionează celulele unite în PDF
și Excel. Excel păstrează contractul `reporting/excel_widths.py`, cu chei
stabile `ANTET_BON_*`; înălțimile antetului sunt calculate după aplicarea
lățimilor finale, inclusiv cele configurate. Previzualizările locale ale
ciornelor și listarea fără dreptul de rapoarte folosesc aceeași structură
prin `static/js/vercon/voucher-header.js`. Bonurile MF salvate folosesc
rapoartele oficiale MF când utilizatorul are dreptul necesar.

Implementare locală, fără teste, randări sau verificări de execuție.

## Macheta NIR

FCS (`NIR_Storno`) folosește exact aceeași machetă și același titlu ca NIR.
Citirea și salvarea configurației de machetă FCS folosesc `layout-nir.json`,
inclusiv în editor: o modificare a machetei se aplică ambelor rapoarte.
Un eventual fișier vechi `layout-nir_storno.json` este păstrat pe disc,
dar nu mai este utilizat. Selecția datelor rămâne specifică documentului FCS.
Cantitățile FCS rămân pozitive la salvare. Modelul de afișare al documentului
FCS prezintă cantitatea, valoarea, TVA-ul disponibil și valoarea antetului cu
minus (`-abs`); prețul unitar rămâne neschimbat. Totalurile se formează din
liniile astfel semnate, pentru PDF, Excel și rezultatul în pagină. O valoare
deja negativă rămâne negativă, iar zero se afișează fără minus. Se modifică
numai copia pentru raport, nu rândurile din baza de date sau formularul de
editare. Previzualizarea locală de rezervă folosește aceeași convenție.
Centralizatoarele și calculul stocurilor păstrează tratarea existentă a
semnului, fără o a doua scădere a stornării.

Machetele implicite NIR/NIR_SFP folosesc `form: nir-classic`: A4 portret,
antet compact cu celule reunite, font monospațiat, opt coloane de produse,
total numai în dreapta, anularea spațiului liber cu diagonală și certificarea
comisiei în partea de jos. Denumirea unității provine din datele comune, iar
subsolul din configurarea raportului; datele din imagine nu sunt copiate ca
valori fixe. Nu se tipărește intervalul anual în această machetă.
Coloanele se încadrează în lățimea utilă a paginii portret; fontul implicit
este de 8pt, iar titlul de 16pt. Previzualizarea VERCON se deschide la 100%.

Rendererul dedicat este `routes/vercon/report_reception.py`, selectat de
`report_output.py`; `report_service.py` atașează datele documentelor la același
snapshot autentificat. Totalurile provin din valorile salvate ale liniilor.
Cantitatea conform documentului ascunde numai zerourile zecimale finale;
cantitățile fracționare nu se rotunjesc la întreg. Numărul NIR include subnumărul,
iar codul articolului folosește identificatorul variantei când COD nu este completat.

Machetele personalizate salvate în `instance/` nu sunt modificate automat.
O machetă personalizată fără `form` păstrează rendererul generic. Formularul
`nir-classic` fixează structura celulelor după referință; coloanele/grupările
libere ale editorului rămân pentru formularul `generic`.

Fonturile Mono sunt redistribuite sub licența DejaVu existentă, din
[distribuția Matplotlib v3.10.7](https://github.com/matplotlib/matplotlib/tree/v3.10.7/lib/matplotlib/mpl-data/fonts/ttf).
La publicare sunt necesare noile fonturi și modulele Python modificate, apoi
repornirea serviciului. Raportul se generează din nou după publicare;
snapshot-urile existente păstrează macheta cu care au fost create.

## Antet comun și identitatea unității (2026-10-19)

Toate rapoartele folosesc datele reale ale unității din
AVACONT_COMUN.Unitati/Unitati_Date și denumirea subunității active din
VC_Subunitati: nume, cod fiscal și adresă în stânga, subunitate în dreapta.
Antetul este comun PDF-urilor generice, NIR/FCS, Excel-ului, rezultatului
în pagină și snapshot-urilor PDF locale. Se repetă pe paginile PDF și
la imprimarea Excel-ului.

Numele bazei de date nu se tipărește și nu este fallback pentru numele
unității. Dacă identificarea necesară lipsește, raportul cere completarea
acesteia. Definițiile vechi care conțin DC în textele de prezentare sunt
adaptate numai în modelul din memorie; datele economice și fișierele de
configurare nu se rescriu. Antetele de unitate/machetă sunt înlocuite la
randare de antetul comun; managerul și subsolul configurat rămân disponibile.
Tokenul {SUBUNITATE} poate fi folosit și în benzile de text ale raportului.

Snapshot-urile generate anterior schimbării cer regenerare la export.
Publicați împreună modulele Python/JS/CSS modificate și reporniți serviciul.
Schimbarea este scrisă local, fără teste, generări PDF/Excel sau verificări
vizuale/automate, conform instrucțiunilor proiectului.

La citirea rapoartelor de stoc se păstrează prețurile istorice cu semn,
inclusiv cele negative, conform politicii importului. Prețul lipsă nu
este convertit în zero: eroarea identifică produsul, varianta, codul și
contul. Pentru corecție este necesar prețul real din datele sursă.
Pozițiile deja excluse din raportul final/ balanță prin opțiunile de
afișare sunt eliminate înaintea validării prețului de evaluare.
Selecțiile se ordonează alfabetic pentru texte, cronologic pentru date
și numeric pentru prețuri; etichetele compuse au chei separate după tip.

Fișa de magazie acceptă mai multe produse și variante de preț în aceeași
generare (Fisa_Magazie, FM1-u, FM2, Fisa_Magazie_BF). Fiecare variantă
are o fișă proprie și începe pe pagină nouă în PDF și la imprimarea Excelului.
Produsele sunt alfabetice, prețurile numerice, iar soldurile și totalurile
rămân independente pe variante, inclusiv când două variante au același preț.
