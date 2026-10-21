# SLICE-CHAT-05 — Revenire la utf8mb4 pentru emoji

Data: 2026-10-20.

Utilizatorul a comunicat schimbarea tabelelor la utf8mb3_general_ci, apoi a cerut DDL pentru revenirea la definitia initiala, cu suport emoji. Schema de baza ramane utf8mb4 / utf8mb4_unicode_ci. Nu au fost introduse restrictii pentru emoji in cod.

Fisier pregatit pentru executare manuala: `sql/chat/SLICE-CHAT-05-conversie-utf8mb4.sql`. Converteste tabelele si coloanele text existente si pastreaza definitiile originale pentru CONTINUT (TEXT) si CHEIE_CLIENT (ASCII / ascii_bin).

Stare: DDL pregatit, neexecutat de asistent. Nu s-au rulat teste sau alte verificari si nu s-a accesat baza de date.
