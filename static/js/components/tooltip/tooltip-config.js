const KEY = 'kbot-tooltip-settings-v1';
export const TOOLTIP_DEFAULTS = Object.freeze({
  appearance: 'soft', position: 'top-right', accent: 'blue', width: 320,
  delay: 0, duration: 6000, animation: 180, showHeader: false, title: '', icon: 'ℹ️',
  fontFamily: '', fontSizePt: 9, textColor: '',
});
export const TOOLTIP_CHOICES = {
  appearance: { soft: 'Pastel', solid: 'Colorat', minimal: 'Simplu' },
  position: { 'top-right': 'Dreapta sus', 'top-left': 'Stânga sus',
    'bottom-right': 'Dreapta jos', 'bottom-left': 'Stânga jos', center: 'Centru' },
  accent: { blue: 'Galben pal', teal: 'Verde', purple: 'Violet' },
  fontFamily: { '': 'Implicit aplicație', Calibri: 'Calibri', Carlito: 'Carlito', 'Segoe UI': 'Segoe UI',
    Arial: 'Arial', Tahoma: 'Tahoma', Verdana: 'Verdana', 'Times New Roman': 'Times New Roman',
    Georgia: 'Georgia', Consolas: 'Consolas' },
};
export const TOOLTIP_FONT_SIZE = Object.freeze({ min: 6, max: 48 });
let current;
export function normalizeTooltipSettings(value = {}) {
  const result = { ...TOOLTIP_DEFAULTS };
  for (const key of Object.keys(TOOLTIP_CHOICES)) {
    if (Object.hasOwn(TOOLTIP_CHOICES[key], value?.[key])) result[key] = value[key];
  }
  for (const [key, min, max] of [['width', 120, 640], ['delay', 0, 10000],
    ['duration', 0, 120000], ['animation', 0, 2000]]) {
    const number = Number(value?.[key]);
    if (value?.[key] != null && Number.isFinite(number)) result[key] = Math.round(Math.max(min, Math.min(max, number)));
  }
  if (typeof value?.title === 'string') result.title = value.title.trim().slice(0, 80);
  if (typeof value?.icon === 'string') result.icon = value.icon.trim().slice(0, 16);
  if (typeof value?.showHeader === 'boolean') result.showHeader = value.showHeader;
  const size = Number(value?.fontSizePt);
  if (value?.fontSizePt != null && value.fontSizePt !== '' && Number.isFinite(size)) {
    result.fontSizePt = Math.round(Math.max(TOOLTIP_FONT_SIZE.min, Math.min(TOOLTIP_FONT_SIZE.max, size)) * 10) / 10;
  }
  if (typeof value?.textColor === 'string') {
    const color = value.textColor.trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(color)) result.textColor = color;
    else if (/^#[0-9a-f]{3}$/.test(color)) result.textColor = '#' + [...color.slice(1)].map(char => char + char).join('');
  }
  return result;
}
export function getTooltipSettings() {
  if (!current) {
    try { current = normalizeTooltipSettings(JSON.parse(localStorage.getItem(KEY) || '{}')); }
    catch (_) { current = { ...TOOLTIP_DEFAULTS }; }
  }
  return { ...current };
}
export function saveTooltipSettings(value) {
  current = normalizeTooltipSettings(value);
  let persisted = true;
  try { localStorage.setItem(KEY, JSON.stringify(current)); } catch (_) { persisted = false; }
  return { settings: { ...current }, persisted };
}
window.addEventListener('storage', event => {
  if (event.key !== KEY && event.key !== null) return;
  try { current = normalizeTooltipSettings(JSON.parse(event.newValue || '{}')); }
  catch (_) { current = { ...TOOLTIP_DEFAULTS }; }
});
