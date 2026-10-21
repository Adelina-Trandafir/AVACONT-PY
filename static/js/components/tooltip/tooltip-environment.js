// Match the applications' mobile layout and exclude touch-only input.
export const TOOLTIP_MOBILE_QUERY = '(max-width: 900px), (hover: none), (pointer: coarse)';
export function tooltipMedia(win = window) { return win.matchMedia(TOOLTIP_MOBILE_QUERY); }
export function tooltipsAllowed(win = window) { return !tooltipMedia(win).matches; }
