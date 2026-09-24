# `routes/inregistrare/` — înregistrarea publică a unei unități noi

Felia **0075**. Plan: `docs/PLAN_AutoProvisioning.md`. Trecerile de până acum:
**0075-01** (§5.1–5.3) — magazinul dinainte de autentificare, proxy-ul ANAF, verificarea
adresei prin cod; **0075-02** (§5.4–5.5) — nomenclatoarele, numele bazei, `FX_Inregistrari`
și `/cerere`; **0075-04** (§4) — pagina solicitantului; **0075-03** (§5.6) — aprobarea:
jobul care creează baza, rândurile și contul, plus linkul de parolă; **0075-05** (§7) —
pagina operatorului, `/operator`, care aprobă, respinge și retrimite linkul.

Toate rutele de aici în afară de cele `/api/operator/*` sunt **publice** — pagina o poate deschide oricine de pe internet.
Deci: limitatorul pe fiecare rută, numai SQL parametrizat, cod-motiv lângă fiecare mesaj
românesc, nicio excepție înghițită.

## Modulele

| Fișier | Ce ține |
|---|---|
| `store.py` | Înregistrarea dinaintea autentificării: o *notă* pe `session_store.STORE`, sub un token propriu. Fără cod nou de backend (constatarea F5 a planului). |
| `anaf.py` | Apelul către ANAF, cu `urllib` din bibliotecă standard. Pur: nu atinge baza de date, nu importă `config.py` decât leneș. |
| `nume.py` | Numele bazei, `1nn_SSSS` (D3/D12). Pur, în afară de `used_prefixes`, care citește două liste. Fără nicio diacritică în fișier — pliere prin `unicodedata`. |
| `nomenclatoare.py` | Cele trei liste din care alege pagina. Primesc o conexiune deschisă, întorc rânduri simple. |
| `cerere.py` | Verificarea cererii trimise și scrierea rândului în `FX_Inregistrari`. |
| `randuri.py` | Rândurile `Clasificatii` ale unei unități noi (§6, D5), verificate în memorie față de nomenclatoare înainte de orice INSERT. Pur, în afară de `read_dictionaries`. |
| `provizionare.py` | Jobul de aprobare (§5.6): `plan()` (doar verifică), `approve()`, `link_nou()`. Fiecare pas își înregistrează anularea; orice eșec desface tot în ordine inversă și lasă cererea `Esuata` cu `Motiv`. Rulează pe **contul de provizionare**, niciodată pe AVACONT. |
| `parola.py` | Linkul de o singură folosință trimis după aprobare (D13): găsirea după hash, `ALTER USER`. |
| `inregistrare.py` | Rutele publice (solicitantul și linkul de parolă). |
| `operator.py` | Pagina operatorului (0075-05): autentificarea în doi pași, lista, o cerere cu planul ei viu, aprobarea ca job urmărit din pagină, respingerea, linkul nou. Blueprint propriu, `operator_bp`. |

`__init__.py` **nu** importă blueprint-ul, ca `anaf.py` și `store.py` să rămână
importabile pe o mașină fără `config.py` (același motiv ca la `routes/migrare/`).
`main.py` le ia explicit: `from routes.inregistrare.inregistrare import inregistrare_bp` și
`from routes.inregistrare.operator import operator_bp`.

## Rutele

