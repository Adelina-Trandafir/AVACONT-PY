# migrare — Access împins pe server, citit pe server (felia 0044)

Înlocuiește lanțul feliei 0042 (VBA scrie JSON → `KBot.Migrator` citește
fișierele → rute de seed). Aici operatorul **împinge chiar fișierul Access**,
iar serverul face restul: îl citește, alege rândurile unității cerute, le
măsoară față de schema MariaDB și abia apoi scrie.

---

## Pasul pe care îl face operatorul, o dată, în Access

**Fișierele trebuie să ajungă pe server FĂRĂ parolă de bază de date.**

`FX_2026.accdb` este criptat cu parola `andreI`
(`Surse/VBA_MIGRARE/mdl_FX_ExportSeed.bas`). Verificat pe fișierele reale: în
`DB_STRUCT.accdb`, care nu are parolă, numele de tabele se citesc direct din
octeți; în `FX_2026.accdb`, nu se citește niciunul. `mdbtools` **nu
decriptează** — nicio unealtă din lanțul de pe Linux nu o face.

Deci, pe o **copie** (niciodată pe fișierul de lucru):

1. Access ▸ Fișier ▸ Deschidere, cu **Deschidere în mod exclusiv**;
2. Fișier ▸ Informații ▸ **Decriptare bază de date**, se dă parola;
3. copia rezultată este cea care se împinge.

Migratorul refuză și el, cu mesaj: dacă `mdb-tables` nu vede niciun tabel,
răspunsul spune exact că fișierul e cel mai probabil încă protejat.

---

## Instalarea pe serverul Linux

### 1. mdbtools

```
sudo apt update
sudo apt install -y mdbtools
```

Trebuie să existe **trei** binare: `mdb-tables`, `mdb-schema` și `mdb-json`.
`mdb-json` a apărut în mdbtools 0.9; pe o distribuție cu un pachet mai vechi
nu e de ajuns `mdb-export` — acela scoate CSV, unde NULL și șirul gol arată la
fel, iar «NULL într-o coloană NOT NULL» este chiar una dintre constatările pe
care le vede operatorul.

Verificare:

```
mdb-tables --help && mdb-schema --help && mdb-json --help
```

Dacă binarele nu sunt în `PATH` (compilare din surse, `/opt/...`), se pune
folderul lor în `config.MDB_TOOLS_BIN`.

### 2. Folderele

```
mkdir -p /var/lib/kbot/pushed-accdb /var/lib/kbot/tmp-upload
chmod 750 /var/lib/kbot/pushed-accdb /var/lib/kbot/tmp-upload
```

Fără `chown`: pe serverul de acum `avacont.service` rulează **ca root**
(`ps -o user= -C gunicorn` → `root`; unitatea nu are `User=`), deci folderele
făcute tot de root sunt deja scriibile. Dacă serviciul ajunge vreodată să
ruleze sub un cont propriu, atunci — și abia atunci — e nevoie de
`chown -R <cont>:<grup> /var/lib/kbot`.

`pushed-accdb` ține fișierele Access complete ale unităților. Nu e public și
nu trebuie servit de nginx.

> De semnalat, nu de rezolvat aici: fiindcă serviciul rulează ca root,
> fișierele împinse — baze de unitate întregi — ajung deținute de root, iar
> orice defect din calea de încărcare scrie cu drepturi de root. Nu blochează
> migrarea; de reparat când se atinge următoarea dată definiția serviciului.

### 3. `config.py`

```python
PUSHED_ACCDB_DIR = "/var/lib/kbot/pushed-accdb"
TEMP_UPLOAD_DIR  = "/var/lib/kbot/tmp-upload"   # există deja pentru routes/ftp.py
MDB_TOOLS_BIN    = None                          # sau folderul binarelor mdbtools
MIGRARE_SQL_DIR  = "/var/lib/kbot/migrare-sql"   # dosarul de instrucțiuni (felia 0044-04)
```

Fără `PUSHED_ACCDB_DIR` rutele răspund cu numele cheii lipsă, nu scriu
undeva la întâmplare. La fel `MIGRARE_SQL_DIR`: fără el **scrierea nu
pornește deloc**, cu numele cheii în mesaj — vezi «Ce SQL s-a rulat» mai jos.
Folderul se face o dată, ca celelalte două:

```
mkdir -p /var/lib/kbot/migrare-sql
chmod 750 /var/lib/kbot/migrare-sql
```

### 4. nginx

