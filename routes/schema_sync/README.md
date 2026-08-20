# schema_sync — sincronizare structură baze de date

Aduce structura fiecărei baze de unitate la forma din `AVACONT_SURSA`:
tabele, coloane, chei primare, indexuri, chei străine și — nou — setul de
caractere (charset / collation) al coloanelor de text.

Scris integral în Python. Înlocuiește procedurile stocate
`proc_SchemaDiff_DDL`, `proc_SchemaDiff_CreateTable`,
`proc_ExecuteSchemaDiff` și funcția `fn_sql_default`.

---

## Starea pasului de deblocare a cheilor străine

Verificat pe un server MariaDB **10.3.32** ridicat special pentru asta, cu
o bază de test construită instrucțiune cu instrucțiune (2026-08-20).

Avertismentul de dinainte — «pasul pune `ADD CONSTRAINT` la loc, dar nu
emite `DROP FOREIGN KEY` înainte» — **era greșit**. Pasul emitea corect
și ștergerea, și recrearea. Ce se stricase era baza de test: fusese
construită ca un singur lot, lotul se oprise la prima eroare, iar cele
două chei străine nu ajunseseră niciodată să existe. Neexistând nicio
cheie în țintă, nu era nimic de desfăcut; cele două `FK CREATE` din
rezumat veneau din comparația obișnuită («cheia lipsește în țintă»), nu
din pasul de deblocare.

Verificarea a scos însă la iveală **patru defecte reale** ale pasului,
toate reproduse și toate corectate:

| Nr. | Ce se întâmpla | Eroarea |
|---|---|---|
| 1 | O coloană modificată în modul FORCE care își schimba **și** tipul, **și** charset-ul nu era înregistrată ca «recolaționată», deci cheia care o bloca nu era ștearsă | 1832 / 1833 |
| 2 | Cheia recreată era construită din definiția **sursei**, iar rescrierea `AVACONT_SURSA` → baza țintă se compara cu majuscule/minuscule exacte; unde serverul întoarce numele cu litere mici, rescrierea nu se făcea și unitatea rămânea cu o cheie **spre șablon** | 1452, sau tăcut |
| 3 | O cheie aflată pe un tabel care urma să fie **șters** era pusă la loc după ștergerea tabelului | 1146 |
| 4 | O cheie pe care sursa **nu o are** era ștearsă corect de comparația obișnuită, apoi **înviată** de pasul de deblocare | niciuna — tăcut |

După corectare, pe aceeași bază de test: `--run --mode FORCE
--allow-destructive` — **11 instrucțiuni, 11 reușite, 0 eșecuri**; a doua
rulare, în ambele moduri, generează **zero** instrucțiuni.

### O limită care rămâne

În modul **SAFE**, dacă o coloană diferă *și* la tip, *și* la charset, nu
se emite nimic pentru ea: SAFE nu modifică tipuri, iar repararea de
charset este pe ramura următoare a aceleiași verificări. Charset-ul
rămâne nereparat până la o rulare `FORCE`. Repararea de charset rulează
în ambele moduri **doar** când charset-ul este singura diferență.

O cheie străină dintr-**altă** bază de unitate care arată spre o coloană
reparată aici nu poate fi văzută (se citește doar ținta) și va bloca
modificarea cu 1833. Nu apare în structura actuală, unde referințele
încrucișate merg doar spre `AVACONT_COMUN`.

---

## Instalare

Fișierele stau în `routes/schema_sync/`. E nevoie de un fișier
`__init__.py` gol în acel folder.

```
AVACONT-PY/
├── config.py                     ← DB_CONFIG (există deja)
├── utils/logger.py               ← de aici se ia RequestIPFilter
└── routes/schema_sync/
    ├── __init__.py               ← gol, dar obligatoriu
    ├── schema_common.py
    ├── schema_introspect.py
    ├── schema_diff.py
    ├── schema_generate.py
    ├── schema_execute.py
    └── schema_sync.py
```

Se rulează **din rădăcina proiectului**, cu `-m`:

```
cd C:\cale\catre\AVACONT-PY
python -m routes.schema_sync.schema_sync --view --mode SAFE
```

Rularea directă (`python routes\schema_sync\schema_sync.py`) **nu
funcționează** — importurile relative cer forma cu `-m`.

---

## Ce face fiecare fișier

| Fișier | Rol |
|---|---|
| `schema_common.py` | conectare, jurnalizare, lista de baze țintă, ordinea de execuție |
| `schema_introspect.py` | citește `information_schema` și o transformă în obiecte Python |
| `schema_diff.py` | compară sursa cu ținta și scrie instrucțiunile DDL |
| `schema_generate.py` | rulează comparația și o salvează în `schema_diff_log` |
| `schema_execute.py` | execută ce s-a salvat |
| `schema_sync.py` | le combină pe amândouă — fișierul pe care îl rulați |

