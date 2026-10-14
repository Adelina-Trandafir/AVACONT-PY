# Infrastructură comună de rapoarte

`excel_widths.py` nu importă ADECHIT, VERCON, Flask sau modele business. Aplicația furnizează identificatorul raportului și cheile stabile ale coloanelor.

1. La fiecare export, `configured_widths(repo, {'report_name': NUME_RAPORT})` citește `CHEIE_COLOANA, LATIME` din `AVACONT_COMUN.RAPOARTE_EXCEL_COLOANE`. Repository-ul expune `sqlite` și `query(sql, params)`; pentru un cursor MariaDB dictionary se folosește `CursorRepository(cursor)`.
2. `column_key(column, index)` păstrează cheia explicită; în lipsa ei derivă cheia din etichetă, prin regula istorică ADECHIT.
3. Rendererul creează un `ColumnWidths(configured)` per foaie și aplică `observe(index_excel, cheie, lățime_machetă, lățime_curentă)` când întâlnește coloana. Indicele Excel începe la 1. Lățimea machetei folosește unitatea existentă `w` din modelele de raport.
4. După toate tabelele, `final(index_excel, lățime_curentă)` aplică prioritatea configurării. Mai multe tabele pe aceeași coloană Excel folosesc maximul lățimilor configurate. Fallback-ul rămâne cel istoric: 14–55 din machetă, minimum final 20. O lățime configurată poate fi mai mică de 20.

Identificatorii și cheile existente nu se redenumesc la refactorizare. ADECHIT păstrează `ADECHIT_...`; VERCON definește `VERCON_...` în adaptorul propriu. Pentru o aplicație nouă se alege un prefix stabil și se folosește același modul. Coloanele duplicate pot primi chei explicite diferite în adaptor (de exemplu `QUANTITY`, `QUANTITY_2`).

Nu există DDL, seed sau sincronizare automată. Tabela lipsă (MariaDB 1146) și SQLite folosesc fallback-ul existent; alte erori nu sunt ascunse. Importurile publice istorice `routes.adechit.report_excel.column_key/configured_widths` rămân disponibile prin reexport, fără o a doua implementare.

Dependențele renderelor PDF/Excel sunt în `PYTHON/requirements-reporting.txt`; `requirements-adechit.txt` rămâne un punct de instalare compatibil. Acest refactor este scris local, fără rularea testelor sau a interogărilor.
