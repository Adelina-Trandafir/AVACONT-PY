# Editor tooltip — Familia, inaltimea in PT si culoarea textului

Data: 2026-10-21.

Adaugate trei proprietati in Setari tooltip:

- Familia fontului, prin Combobox custom: implicit aplicatie sau Calibri, Carlito, Segoe UI, Arial, Tahoma, Verdana, Times New Roman, Georgia, Consolas.
- Inaltimea fontului in puncte, prin TextBox custom numeric cu virgula/punct zecimal; interval 6–48 PT. Implicit 9 PT, echivalentul celor 12 px folositi anterior la dimensiunea de baza a aplicatiei.
- Culoarea textului, prin componenta custom ColorPicker cu selector de culoare si bifa Automat. Automat pastreaza culoarea corespunzatoare aspectului/paletei/temei tooltipului.

Proprietatile sunt incluse in normalizare, previzualizare, revenirea la valorile implicite si salvarea existenta in localStorage. Setarile vechi fara aceste proprietati primesc valorile implicite; cheia de stocare este pastrata. Datele din stocare sunt limitate la familii cunoscute, puncte in interval si culori hex valide.

Tooltipul aplica valorile prin variabile CSS gestionate de API-ul componentei. Familia selectata este respectata si de textul/antetul interior, inclusiv in paginile care impun global fontul aplicatiei. Culoarea explicita este folosita si in tema intunecata sau aspectul Colorat. Pentru culoarea automata raman valabile regulile existente de aspect/tema. Greutatea si stilul fontului nu au fost schimbate.

Fisiere:

- `static/js/components/tooltip/tooltip-config.js`
- `static/js/components/tooltip/tooltip-settings.js`
- `static/js/components/tooltip/tooltip.js`
- `static/js/components/tooltip/tooltip.css`
- `static/js/components/colorpicker/colorpicker.js`
- `static/js/components/colorpicker/colorpicker.css`

Stare: modificari scrise in surse. Analiza de proiectare pe surse; nu s-au rulat teste, verificari de sintaxa sau probe in browser/server, conform instructiunilor. Nicio modificare de schema sau configuratie de server.