---

## Cum funcționează, pe scurt

Lucrul se face în două faze separate.

**Faza 1 — generare.** Se citește structura din `AVACONT_SURSA` și din
fiecare bază țintă, se compară în Python, iar diferențele se scriu ca
instrucțiuni SQL în tabelul `AVACONT_COMUN.schema_diff_log`. **Nu se
modifică nimic** în această fază.

**Faza 2 — execuție.** Se citesc instrucțiunile din acel tabel și se
rulează, una câte una, în ordinea corectă. Fiecare rând primește data
execuției și, dacă a eșuat, numărul erorii MariaDB.

Separarea permite să vedeți exact ce urmează să se întâmple înainte ca
ceva să se întâmple.

### De unde vin bazele țintă

Din `AVACONT_COMUN.CAI`, coloana `DbName`, valori distincte. O bază
trecută în `CAI` dar care nu există pe server este ignorată, cu avertisment
în jurnal. `AVACONT_COMUN` și `AVACONT_SURSA` nu pot fi niciodată ținte.

### Ordinea de execuție

Contează, pentru că MariaDB refuză multe operații dacă nu sunt făcute în
ordine. Instrucțiunile se execută astfel:

| Nr. | Operație |
|---|---|
| 1 | ștergere chei străine |
| 2 | ștergere indexuri |
| 3 | ștergere / modificare chei primare |
| 4 | ștergere coloane |
| 5 | ștergere tabele |
| 6 | creare tabele |
| 7 | adăugare / modificare / redenumire coloane |
| **8** | **reparare charset (collation)** |
| 9 | creare chei primare |
| 10 | creare indexuri |
| 11 | creare chei străine |
| 99 | curățare marcaje `rename:` din `AVACONT_SURSA` |

Repararea charset-ului stă la pasul 8: **după** ce coloanele există, dar
**înainte** de a se construi vreo cheie peste ele. O cheie construită
peste coloane cu seturi de caractere diferite eșuează cu eroarea 3780.

`CREATE TABLE` (pasul 6) **nu conține chei străine**. Ele se emit separat, la
pasul 11. Tabelele se creează în ordine alfabetică, deci o cheie scrisă în
interiorul lui `CREATE TABLE` poate arăta spre un tabel la care lotul nu a
ajuns încă — `FX_Extrase` spre `FX_Extrase_H` este exact cazul, și pică cu
eroarea 1005 / 150. Scoase la pasul 11, toate tabelele există deja.

---

## O singură bază, pentru probe

`--targets` primește numele bazei și lucrează **numai** pe ea, sărind peste
tot ce e trecut în `CAI`:

```
python -m routes.schema_sync.schema_sync --view --targets 000_DEMO
python -m routes.schema_sync.schema_sync --run --mode FORCE --targets 000_DEMO --allow-destructive
```

Mai multe baze se despart prin virgulă: `--targets 000_DEMO,018_GRRS`.
Opțiunea merge la fel și cu `schema_generate`, și cu `schema_execute`.

**Un nume greșit oprește rularea, nu o continuă.** O bază trecută în `CAI`
dar absentă de pe server este doar ignorată, cu avertisment — registrul are
voie să fie înaintea realității. Un nume **tastat de mână** e altceva: fără
verificarea asta, `--targets 000_DEMOO` scria un avertisment, apoi
«Schemele sunt deja sincronizate», apoi ieșea cu codul 0 — adică exact ce
se vede după o rulare curată pe baza corectă.

---

## SAFE și FORCE

**SAFE** — adaugă și repară, dar nu strică nimic din ce nu trebuie:
creează tabele, coloane, indexuri și chei care lipsesc, și repară
charset-ul. Nu modifică tipul unei coloane existente.

**FORCE** — în plus, modifică ce diferă: tipuri de coloane, chei primare,
indexuri și chei străine cu altă definiție.

Repararea charset-ului rulează **în ambele moduri**. Nu e o preferință, e
o corecție: lăsată nefăcută, blochează crearea cheilor mai târziu.
Excepție: dacă aceeași coloană diferă *și* la tip, în SAFE nu se atinge
deloc — vezi «O limită care rămâne», mai sus.

---

## Legăturile spre alte baze (`AVACONT_COMUN`)

O cheie străină care arată spre `AVACONT_SURSA` înseamnă «schema proprie» și
este **rescrisă** spre baza țintă. Una care arată spre `AVACONT_COMUN` este
lăsată așa cum e: aceea chiar este o legătură între baze, intenționat.

