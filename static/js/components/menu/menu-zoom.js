// One zoom control per application menu. The document owns the scale, so page
// replacements, dialogs and controls appended to body inherit the same zoom.
import { closeMenu } from './menu.js';
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

const STORAGE_KEY = 'kbot-application-zoom';
const MIN = 75, MAX = 150;
const SNAPS = [90, 100, 110, 125];

function normalize(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= MIN && number <= MAX ? Math.round(number) : 100;
}

function snap(value) {
  return SNAPS.find(point => Math.abs(point - value) <= 2) ?? value;
}

export function bindMenuZoom(helpButton) {
  const root = document.documentElement;
  const listeners = new AbortController();
  const options = { signal: listeners.signal };
  let level = 100, popup = null;
  try { level = normalize(localStorage.getItem(STORAGE_KEY)); } catch (_) { /* Keep zoom usable without storage. */ }

  const button = document.createElement('button');
  button.type = 'button';
  button.className = `${helpButton.className} application-zoom-button`;
  button.classList.remove('ade-help-btn');
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  helpButton.before(button);

  function paintButton() {
    button.textContent = `🔍 ${level}%`;
    button.title = `Zoom aplicație: ${level}%`;
    button.setAttribute('aria-label', `Zoom aplicație: ${level}%`);
  }

  function apply(value, remember = true) {
    level = normalize(value);
    // Dynamic control values are written only by this control's API.
    root.dataset.applicationZoom = String(level);
    root.style.setProperty('--application-scale', String(level / 100));
    paintButton();
    if (remember) {
      try { localStorage.setItem(STORAGE_KEY, String(level)); } catch (_) { /* Current visit still works. */ }
    }
    // Existing controls recalculate their viewport-dependent layout on resize.
    window.dispatchEvent(new Event('resize'));
    place();
  }

  function close(focus = false) {
    popup?.remove();
    popup = null;
    button.setAttribute('aria-expanded', 'false');
    if (focus) button.focus({ preventScroll: true });
  }

  function place() {
    if (!popup) return;
    const rect = layoutRect(button), viewport = layoutViewport();
    popup.style.left = `${Math.max(4, Math.min(rect.right - popup.offsetWidth, viewport.width - popup.offsetWidth - 4))}px`;
    popup.style.top = `${Math.max(4, Math.min(rect.bottom + 4, viewport.height - popup.offsetHeight - 4))}px`;
  }

  function open() {
    if (popup) { close(); return; }
    closeMenu();
    popup = document.createElement('div');
    popup.className = 'application-zoom-popup';
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-label', 'Zoom aplicație');
    popup.style.zIndex = String(window.ZIndexManager?.getNext() || 1001);
    const heading = document.createElement('div');
    heading.className = 'application-zoom-popup__heading';
    const label = document.createElement('span');
    label.textContent = 'Zoom aplicație';
    const output = document.createElement('output');
    heading.append(label, output);
    const slider = document.createElement('input');
    let dragging = false;
    slider.type = 'range';
    slider.min = String(MIN); slider.max = String(MAX); slider.step = '1';
    slider.setAttribute('aria-label', 'Zoom aplicație');
    const marks = document.createElement('datalist');
    marks.id = 'application-zoom-stops';
    slider.setAttribute('list', marks.id);
    for (const value of SNAPS) {
      const mark = document.createElement('option');
      mark.value = String(value); mark.label = `${value}%`;
      marks.append(mark);
    }
    const paintValue = value => {
      slider.value = String(value);
      slider.setAttribute('aria-valuetext', `${value}%`);
      output.value = `${value}%`;
    };
    paintValue(level);
    slider.addEventListener('pointerdown', () => { dragging = true; });
    slider.addEventListener('pointerup', () => { dragging = false; });
    slider.addEventListener('pointercancel', () => { dragging = false; paintValue(level); });
    slider.addEventListener('input', () => paintValue(dragging ? snap(Number(slider.value)) : Number(slider.value)));
    // Reflow once at the end of a mouse/touch/keyboard gesture, as in K-BOT.
    slider.addEventListener('change', () => apply(Number(slider.value)));
    const presets = document.createElement('div');
    presets.className = 'application-zoom-popup__presets';
    for (const value of SNAPS) {
      const preset = document.createElement('button');
      preset.type = 'button'; preset.textContent = `${value}%`;
      preset.addEventListener('click', () => { paintValue(value); apply(value); });
      presets.append(preset);
    }
    popup.append(heading, slider, marks, presets);
    document.body.append(popup);
    button.setAttribute('aria-expanded', 'true');
    place();
    slider.focus({ preventScroll: true });
  }

  button.addEventListener('click', open, options);
  document.addEventListener('pointerdown', event => {
    if (popup && !popup.contains(event.target) && !button.contains(event.target)) close();
  }, { capture: true, ...options });
  document.addEventListener('focusin', event => {
    if (popup && !popup.contains(event.target) && event.target !== button) close();
  }, options);
  document.addEventListener('keydown', event => {
    if (popup && event.key === 'Escape') { event.preventDefault(); close(true); }
  }, options);
  window.addEventListener('resize', place, options);
  window.addEventListener('storage', event => {
    if (event.key === STORAGE_KEY || event.key === null) { close(); apply(normalize(event.newValue), false); }
  }, options);
  window.addEventListener('pagehide', event => {
    close();
    if (!event.persisted) listeners.abort();
  }, options);
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    try { apply(normalize(localStorage.getItem(STORAGE_KEY)), false); } catch (_) { apply(level, false); }
  }, options);
  apply(level, false);
}
