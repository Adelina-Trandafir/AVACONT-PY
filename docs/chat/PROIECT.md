# SLICE-CHAT — Chat comun in aplicatiile web

Data: 2026-10-20. Toate fisierele acestui proiect sunt in `PYTHON`, conform cerintei.

## Cerinte acceptate

- O singura conversatie comuna pentru utilizatorii autentificati K-BOT, ADECHIT si VERCON.
- Mesaje text, raspuns la mesaj, istoric paginat si indicator de necitite.
- Alegere explicita intre pseudonim si nume afisat. Implicit: pseudonim.
- Pseudonim aleator si stabil pe cont, comun celor trei aplicatii.
- Marcajul aplicatiei din care s-a trimis: V / A / K. V si A reproduc simbolurile albastre din antete; K foloseste logo-ul existent.
- Filtru cu selectie multipla, salvat pe utilizator in server. Initial: toate aplicatiile.
- Doar controale custom din `static/js/components`.
- Structura pregatita pentru extindere; conversatiile private, atasamentele, reactiile si notificarile externe nu sunt implementate in aceasta versiune.

## Subslice-uri

| Subslice | Responsabilitate | Stare reala |
| --- | --- | --- |
| SLICE-CHAT-01 | Model relational, DDL, seed, decizii de proiectare | Fisiere pregatite; SQL neexecutat |
| SLICE-CHAT-02 | API, sesiune, pseudonime, paginare, citire, retrimiteri | Cod implementat; neverificat functional |
| SLICE-CHAT-03 | Componenta comuna exclusiv din controale custom | Cod implementat; fara proba vizuala |
| SLICE-CHAT-04 | Integrare K-BOT / ADECHIT / VERCON | Cod integrat in surse; nepublicat |
| SLICE-CHAT-05 | Revenire la utf8mb4 pentru emoji | DDL pregatit pentru executare manuala |
| SLICE-CHAT-06 | Diagnostic 503 | Cauza raportata: seed neexecutat; diagnostic adaugat |
| SLICE-CHAT-07 | Inchidere, tastatura, polling si antet DISCUTII | Cod actualizat; fara teste sau probe in browser |
| SLICE-CHAT-08 | Bula mobila si pozitionare in viewport | Cod actualizat; fara teste sau probe in browser |
| SLICE-CHAT-09 | Mesaje compacte si alinierea compozitorului | Cod actualizat; fara teste sau probe in browser |

## Model si limite de acces

`CHAT_CONVERSATII` contine conversatiile, iar `CHAT_PARTICIPANTI` apartenenta si preferintele utilizatorului pentru o conversatie. Momentan API-ul rezolva exclusiv cheia `COMUN`, tip `COMUN`; nu accepta un ID de conversatie din client. O extensie cu conversatii private va trebui sa introduca verificarea explicita a apartenentei pentru fiecare operatie.

`CHAT_IDENTITATI` pastreaza intern contul, pseudonimul si numele afisat ales de utilizator. Nu exista un nume de persoana oferit de sesiunea actuala, de aceea numele afisat se completeaza in chat si se salveaza la trimitere. Acesta este un nume declarat, nu o identitate verificata. E-mailul contului nu devine implicit nume public.

`CHAT_MESAJE` pastreaza aplicatia sursa, modul de afisare si un instantaneu al numelui pentru mesajele cu nume. O schimbare ulterioara a numelui nu redenumeste mesajele vechi. Mesajele cu pseudonim nu salveaza numele in coloana publica `NUME_AUTOR`.

Proiectia publica trimite numai identitatea aferenta modului ales, inclusiv in previzualizarea raspunsului. Nu transmite contul, ID-ul autorului sau un identificator comun care sa lege pentru ceilalti mesajele cu nume de cele cu pseudonim. `mine` exista doar pentru utilizatorul care solicita lista. Raspunsul `state.self` contine doar propriile date de afisare. Nu exista endpoint public de enumerare sau de identificare a autorilor.

Legatura reala ramane in baza de date, pentru administrare ulterioara. Nu exista in aceasta versiune interfata sau API de dezvaluire a autorului/moderare. Aplicatia, momentul si continutul mesajului raman publice, conform cerintei de chat comun.

## Filtrare si necitite

- Eticheta V/A/K este contextul aplicatiei configurat la montarea componentei. Serverul valideaza codul; codul este un marcaj de discutie, nu o permisiune de acces la acea aplicatie.
- Preferinta nu separa conversatia si nu constituie restrictie de acces. Un raspuns poate afisa un scurt citat al unui mesaj din alta aplicatie.
- `CHAT_CITIRI` are cate o pozitie de citire pentru fiecare aplicatie si participant. Citirea V nu avanseaza pozitiile A sau K.
- Mesajele proprii nu se numara ca necitite.
- Mesajele se marcheaza citite numai cu panoul deschis, fila vizibila si focalizata, dupa incarcarea loturilor noi si cand utilizatorul se afla la finalul listei.
- Deschiderea la sfarsitul istoricului considera citit istoricul anterior pentru aplicatiile vizibile, comportament obisnuit pentru un chat.
- Filtrul se pastreaza pe cont si se sincronizeaza la urmatoarea actualizare intre file. O schimbare de filtru reincarca lista, pastrand ciorna.

## Ordine si retrimiteri

ID-urile de mesaj circula ca siruri zecimale; clientul le compara prin `BigInt`, fara pierdere de precizie. API-ul accepta fie `before`, fie `after`; maximum 100 de mesaje pe cerere, implicit 50. Incarcarea initiala arata cele mai recente mesaje, istoricul se cere separat.