`AVACONT_COMUN` **nu este niciodată sincronizată** de acest program. Deci tot
ce ține de ea — tabelele-părinte și coloanele lor — trebuie să fie deja
corect înainte de rulare.

### Ce se verifică înainte de a crea o cheie

Înainte ca vreo instrucțiune să ajungă pe server, fiecare cheie care urmează
să fie creată este trecută prin trei verificări:

1. **există tabelul și coloana spre care arată?** Dacă lipsesc în
   `AVACONT_COMUN`, se spune limpede că acolo nu se poate repara de aici;
2. **se potrivesc seturile de caractere?** Comparația se face cu forma pe
   care coloanele o vor **avea la sfârșit**, nu cu cea de acum: în interiorul
   țintei, coloana referită este reparată la pasul 8, adică înainte ca cheia
   să fie refăcută la pasul 11. Pentru `AVACONT_COMUN`, starea de acum **este**
   starea finală, fiindcă nimic nu o schimbă;
3. **se potrivesc datele?** Se numără rândurile din țintă a căror valoare nu
   există în tabelul-părinte. Acestea sunt rândurile care ar face
   `ADD CONSTRAINT` să eșueze cu eroarea 1452.

Verificarea datelor (3) se face **numai dacă toate coloanele implicate
există deja**. O coloană pe care chiar acest lot urmează să o adauge (pasul
7) nu poate fi numărată — întrebarea ar fi `Unknown column`, eroarea 1054.
În cazul acela cheia se creează fără verificare prealabilă, iar dacă datele
nu se potrivesc, eșecul apare la execuție, cu 1452, și este scris în
`schema_diff_log`.

Ce nu trece este scris în `schema_diff_log` cu un mesaj în `error_msg` și
**nu se execută** — rândurile cu eroare sunt sărite. Mesajul numește și
câteva dintre valorile vinovate:

```
Cheia `fk_ist_tip` nu poate fi creată: 3 rânduri din `000_DEMO`.`FX_Istoric`
au în `TipRand` valori care nu există în `AVACONT_COMUN`.`DefaTipRand`
(de exemplu «X», «Z»). Datele trebuie corectate întâi.
```

### Lista completă: `schema_diff/blocaje_<dată>.json`

Mesajele din jurnal dau trei exemple — destul cât să știți că e o problemă,
prea puțin cât să o reparați. Lista întreagă se scrie, la fiecare generare
care găsește blocaje, într-un fișier JSON alături de `.sql`:

```
2 chei nu se pot crea din cauza datelor. Lista completă:
/root/AVACONT/schema_diff/blocaje_20260820_093144.json
```

Pentru fiecare cheie blocată fișierul conține:

| Câmp | Ce e |
|---|---|
| `baza`, `tabel`, `cheie` | unde anume |
| `tip` | `date_orfane`, `charset_diferit` sau `structura_lipsa` |
| `motiv` | aceeași frază ca în jurnal |
| `coloane`, `refera` | ce coloană arată spre ce tabel |
| `randuri_afectate` | numărul **exact**, nu al eșantionului |
| `valori` | fiecare valoare fără corespondent, cu `chei_primare` — **rândurile exacte** care o poartă |
| `sql_inspectare` | `SELECT`-ul gata scris care le listează pe toate |
| `esantion_limitat` | `true` dacă listarea s-a oprit la 500 de rânduri |

Deci: `randuri_afectate` spune cât de mare e problema, `chei_primare` spune
exact ce rânduri să deschideți, iar `sql_inspectare` se poate copia direct în
client.

Se scrie și la `--view`, fiindcă blocajele se află la generare, nu la
execuție.

### De ce se amână și ștergerea, nu doar crearea

Dacă o cheie **există deja** în țintă și urma să fie desfăcută doar ca să se
poată schimba setul de caractere de sub ea, atunci refuzul de a o recrea nu
poate fi luat singur. Ștergerea ar rămâne, cheia s-ar pierde definitiv, iar
programul ar lăsa baza mai puțin consistentă decât a găsit-o.

De aceea, într-un asemenea caz **stau pe loc toate**: ștergerea cheii,
schimbarea de charset de pe ambele capete și recrearea. Se scrie:

```
Amânat: cheia `fk_pl_cod` de pe `FX_PlatiLinii` nu se poate reface (...),
deci nu se șterge și nu se schimbă setul de caractere sub ea — altfel cheia
s-ar pierde definitiv.
```

Restul lotului merge mai departe. După ce datele sunt corectate, o rulare
nouă face ce a rămas.