| Rută | Corp | Răspuns |
|---|---|---|
| `POST /api/inregistrare/anaf` | `{cf}` | `{token, expires_in, cf, unitate:{cui,denumire,adresa,nr_reg_com}}` |
| `POST /api/inregistrare/cod` | `{email}` | `{email_masked, expires_in}` |
| `POST /api/inregistrare/verifica` | `{cod}` | `{ok, email_masked, expires_in}` |
| `GET /api/inregistrare/sursasector` | — | `{surse:[{cod,sursa,sector,denumire}]}` |
| `GET /api/inregistrare/clasificatii?tip=F\|E` | — | `{tip, coduri:[{cod,denumire}], grupuri:{"20":…,"2001":…}}` (`grupuri` = numele celor două niveluri de sus; numai la E, din `DefaTitlu`/`DefaArticol`) |
| `GET /api/inregistrare/nume?denumire=` | — | `{db_name, numar, litere, previzualizare}` |
| `POST /api/inregistrare/cerere` | `{denumire, an, sursasector[], clsf_f[], clsf_e[]}` | `{id_cerere, randuri, operator_anuntat}` |
| `POST /api/inregistrare/parola/stare` | `{token}` | `{email_masked, denumire, expires_in}` |
| `POST /api/inregistrare/parola` | `{token, parola}` | `{ok, utilizator}` |

Până la `/cerere` inclusiv, toate în afară de `/anaf` cer token-ul înapoi în antetul
**`X-Registration-Token`**. Antet, nu corp, fiindcă trei dintre ele sunt GET-uri.

Cele două rute `/parola` (0075-03) au alt token, cu altă viață: cel din linkul de după
aprobare. Vine în **corp**, fiindcă în link stă după `#` — browserul nu-l trimite niciodată
serverului într-o adresă, deci nu ajunge în jurnalele nginx și nici în antetul `Referer`.

Pagini: `GET /inregistrare` (0075-04), `GET /parola` (0075-03) și `GET /operator` (0075-05),
din `PYTHON/static/`.

### Rutele operatorului (0075-05)

Toate în afară de primele două cer antetul **`X-Operator-Token`**.

| Rută | Corp | Răspuns |
|---|---|---|
| `POST /api/operator/login` | `{email, parola}` | `{pending, email_masked, expires_in}` — parola bună, codul trimis |
| `POST /api/operator/verifica` | `{pending, cod}` | `{token, email, expires_in}` |
| `POST /api/operator/logout` | — | `{ok}` |
| `GET /api/operator/cereri?stare=InAsteptare\|Esuata\|Aprobata\|Respinsa\|toate` | — | `{cereri:[…], numar:{stare:n}, limita}` |
| `GET /api/operator/cereri/<id>` | — | rândul + `ss/f/e` cu denumiri + `randuri` + `aprobabila` + `plan` (dacă e aprobabilă) + `job` (dacă rulează) |
| `POST /api/operator/cereri/<id>/verifica` | `{cod_program:{SS:val}, db_name}` | `{ok, rezultat \| problema, linii}` — `plan()` cu valorile operatorului |
| `POST /api/operator/cereri/<id>/aproba` | `{cod_program:{SS:val}, db_name}` | `202 {job}` |
| `GET /api/operator/joburi/<job>?de_la=N` | — | `{linii, total, gata, ok, rezultat, eroare, ramas}` |
| `POST /api/operator/cereri/<id>/respinge` | `{motiv}` | `{ok, anuntat, email}` |
| `POST /api/operator/cereri/<id>/link-nou` | — | `{link_trimis, link, linii}` |

**Cine intră:** parola contului K-BOT (verificată ca la `LoginForm`, printr-o conectare
MariaDB CA operatorul), apoi un cod de 6 cifre trimis pe e-mail (10 minute, 5 încercări), și
adresa trebuie să fie în `OPERATORI` din `config.py`. Listă goală ▸ pagina e închisă.
Sesiunea: 30 de minute de inactivitate, cel mult 4 ore. E o notă în `STORE` sub alt nume și
alt antet decât sesiunea K-BOT — un token de-al unuia nu merge la celălalt.

**Numele bazei și `CodProgram` sunt ale operatorului** (D25 și operatorul, 23.09.2026). Pagina
trimite la aprobare exact numele de pe ecran; jobul îl ține la aceleași reguli ca pe cel
calculat — forma `1nn_SSSS` (contul de provizionare poate crea doar asemenea baze, iar
procedura de GRANT refuză altceva), un număr nefolosit de altă bază, o bază care nu există.
Un nume greșit e refuzat înainte să se creeze ceva și **nu** trece cererea în `Esuata`.

