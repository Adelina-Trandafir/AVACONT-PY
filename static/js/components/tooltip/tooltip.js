import { getTooltipSettings, normalizeTooltipSettings } from './tooltip-config.js';
import { tooltipMedia } from './tooltip-environment.js';
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

const styled = new WeakSet();
const active = new Map();
const waiting = [];
const titles = { info: 'Informare', success: 'Operație finalizată', warning: 'Atenție', error: 'Eroare' };

export function ensureTooltipStyle(doc = document) {
  if (styled.has(doc)) return;
  styled.add(doc);
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = '/static/js/components/tooltip/tooltip.css';
  doc.head.append(link);
}

const hasRoom = parent => [...active.values()].filter(value => value === parent).length < 3;
function drain() {
  let index;
  while ((index = waiting.findIndex(entry => hasRoom(entry.parent))) >= 0) waiting.splice(index, 1)[0].display();
}

/** Plain text only. Milliseconds; duration: 0 keeps the notice until dismissed.
 * Returns { close(), closed }; a pending/delayed notice can also be cancelled.
 * Overrides apply to this notice only. Defaults are shared by the three apps.
 */
export function showTooltip(message, options = {}) {
  const source = options.source;
  const doc = options.ownerDocument || source?.ownerDocument || document;
  const win = doc.defaultView;
  const mobile = tooltipMedia(win);
  if (mobile.matches) return { close() {}, closed: Promise.resolve('mobile'), get element() { return null; } };
  const anchor = options.anchor;
  const config = normalizeTooltipSettings({ ...getTooltipSettings(), ...options });
  const level = Object.hasOwn(titles, options.level) ? options.level : 'info';
  const parent = (source ? source.closest('dialog') : doc.activeElement?.closest('dialog[open]')) || doc.body;
  ensureTooltipStyle(doc);
  let done = false, visible = false, entered = false, host, card, timer, delayTimer, animationTimer, enterTimer, observer;
  let remaining = config.duration, started = 0, hovered = false;
  const listeners = new AbortController();
  let resolveClosed;
  const closed = new Promise(resolve => { resolveClosed = resolve; });
  const relevant = () => (!source || source.isConnected) && parent.isConnected &&
    (parent.tagName !== 'DIALOG' || parent.open) && !source?.parentElement?.closest('[hidden]') &&
    !mobile.matches && (!anchor || !anchor.disabled);
  const finish = () => {
    clearTimeout(animationTimer);
    card?.remove();
    if (host && !host.childElementCount) host.remove();
    active.delete(close); drain();
  };
  function close(reason = 'dismissed', immediate = false) {
    if (done) return;
    done = true;
    clearTimeout(timer); clearTimeout(delayTimer); clearTimeout(enterTimer); listeners.abort(); observer?.disconnect();
    const index = waiting.findIndex(entry => entry.display === display);
    if (index >= 0) waiting.splice(index, 1);
    resolveClosed(reason);
    if (visible && !immediate) {
      card.classList.add('kbot-tooltip--closing');
      animationTimer = setTimeout(finish, config.animation);
    } else finish();
  }
  const pause = () => {
    if (!started) return;
    remaining = Math.max(0, remaining - (win.performance.now() - started));
    clearTimeout(timer); started = 0;
  };
  const resume = () => {
    if (done || !visible || !entered || !config.duration || started || hovered || doc.hidden || card.contains(doc.activeElement)) return;
    started = win.performance.now();
    timer = setTimeout(() => close('timeout'), remaining);
  };
  function display() {
    if (done) return;
    if (!relevant()) { close('source-closed'); return; }
    if (!anchor) active.set(close, parent);
    host = anchor ? null : parent.querySelector(`:scope > .kbot-tooltip-host[data-position="${config.position}"]`);
    if (!host) {
      host = doc.createElement('div');
      host.className = `kbot-tooltip-host kbot-tooltip-host--${anchor ? 'anchored' : config.position}`;
      host.setAttribute('popover', 'manual');
      // An empty title stops native tooltips inherited from any ancestor.
      host.setAttribute('title', '');
      host.setAttribute('data-z-index-ignore', '');
      host.dataset.position = anchor ? 'anchored' : config.position;
      host.style.zIndex = String(win.ZIndexManager?.getNext?.() || 10000);
      parent.append(host);
    }
    card = doc.createElement('section');
    card.className = `kbot-tooltip kbot-tooltip--${config.appearance} kbot-tooltip--${config.accent} kbot-tooltip--${level}`;
    if (anchor) card.classList.add('kbot-tooltip--anchored');
    // Dynamic geometry/animation is controlled through this component's API.
    card.style.setProperty('--kbot-tooltip-width', `${config.width}px`);
    card.style.setProperty('--kbot-tooltip-animation', `${config.animation}ms`);
    const announcement = doc.createElement('div');
    announcement.setAttribute('role', anchor ? 'tooltip' : level === 'error' ? 'alert' : 'status');
    if (options.descriptionId) announcement.id = options.descriptionId;
    announcement.setAttribute('aria-atomic', 'true');
    if (config.showHeader) {
      const header = doc.createElement('header'); header.className = 'kbot-tooltip__header';
      const heading = doc.createElement('strong');
      const icon = doc.createElement('span'); icon.setAttribute('aria-hidden', 'true'); icon.textContent = config.icon;
      heading.append(icon, doc.createTextNode(`${config.icon ? ' ' : ''}${config.title || titles[level]}`));
      header.append(heading); announcement.append(header);
    }
    const text = doc.createElement('p'); text.className = 'kbot-tooltip__text';
    announcement.append(text); card.append(announcement); host.append(card);
    if (!anchor) {
      const dismiss = doc.createElement('button'); dismiss.type = 'button'; dismiss.className = 'kbot-tooltip__close';
      dismiss.textContent = '×'; dismiss.setAttribute('aria-label', 'Închide notificarea');
      dismiss.addEventListener('click', () => close()); card.append(dismiss);
    }
    try { host.showPopover?.(); } catch (_) { /* z-index fallback for older browsers */ }
    visible = true;
    // Populate after insertion so assistive technologies receive the live-region update.
    text.textContent = String(message);
    const opts = { signal: listeners.signal };
    if (anchor) {
      const place = () => {
        if (!relevant()) { close('source-closed'); return; }
        const rect = layoutRect(anchor), viewport = layoutViewport();
        const left = Math.max(8, Math.min(rect.left, viewport.width - host.offsetWidth - 8));
        const below = rect.bottom + 8;
        const fitsBelow = below + host.offsetHeight <= viewport.height - 8;
        const top = fitsBelow ? below : rect.top - host.offsetHeight - 8;
        host.dataset.placement = fitsBelow ? 'below' : 'above';
        host.style.setProperty('--kbot-tooltip-arrow-left', `${Math.max(8, Math.min(rect.left + rect.width / 2 - left, host.offsetWidth - 8))}px`);
        host.style.left = `${left}px`;
        host.style.top = `${Math.max(8, Math.min(top, viewport.height - host.offsetHeight - 8))}px`;
      };
      place(); win.addEventListener('resize', place, opts);
      doc.addEventListener('scroll', place, { capture: true, ...opts });
      if (win.ResizeObserver) {
        const sizeObserver = new win.ResizeObserver(place);
        sizeObserver.observe(host);
        listeners.signal.addEventListener('abort', () => sizeObserver.disconnect(), { once: true });
      }
    }
    card.addEventListener('pointerenter', () => { hovered = true; pause(); }, opts);
    card.addEventListener('pointerleave', () => { hovered = false; resume(); }, opts);
    card.addEventListener('focusin', pause, opts);
    card.addEventListener('focusout', () => queueMicrotask(resume), opts);
    doc.addEventListener('visibilitychange', () => doc.hidden ? pause() : resume(), opts);
    enterTimer = setTimeout(() => { entered = true; resume(); }, config.animation);
  }
  const enqueue = () => {
    if (!message) { close('empty'); return; }
    if (anchor || hasRoom(parent)) display(); else waiting.push({ display, parent });
  };
  win.addEventListener('pagehide', () => { close('pagehide'); finish(); }, { signal: listeners.signal });
  mobile.addEventListener('change', () => { if (mobile.matches) { close('mobile'); finish(); } }, { signal: listeners.signal });
  if (parent.tagName === 'DIALOG') parent.addEventListener('close', () => close('source-closed'), { signal: listeners.signal });
  if (source || parent.tagName === 'DIALOG') {
    observer = new win.MutationObserver(() => { if (!relevant()) close('source-closed'); });
    observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['open', 'hidden', 'disabled'] });
  }
  if (config.delay) delayTimer = setTimeout(enqueue, config.delay); else enqueue();
  return { close, closed, get element() { return card; } };
}
