import { ensureTooltipStyle, showTooltip } from './tooltip.js';
import { tooltipMedia, tooltipsAllowed } from './tooltip-environment.js';
import { treeTooltipTarget, treeNodeClipped, treeTooltipAllowed, treeTooltipText } from '../treeview/treeview-tooltip.js';

const installed = new WeakMap();
let nextId = 0;

// Convert title attributes once, and keep accepting titles written by dynamic controls.
// Event delegation avoids per-cell listeners in grids. No touch or long-press handlers.
export function installTitleTooltips(doc = document) {
  if (installed.has(doc)) return installed.get(doc);
  const win = doc.defaultView, texts = new WeakMap(), treeTexts = new WeakMap();
  const listeners = new AbortController(), opts = { signal: listeners.signal };
  if (tooltipsAllowed(win)) ensureTooltipStyle(doc);
  tooltipMedia(win).addEventListener('change', () => {
    if (tooltipsAllowed(win)) ensureTooltipStyle(doc);
  }, opts);
  let current = null, hideTimer, suppressed = null, hoverFrame = 0, hoverTarget = null;
  const overflowAllowed = node => !node.hasAttribute('data-kbot-tooltip-overflow') ||
    !!node.textContent.trim() && node.scrollWidth > node.clientWidth;
  const sizeObserver = typeof win.ResizeObserver === 'function' ? new win.ResizeObserver(() => {
    if (current && (!treeTooltipAllowed(current.source) || !overflowAllowed(current.source))) hide(true);
  }) : null;
  const contains = target => target && current &&
    (current.source.contains(target) || current.notice.element?.contains(target));
  function hide(immediate = false) {
    clearTimeout(hideTimer);
    const previous = current; current = null;
    sizeObserver?.disconnect();
    previous?.notice.close('trigger-left', immediate);
  }
  function suppress(node) {
    if (!node) return;
    const element = node.closest('button, a, input, select, textarea, [role="button"]') || node;
    if (suppressed?.element === element) return;
    suppressed = { element, bounds: element.getBoundingClientRect() };
  }
  function collect(node) {
    if (node.nodeType !== 1 || !node.hasAttribute('title') || node.closest('.kbot-tooltip-host')) return;
    const text = node.getAttribute('title');
    node.removeAttribute('title');
    if (node.closest('[data-kbot-tooltip="off"]')) {
      texts.delete(node);
      if (current?.source === node) hide(true);
      return;
    }
    if (node.matches('iframe, frame')) {
      // Keep the frame's accessible name without a native hover tooltip.
      if (text && !node.hasAttribute('aria-label')) node.setAttribute('aria-label', text);
      return;
    }
    if (text && node.matches('button, a, input, select, textarea, [role="button"]') &&
      !node.hasAttribute('aria-label') && !node.hasAttribute('aria-labelledby') &&
      !node.textContent.trim() && !node.labels?.length) node.setAttribute('aria-label', text);
    if (text) texts.set(node, text); else texts.delete(node);
    if (current?.source === node) hide();
  }
  function scan(root) {
    collect(root);
    root.querySelectorAll?.('[title]').forEach(collect);
  }
  function sourceFor(target) {
    const element = target?.nodeType === 1 ? target : target?.parentElement;
    if (element?.closest('[data-kbot-tooltip="off"]')) return null;
    const tree = treeTooltipTarget(element);
    if (tree) {
      let text = '';
      const stop = tree.action || tree.scope;
      for (let node = element; node; node = node.parentElement) {
        collect(node);
        if (!text && texts.has(node)) text = texts.get(node);
        if (node === stop) break;
      }
      // Icon actions have their own help; they never inherit the row's title.
      if (tree.action) {
        if (!text) return null;
        treeTexts.set(tree.action, text);
        return tree.action;
      }
      if (!treeNodeClipped(tree.scope)) return null;
      const label = tree.scope.querySelector('.treeview-label');
      if (label) collect(label);
      text ||= texts.get(tree.scope) || (label && texts.get(label)) || treeTooltipText(tree.scope);
      if (!text) return null;
      treeTexts.set(tree.scope, text);
      return tree.scope;
    }
    for (let node = element; node; node = node.parentElement) {
      collect(node);
      if (node.hasAttribute('data-kbot-tooltip-overflow')) return overflowAllowed(node) ? node : null;
      if (texts.has(node)) return node;
    }
    return null;
  }
  function show(node) {
    clearTimeout(hideTimer);
    if (node && (!treeTooltipAllowed(node) || !overflowAllowed(node))) { hide(true); return; }
    if (current?.source === node) return;
    hide();
    if (!node || node.disabled || !tooltipsAllowed(win) ||
      (suppressed && (suppressed.element.contains(node) || node.matches(':hover')))) return;
    const descriptionId = `kbot-tooltip-description-${++nextId}`;
    const described = new Set((node.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    described.add(descriptionId); node.setAttribute('aria-describedby', [...described].join(' '));
    // Report buttons use the requested trial values, regardless of saved defaults.
    const reportButton = node.matches('button') && node.closest('.report-view, .rpt-bar');
    const text = node.hasAttribute('data-kbot-tooltip-overflow') ? node.textContent.trim() : treeTexts.get(node) || texts.get(node);
    const notice = showTooltip(text, { source: node, anchor: node, descriptionId,
      ...(reportButton ? { delay: 100, duration: 20000 } : {}) });
    const entry = { source: node, notice }; current = entry;
    if ((treeTooltipTarget(node) || node.hasAttribute('data-kbot-tooltip-overflow')) && sizeObserver) {
      for (let parent = node; parent; parent = parent.parentElement) sizeObserver.observe(parent);
    }
    notice.closed.then(() => {
      const remaining = (node.getAttribute('aria-describedby') || '').split(/\s+/)
        .filter(value => value && value !== descriptionId);
      if (remaining.length) node.setAttribute('aria-describedby', remaining.join(' '));
      else node.removeAttribute('aria-describedby');
      if (current === entry) current = null;
    });
  }
  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!contains(doc.activeElement)) hide();
    }, 150);
  }
  scan(doc);
  const observer = new win.MutationObserver(records => {
    for (const record of records) {
      if (current?.source.hasAttribute('data-kbot-tooltip-overflow') && record.type !== 'attributes' &&
        current.source.contains(record.target)) hide(true);
      if (record.type === 'attributes') collect(record.target);
      else if (record.type === 'childList') record.addedNodes.forEach(scan);
    }
    if (current && (!current.source.isConnected || current.source.disabled ||
      current.source.closest('[data-kbot-tooltip="off"]') || !treeTooltipAllowed(current.source) || !overflowAllowed(current.source))) hide(true);
  });
  observer.observe(doc.documentElement, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['title', 'disabled', 'data-kbot-tooltip', 'data-kbot-tooltip-overflow'] });
  doc.addEventListener('pointerover', event => {
    if (event.pointerType === 'touch' || !tooltipsAllowed(win)) return;
    if (current?.notice.element?.contains(event.target)) { clearTimeout(hideTimer); return; }
    const node = sourceFor(event.target);
    if (node) show(node); else if (current) scheduleHide();
  }, opts);
  doc.addEventListener('pointerout', event => {
    if (contains(event.target) && !contains(event.relatedTarget)) scheduleHide();
  }, opts);
  // Only actual mouse movement out of the clicked control releases suppression.
  // Focus changes, menu opening and DOM replacement under the pointer do not.
  doc.addEventListener('pointermove', event => {
    if (event.pointerType === 'touch') return;
    if (suppressed) {
      const { element, bounds } = suppressed;
      const inside = event.clientX >= bounds.left && event.clientX <= bounds.right &&
        event.clientY >= bounds.top && event.clientY <= bounds.bottom;
      if (inside || element.contains(event.target)) return;
      suppressed = null;
      if (tooltipsAllowed(win)) show(sourceFor(event.target));
    }
    // Re-evaluate a hovered row after resizing without per-node listeners or measuring every event.
    if (!event.buttons && tooltipsAllowed(win) && treeTooltipTarget(event.target)) {
      hoverTarget = event.target;
      if (!hoverFrame) hoverFrame = win.requestAnimationFrame(() => {
        hoverFrame = 0;
        if (hoverTarget?.isConnected && hoverTarget.matches(':hover')) show(sourceFor(hoverTarget));
      });
    }
  }, opts);
  doc.addEventListener('focusin', event => {
    if (current?.notice.element?.contains(event.target)) return;
    show(sourceFor(event.target));
  }, opts);
  doc.addEventListener('focusout', event => {
    if (contains(event.target) && !contains(event.relatedTarget)) scheduleHide();
  }, opts);
  doc.addEventListener('pointerdown', event => {
    suppress(sourceFor(event.target));
    hide(true);
  }, { capture: true, ...opts });
  doc.addEventListener('click', event => {
    suppress(sourceFor(event.target)); hide(true);
  }, { capture: true, ...opts });
  doc.addEventListener('keydown', event => {
    if (current && event.key === 'Escape') {
      suppress(current.source); hide(true); event.preventDefault(); event.stopPropagation();
    }
  }, { capture: true, ...opts });
  doc.addEventListener('visibilitychange', () => { if (doc.hidden) hide(); }, opts);
  const recheckTree = () => { if (current && (!treeTooltipAllowed(current.source) || !overflowAllowed(current.source))) hide(true); };
  win.addEventListener('resize', recheckTree, opts);
  doc.addEventListener('scroll', recheckTree, { capture: true, ...opts });
  const stop = () => {
    hide(); win.cancelAnimationFrame(hoverFrame); sizeObserver?.disconnect();
    observer.disconnect(); listeners.abort(); installed.delete(doc);
  };
  win.addEventListener('pagehide', event => { if (event.persisted) hide(); else stop(); }, opts);
  installed.set(doc, stop);
  return stop;
}

installTitleTooltips();