**Aprobarea rulează într-un fir al procesului** și pagina o urmărește la 0,8 s. Registrul de
joburi e în memorie — merge numai fiindcă gunicorn are un singur worker. ⚠ **Nu reporniți
gunicorn cât pagina arată o aprobare în curs**: firul moare fără anulare și rămâne o unitate
pe jumătate.

## Numele bazei — `1nn_SSSS` (D3, D12)

`SSSS` = primele patru consoane ale denumirii; dacă nu sunt patru, vocalele umplu restul,
în ordinea din nume; sub patru litere cu totul ▸ refuz. `nn` = cea mai mică pereche liberă
din `11..99` **fără cifra zero**, liber însemnând că nici `information_schema.SCHEMATA`,
nici `CAI.DbName` nu au deja un nume care începe cu acel `1nn_`.

De ce fără zero: toate bazele existente sunt `NNN_XXXX` numerotate de la `000` în sus
(`000_DEMO`, `001_GR23`, `053_LTTR`, plus `101_CCDP`, care e dinaintea regulii). Blocul
`111`–`199` nu poate fi atins de numerotarea veche, deci un nume ales singur de server nu
poate cădea peste unul pe care operatorul urmează să îl scrie de mână.

Verificat prin apel direct: `AVATAR SOFT SRL` ▸ `VTRS`, `LICEUL TEORETIC` ▸ `LCLT`,
`Direcția de Asistență Socială` ▸ `DRCT`, `MUN. PLOIEŞTI` ▸ `MNPL` (ambele grafii de
diacritice se pliază), `AEIOU` ▸ `AEIO`, `ANA` ▸ refuzat.

`/nume` e o **previzualizare**. Numele adevărat se recalculează la aprobare (§5.4): un
număr liber acum poate fi luat până ajunge operatorul la cerere.

**Ce poate conține denumirea** (operator, 22.09.2026): litere, cifre, `_`, spații și
virgule — `[\w\s,]`, cu `\w` Unicode, deci literele cu diacritice trec. Spațiile repetate
devin unul singur. Pagina **scoate** orice altceva înainte să-i arate solicitantului
numele (și pe cel venit de la ANAF); `/nume` și `/cerere` doar **refuză**, cu
`DENUMIRE_CARACTERE_INTERZISE`.

## De ce numai `ClsfE` e filtrat

`Clasificatii` are patru chei străine spre `AVACONT_COMUN`, iar două sunt pe valori
**derivate** din codul E: `Articol = Left(E,2) + "." + Mid(E,3,2)` și `Titlu = Left(E,2)`.
Cincisprezece coduri din `DefaClsfE` derivă spre un `Articol` sau un `Titlu` inexistent
(numărate pe server: opt fără Articol, patru fără Titlu). Oferite în arbore, ar fi frunze
care nu pot fi salvate — solicitantul alege una, așteaptă aprobarea, iar provizionarea moare
cu `1452` la ultimul pas.

Deci filtrul **este chiar join-ul**: un cod apare doar dacă rândul în care s-ar transforma
își găsește ambii părinți. Așa lista nu se poate îndepărta de constrângere — dacă mâine se
adaugă rândurile lipsă din dicționare, codurile apar singure, fără o listă de cincisprezece
excepții care s-ar învechi în tăcere. `DefaClsfF` nu are nevoie de filtru: `ClsfF` e propria
lui cheie străină.

### De ce token-ul se naște la `/anaf`, nu la `/cod`

Planul se contrazice singur: §4 spune «token-ul de la pasul 2», dar §5.1 pune `cf` și
`anaf` printre câmpurile notei — date de la pasul 1. Operatorul a tranșat (22.09.2026):
token-ul vine de la `/anaf`.