Situația asta apare doar dacă cineva a încărcat date cu
`FOREIGN_KEY_CHECKS=0`: cât timp cheia e activă, InnoDB nu lasă să intre
rânduri fără părinte.

### O limită de fond

O coloană aflată sub o cheie spre `AVACONT_COMUN` **nu-și poate schimba
setul de caractere singură.** MariaDB cere ca cele două capete ale unei chei
să aibă aceeași colaționare, nu doar același charset (altfel 1005 / errno
150), deci ori se mută amândouă, ori niciunul. Cum `AVACONT_COMUN` nu este
sincronizată de aici, o astfel de diferență se raportează, dar nu se repară
— se corectează în `AVACONT_COMUN`, separat.

---

## Operațiile distructive

Sunt marcate `is_destructive` următoarele: ștergerea unui tabel, a unei
coloane, a unui index sau a unei chei; modificarea unei chei primare; și
îngustarea unui charset (`utf8mb4` → `utf8`).

**Fără `--allow-destructive` nu se execută NIMIC** — nici măcar
instrucțiunile nedistructive din același lot. Este un refuz total, nu o
sărire selectivă: multe operații distructive sunt primul pas dintr-o
pereche (întâi `DROP FOREIGN KEY`, apoi `ADD CONSTRAINT`), iar executarea
doar a celei de-a doua lasă structura mai rău decât dacă nu se începea.

Cu `--allow-destructive`, înainte de execuție:

1. se face automat o copie de siguranță cu `mysqldump`, pentru fiecare
   bază afectată, iar fișierul este verificat că nu e gol;
2. vi se cere să tastați exact `DA`.

Dacă `mysqldump` nu există pe calculatorul de pe care rulați, operațiile
distructive sunt refuzate. `--skip-backup` ocolește asta, pe răspunderea
dumneavoastră.

### Nu există anulare

Instrucțiunile DDL (`ALTER`, `DROP`, `CREATE`) produc în MariaDB un
*commit implicit*: tranzacția curentă se închide singură înainte ca
instrucțiunea să ruleze. Un `ROLLBACK` după un `ALTER TABLE` nu anulează
nimic.

De aceea singura revenire este copia de siguranță, restaurată manual.
Când o execuție eșuează, programul afișează comanda exactă de restaurare.
Nu o rulează singur — restaurarea pierde tot ce s-a scris în baza
respectivă după momentul copiei.

---

## Comenzi

Se pune `python -m routes.schema_sync.` înaintea fiecăreia.

```
schema_sync --view --mode SAFE
```
Calculează diferențele, le scrie într-un fișier `.sql` în folderul
`schema_diff/`, afișează
rezumatul. **Nu execută nimic.** Modul recomandat pentru început.

```
schema_sync --mode SAFE
```
Calculează, afișează, întreabă, execută dacă răspundeți `da`.

```
schema_sync --run --mode SAFE
```
Calculează și execută fără a întreba. Operațiile distructive rămân
refuzate fără `--allow-destructive`.

```
schema_sync --run --mode FORCE --allow-destructive --backup-dir D:\backup
```
Sincronizare completă, cu copie de siguranță și confirmare tastată.

```
schema_sync --drop-legacy --view
```
Șterge procedurile stocate vechi. Se face o singură dată.

### Opțiuni

| Opțiune | Efect |
|---|---|
| `--view` | doar afișează; nu execută |
| `--run` | execută fără a întreba |
| `--mode SAFE\|FORCE` | implicit `SAFE` |
| `--targets 000_DEMO,018_GRRS` | doar aceste baze, în loc de tot ce e în `CAI`; un nume inexistent oprește rularea |
| `--out fisier.sql` | unde se scrie SQL-ul generat; implicit `schema_diff/schema_diff_<dată>.sql`. Folderul se creează singur și este ignorat de git |
| `--allow-destructive` | permite operațiile distructive |
| `--backup-dir CALE` | unde se scriu copiile; implicit `backup` |
| `--skip-backup` | fără copie de siguranță — **nerecomandat** |
| `--continue-on-error` | continuă după o eroare; implicit se oprește |
| `--no-reset` | păstrează instrucțiunile negenerate din rulările anterioare |
| `--drop-legacy` | șterge procedurile stocate înlocuite |
| `--verbose` | afișează fiecare instrucțiune SQL |

Cele două faze se pot rula și separat, cu `schema_generate` și
`schema_execute`, aceleași opțiuni.

---

## Redenumirea unei coloane

Ca să redenumiți o coloană în toate bazele, puneți în `AVACONT_SURSA`, în
comentariul coloanei **noi**, numele **vechi**:

