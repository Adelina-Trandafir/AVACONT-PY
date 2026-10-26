# Infrastructură comună de rapoarte

`excel_widths.py` nu importă ADECHIT, VERCON, Flask sau modele business. Aplicația furnizează identificatorul raportului și cheile stabile ale coloanelor.

1. La fiecare export, `configured_widths(repo, {'report_name': NUME_RAPORT})` citește `CHEIE_COLOANA, LATIME` din `AVACONT_COMUN.RAPOARTE_EXCEL_COLOANE`. Repository-ul expune `sqlite` și `query(sql, params)`; pentru un cursor MariaDB dictionary se folosește `CursorRepository(cursor)`.
2. `column_key(column, index)` păstrează cheia explicită; în lipsa ei derivă cheia din etichetă, prin regula istorică ADECHIT.
3. Rendererul creează un `ColumnWidths(configured)` per foaie și aplică `observe(index_excel, cheie, lățime_machetă, lățime_curentă)` când întâlnește coloana. Indicele Excel începe la 1. Lățimea machetei folosește unitatea existentă `w` din modelele de raport.
4. După toate tabelele, `final(index_excel, lățime_curentă)` aplică prioritatea configurării. Mai multe tabele pe aceeași coloană Excel folosesc maximul lățimilor configurate. Fallback-ul rămâne cel istoric: 14–55 din machetă, minimum final 20. O lățime configurată poate fi mai mică de 20.

Identificatorii și cheile existente nu se redenumesc la refactorizare. ADECHIT păstrează `ADECHIT_...`; VERCON definește `VERCON_...` în adaptorul propriu. Pentru o aplicație nouă se alege un prefix stabil și se folosește același modul. Coloanele duplicate pot primi chei explicite diferite în adaptor (de exemplu `QUANTITY`, `QUANTITY_2`).

Nu există DDL, seed sau sincronizare automată. Tabela lipsă (MariaDB 1146) și SQLite folosesc fallback-ul existent; alte erori nu sunt ascunse. Importurile publice istorice `routes.adechit.report_excel.column_key/configured_widths` rămân disponibile prin reexport, fără o a doua implementare.

Dependențele renderelor PDF/Excel sunt în `PYTHON/requirements-reporting.txt`; `requirements-adechit.txt` rămâne un punct de instalare compatibil. Acest refactor este testat, rulat și publicat (confirmat de operator, 23.10.2026).


## Motorul și previzualizarea comune (SLICE-ADE9-05)

`output.py` este motorul generic PDF/Excel extras din VERCON. Primește modelul
cu `layout`, `identity`, `blocks`, `title`, `filename`, `gestiune_name` și
`period_text`. Nu importă aplicațiile sau Flask. Suportă și formule de antet,
culori de celule, text formatat (`richtext.py`) și coloane numerice opționale.
ADECHIT traduce blocurile sale în `routes/adechit/report_model.py`, păstrând
identificatorii de raport/coloană și filtrarea coloanelor. VERCON păstrează
semantica textelor exacte în Excel și rendererul special al formularului NIR.

`snapshots.py` implementează salvarea atomică pe disc, expirarea de 10 minute
și legarea de proprietar, comună ambelor aplicații. Adaptorul definește directorul
și tipul erorii. ADECHIT folosește `instance/adechit-reports/snapshots` (sau
`ADECHIT_REPORT_CONFIG_DIR`), cu proprietar email + DC + subunitate. La export,
autentificarea, drepturile și contextul sunt controlate din nou. Lățimile Excel
se citesc la fiecare export din configurarea existentă.

Controlul comun este `static/js/reporting/pdf-preview.js`, cu CSS propriu
`static/css/report-preview.css`, peste PDF.js din Portal. Antetul aplicației și
acțiunea Excel se furnizează prin opțiuni. Adaptoarele VERCON își păstrează
importurile publice, iar ADECHIT nu mai deschide pagini HTML pentru rapoartele
din panou. Rutele HTML vechi rămân pentru compatibilitate.

Publicați împreună modulele comune și adaptoarele ambelor aplicații. Nu se
suprascriu directoarele runtime din `instance/`. Fără DDL nou. Implementare
scrisă local; teste, generare de documente și probe în browser neexecutate.


## Pregătire exclusivă și loading imediat (SLICE-ADE9-07)

`static/js/reporting/report-progress.js` ține o singură operație în pregătire
per pagină. `openPdfReport(title, source, owner, details)` primește acum și o
funcție `source(view)`: componenta ia blocarea, deschide fereastra și permite
pictarea indicatorului înainte să apeleze funcția. Nu generați/descărcați
raportul înaintea acestui apel; altfel loading-ul nu poate acoperi acea parte.
Funcția întoarce bytes sau `{data, title, filename, mime, downloadOnly,
exportExcel}`; `null` încheie fără rezultat, după invalidarea contextului sau
renunțarea la selecție. `details.isCurrent` protejează rezultatele întârziate.

PDF.js notifică `onRendered` pentru pagina curentă. Preview-ul păstrează
loading-ul până la primul render reușit și eliberează blocarea în `finally`.
Un export cu `downloadOnly` închide progresul după pregătirea și predarea
fișierului browserului. Erorile sunt jurnalizate și afișate în aceeași
fereastră, cu descărcarea PDF ca rezervă dacă bytes au fost primiți.
Închiderea ferestrei nu eliberează prematur o cerere încă în desfășurare.
Blocarea este pentru pagina curentă; nu este un lock MariaDB sau între taburi.
