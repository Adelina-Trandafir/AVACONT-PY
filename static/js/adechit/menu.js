// SLICE-AD10 (UI): the one popup menu of the ADE page -- the child filter in the grid footer, the header menu and
// the application menu all open through it. Same look as the receipt menu (ade-receipt-menu): an emoji per entry.
// entries: [{ icon, label, on, disabled, rule, run }]  (`rule: true` puts a separating line before the entry)
let closeCurrent = null;

export function closeMenu() { closeCurrent?.(); }

export function openMenu(k_anchor, k_entries, { label = 'Meniu', align = 'left', up = false, checkable = false, iconRight = false, below = null } = {}) {
  closeCurrent?.();
  const k_menu = document.createElement('div');
  k_menu.className = `ade-receipt-menu ade-filter-menu${iconRight ? ' ade-menu--icon-right' : ''}`;
  k_menu.setAttribute('role', 'menu'); k_menu.setAttribute('aria-label', label);
  const k_listeners = new AbortController();
  const k_close = (k_focus = false) => {
    k_listeners.abort(); k_menu.remove(); k_anchor.setAttribute('aria-expanded', 'false');
    if (k_focus && k_anchor.isConnected) k_anchor.focus();
    if (closeCurrent === k_close) closeCurrent = null;
  };
  closeCurrent = k_close;
  for (const k_entry of k_entries) {
    if (k_entry.rule) { const k_rule = document.createElement('div'); k_rule.className = 'ade-receipt-menu__rule'; k_rule.setAttribute('role', 'separator'); k_menu.append(k_rule); }
    const k_item = document.createElement('button'); k_item.type = 'button';
    k_item.setAttribute('role', checkable ? 'menuitemcheckbox' : 'menuitem');
    if (checkable) k_item.setAttribute('aria-checked', String(Boolean(k_entry.on)));
    k_item.disabled = Boolean(k_entry.disabled);
    const k_icon = document.createElement('span'); k_icon.className = 'ade-receipt-menu__icon'; k_icon.setAttribute('aria-hidden', 'true'); k_icon.textContent = k_entry.icon;
    const k_text = document.createElement('span'); k_text.textContent = k_entry.on ? `${k_entry.label} ✓` : k_entry.label;
    k_item.append(k_icon, k_text);
    if (k_entry.run) k_item.addEventListener('click', () => { k_close(); k_entry.run(); });
    k_menu.append(k_item);
  }
  document.body.append(k_menu); k_anchor.setAttribute('aria-expanded', 'true');
  k_menu.style.zIndex = String(window.ZIndexManager?.getNext() || 1001);
  const k_rect = k_anchor.getBoundingClientRect();
  const k_left = align === 'right' ? k_rect.right - k_menu.offsetWidth : k_rect.left;
  k_menu.style.left = `${Math.max(8, Math.min(k_left, innerWidth - k_menu.offsetWidth - 8))}px`;
  // `below`: an element (the header) whose lower edge the menu hangs from, instead of the button's own
  const k_top = up ? k_rect.top - k_menu.offsetHeight - 2 : (below ? below.getBoundingClientRect().bottom - 1 : k_rect.bottom + 2); // under the header: no gap
  k_menu.style.top = `${Math.max(8, Math.min(k_top, innerHeight - k_menu.offsetHeight - 8))}px`;
  const k_items = [...k_menu.querySelectorAll('button:not(:disabled)')]; k_items[0]?.focus();
  k_menu.addEventListener('keydown', (k_event) => {
    const k_at = k_items.indexOf(document.activeElement);
    if (k_event.key === 'ArrowDown' || k_event.key === 'ArrowUp') {
      k_event.preventDefault(); k_items[(k_at + (k_event.key === 'ArrowDown' ? 1 : k_items.length - 1)) % k_items.length]?.focus();
    }
  });
  document.addEventListener('pointerdown', (k_event) => { if (!k_menu.contains(k_event.target) && !k_anchor.contains(k_event.target)) k_close(); }, { capture: true, signal: k_listeners.signal });
  document.addEventListener('keydown', (k_event) => { if (k_event.key === 'Escape') { k_event.preventDefault(); k_close(true); } else if (k_event.key === 'Tab') k_close(); }, { signal: k_listeners.signal });
  addEventListener('resize', () => k_close(), { signal: k_listeners.signal });
  return k_close;
}