Bucata de încărcare e de 4 MB (`storage.MAX_CHUNK_BYTES`), sub plafonul
global de 17 MB al aplicației (`main.py`, `MAX_CONTENT_LENGTH`). `nginx`
trebuie să lase să treacă cel puțin atât:

```
client_max_body_size 20m;
```

### 5. Repornire

Unitatea de systemd se numește **`avacont.service`** («AVACONT Server Procesare
PDF») — verificat pe serverul de acum, nu ghicit:

```
systemctl restart avacont
systemctl status avacont --no-pager
```

**Un singur worker Gunicorn** rămâne obligatoriu (decizie blocată, cu gardă
în `gunicorn.conf.py`): și sesiunile de încărcare, și registrul de lucrări
stau în memoria procesului.

### Nimic de instalat cu pip

Pachetele Python folosite sunt cele care există deja
(`flask`, `mysql-connector-python`). **`pyaccdb` nu există pe PyPI** —
verificat pe index; planul care îl pomenea era scris fără acces la structura
reală.

**`mdbtools` NU se instalează în `.venv`.** Nu e un pachet Python, ci binare C;
un mediu virtual izolează doar pachete Python și nu are cum să țină sau să
rezolve executabile de sistem. `accdb.py` le pornește prin `subprocess`, care
caută în `PATH`, nu în venv. (Există și un pachet `mdbtools` pe PyPI, dar e o
învelitoare subțire peste aceleași binare: tot are nevoie de pachetul de
sistem dedesubt, deci nu aduce nimic.) Alternativa la instalarea în `PATH` e
compilarea într-un folder propriu și `config.MDB_TOOLS_BIN` către el.

---

## Rutele

| Rută | Ce face |
|---|---|
| `GET /api/migrare/baze` | bazele de unitate de pe MariaDB + câte tabele `FX_` au deja |
| `GET /api/migrare/fisiere` | ce fișiere Access sunt deja împinse |
| `POST /api/migrare/push/init` | deschide o încărcare în bucăți |
| `POST /api/migrare/push/bucata` | o bucată, cu amprentă SHA-256 |
| `POST /api/migrare/push/final` | lipește, verifică amprenta întregului fișier, mută în loc |
| `POST /api/migrare/tabele` | inventarul fișierului: ce tabel există și cu câte rânduri |
| `POST /api/migrare/analiza` | pornește analiza; întoarce un id de lucrare |
| `POST /api/migrare/rulare` | pornește scrierea; cere id-ul analizei care a aprobat-o |
| `GET /api/migrare/stare/<id>` | starea unei lucrări + jurnalul ei |

Garda e `X-Api-Key`, ca pe rutele de seed pe care le înlocuiesc.

Numele fișierului pe server: `fx_<an>_<dc>.accdb` (litere mici). E **singurul**
fișier pe care îl ia migrarea.

---

## Un fișier, mai multe unități — se scrie DOAR unitatea aleasă

Un `FX_<an>.accdb` poate purta mai multe unități (DC-uri). Operatorul alege pe
ecran baza țintă, iar migrarea scrie **numai rândurile unității ei**; restul
rămân în fișier, neatinse. Nu e o eroare și nu se raportează ca atare: e cazul
obișnuit.

**Nu există niciun `cale.accdb`** și niciun tabel `[Cai]`. Perechea
`IdUnitate ↔ DC` e chiar în fișierul FOREXE (verificat în exportul Access,
`TABLES/*.md`):

* `FX_Angajamente` poartă **și** `IdUnitate`, **și** `DC`;
* `FX_Indicatori` poartă `IdUnitate` lângă `CodAngajament`, deci completează
  angajamentele al căror rând din `FX_Angajamente` n-are unitate.

`routing.build_plan()` citește tabelele-cheie o singură dată și construiește
mulțimile de chei ale unității alese (tabelele grele la Memo nu sunt printre
ele):

| Familie | Cheia | De unde |
|---|---|---|
| unitate | `IdUnitate` | `FX_Angajamente` + `FX_Indicatori` |
| angajament | `CodAngajament` | `FX_Angajamente` (DC propriu, altfel `IdUnitate`) |
| rezervare | `IDRZ` | `FX_Rezervari`, prin angajament |
| recepție R | `IDRR` | `FX_Receptii_R`, prin angajament |
| recepție H | `IDRH` | `FX_Receptii_H`, prin angajament |
| extras | `IDEXF` | `FX_Extrase_H`, prin unitate |
| antet de extras | `IDEXH` | `FX_Extrase_H` însuși — liniile `FX_Extrase` fără `IdUnitate` propriu se rutează prin `IDFXH` spre antetul lor |
| ddf | `IDDF` | `FX_DDF`, prin DC propriu / `IdUnitate` |
| revizie | `IDREV` | `FX_DDF_REV`, prin DDF |
| ord | `IDORD` | `FX_ORD`, prin angajament |