```
rename:NumeVechi
rename:NumeVechi|comentariul care rămâne pe coloană
```

Programul generează `CHANGE COLUMN NumeVechi NumeNou ...` în fiecare bază
unde coloana veche există, iar la sfârșit (pasul 99) șterge marcajul
`rename:` din `AVACONT_SURSA`, păstrând doar textul de după `|`.

Dacă în vreo bază coloana veche nu există, se scrie o eroare în
`schema_diff_log` și rândul respectiv nu se execută. Restul continuă.

---

## Verificări făcute automat înainte de orice

**`sql_mode` trebuie să conțină `STRICT_TRANS_TABLES`.** Fără el, o
conversie de charset care nu încape **trunchiază datele în tăcere**, fără
nicio eroare. Programul refuză să pornească altfel.

Se raportează și dacă `AVACONT_SURSA` are mai multe combinații de
charset — ele se propagă în ținte exact așa cum sunt.

---

## Despre diacritice

Trecerea de la `utf8mb4` la `utf8` **nu afectează diacriticele
românești**. Toate — ă â î ș ț Ă Â Î Ș Ț — fac parte din planul de bază
Unicode și încap în `utf8`. Verificat pe date reale.

Se pierd doar caracterele din planurile superioare: emoji și caractere
chinezești rare. Dacă bănuiți că există așa ceva într-o coloană:

```sql
SELECT COUNT(*) FROM `000_DEMO`.`Clasificatii`
WHERE Denumire <> CONVERT(CONVERT(Denumire USING utf8) USING utf8mb4);
```

Zero înseamnă că se poate converti fără pierderi.

---

## Jurnal și urme

`schema_sync.log` — în folderul din care rulați, cu rotire la 10 MB și
cinci generații păstrate, în același format ca `api_server.log`.

`AVACONT_COMUN.schema_diff_log` — fiecare instrucțiune generată, cu data
execuției și eventuala eroare. Tabelul este creat automat la prima rulare.
Stă în `AVACONT_COMUN` **intenționat**: pus în `AVACONT_SURSA`, ar fi fost
copiat ca tabel nou în fiecare bază de unitate.

Ce a eșuat, cu numărul erorii:

```sql
SELECT id, target_db, table_name, object_type, action_type, error_msg
FROM AVACONT_COMUN.schema_diff_log
WHERE error_msg IS NOT NULL AND error_msg <> ''
ORDER BY id DESC;
```

Ce a rămas neexecutat după o oprire din eroare:

```sql
SELECT id, target_db, table_name, object_type, action_type, ddl_sql
FROM AVACONT_COMUN.schema_diff_log
WHERE executed_at IS NULL
ORDER BY priority, id;
```

La o rulare nouă, rândurile neexecutate sunt șterse și regenerate, ca să
nu se dubleze. `--no-reset` păstrează.

Ștergerea se face **după** ce noua comparație s-a încheiat cu bine, nu
înainte. Altfel, o comparație care crapă la jumătate lăsa tabelul gol: și
planul vechi șters, și cel nou nescris.

---

## Erori MariaDB întâlnite

| Nr. | Înseamnă |
|---|---|
| 1832 | charset-ul unei coloane nu poate fi schimbat cât timp e sub o cheie străină (coloana care *ține* cheia) |
| 1833 | același lucru, dar pentru coloana **spre care** arată cheia; mesajul numește tabelul care o referă |
| 3780 | cheie străină între coloane cu seturi de caractere diferite |
| 1826 | cheie străină cu nume duplicat — a fost adăugată fără să fi fost ștearsă |
| 1061 | index cu nume duplicat |
| 1071 | cheie prea lungă (poate apărea la lărgire, nu la îngustare) |
| 1060 | coloană cu nume duplicat |
| 1005 / 150 | cheie străină „prost formată" — de obicei tabelul referit nu există încă, sau cele două capete nu au aceeași colaționare |
| 1452 | rândurile nu se potrivesc cu cele din tabelul referit — apare dacă o cheie ajunge din greșeală să arate spre altă bază |
| 1146 | tabelul nu există — o cheie pusă la loc pe un tabel șters între timp |

Numărul erorii se salvează în `schema_diff_log`, nu doar textul.

---

## Ce nu face

- Nu atinge datele — doar structura.
- Nu sincronizează vederi (`views`), proceduri sau declanșatoare.
- Nu modifică `AVACONT_COMUN` și nu o tratează niciodată ca țintă.
- Nu sare peste coloanele generate (`GENERATED ... PERSISTENT`) — le
  ignoră complet, la comparație și la creare.
- Nu restaurează singur o copie de siguranță.
