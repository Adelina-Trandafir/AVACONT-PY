# Politica orelor AVACONT

Actualizare: 20.10.2026. Implementat local; nepublicat si nerulat.

## Regula

Momentele tehnice generate de server sunt UTC: creare, modificare, trimitere,
citire, audit, expirari, loguri, metadatele copiilor si momentele generarii
fisierelor. `utils/clock.py` contine ceasul comun. Pentru coloanele DATETIME
existente, `utc_now()` si `utc_from_timestamp()` returneaza datetime fara
tzinfo, dar cu valoare UTC explicita. Nu depind de fusul calculatorului gazda.

Conexiunile MariaDB ale aplicatiei impun `time_zone='+00:00'` pe sesiune,
inclusiv conexiunile directe ale comenzilor de administrare si scripturilor.
Astfel NOW(), CURRENT_TIMESTAMP, conversiile TIMESTAMP si calculele Python
folosesc aceeasi conventie. Nu se schimba configuratia globala MariaDB, nu se
creeaza tabele de fusuri orare si nu se executa DDL pentru aceasta schimbare.
Conexiunile altor clienti (de exemplu Access) isi pastreaza configuratia proprie.

`main.py`, configuratia Gunicorn si comenzile administratorului fixeaza si
ceasul local al procesului Linux la UTC, pentru biblioteci si loguri. Formatterele
proprii si timpii din jurnalele de performanta folosesc UTC explicit. Citirea si
retentia logului de acces respecta offsetul inscris in fiecare linie.

## Date calendaristice

Data facturii, data bonului, perioada raportului, anul de lucru si data efectiva
a unui eveniment din istoricul copiilor ADECHIT sunt date de activitate, nu
momente UTC. Valorile introduse de utilizator nu se deplaseaza cu ore.
Implicitul „azi” se calculeaza cu `bucharest_today()`, dupa Europe/Bucharest,
inclusiv intre miezul noptii din Romania si miezul noptii UTC. Zona necesita
datele IANA tzdata disponibile pe serverul Linux.

## Afisare si compatibilitate

Chatul continua sa salveze UTC si sa trimita created_at cu sufixul Z. Browserul
il afiseaza in fusul calculatorului utilizatorului, conform comportamentului
existent. Actiunile recente continua sa trimita UTC cu offset, iar clientul
desktop le converteste la ora calculatorului sau.

Contractele mai vechi care transmit un datetime fara offset isi pastreaza forma;
momentele tehnice produse de server au conventia UTC. Nu se adauga automat Z
datelor calendaristice, datelor importate sau tuturor campurilor DATETIME.
Semnaturile PDF cu offset sunt convertite explicit la UTC inainte de salvare;
valorile vechi fara offset sunt tratate conform conventiei existente UTC.

La o conversie de afisare se foloseste Europe/Bucharest: UTC+2 iarna, UTC+3 vara.
Nu se foloseste o adunare fixa de 3 ore pentru toate datele. Ora salvarii unui
mesaj reprezinta inserarea in baza de date; nu certifica momentul livrarii sale
in browserul destinatarului. Citirea are propriul marcaj.

## Publicare

Se publica impreuna `utils/clock.py` si fisierele Python modificate: pornirea
aplicatiei, configuratia Gunicorn, conexiunile, logurile si modulele afectate din
VERCON, ADECHIT, FOREXE, e-Factura, inregistrare, migrare si administrare.
Se reporneste apoi serviciul:

```sh
systemctl restart avacont
```

VPS-ul din discutie era deja pe Etc/UTC; nu necesita schimbarea ceasului.
Daca intre timp a fost trecut pe alt fus, revenirea gazdei se face separat:

```sh
timedatectl set-timezone Etc/UTC
```

Schimbarea codului nu converteste istoricul, copiile restaurate sau datele
primite din sisteme externe. Un DATETIME istoric nu indica singur daca a fost
scris in UTC ori in ora Romaniei; o conversie a istoricului necesita stabilirea
originii datelor. Nu se aplica o deplasare globala automata.

Nu au fost rulate teste, compilari, verificari de sintaxa, interogari sau comenzi
pe VPS. S-a facut numai revizuirea de design a surselor. Testul existent pentru
semnaturi PDF a fost adaptat asteptarii UTC explicite, fara executare.