Fiecare mulțime se ține **de două ori**: cheile unității alese și **toate**
cheile din fișier. Diferența dintre ele e diferența dintre două lucruri care
NU trebuie confundate:

| Ce e rândul | Ce se întâmplă |
|---|---|
| al unității alese | se scrie |
| al altei unități din fișier | se sare, tăcut și pe bună dreptate |
| cu o cheie care nu există nicăieri în fișier | **constatare `SELECTIE`** (forțabilă), cu cheia primară și motivul |

Dacă baza aleasă nu apare pe niciun rând din `FX_Angajamente` și fișierul poartă
mai multe unități, analiza **se OPREȘTE** cu numerele unităților și DC-urile
găsite în mesaj. Nu se cade înapoi pe «tot în baza aleasă»: exact acolo ar intra
tăcut rândurile altei unități. Fișierul cu **o singură** unitate merge în baza
aleasă oricum ar fi scris DC-ul.

Un rând cu doi părinți (`FX_Receptii_IMG`, `FX_Receptii_Plati`) în care un
părinte e al unității alese și celălalt sigur al alteia oprește migrarea: nu se
ghicește care are dreptate.

Planul se rezolvă **o singură dată**, la analiză, și e refolosit identic la
scriere — altfel selecția s-ar putea schimba între măsurare și scriere.

---

## Ce tabele se actualizează (lista cu bife)

`POST /api/migrare/tabele` numără rândurile fiecăruia dintre cele 16 tabele din
fișierul deja împins (`accdb.count_rows` — numără linii, fără să interpreteze
niciun rând, deci e cu mult mai ieftin decât analiza). Migratorul arată lista
cu bife, iar **un tabel fără rânduri se oferă NEBIFAT**.

Analiza și scrierea primesc amândouă lista bifată, în câmpul `tabele`:

* lipsa câmpului înseamnă «tot setul migrat» (cele 12 tabele de bază, familia
  DDF, familia ORD, plus Salarii/IMG/Receptii_Plati — 27 în total), în ordinea
  implicită;
* lista goală **nu** înseamnă «toate» — se răspunde cu eroare, fiindcă nu asta
  a cerut operatorul;
* un nume care nu face parte din setul migrat oprește cu eroare, niciodată
  tăcut; un nume trimis de două ori la fel;
* bifele se scriu **în ordinea trimisă** — migratorul lasă tabelele să fie
  rearanjate (săgeți sau tragere), iar acea ordine ESTE ordinea de scriere.
  Lipsa câmpului dă ordinea implicită, cu părinții înaintea copiilor;
* un tabel care n-a fost **analizat** nu se poate scrie;
* **o ordine care pune un copil înaintea părintelui lui e refuzată** — vezi mai
  jos.

### Ordinea de scriere și cheile străine

O cheie străină cere rândul părinte **prezent la INSERT**. Golirea părintelui
întâi, în «Înlocuiește tot», nu ajută deloc: face eșecul sigur, nu îl evită.
Asta a picat pe 21.08 — `FX_Rezervari.IDREV` are cheie străină pe `FX_DDF_REV`
(`FX_Rezervari__FX_DDF_REV`), iar `FX_Rezervari` se scria pe poziția 4 și
`FX_DDF_REV` pe 14: `1452`.

Două schimbări, pe 22.08:

* **ordinea implicită** mută perechea `FX_DDF` / `FX_DDF_REV` pe pozițiile 3 și
  4, imediat după `FX_Angajamente` și `FX_Indicatori`. Nimic altceva nu se mută.
  `FX_DDF` are nevoie doar de `FX_Angajamente`, iar `FX_DDF_REV` doar de
  `FX_DDF`;