Motivul e `FX_Inregistrari`, care are **două** coloane de denumire: `DenumireAnaf` (ce a
spus ANAF) și `Denumire` (ce a scris solicitantul, care **are voie** să o schimbe). Pagina
de aprobare le arată una lângă alta. Dacă denumirea de la ANAF ar călători prin browser,
pagina de aprobare ar compara ce a scris solicitantul cu ce a scris solicitantul. La fel
și codul fiscal: D11 refuză un CF care are deja bază — ținut în browser, cineva ar putea
trece verificarea la pasul 1 cu un cod liber și trimite altul la pasul 6.

## Codurile-motiv

ASCII, stabile, pentru ca pagina să poată ramifica fără să citească textul.

`CF_INVALID` · `CF_EXISTS` · `ANAF_NOT_FOUND` · `ANAF_INCOMPLETE` · `ANAF_UNAVAILABLE` ·
`RATE_LIMITED` · `DB_ERROR` · `TOKEN_ABSENT` · `TOKEN_UNKNOWN` · `TOKEN_EXPIRED` ·
`EMAIL_INVALID` · `EMAIL_TOO_LONG` · `EMAIL_TAKEN` · `MAIL_NOT_CONFIGURED` ·
`MAIL_FAILED` · `CODE_ABSENT` · `CODE_NOT_REQUESTED` · `CODE_EXPIRED` · `CODE_WRONG` ·
`CODE_ATTEMPTS_EXHAUSTED` · `TIP_INVALID` · `DENUMIRE_ABSENTA` · `DENUMIRE_CARACTERE_INTERZISE` · `DENUMIRE_PREA_SCURTA` ·
`NUMAR_EPUIZAT` · `EMAIL_NEVERIFICAT` · `DENUMIRE_PREA_LUNGA` · `AN_INVALID` ·
`AN_IN_AFARA_INTERVALULUI` · `SS_ABSENT` · `SS_NECUNOSCUT` · `CLSF_F_ABSENT` ·
`CLSF_E_ABSENT` · `CLSF_F_NECUNOSCUT` · `CLSF_E_NECUNOSCUT` · `CLSF_F_NU_E_FRUNZA` ·
`CLSF_E_NU_E_FRUNZA` · `PREA_MULTE_RANDURI` · `LINK_INVALID` · `PAROLA_INVALIDA` ·
`PAROLA_NESETATA`

Operatorul (0075-05): `DATE_INCOMPLETE` · `OPERATORI_NECONFIGURATI` · `CREDENTIALE` ·
`NU_ESTE_OPERATOR` · `COD_EXPIRAT` · `COD_ABSENT` · `COD_GRESIT` · `COD_EPUIZAT` ·
`SESIUNE_EXPIRATA` · `STARE_INVALIDA` · `CERERE_INEXISTENTA` · `COD_PROGRAM_INVALID` ·
`NUME_INVALID` · `NUME_ABSENT` · `JOB_IN_CURS` · `JOB_NECUNOSCUT` · `DE_LA_INVALID` ·
`MOTIV_ABSENT` · `MOTIV_PREA_LUNG` · `RESPINGERE_REFUZATA` · `LINK_REFUZAT` (plus `RATE_LIMITED`,
`DB_ERROR`, `MAIL_NOT_CONFIGURED`, `MAIL_FAILED` de mai sus)

## Config pe VPS

Toate au valori implicite în cod (sau lipsa lor e raportată, nu e eroare), deci serverul
pornește și fără ele:

```python
ANAF_TVA_URL   = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
ANAF_TIMEOUT   = 15
OPERATOR_EMAIL = "..."   # cui i se anunță o cerere nouă; gol ▸ nu se anunță nimeni

# 0075-03. FĂRĂ el aprobarea și linkul de parolă nu merg (eroare clară, nu cădere pe AVACONT).
DB_CONFIG_PROVIZIONARE = {
    'unix_socket': '/run/mysqld/mysqld.sock',   # contul există doar @'localhost'
    'user': 'kbot_provizionare', 'password': '...',   # din sql/0075_03_provizionare.sql
}
PUBLIC_BASE_URL = "https://kbot.avatarsoft.ro"   # începutul linkului din e-mail (implicit acesta)

# 0075-05. Cine intră pe /operator. Adrese cu litere mici, conturi K-BOT existente.
OPERATORI = ["..."]
```

