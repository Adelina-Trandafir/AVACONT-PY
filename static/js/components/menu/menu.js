/**
 * MENU - the one menu control of the web applications: a BAR (the navigation of a page) and a POPUP (the list that opens from a button).
 * Both look the same everywhere: the text sizes come from css/typography.css (--fs-nav, --fs-menu), the colours from the theme of the
 * page, and each menu can be tuned with --hover-backcolor and --mn-* variables on its container (see css/menu.css).
 *
 * POPUP    openMenu(anchor, entries, options)  ->  close()      (closeMenu() closes the open one)
 *   entry    { key, icon, label, on, disabled, reason, rule, run }
 *              on / checked  ticked entry (needs options.checkable);  reason  the tooltip of a disabled entry;
 *              rule: true  puts a separating line before the entry;  run()  is called after the menu closed.
 *   options  { label, align: 'left'|'right', up, below, checkable, iconRight, dropdownMinWidth, onSelect(entry), onBeforeSelect(entry), onOpen, onClose }
 *              up: opens above the anchor;  below: an element whose lower edge the menu hangs from;  iconRight: icon column on the right;
 *              onBeforeSelect returning false cancels the choice (the menu stays open).
 *              dropdownMinWidth: optional minimum outer width in pixels, capped to the viewport.
 *
 * BAR      const menu = new Menu(container, { items, active, label, onSelect, onBeforeSelect })
 *   item     { key, label, icon, group, enabled, reason, children, dropdownMinWidth, run }
 *              children: items of a popup that opens from this button (the button itself selects nothing);
 *              group: items with the same group sit together.
 *              dropdownMinWidth: minimum popup width in pixels for THIS button; omitted = button width, less --reduce-by per side.
 *   methods  setItems(items), setActive(key), setEnabled(key, on), setBusy(on), destroy()
 *   events   onSelect({ item }) after a choice, onBeforeSelect(item) -> false cancels
 *   The active item (or the parent of the active child) is marked aria-current="true".
 */
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

let closeCurrent = null;
let currentPopup = null;

export function closeMenu() { closeCurrent?.(); }

