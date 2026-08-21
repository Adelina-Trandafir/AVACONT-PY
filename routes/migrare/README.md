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
```

Fără `PUSHED_ACCDB_DIR` rutele răspund cu numele cheii lipsă, nu scriu
undeva la întâmplare.

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
* un tabel care n-a fost **analizat** nu se poate scrie.

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

Corelate după nume, cele două id-uri ar intra fiecare în coloana celuilalt.
Regulile stau în `tables.COLUMN_RENAMES` și se aplică **doar acolo unde ținta
chiar are coloana pe care o numesc** — un tabel al cărui echivalent MariaDB
n-a căpătat niciodată `IdClsfAcc` rămâne cu potrivirea simplă după nume.

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

## Cele două butoane

Constatările analizei au două clase, și clasa e cea care aprinde butoanele:

**BLOCANT** — tabel lipsă, coloană lipsă, tip greșit, depășire de lungime sau
de interval, NULL într-o coloană `NOT NULL`. Cât timp există una, **niciun**
buton nu pornește; nici «Forțează rularea» nu trece peste ele, fiindcă acelea
strică date, nu doar legături.

**FORȚABIL** — cheie străină fără corespondent, cheie primară dublă în fișier,
rând a cărui cheie nu există nicăieri în fișier. «Rulează» rămâne oprit,
«Forțează rularea» pornește și **sare** peste rândurile vinovate — rămân în
raport, nu ajung în baza de date.

O cheie străină spre un tabel scris **în aceeași rulare** se verifică pe
reuniunea dintre rândurile țintei și rândurile pe care chiar această rulare le
va scrie: pe o bază goală, altfel, absolut totul ar ieși «lipsă» — exact pe
dos. (Verificarea id-urilor `IDDF`/`IDREV` împotriva `FX_DDF`/`FX_DDF_REV` a
fost SCOASĂ: tabelele acelea nu fac parte din setul migrat, deci pe o bază
proaspătă sunt goale, iar verificarea marca totul și arunca rândurile la
rularea forțată.)

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