* **analiza verifică ordinea pe care a trimis-o operatorul**, citind
  constrângerile din `information_schema.KEY_COLUMN_USAGE`. Un copil scris
  înaintea unui părinte **bifat și el** e o constatare **BLOCANT**:

  ```
  «FX_Rezervari» se scrie înaintea lui «FX_DDF_REV», dar depinde de el prin
  «FX_Rezervari__FX_DDF_REV» (IDREV). Mută-l după el în lista de tabele.
  ```

  Se citește din bază, **nu** dintr-o listă din `tables.py`: constrângerea care
  a picat rularea nici nu exista în copia de schemă pe care o aveam. O
  constrângere adăugată în bază schimbă răspunsul fără nicio schimbare de cod.
  `execute.run()` face aceeași verificare încă o dată, pe ordinea cererii de
  rulare. Ordinea implicită e deci un punct de plecare bun, nu o promisiune pe
  care se sprijină codul.

Analiza mai primește și `coloane` — `{tabel: [coloane]}`, coloanele Access pe
care operatorul vrea să le scrie. Un tabel absent din dicționar își păstrează
toate coloanele; cheile primare se adaugă pe server oricum. O coloană debifată
nu se scrie, deci nu e nici măsurată și nici raportată ca lipsă din țintă —
așa ies din drum coloanele de rutare (`IdUnitate`, `DC`) scoase intenționat
din MariaDB. Scrierea folosește coloanele DE PE RAPORTUL analizei, nu o
alegere nouă. Numele de coloane se potrivesc FĂRĂ litere mari/mici — Access e
case-insensitive («Cual» și «CUAL» sunt aceeași coloană acolo) — iar pe
MariaDB se scrie întotdeauna cu numele exact al țintei.

---

## Corelațiile de coloane (Access ▸ MariaDB)

Potrivirea după nume nu e întotdeauna cea bună. Perechea clasificațiilor e
motivul pentru care există fila «Corelații coloane» din migrator:

* în **Access**, `IdClsf` arată spre un tabel din ALT `.accdb`, iar `IdClsfPY`
  poartă id-ul rândului din `Clasificatii` de pe MariaDB;
* în **MariaDB** cele două își schimbă numele: `IdClsfAcc` ține id-ul Access,
  iar `IdClsf` ține id-ul MariaDB.

**O țintă vidă pe o coloană obligatorie este REFUZATĂ, nu ascultată.** Migratorul
trimite harta de corelații ÎNTREAGĂ pentru fiecare tabel bifat, deci un singur
«(nu se scrie)» pus pe o cheie primară ștergea coloana din `INSERT`, iar MariaDB
răspundea `1364 (HY000): Field '<col>' doesn't have a default value` — o eroare
despre LISTA DE COLOANE, nu despre valori (un NULL într-o coloană `NOT NULL` ar
fi 1048, altă eroare). De la felia 0044-04, analiza se oprește cu 400 și spune
tabelul, coloana din Access și coloana de pe MariaDB. «Obligatorie» înseamnă
`NOT NULL`, fără valoare implicită și necompletată de server: `auto_increment`,
coloanele generate și cele cu `on update` **nu** sunt obligatorii.

Corelate după nume, cele două id-uri ar intra fiecare în coloana celuilalt.
Regulile stau în `tables.COLUMN_RENAMES` și se aplică **doar acolo unde ținta
chiar are coloana pe care o numesc** — un tabel al cărui echivalent MariaDB
n-a căpătat niciodată `IdClsfAcc` rămâne cu potrivirea simplă după nume.

**Familia ORD — id-urile din Access SUNT id-urile de pe MariaDB** (decizia
operatorului, 22.08). Pe MariaDB cheile familiei sunt coloanele cu `P` la
coadă, iar ele sunt `AUTO_INCREMENT`; dar auto-increment-ul pornește doar când
coloana **lipsește** din `INSERT`. Scrisă explicit, valoarea din Access intră
cum e — exact ce fac deja `FX_DDF.IDDF` și `FX_DDF_REV.IDREV`, tot
`AUTO_INCREMENT` amândouă, și de-aia ține lanțul DDF și nu ținea cel ORD. Fără
hartă de id-uri, fără `lastrowid`, fără a doua trecere.

| Access | MariaDB |
|---|---|
| `IDORD` | `IDORDP` |
| `IDORDPART` | `IDORDPARTP` |
| `IDORDTBL` | `IDORDTBLP` |
| `IDORDDOC` | `IDORDDOCP` |
| `IDORDATT` | `IDORDATTP` |

