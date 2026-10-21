// One overflow policy for inline trees, dropdown trees and ComboTree's inherited rows.
const CLIPPING = /^(hidden|clip|auto|scroll)$/;

export function treeTooltipTarget(element) {
  const scope = element?.closest?.('.treeview-item, .treeview-breadcrumb-parent, .treeview-breadcrumb-current');
  if (!scope) return null;
  const action = element.closest('.treeview-righticon, button, a[href], [role="button"]');
  return { scope, action: action && scope.contains(action) ? action : null };
}

function textElements(scope) {
  if (scope.matches('.treeview-breadcrumb-parent')) return [scope];
  if (scope.matches('.treeview-breadcrumb-current')) return [...scope.querySelectorAll('.treeview-breadcrumb-text')];
  return [...scope.children].filter(node => node.matches('.treeview-label, .treeview-righttext'));
}

function innerRect(element) {
  const rect = element.getBoundingClientRect();
  const scaleX = element.offsetWidth ? rect.width / element.offsetWidth : 1;
  const scaleY = element.offsetHeight ? rect.height / element.offsetHeight : 1;
  const left = rect.left + element.clientLeft * scaleX;
  const top = rect.top + element.clientTop * scaleY;
  return { left, top, right: left + element.clientWidth * scaleX, bottom: top + element.clientHeight * scaleY };
}

function textClipped(element) {
  if (!element.textContent.trim() || !element.getClientRects().length) return false;
  if (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1) return true;
  const doc = element.ownerDocument, win = doc.defaultView;
  const visible = innerRect(element);
  // A row can be wider than its visible panel even though the label itself has no ellipsis.
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    const style = win.getComputedStyle(parent);
    const clipX = CLIPPING.test(style.overflowX), clipY = CLIPPING.test(style.overflowY);
    if (!clipX && !clipY) continue;
    const rect = innerRect(parent);
    if (clipX) { visible.left = Math.max(visible.left, rect.left); visible.right = Math.min(visible.right, rect.right); }
    if (clipY) { visible.top = Math.max(visible.top, rect.top); visible.bottom = Math.min(visible.bottom, rect.bottom); }
  }
  visible.left = Math.max(visible.left, 0);
  visible.right = Math.min(visible.right, win.innerWidth);
  visible.top = Math.max(visible.top, 0);
  visible.bottom = Math.min(visible.bottom, win.innerHeight);
  const range = doc.createRange();
  range.selectNodeContents(element);
  if (!range.getClientRects().length) return false;
  const text = range.getBoundingClientRect(), tolerance = 1;
  return text.left < visible.left - tolerance || text.right > visible.right + tolerance
    || text.top < visible.top - tolerance || text.bottom > visible.bottom + tolerance;
}

export function treeNodeClipped(scope) {
  if (!scope.isConnected || !scope.getClientRects().length) return false;
  return textElements(scope).some(textClipped);
}

export function treeTooltipAllowed(source) {
  const tree = treeTooltipTarget(source);
  return !tree || Boolean(tree.action) || treeNodeClipped(tree.scope);
}

export function treeTooltipText(scope) {
  return scope.dataset.treeTooltip || textElements(scope).map(element =>
    element.matches('.treeview-label') ? scope.dataset.text || element.textContent.trim() : element.textContent.trim()
  ).filter(Boolean).join(' · ');
}