Trimiterea blocheaza randul conversatiei inainte de alocarea ID-ului. Astfel, doua trimiteri concurente nu pot confirma ID-uri in ordine inversa si nu pot produce goluri pierdute de cursor. Tranzactiile folosesc READ COMMITTED si blocarea identitatii pentru serializarea trimiterilor aceluiasi cont. Deadlock-ul sau expirarea asteptarii unui lock permit cel mult doua reluari, fiecare dupa rollback complet.

Cheia UUID a trimiterii este unica pe autor si permite reluarea aceleiasi cereri fara mesaj dublu. Refolosirea ei cu alt continut este refuzata. Clientul pastreaza cheia unei trimiteri esuate cat timp ciorna ramane identica; confirmarea serverului goleste ciorna. Cheia nu se persista in browser. Exista o pauza minima de doua secunde intre mesaje, calculata in baza de date, inclusiv intre file/workeri.

## Sesiune si componenta

Toate endpoint-urile necesita sesiunea existenta `X-Portal-Token`. Chatul este independent de unitatea selectata si nu acorda acces la datele aplicatiilor. Refoloseste verificarea termenului din modulul portal, fara sa prelungeasca sesiunea prin polling. Dupa expirare panoul se distruge; navigarea si deconectarea opresc cererile si ascultatorii.

Componenta comuna este in `static/js/components/chat`. Foloseste `ActionAddButton`, `CheckBox` si `TextBox`. `TextBox` a primit optiunea explicita `multiline` pentru tipul text, cu aceeasi interfata si validare; comportamentul implicit al celorlalte campuri ramane cel existent. CSS-ul comun este langa componenta, importat prin stylesheet-ul de intrare `portal.css`, deja folosit de toate cele trei aplicatii.

Textele utilizatorilor sunt introduse in DOM ca text, nu HTML. Actualizarea este la aproximativ 5 secunde cu panoul deschis, respectiv 60 de secunde cu el inchis. Fila ascunsa nu face polling. La redeschiderea panoului sau revenirea in fila se actualizeaza numai datele invechite: 5 secunde pentru lista deschisa, 60 de secunde pentru starea panoului inchis. Actualizarea starii/necititelor nu face lista de mesaje proaspata; aceste momente sunt urmarite separat. Prima deschidere incarca lista. Butonul de actualizare manuala si trimiterea unui mesaj pot solicita actualizare imediata.

Enter trimite mesajul; Ctrl+Enter insereaza un rand nou la cursor, in limita de 4000 de caractere. Compozitia IME nu declanseaza trimiterea si mentinerea tastei Enter apasate nu repeta trimiteri. Panoul se inchide la apasare in exterior, fara sa anuleze actiunea controlului apasat si fara sa-i preia focalizarea. Ciorna ramane pastrata. Inchiderea reprogrameaza polling-ul la intervalul pentru panoul inchis.

Antetul are albastrul #3b5a85 din antetele aplicatiilor (variabila existenta --avacont-blue unde este definita), cu text si butoane albe. Titlul si butonul plutitor folosesc acelasi simbol 💬 si textul DISCUTII. Subtitlul a fost eliminat. Marcajele V/A/K raman pe mesaje si in filtru.

Actualizare 2026-10-21, SLICE-CHAT-08: bula DISCUȚII se muta prin tragere cu mouse-ul. Clicul simplu continua sa deschida/inchida panoul. Miscarea trebuie sa depaseasca 6 pixeli de ecran pentru a deveni tragere, iar clicul generat la finalul tragerii nu deschide accidental panoul. Pozitia este limitata la zona vizibila si retinuta local in browser ca proportii ale spatiului disponibil, fara cereri catre server. La redimensionare, modificarea zoom-ului aplicatiei sau schimbarea dimensiunii butonului, pozitia este readaptata. Panoul deschis se pozitioneaza in zona vizibila, deasupra, dedesubt sau lateral fata de bula, in functie de spatiul disponibil. Logica si valorile dinamice de pozitionare sunt gestionate de API-ul componentei din `components/chat/chat-position.js`; CSS-ul ramane separat.

## Activare manuala

1. Operatorul executa `PYTHON/sql/chat/SLICE-CHAT-01-schema.sql` in AVACONT_COMUN de pe serverul K-BOT.
2. Operatorul executa `PYTHON/sql/chat/SLICE-CHAT-01-seed.sql`, care adauga conversatia comuna.
3. Se publica fisierele noi si modificarile proiectului PYTHON prin fluxul obisnuit si se reporneste serviciul Flask. Acest pas nu a fost executat.

Este folosit contul existent al aplicatiei, prin `get_kbot_connection("AVACONT_COMUN")`; acesta trebuie sa aiba drepturile DML necesare pentru tabelele chat. Nu s-au schimbat configuratii, drepturi sau scheme. Nicio ruta nu contine creare automata de tabele/conversatii. Lipsa schemei/seed-ului produce indisponibilitatea chatului, fara a impiedica pornirea aplicatiilor.

## Limitele livrarii

Nu s-au rulat teste, verificari de sintaxa, verificari in browser, interogari de verificare sau comenzi pe server, conform instructiunilor proiectului. A fost facuta numai analiza de proiectare pe baza surselor existente. Implementarea necesita validare functionala si vizuala atunci cand aceasta va fi solicitata explicit. Nu exista atasamente, notificari in afara aplicatiei, conversatii private sau moderare in aceasta prima versiune.