Trei urmări, toate voite: coloana Access `IDORDP` (măsurată numai zerouri,
144/144 pe `FX_ORD_PART`) **nu mai călătorește deloc**; legăturile către părinte
supraviețuiesc fiindcă numerele supraviețuiesc; și upsert-ul lui `FX_ORD` începe
să funcționeze — cheia lui adevărată e `IDORDP`, deci `ON DUPLICATE KEY UPDATE`
are în sfârșit pe ce să se potrivească. Până acum nu avea: `tables.py` declara
cheia `IDORD`, cea de pe MariaDB e `IDORDP`, iar `IDORD` n-are index unic — deci
o a doua rulare «Adaugă/actualizează» insera din nou toate cele 38 de ordine.

**O corelație eliberează potrivirea după nume pe care o înlocuiește.** Dacă
`IDORD` intră în `IDORDP`, atunci coloana Access `IDORDP` nu mai are corelație:
altfel două coloane din Access ar ajunge pe aceeași coloană de pe MariaDB, ceea
ce oprește totul. Coloana care e ea însăși SURSA unei corelații e lăsată în pace
(`IdClsf` e și revendicat de `IdClsfPY`, și redenumit în `IdClsfAcc`).

Coloanele vechi de pe MariaDB (`FX_ORD.IDORD`, `COMMENT 'ACCESS'`, și
perechile ei de pe copii) rămân astfel **negoale de nimeni**. Dacă vreuna e
`NOT NULL` fără valoare implicită, analiza o numește ca `COLOANA_OBLIGATORIE`
și **oprește rularea** — zgomotos, cu numele coloanei, înainte să se scrie ceva.

> `primary_key` din `tables.py` e numele de pe **țintă**; `key_column` și
> `access_key` sunt nume din **Access**. Pe familia ORD cele două chiar diferă,
> ceea ce e nou. Rutarea citește rândul cu numele lui din Access, înainte de
> orice corelație, iar `access_key` e numele sub care rândul apare în raport.

`POST /api/migrare/tabele` întoarce, pe fiecare tabel, `coloane_tinta` (numele
de pe MariaDB) și, pe fiecare coloană, `tinta` — corelația **propusă**.
Migratorul le arată și le lasă schimbate rând cu rând; ce a aranjat operatorul
vine înapoi la analiză în `corelatii` — `{tabel: {coloana_access:
coloana_tinta}}`. O țintă vidă înseamnă «coloana asta nu călătorește»; o țintă
pe care baza n-o are e ignorată, nu ascultată. **Două coloane din Access
corelate cu aceeași coloană de pe MariaDB opresc totul** (400 la analiză,
`ExecuteError` la scriere): una dintre valori s-ar pierde și nu se poate spune
care. Rutarea NU e atinsă de nimic din toate astea — ea citește rândul cu
numele lui din **Access** (`IdUnitate`, `DC`, `CodAngajament`), înainte de
orice corelație. Ca și coloanele bifate, corelațiile călătoresc pe RAPORTUL
analizei: scrierea folosește exact ce a măsurat analiza.

Scrierea mai primește `inlocuieste` (bool): **Înlocuiește tot pe server** —
tabelele bifate se golesc întâi (`DELETE`, copiii înaintea părinților, adică
ordinea de scriere inversată), apoi se umplu din fișier, totul într-o
**singură tranzacție** cu commit doar la final. Orice eroare întoarce totul —
deliberat `DELETE`, nu `TRUNCATE`, fiindcă `TRUNCATE` e DDL și face commit
implicit, ceea ce ar face promisiunea de rollback o minciună.

După analiză, coloana «Ale unității» arată câte dintre rândurile citite sunt
chiar ale bazei alese, iar tabelele cu zero se debifează singure.

---

## Coloane obligatorii — a treia pază

O coloană poate cădea din `INSERT` pe două drumuri: **necorelată** (harta de
corelații n-o trimite nicăieri) sau **debifată** (operatorul a scos-o din
`coloane`). Cheile primare erau apărate doar de al doilea drum. Regula se
verifică acum pe REZULTAT, nu pe drumuri, și de trei ori:

1. corelația «(nu se scrie)» pe o coloană obligatorie — **400** la
   `POST /api/migrare/analiza`;
2. coloana obligatorie absentă din listă din orice alt motiv — constatare
   **`COLOANA_OBLIGATORIE`**, clasă **BLOCANT**, deci niciun buton nu pornește
   și coloana e numită în grila de constatări;
3. aceeași condiție în `execute.run()`, **înainte de primul rând**.

Lista de coloane a analizei și cea a scrierii se construiesc cu **aceeași**
funcție (`validate.insert_columns`) — două copii ale regulii care se depărtează
una de alta sunt chiar felul în care s-a născut defectul.

