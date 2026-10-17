# Resursele rapoartelor VERCON

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
Rapoartele în folosință se filtrează separat după conturi și deținători.
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
comisiei în partea de jos. Denumirea unității și subsolul sunt preluate din
configurarea unității; datele din imaginea de referință nu sunt copiate ca
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