export function openMenu(k_anchor, k_entries, { label = 'Meniu', align = 'left', up = false, checkable = false, iconRight = false, below = null, dropdownMinWidth = null, onSelect = null, onBeforeSelect = null, onOpen = null, onClose = null } = {}) {
  const k_bar = k_anchor.closest('.kmenu-bar');
  const k_previousRect = currentPopup && currentPopup.anchor !== k_anchor && k_bar &&
    currentPopup.anchor.closest('.kmenu-bar') === k_bar && currentPopup.menu.isConnected
    ? layoutRect(currentPopup.menu) : null;
  closeCurrent?.();
  const k_menu = document.createElement('div');
  k_menu.className = `kmenu-popup${iconRight ? ' kmenu-popup--icon-right' : ''}${up ? ' kmenu-popup--up' : ''}`;
  k_menu.setAttribute('role', 'menu'); k_menu.setAttribute('aria-label', label);
  const k_listeners = new AbortController();
  let k_openFrame = 0, k_openTimer = 0;
  const k_close = (k_focus = false) => {
    cancelAnimationFrame(k_openFrame); clearTimeout(k_openTimer);
    k_listeners.abort(); k_menu.remove(); k_anchor.setAttribute('aria-expanded', 'false');
    if (k_focus && k_anchor.isConnected) k_anchor.focus();
    if (closeCurrent === k_close) closeCurrent = null;
    if (currentPopup?.menu === k_menu) currentPopup = null;
    onClose?.();
  };
  closeCurrent = k_close;
  currentPopup = { anchor: k_anchor, menu: k_menu };
  for (const k_entry of k_entries) {
    if (k_entry.rule) { const k_rule = document.createElement('div'); k_rule.className = 'kmenu-popup__rule'; k_rule.setAttribute('role', 'separator'); k_menu.append(k_rule); }
    const k_ticked = Boolean(k_entry.on ?? k_entry.checked);
    const k_item = document.createElement('button'); k_item.type = 'button';
    k_item.setAttribute('role', checkable ? 'menuitemcheckbox' : 'menuitem');
    if (checkable) k_item.setAttribute('aria-checked', String(k_ticked));
    k_item.disabled = Boolean(k_entry.disabled);
    if (k_entry.disabled && k_entry.reason) k_item.title = k_entry.reason;
    const k_icon = document.createElement('span'); k_icon.className = 'kmenu-popup__icon'; k_icon.setAttribute('aria-hidden', 'true'); k_icon.textContent = k_entry.icon ?? '';
    const k_text = document.createElement('span'); k_text.textContent = k_ticked ? `${k_entry.label} ✓` : k_entry.label;
    k_item.append(k_icon, k_text);
    k_item.addEventListener('click', () => {
      if (onBeforeSelect?.(k_entry) === false) return;
      k_close(); onSelect?.(k_entry); k_entry.run?.();
    });
    k_menu.append(k_item);
  }
  (k_anchor.closest('dialog') || document.body).append(k_menu); k_anchor.setAttribute('aria-expanded', 'true');
  // The popup is portalled outside the bar; carry the anchor's CSS configuration with it.
  const k_anchorStyle = getComputedStyle(k_anchor);
  for (const k_name of ['--hover-backcolor', '--reduce-by', '--mn-reveal-duration', '--mn-font-family', '--mn-popup-font-size', '--mn-icon-size']) {
    const k_value = k_anchorStyle.getPropertyValue(k_name).trim();
    if (k_value) k_menu.style.setProperty(k_name, k_value);
  }
  if (Number.isFinite(dropdownMinWidth) && dropdownMinWidth >= 0) {
    k_menu.style.setProperty('--_menu-min-width', `${dropdownMinWidth}px`);
  }
  k_menu.style.zIndex = String(window.ZIndexManager?.getNext() || 1001);
  const k_rect = layoutRect(k_anchor);
  const k_viewport = layoutViewport();
  const k_barButton = k_anchor.classList.contains('kmenu-item');
  if (k_barButton) {
    k_menu.style.setProperty('--_menu-anchor-width', `${k_rect.width}px`);
    k_menu.classList.add('kmenu-popup--anchored');
  }
  const k_menuWidth = layoutRect(k_menu).width;
  const k_inset = k_barButton ? Math.max(0, (k_rect.width - k_menuWidth) / 2) : 0;
  const k_left = align === 'right' ? k_rect.right - k_inset - k_menuWidth : k_rect.left + k_inset;
  const k_gutter = k_barButton ? 0 : 8;
  const k_placedLeft = Math.max(k_gutter, Math.min(k_left, k_viewport.width - k_menu.offsetWidth - k_gutter));
  k_menu.style.left = `${k_placedLeft}px`;
  // The icon band keeps the same compact CSS width for every popup, independently of its anchor icon.
  // `below`: an element (the header) whose lower edge the menu hangs from, instead of the button's own
  const k_top = up ? k_rect.top - k_menu.offsetHeight - 2 : (below ? layoutRect(below).bottom - 1 : k_rect.bottom + (k_barButton ? 0 : 2)); // bar popups touch their button
  const k_placedTop = Math.max(8, Math.min(k_top, k_viewport.height - k_menu.offsetHeight - 8));
  k_menu.style.top = `${k_placedTop}px`;
  if (k_barButton && !up && !below && Math.abs(k_placedTop - k_rect.bottom) < .5) {
    const k_joinStart = Math.max(0, Math.min(k_menuWidth, k_rect.left + 1 - k_placedLeft));
    const k_joinEnd = Math.max(k_joinStart, Math.min(k_menuWidth, k_rect.right - 1 - k_placedLeft));
    k_menu.style.setProperty('--_menu-join-start', `${k_joinStart}px`);
    k_menu.style.setProperty('--_menu-join-end', `${k_joinEnd}px`);
    k_menu.classList.add('kmenu-popup--joined');
  }
  const k_items = [...k_menu.querySelectorAll('button:not(:disabled)')]; k_items[0]?.focus({ preventScroll: true });
  k_menu.addEventListener('keydown', (k_event) => {
    const k_at = k_items.indexOf(document.activeElement);
    if (k_event.key === 'ArrowDown' || k_event.key === 'ArrowUp') {
      k_event.preventDefault(); k_items[(k_at + (k_event.key === 'ArrowDown' ? 1 : k_items.length - 1)) % k_items.length]?.focus();
    } else if (k_event.key === 'Home' || k_event.key === 'End') {
      k_event.preventDefault(); k_items[k_event.key === 'Home' ? 0 : k_items.length - 1]?.focus();
    }
  });
  document.addEventListener('pointerdown', (k_event) => {
    if (k_menu.contains(k_event.target) || k_anchor.contains(k_event.target)) return;
    const k_nextButton = k_event.target.closest?.('.kmenu-item[aria-haspopup="menu"]:not(:disabled)');
    // Keep the current geometry until the other button's click starts the move.
    if (k_bar && k_nextButton?.closest('.kmenu-bar') === k_bar) return;
    k_close();
  }, { capture: true, signal: k_listeners.signal });
  document.addEventListener('keydown', (k_event) => { if (k_event.key === 'Escape') { k_event.preventDefault(); k_close(true); } else if (k_event.key === 'Tab') k_close(); }, { signal: k_listeners.signal });
  addEventListener('resize', () => k_close(), { signal: k_listeners.signal });
  k_menu.style.setProperty('--_menu-open-height', `${k_menu.offsetHeight}px`);
  const k_initialClass = k_previousRect ? 'is-moving' : 'is-opening';
  const k_finalClass = k_previousRect ? 'is-positioned' : 'is-revealed';
  if (k_previousRect) {
    const k_targetRect = layoutRect(k_menu);
    for (const [k_name, k_value] of Object.entries({
      '--_menu-from-x': k_previousRect.left - k_targetRect.left,
      '--_menu-from-y': k_previousRect.top - k_targetRect.top,
      '--_menu-from-width': k_previousRect.width,
      '--_menu-from-height': k_previousRect.height,
      '--_menu-to-width': k_targetRect.width,
      '--_menu-to-height': k_targetRect.height,
    })) k_menu.style.setProperty(k_name, `${k_value}px`);
  }
  k_menu.classList.add(k_initialClass);
  // Commit the starting geometry with transitions off before setting the target.
  void k_menu.offsetHeight;
  const k_finishMotion = () => {
    clearTimeout(k_openTimer);
    k_menu.classList.remove(k_initialClass, k_finalClass);
  };
  k_menu.addEventListener('transitionend', (k_event) => {
    if (k_event.target === k_menu && k_event.propertyName === (k_previousRect ? 'transform' : 'max-height')) k_finishMotion();
  }, { signal: k_listeners.signal });
  // Paint the starting state first, then move/reveal on the following frame.
  k_openFrame = requestAnimationFrame(() => {
    k_openFrame = requestAnimationFrame(() => {
      k_menu.classList.add(k_finalClass);
      const k_durations = getComputedStyle(k_menu).transitionDuration.split(',').map((k_duration) => {
        const k_value = k_duration.trim();
        return parseFloat(k_value) * (k_value.endsWith('ms') ? 1 : 1000);
      });
      const k_revealMs = Math.max(0, ...k_durations.filter(Number.isFinite));
      k_openTimer = setTimeout(k_finishMotion, k_revealMs + 80);
    });
  });
  onOpen?.();
  return k_close;
}