Jurnalul lucrării spune acum, pe fiecare tabel, ce coloane se scriu și ce
coloane Access s-au sărit, cu motivul fiecăreia:

```
«FX_Angajamente»: 23 coloane — CodAngajament, Denumire, DataCreare, …
«FX_Angajamente»: coloane Access sărite — IdUnitate (necorelată), DC (debifată).
```

---

## Ce SQL s-a rulat (dosarul de instrucțiuni)

Fiecare **scriere** lasă pe disc, în text simplu, instrucțiunile pe care le-a
trimis. Un dosar pe bază de unitate, un fișier pe tabel:

```
<MIGRARE_SQL_DIR>/
  000_DEMO/
    20260821_142942_scriere/
      _00_info.txt        bază, an, fișier, lucrare, mod, forțat, ordinea de scriere
      _01_stergeri.sql    doar în «Înlocuiește tot», cu numărul de rânduri șterse
      FX_Angajamente.sql  antet + o instrucțiune pe rând
      FX_Indicatori.sql
      …
      _02_parsare.log     fiecare valoare pe care parserul a schimbat-o
      _99_final.txt       COMMIT + totaluri, sau ROLLBACK + eroarea + instrucțiunea
```

`_02_parsare.log` ține **numai ce s-a schimbat**, cu valoarea dinainte, cea de
după și motivul — plus totaluri pe coloană la sfârșit:

```
=== FX_Angajamente ===
CodAngajament=AN-2026-0001 | DTQ | «04/28/26 15:28:03» → 2026-04-28 15:28:03
CodAngajament=AN-2026-0001 | Valoare | «1234,56» → 1234.56
CodAngajament=AN-2026-0001 | Activ | -1 → 1 (Access folosește -1 pentru «da»)
```

O valoare care a trecut neatinsă n-are ce spune, iar scrisul tuturor celulelor ar
face fișierul de neconsultat exact pentru cel care caută o conversie greșită.

* Subdosarul cu marcaj de timp există ca **rularea eșuată — singura care merită
  citită — să nu fie acoperită de următoarea încercare**.
* **Nu se șterge nimic, niciodată.** Migrarea se face o dată pe bază de unitate;
  un an întreg înseamnă 20–40 MB pe rulare.
* Toate fișierele sunt UTF-8, cu diacritice adevărate.
* **Analiza nu scrie nimic** în dosar. Doar scrierea.

Două lucruri care trebuie spuse, și sunt spuse și în fișiere:

**RECONSTRUCȚIE.** Driverul trimite **parametri**, nu text. Valorile din fișiere
trec prin funcția de escape a driverului însuși (`MySQLConverter`), deci sunt
fidele — dar fișierul **nu** e o transcriere a octeților de pe fir. O valoare pe
care driverul n-o poate reprezenta apare ca
`/* VALOARE NEREPREZENTABILĂ: <tip> */`, iar coloana și cheia rândului se trec
în `_99_final.txt`; instrucțiunea chiar trimisă nu e atinsă, doar consemnarea ei.

**Scrisul pe disc NU face parte din tranzacție.** Dosarul consemnează ce s-a
ÎNCERCAT. La o rulare «Înlocuiește tot» eșuată, `_99_final.txt` spune `ROLLBACK`,
iar fișierele `.sql` de deasupra descriu o muncă ce **nu mai există** în baza de
date. Așa e proiectat — dar cine citește dosarul trebuie să știe, altfel ia acele
fișiere drept dovadă că rândurile sunt acolo.

Fără `MIGRARE_SQL_DIR` în `config.py`, scrierea **nu pornește**, cu numele cheii
în mesaj: nu există cale de rezervă și nu se scrie nimic altundeva. Un eșec al
consemnării **în timpul** rulării nu oprește migrarea: se scrie în jurnalul
serverului cu urma completă, se spune o dată în jurnalul lucrării, iar
consemnarea se oprește de acolo încolo.

---

## Parsarea Access ▸ MariaDB (`parser.py`)

Access spune valorile într-un fel, MariaDB le acceptă în altul. `parser.py` e
**singurul** loc care traduce, iar analiza și scrierea îl cheamă pe **același**:
ce s-a măsurat e ce pleacă. Altfel se întâmplă exact ce s-a întâmplat —
`validate._DATE_FORMATS` ACCEPTA deja `04/28/26 15:28:03` la verificare, dar
scrierea trimitea șirul original, iar MariaDB răspundea:

```
1292 (22007): Incorrect datetime value: '04/28/26 15:28:03'
for column `000_DEMO`.`FX_Angajamente`.`DTQ` at row 1
```

**Ținta decide, întotdeauna.** Forma o dă tipul coloanei de pe MariaDB; Access e
doar locul din care vine valoarea. Nimic nu se ghicește din tipul Access.

| Ce vine | Ce pleacă |
|---|---|
| `04/28/26 15:28:03`, `28/04/2026`, `28.04.2026`, `04/28/2026`, `2026-04-28`, cu sau fără oră, `AM`/`PM` | `datetime` / `date` adevărat |
| `1234,56` (virgulă zecimală) | `1234.56` |
| `-1` / `0`, `True`/`False`, `Da`/`Nu` pe o coloană `tinyint(1)` | `1` / `0` |
| text gol într-o coloană care nu e text | `NULL` |
| o coloană `time` primind o dată întreagă | doar ora, `15:28:03` |

**Ce NU face: nu inventează.** O valoare pe care n-o poate citi trece mai departe
**neschimbată**, ca `validate.check_value` s-o raporteze drept constatarea `TIP`
care este — și aceea e blocantă. Un zero pus în locul unei valori pe care nimeni
n-a putut-o citi e mai rău decât o rulare oprită.

Trei lucruri decise anume, fiindcă ghicitul aici strică date:

* **`tinyint(1)` e boolean, `tinyint` simplu NU.** `-1` e un tinyint perfect
  valid; pe o coloană care numără ceva, transformarea lui în `1` ar fi corupție,
  nu conversie.
* **Fără separator de mii.** Access nu-l scrie, nici când coloana are format de
  afișare (confirmat de operator, 2026-08-21) — formatul e cum se ARATĂ valoarea,
  nu cum se păstrează. Deci `,` e separator zecimal, punct. Un șir cu **amândoi**
  separatorii, sau cu spațiu între cifre, **nu se ghicește**: nu poate veni din
  Access, deci înseamnă că valoarea nu e ce credem, și nu există citire sigură a
  lui `1.234,56`. Rămâne neatins și îl raportează analiza.
* **Ziua și luna, când sunt amândouă ≤ 12.** `04/28/26` nu e ambiguu — 28 nu
  poate fi lună. `05/04/26` este, și cineva trebuie să decidă:
  an din **două** cifre cu `/` → **luna prima** (e formatul propriu al lui
  mdbtools, `%m/%d/%y`, iar mdbtools e cel care ne produce rândurile);
  an din **patru** cifre cu `/` → **ziua prima**;
  `.` sau `-` → **ziua prima**, notație europeană.
  **Fiecare** dată ambiguă intră în `_02_parsare.log` cu citirea aleasă, ca
  operatorul să poată verifica alegerea în loc s-o creadă pe cuvânt. Cele două
  reguli stau în constante, sus în `parser.py`.

---

## Cele două butoane

Constatările analizei au două clase, și clasa e cea care aprinde butoanele:

**BLOCANT** — tabel lipsă, coloană lipsă, tip greșit, depășire de lungime sau
de interval, NULL într-o coloană `NOT NULL`, coloană obligatorie care nu ajunge
în `INSERT` (`COLOANA_OBLIGATORIE`), un copil scris înaintea părintelui lui
(`ORDINE_TABELE`), cheie străină fără corespondent pe o coloană `NOT NULL`
(`CHEIE_STRAINA_OBLIGATORIE`). Cât timp există una, **niciun**
buton nu pornește; nici «Forțează rularea» nu trece peste ele, fiindcă acelea
strică date, nu doar legături.

**FORȚABIL** — cheie primară dublă în fișier, rând a cărui cheie nu există
nicăieri în fișier, cheie străină fără corespondent **pe o coloană care acceptă
NULL**. «Rulează» rămâne oprit, «Forțează rularea» pornește și **sare** peste
rândurile vinovate — rămân în raport, nu ajung în baza de date.

O cheie străină spre un tabel scris **în aceeași rulare** se verifică pe
reuniunea dintre rândurile țintei și rândurile pe care chiar această rulare le
va scrie: pe o bază goală, altfel, absolut totul ar ieși «lipsă» — exact pe
dos. (Verificarea id-urilor `IDDF`/`IDREV` împotriva `FX_DDF`/`FX_DDF_REV` a
fost SCOASĂ: tabelele acelea nu fac parte din setul migrat, deci pe o bază
proaspătă sunt goale, iar verificarea marca totul și arunca rândurile la
rularea forțată.)

