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
  const doc = element.ownerDocument, win = doc.defaultView;
  const box = innerRect(element);
  const visible = { left: 0, top: 0, right: win.innerWidth, bottom: win.innerHeight };
  // A row can be wider than its visible panel even though the label itself has no ellipsis.
  for (let parent = element; parent; parent = parent.parentElement) {
    // The root's clientWidth is the window itself (not zoomed), so under application zoom its inner box comes out too small;
    // the window is already `visible` above, in the same screen coordinates as the text rectangles.
    if (parent === doc.documentElement) continue;
    const style = win.getComputedStyle(parent);
    const clipX = CLIPPING.test(style.overflowX), clipY = CLIPPING.test(style.overflowY);
    if (!clipX && !clipY) continue;
    const rect = innerRect(parent);
    if (clipX) { visible.left = Math.max(visible.left, rect.left); visible.right = Math.min(visible.right, rect.right); }
    if (clipY) { visible.top = Math.max(visible.top, rect.top); visible.bottom = Math.min(visible.bottom, rect.bottom); }
  }
  // Measure text, excluding template indentation and whitespace-only nodes.
  // Font rectangles can extend past their line box vertically even when the
  // label fits; scrollHeight and raw font height are not truncation signals.
  const walker = doc.createTreeWalker(element, win.NodeFilter.SHOW_TEXT);
  const range = doc.createRange();
  const tolerance = 1;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const start = node.data.search(/\S/);
    if (start < 0) continue;
    const end = node.data.trimEnd().length;
    range.setStart(node, start); range.setEnd(node, end);
    for (const text of range.getClientRects()) {
      if (!text.width || !text.height) continue;
      const top = Math.max(text.top, box.top), bottom = Math.min(text.bottom, box.bottom);
      if (text.left < visible.left - tolerance || text.right > visible.right + tolerance
        || top < visible.top - tolerance || bottom > visible.bottom + tolerance) return true;
    }
  }
  return false;
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