export class Menu {
  constructor(k_container, k_options = {}) {
    if (!k_container) throw new Error('Menu: container is required');
    this.container = k_container;
    this.options = { items: [], active: null, label: 'Meniu', onSelect: null, onBeforeSelect: null, ...k_options };
    this.items = [];
    this.active = this.options.active;
    this._abort = new AbortController();
    this.container.classList.add('kmenu-bar');
    this.container.setAttribute('role', 'navigation');
    this.container.setAttribute('aria-label', this.options.label);
    this.setItems(this.options.items);
  }

  setItems(k_items) {
    this.items = k_items.filter((k_item) => k_item && !k_item.hidden);
    this._render();
  }

  setActive(k_key) { this.active = k_key; this._render(); }

  setEnabled(k_key, k_on) {
    const k_all = this.items.flatMap((k_item) => [k_item, ...(k_item.children || [])]);
    const k_item = k_all.find((k_candidate) => k_candidate.key === k_key);
    if (k_item) { k_item.enabled = Boolean(k_on); this._render(); }
  }

  setBusy(k_on) { this.container.inert = Boolean(k_on); }

  _isActive(k_item) { return k_item.key === this.active || (k_item.children || []).some((k_child) => k_child.key === this.active); }

  _select(k_item) {
    if (this.options.onBeforeSelect?.(k_item) === false) return;
    this.options.onSelect?.({ item: k_item });
    k_item.run?.();
  }

  _render() {
    this._abort.abort(); this._abort = new AbortController();
    const { signal } = this._abort;
    this.container.hidden = !this.items.length;
    this.container.replaceChildren();
    const k_groups = new Map();
    for (const k_item of this.items) {
      const k_name = k_item.group ?? '';
      if (!k_groups.has(k_name)) k_groups.set(k_name, []);
      k_groups.get(k_name).push(k_item);
    }
    for (const k_list of k_groups.values()) {
      const k_group = document.createElement('div'); k_group.className = 'kmenu-group';
      for (const k_item of k_list) {
        const k_button = document.createElement('button'); k_button.type = 'button'; k_button.className = 'kmenu-item';
        if (k_item.icon) { const k_icon = document.createElement('span'); k_icon.className = 'kmenu-item__icon'; k_icon.setAttribute('aria-hidden', 'true'); k_icon.textContent = k_item.icon; k_button.append(k_icon); }
        k_button.append(Object.assign(document.createElement('span'), { className: 'kmenu-item__text', textContent: k_item.label }));
        k_button.disabled = k_item.enabled === false;
        if (k_item.reason) k_button.title = k_item.reason;
        if (this._isActive(k_item)) k_button.setAttribute('aria-current', 'true');
        if (k_item.children?.length) {
          k_button.setAttribute('aria-haspopup', 'menu');
          k_button.addEventListener('click', () => {
            if (this.options.onBeforeSelect?.(k_item) === false) return;
            openMenu(k_button, k_item.children.map((k_child) => ({ ...k_child, disabled: k_child.enabled === false, run: () => this._select(k_child) })), { label: k_item.label, dropdownMinWidth: k_item.dropdownMinWidth });
          }, { signal });
        } else k_button.addEventListener('click', () => this._select(k_item), { signal });
        k_group.append(k_button);
      }
      this.container.append(k_group);
    }
  }

  destroy() { this._abort.abort(); this.container.replaceChildren(); this.container.classList.remove('kmenu-bar'); }
}
