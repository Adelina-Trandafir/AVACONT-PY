# SLICE-CHAT-01 — Modelul de date si baza proiectului

Data: 2026-10-20.

Pregatite cinci tabele InnoDB, exclusiv cu identificatori noi uppercase: CHAT_IDENTITATI, CHAT_CONVERSATII, CHAT_PARTICIPANTI, CHAT_MESAJE, CHAT_CITIRI. Identitatea privata este separata de mesajele publice; preferintele si pozitiile de citire sunt pe participant/conversatie, citirea fiind separata pe V/A/K. Sunt prevazute raspunsuri, chei de retrimitere si indecsi pentru flux si filtrare.

Fisiere:
- `sql/chat/SLICE-CHAT-01-schema.sql`
- `sql/chat/SLICE-CHAT-01-seed.sql`
- `docs/chat/PROIECT.md`

Stare: SQL pregatit pentru executare manuala de operator. Nicio schema nu a fost modificata. Nu s-au rulat teste sau verificari; analiza de proiectare numai pe surse.