---

## Cheile străine: orfanii

O valoare de cheie străină fără părinte — nici pe țintă, nici printre rândurile
pe care le scrie chiar această rulare — se judecă după **coloana copilului**, nu
după tabel (decizia operatorului, 22.08):

* **coloana acceptă NULL** → rândul se **scrie**, cu coloana aceea golită.
  Rândul se păstrează; se pierde doar legătura. Constatare **FORȚABIL**, una pe
  rând, cu tabelul, cheia, coloana și valoarea aruncată; o linie pe tabel în
  jurnalul lucrării — `«FX_Rezervari»: 3 valori IDREV fără corespondent, scrise
  ca NULL.` — și aceeași linie în `_99_final.txt` din dosarul de instrucțiuni;
* **coloana e `NOT NULL`** → NULL nu e disponibil, deci rândul nu poate fi nici
  scris, nici golit. Constatare **BLOCANT**
  (`CHEIE_STRAINA_OBLIGATORIE`), cu coloana numită, și nu se scrie nimic.
  `FX_ORD_TBL.IdUnitate` e cea care va întâlni asta.

Un **`0`** e orfan oriunde cheia părintelui e `AUTO_INCREMENT`: un asemenea rând
nu poate exista. (`FX_Rezervari.IDREV`, măsurat: 125 NULL, 0 zerouri, 34 reale.)

### Chei străine care ies din setul migrat

Zece dintre ele arată spre tabele pe care migrarea nu le atinge, iar
«Înlocuiește tot» nu poate ajuta cu niciuna: părinții aceia nici nu se golesc,
nici nu se scriu, deci **valorile trebuie să existe deja pe țintă**.

```
FX_DDF_REV_SA   IdPartener -> Parteneri   IdClsf -> Clasificatii   IdUnitate -> Unitati
FX_DDF_REV_SB   IdPartener -> Parteneri   IdClsf -> Clasificatii   IdUnitate -> Unitati
FX_DDF_REV_PRT  IdClsf -> Clasificatii
FX_ORD_TBL      IdPartener -> Parteneri   IdClsf -> Clasificatii   IdUnitate -> Unitati
```

Se verifică pe rândurile țintei și atât — nu există «se va scrie în rularea
asta» de adăugat. `FX_ORD_TBL.IdClsf` acceptă NULL, deci o clasificație
nepotrivită devine NULL după regula de mai sus: rândul supraviețuiește,
legătura nu. E o pierdere de înțeles adevărată, de-aia apare în raport, nu doar
într-o linie de jurnal. Aici trebuie să fie corectă perechea
`IdClsf` / `IdClsfPY`.

`FX_ORD_TBL.IdUnitate` e `int(11) NOT NULL`, cheie străină spre
`Unitati(IdUnitate)`, și **călătorește din Access** — nu se completează pe
server. Arată a coloană de rutare și coloanele de rutare se debifează din
obișnuință, dar fiind `NOT NULL` fără valoare implicită, debifarea ei oprește
rularea și o numește (`COLOANA_OBLIGATORIE`). **Înainte de prima rulare
adevărată: verificați că `Unitati` de pe țintă chiar conține id-urile de unitate
pe care le poartă fișierul** — altfel toate rândurile `FX_ORD_TBL` pică deodată.

Serverul verifică regula din nou, la `POST /api/migrare/rulare` și încă o dată
în `execute.run()`. Interfața nu e singura pază.

---

## Ce nu face

* **Nu creează și nu modifică niciun tabel.** Un tabel absent pe țintă
  oprește scrierea cu numele lui; schema se instalează separat (`schema_sync`).
* **Nu sare peste rândurile deja existente: le aduce la zi.**
  `ON DUPLICATE KEY UPDATE <fiecare coloană> = VALUES(...)` — un upsert
  adevărat, pe toate tabelele. Coloanele de cheie primară rămân în afara listei
  de actualizat: ele identifică rândul. Deliberat **nu** `INSERT IGNORE`, care
  ar degrada la avertisment și erorile de tip. Numărătoarea e exactă: MariaDB
  raportează 1 pentru un rând inserat, 2 pentru unul chiar schimbat și 0 pentru
  unul deja identic — de acolo vin «scrise / actualizate / deja identice».
* **Nu traduce id-uri.** `IDDF`/`IDREV` se copiază cum sunt; nu se mai
  verifică împotriva `FX_DDF`/`FX_DDF_REV` (tabele din afara setului migrat).