`SMTP_*` există deja, de la felia 0072.

## Tabela

`sql/0075_fx_inregistrari.sql` creează `AVACONT_COMUN.FX_Inregistrari` pe serverul K-BOT.
**Fără chei străine, intenționat:** o cerere e o corespondență, nu o unitate — există
înaintea bazei, a rândului din `CAI` și a contului, se poate sfârși `Respinsa` fără să arate
spre nimic, iar o rulare eșuată trebuie să lase rândul în urmă cu `Motiv`-ul intact, nu să
fie luat de o cascadă odată cu resturile.

## Aprobarea — pagina `/operator` (0075-05) sau linia de comandă (0075-03)

Pagina face tot ce face linia de comandă. Linia de comandă rămâne pentru când pagina nu merge.

Pe VPS, din `/root/AVACONT` (acolo stă `PYTHON/` pe server), **cu Python-ul din venv**:
`/root/AVACONT/.venv/bin/python3`, sau `source /root/AVACONT/.venv/bin/activate` o dată pe
sesiune. `python3` simplu e cel al sistemului și nu are `mysql.connector`.

```bash
python -m scripts.aproba_cerere --lista          # cererile InAsteptare și Esuata
python -m scripts.aproba_cerere 7 --plan         # toate verificările, numele propus, nr. de rânduri; nu scrie nimic
python -m scripts.aproba_cerere 7                # aprobă (cere DA scris)
python -m scripts.aproba_cerere 7 --cod-program 01A=0000002510
python -m scripts.aproba_cerere 7 --nume-baza 115_TRND   # alt nume decât cel calculat
python -m scripts.aproba_cerere 7 --link-nou     # link nou de parolă (cel vechi moare)
```

Pașii, cu anularea fiecăruia:

| # | Pas | Anulare |
|---|---|---|
| 1 | Numele `1nn_SSSS`, calculat din nou | — |
| 2 | `CREATE DATABASE` + toate tabelele și view-urile din `AVACONT_SURSA` | `DROP DATABASE` |
| 3–4a | Sub `GET_LOCK('cai_idunitate')`: `CAI` (un rând pe sursă-sector, `IdUnitate` = MAX+1…), `Unitati`, `Unitati_Ani` — o tranzacție | `DELETE` pe fiecare |
| 5–6 | În baza nouă: `Unitati` + toate rândurile `Clasificatii` — o tranzacție | pleacă odată cu baza |
| 7 | `CREATE USER '<email>'@'%'` cu parolă aleatoare; `GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE` numai pe baza nouă; `Unitati_Utilizatori (Rol='Contabil')` | `DROP USER`, `DELETE` |
| 8 | Cererea ▸ `Aprobata`, `DbName`, hash-ul linkului; e-mailul cu linkul | — |

E-mailul de la pasul 8 **nu** desface nimic dacă nu pleacă: unitatea e gata și corectă.
Răspunsul spune `link_trimis: false`, linia de comandă tipărește linkul ca să poată fi dat
de mână, iar `--link-nou` trimite altul.

Jobul **refuză să pornească** (fără să creeze nimic) dacă: cererea nu e `InAsteptare` sau
`Esuata`; adresa e deja cont sau utilizator; CF-ul are deja bază (D11); șablonul mai are
coloanele `*_w` sau are declanșatori / proceduri / evenimente (nu se copiază); un cod nu mai
trece de nomenclatoare. În toate cazurile de după încărcarea cererii, motivul ajunge în
`Motiv` și cererea devine `Esuata` — se poate relua după reparare.

