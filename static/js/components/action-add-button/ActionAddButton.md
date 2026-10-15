# ActionAddButton

Buton comun pentru adăugare, folosit în Produse și în antetul arborelui
„Documente salvate” din NIR. Aspectul, cursorul, hover-ul și dimensiunile
implicite sunt definite o singură dată în `action-add-button.css`.

Componenta urmează modelul controalelor JS existente: clasa `ActionAddButton`
creează un element HTML nativ `button`, accesibil prin `.element`.
Butonul are `type="button"`, deci nu trimite automat formularele.

## Încărcare și utilizare

Pagina încarcă stylesheet-ul comun înaintea CSS-ului aplicației:

```html
<link rel="stylesheet" href="/static/js/components/action-add-button/action-add-button.css">
```

```js
import { ActionAddButton } from '/static/js/components/action-add-button/action-add-button.js';

const addProduct = new ActionAddButton(toolbar, {
  title: 'Adauga produs nou',
  onClick: () => startAdd().catch(report),
});

const addReceipt = new ActionAddButton(heading, {
  title: 'Adaugă NIR',
  onClick: () => add().catch(report),
});
```

Primul argument este containerul în care se adaugă butonul. Poate fi
`null` pentru compunerea ulterioară: `toolbar.prepend(button.element)`.
Textele și iconul sunt atribuite prin `textContent`, fără HTML interpretat.

## Opțiuni

| Opțiune | Implicit | Rol |
|---|---|---|
| `icon` | `➕` | Emoji sau textul iconului; șirul gol ascunde iconul. |
| `label` | `''` | Text opțional; fără text, butonul are varianta pătrată pentru icon. |
| `iconPosition` | `'left'` | `'right'` pune iconul după text. |
| `title` | `''` | Tooltip; fallback la textul butonului sau „Adaugă”. |
| `ariaLabel` | `''` | Nume accesibil; fallback la tooltip. |
| `disabled` | `false` | Dezactivează butonul nativ. |
| `className` | `''` | Clase pentru poziționare sau variabile CSS ale paginii. |
| `onClick(event, button)` | — | Acțiunea paginii. Pagina gestionează erorile operației asincrone. |

## API și ciclu de viață

- `.element`: butonul nativ, pentru inserare, focus și proprietățile DOM.
- `setEnabled(boolean)`: activează/dezactivează butonul.
- `setIcon(text)`: schimbă sau ascunde iconul.
- `setLabel(text)`: schimbă textul și actualizează varianta doar cu icon.
- `destroy()`: elimină listener-ul prin AbortController și scoate butonul din DOM.

Pagina sau scope-ul care creează componenta trebuie să apeleze `destroy()`
la demontare. Nu recreați butonul la fiecare actualizare dacă antetul lui
rămâne în DOM; folosiți `setEnabled()` pentru schimbarea disponibilității.
Enter și Space folosesc comportamentul nativ al butonului. Iconul este
ascuns cititoarelor de ecran; numele accesibil vine din `ariaLabel`/tooltip.

## Variabile CSS

Variabilele pot fi stabilite pe buton, pe container sau la nivelul aplicației.
Paginile folosesc aceste variabile pentru diferențe deliberate și păstrează
regulile de hover, border, padding și cursor în componenta comună.

| Variabilă | Fallback |
|---|---|
| `--action-add-button-font-size` | `--fs-button`, apoi `16px` |
| `--action-add-button-icon-size` | `24px` |
| `--action-add-button-height` | `40px` |
| `--action-add-button-min-width` | `44px`; este și lățimea variantei doar cu icon |
| `--action-add-button-padding-x` | `10px`; varianta doar cu icon are padding orizontal zero |
| `--action-add-button-gap` | `6px` |
| `--action-add-button-radius` | `8px` |
| `--action-add-button-back` | `--card`, apoi alb |
| `--action-add-button-color` | `--ink`, apoi culoarea moștenită |
| `--action-add-button-border` | `--line`, apoi `#cdd5e0` |
| `--action-add-button-hover-back` | `--sky-2`, apoi `#dceaff` |
| `--action-add-button-hover-border` | `--accent`, apoi `#2684ff` |
| `--action-add-button-active-back` | `--sky-2`, apoi `#dceaff` |
| `--action-add-button-focus-color` | `--accent`, apoi `#2684ff` |

NIR păstrează dimensiunile anterioare ale textului și iconului:

```css
.vc-doc--nir .vc-receipt-tree__add {
  margin-inline-start: auto;
  --action-add-button-font-size: var(--fs-ms);
  --action-add-button-icon-size: var(--fs-lg);
}
```

Butonul disponibil are cursor `pointer`, fundal și bordură evidențiate la
hover și contur la focus din tastatură. Butonul dezactivat are cursor normal,
opacitate redusă și nu primește evidențiere la hover. Starea `hidden` este
respectată de CSS.

Integrarea VERCON este în `vercon.html`, `vercon/nomenclator-grid.js` și
`vercon/docs.js`. Stilurile vechi ale butoanelor de adăugare au fost eliminate
din CSS-ul produselor și al NIR; regula generică a barelor nomenclatoarelor
exclude `.k-action-add-button`, pentru a nu suprascrie stilul componentei.

Stare la 15.10.2026: implementare în surse; fără teste sau verificări executate,
conform AGENTS.md. Aspectul în browser nu este confirmat prin rulare.
