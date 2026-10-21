# SLICE-CHAT-03 — Componenta custom pentru chat

Data: 2026-10-20.

Panou comun cu buton de deschidere si necitite, mesaje, citate, raspunsuri, paginare, filtru V/A/K si alegerea nume/pseudonim. Exclusiv ActionAddButton, CheckBox si TextBox din components pentru elementele interactive. TextBox suporta acum optiunea multiline, fara schimbarea modului implicit al campurilor existente. Mesajele se afiseaza ca text, fara interpretare HTML.

Simbolurile V/A reproduc grafica albastra a antetelor; K foloseste logo-ul deja existent. Ciorna se pastreaza la inchiderea panoului si la schimbarea filtrului, cat timp pagina ramane deschisa. CSS-ul este langa componenta si foloseste culorile temei curente.

Fisiere:
- `static/js/components/chat/chat-panel.js`
- `static/js/components/chat/chat.css`
- `static/js/components/chat/applications.js`
- `static/js/components/chat/icons/v.svg`
- `static/js/components/chat/icons/a.svg`
- `static/js/components/textbox/textbox.js`
- `static/css/textbox.css`

Stare: cod implementat. Nu s-au rulat teste, probe vizuale sau verificari in browser.
