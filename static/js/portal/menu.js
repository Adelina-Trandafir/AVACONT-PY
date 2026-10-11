// The menu of the portal header (next to the K-BOT brand): ONE button with a menu emoji; every
// entry lives in the list it opens. Built by hand, so anything about it can be changed: the entries
// are plain data in MENU, the markup and the look (.pmenu* in portal.css) are ours.
//
// An entry is {key, label} for a button, or {key, label, items: [{key, label}]} for a group (a
// caption with its entries indented under it). A click on a leaf fires onPick(key); the page
// decides what each key does.

const MENU = [
  {
    key: 'nomenclatoare',
    label: 'Nomenclatoare',
    items: [
      { key: 'clasificatii', label: 'Clasificații' },
      { key: 'parteneri', label: 'Parteneri' },
    ],
  },
  { key: 'extrase', label: 'Extrase' },
];

export function createMenu(host, onPick) {
  let isOpen = false;

  const li = document.createElement('li');
  li.className = 'pmenu__item';
  const top = document.createElement('button');
  top.type = 'button';
  top.className = 'pmenu__top';
  top.setAttribute('aria-haspopup', 'menu');
  top.setAttribute('aria-expanded', 'false');
  top.setAttribute('aria-label', 'Meniu');
  top.title = 'Meniu';
  top.innerHTML = '<span class="pmenu__ico" aria-hidden="true">☰</span> <span class="pmenu__txt">Meniu</span>';
  const list = document.createElement('ul');
  list.className = 'pmenu__list card';
  list.setAttribute('role', 'menu');
  list.hidden = true;
  li.appendChild(top);
  li.appendChild(list);
  host.appendChild(li);

  function closeAll() {
    isOpen = false;
    top.setAttribute('aria-expanded', 'false');
    list.hidden = true;
  }

  function toggle() {
    isOpen = !isOpen;
    top.setAttribute('aria-expanded', String(isOpen));
    list.hidden = !isOpen;
  }

  function leaf(entry, nested) {
    const sli = document.createElement('li');
    sli.setAttribute('role', 'none');
    const b = document.createElement('button');
    b.type = 'button';
    b.className = nested ? 'pmenu__sub pmenu__sub--nested' : 'pmenu__sub';
    b.setAttribute('role', 'menuitem');
    b.textContent = entry.label;
    b.addEventListener('click', () => { closeAll(); onPick(entry.key, entry.label); });
    sli.appendChild(b);
    return sli;
  }

  function addEntry(entry) {
    const els = [];
    if (entry.items) {
      const cap = document.createElement('li');
      cap.className = 'pmenu__group';
      cap.setAttribute('role', 'presentation');
      cap.textContent = entry.label;
      els.push(cap);
      entry.items.forEach((sub) => els.push(leaf(sub, true)));
    } else {
      els.push(leaf(entry, false));
    }
    els.forEach((e) => list.appendChild(e));
    return els;
  }

  top.addEventListener('click', toggle);
  MENU.forEach(addEntry);

  // a click anywhere else, or Escape, closes the open list
  document.addEventListener('click', (e) => { if (!li.contains(e.target)) closeAll(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });
  return {
    closeAll
  };
}