O singură aprobare odată pe tot serverul (`GET_LOCK('kbot_provizionare')`): linia de comandă
și pagina nu se pot încurca una pe alta. Respingerea ia același lacăt.

## Plafonul de rânduri — adăugat, nu din plan

O cerere devine un rând `Clasificatii` per frunză F × frunză E × sursă-sector. Exemplul
planului e 20 × 40 × 2 = 1 600. Aritmetica nu se oprește acolo: toate frunzele ambilor
arbori și toate cele 14 surse ar însemna 531 × 686 × 14 — cinci milioane de rânduri, puse la
coadă dintr-un formular anonim. `cerere.MAX_ROWS` (50 000) e paza. **Nu e din plan**, e o
constantă și operatorul o poate muta.

## Durate

- Înregistrarea: **30 de minute**, absolut, de la căutarea ANAF. Fiecare scriere pune nota
  la loc cu **cât a mai rămas**, niciodată cu 30 de minute noi — fereastra nu poate fi
  plimbată înainte cerând cod după cod.
- Codul: **10 minute**, cel mult **5** încercări greșite (ca la felia 0072). Un cod nu poate
  trăi mai mult decât înregistrarea care îl poartă, iar `expires_in` spune numărul adevărat.

## Ce nu e verificat

- ~~**Jobul de aprobare n-a rulat niciodată**~~ — **a rulat** (23.09.2026): cererea 1 a picat
  de două ori la pasul 7 (1044, apoi gazda `'%%'`) și de fiecare dată **anularea a curățat
  tot**; a treia oară a trecut, iar e-mailul cu linkul a sosit. Anulările pașilor 3–6 au fost
  deci exersate; cea a pasului 7 (`Unitati_Utilizatori`) și pasul 8 în eșec, nu.
- **Pagina `/operator` (0075-05) n-a atins un Flask viu.** Verificată în browser pe un ciot
  care montează blueprint-ul și pagina adevărate, cu baza, e-mailul și jobul mimate.
- ~~`GRANT … WITH GRANT OPTION` pe tiparul `1__\_____`~~ — **infirmat pe server**
  (23.09.2026, 1044 la pasul 7): în `GRANT … ON db.*` numele e tipar, iar drepturile
  acordantului contează doar pe exact același șir. GRANT-ul trece acum prin procedura
  `AVACONT_COMUN.proc_Provizionare_Grant` a lui root (`sql/0075_03_02_grant_procedura.sql`).
- **Drepturile contului de provizionare**, restul: vizibilitatea
  declanșatorilor / procedurilor șablonului în `information_schema` pentru un cont fără
  drepturi pe ele — dacă nu le vede, verificarea lor trece pe lângă.
- **Nicio rută n-a fost pornită.** Nimic de aici n-a atins un Flask viu sau un MariaDB.
- ~~**Coloana de captiune a lui `DefaSursaSector`**~~ — **lămurită** pe 22.09.2026 seara, din
  schema adevărată (`MariaDB_Schema/AVACONT_COMUN.sql`, folder local, în `.gitignore`):
  `(SursaSector PK, Sursa, Sectorul, Denumire)`. `read_sursasector` găsește `Denumire` din
  prima. Căutarea de captiune (`Denumire` ▸ `Explicatie` ▸ `Descriere`, iar fără niciuna codul
  își ține loc de etichetă) **rămâne** în cod: nu costă nimic, și o listă de 14 coduri face mai
  mult decât un 500 pe un nume de coloană.
- **Limita de apeluri a ANAF pe v9** nu e cunoscută. Un apel per înregistrare e mult sub
  orice cifră publicată, dar numărul în sine n-a fost confirmat.
- **v9 nu are câmpul `cod`.** Funcția din Access decide «negăsit» după `cod <> "200"`;
  răspunsul real de pe 22.09.2026 avea exact două chei de nivel întâi, `found` și
  `notFound`. Aici se citește `found` gol. Diferența e intenționată.
