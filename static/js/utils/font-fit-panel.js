/**
 * Debug-only panel to try, on the live page, how the text follows the width of the window (css/typography.css, FIT).
 * Loaded by debug-tools.js ONLY in test mode, and only when asked for: Ctrl+Alt+F or kbotDebug.fontFit(). The round «Aa» button is switched off
 * (mount({ button: true }) brings it back); until the panel is opened nothing here runs, so stored test values never touch a page.
 *
 * It only writes the four typography variables on <html>; nothing else on the page is touched. The values are kept in
 * localStorage so they survive a page change (to compare Intrare / NIR / Facturi), and «Resetare» puts typography.css back.
 * «Copiaza» puts the lines to paste into typography.css on the clipboard.
 */
const k_root = document.documentElement;
const k_store = 'kbot-font-fit-debug';
const k_defaults = { on: false, max: 16, min: 11, start: 1920, floor: 1400 };

let k_state = { ...k_defaults };
let k_panel = null, k_button = null, k_probe = null, k_readout = null, k_fields = {};

// true when values from an earlier page were found and taken over
function load() {
  try {
    const k_saved = JSON.parse(localStorage.getItem(k_store) || 'null');
    if (k_saved && typeof k_saved === 'object') { k_state = { ...k_state, ...k_saved }; return true; }
  } catch (_) { /* a broken stored value just means the values of typography.css */ }
  return false;
}

function save() {
  try { localStorage.setItem(k_store, JSON.stringify(k_state)); } catch (_) { /* the values still work for this visit */ }
}

function rate() {
  const k_span = Math.max(1, k_state.start - k_state.floor);
  return k_state.on ? Math.max(0, (k_state.max - k_state.min) / k_span) : 0;
}

function apply() {
  k_root.style.setProperty('--fs-max', `${k_state.max}px`);
  k_root.style.setProperty('--fs-fit-min', `${k_state.min}px`);
  k_root.style.setProperty('--fs-fit-start', `${k_state.start}px`);
  k_root.style.setProperty('--fs-fit-rate', String(rate()));
  window.dispatchEvent(new Event('resize'));   // grids and trees re-measure themselves on resize
  paint();
}

function clearOverrides() {
  for (const k_name of ['--fs-max', '--fs-fit-min', '--fs-fit-start', '--fs-fit-rate']) k_root.style.removeProperty(k_name);
}

function px(k_element, k_name) { return Math.round(parseFloat(getComputedStyle(k_element)[k_name]) * 10) / 10; }

function paint() {
  if (!k_panel) return;
  for (const [k_key, k_field] of Object.entries(k_fields)) {
    if (k_key === 'on') { k_field.input.checked = k_state.on; continue; }
    k_field.input.value = String(k_state[k_key]);
    k_field.output.value = `${k_state[k_key]}px`;
  }
  const k_width = Math.round(window.innerWidth / (Number(k_root.dataset.applicationZoom || 100) / 100));
  k_readout.textContent = `Fereastra ${k_width}px  ·  text acum ${px(k_probe, 'fontSize')}px  ·  cel mai mic pas ${px(k_probe, 'fontSize') * .75}px`;
  k_panel.classList.toggle('fsfit--off', !k_state.on);
}

function field(k_key, k_label, k_min, k_max, k_step) {
  const k_row = document.createElement('label');
  k_row.className = 'fsfit__row';
  const k_text = document.createElement('span');
  k_text.textContent = k_label;
  const k_output = document.createElement('output');
  const k_input = document.createElement('input');
  k_input.type = 'range'; k_input.min = String(k_min); k_input.max = String(k_max); k_input.step = String(k_step);
  k_input.addEventListener('input', () => { k_state[k_key] = Number(k_input.value); save(); apply(); });
  k_row.append(k_text, k_output, k_input);
  k_fields[k_key] = { input: k_input, output: k_output };
  return k_row;
}

function copyLines() {
  const k_lines = `--fs-max: ${k_state.max}px;\n--fs-fit-start: ${k_state.start}px;\n--fs-fit-min: ${k_state.min}px;\n--fs-fit-rate: ${k_state.on ? +rate().toFixed(5) : 0};`;
  navigator.clipboard?.writeText(k_lines).catch(() => {});
  return k_lines;
}

function build() {
  k_panel = document.createElement('section');
  k_panel.className = 'fsfit';
  k_panel.setAttribute('aria-label', 'Test font in functie de fereastra');
  k_panel.hidden = true;

  const k_head = document.createElement('header');
  k_head.className = 'fsfit__head';
  const k_title = document.createElement('b');
  k_title.textContent = 'Text după fereastră';
  const k_close = document.createElement('button');
  k_close.type = 'button'; k_close.textContent = '✕'; k_close.setAttribute('aria-label', 'Închide');
  k_close.addEventListener('click', () => toggle(false));
  k_head.append(k_title, k_close);

  const k_onRow = document.createElement('label');
  k_onRow.className = 'fsfit__row fsfit__row--check';
  const k_on = document.createElement('input');
  k_on.type = 'checkbox';
  k_on.addEventListener('change', () => { k_state.on = k_on.checked; save(); apply(); });
  const k_onText = document.createElement('span');
  k_onText.textContent = 'Textul urmează fereastra';
  k_onRow.append(k_on, k_onText);
  k_fields.on = { input: k_on };

  k_readout = document.createElement('p');
  k_readout.className = 'fsfit__readout';
  k_probe = document.createElement('span');
  k_probe.className = 'fsfit__probe';
  k_probe.setAttribute('aria-hidden', 'true');

  const k_actions = document.createElement('div');
  k_actions.className = 'fsfit__actions';
  const k_reset = document.createElement('button');
  k_reset.type = 'button'; k_reset.textContent = 'Resetare';
  k_reset.addEventListener('click', () => {
    try { localStorage.removeItem(k_store); } catch (_) { /* nothing stored to remove */ }
    clearOverrides(); readCss(); window.dispatchEvent(new Event('resize')); paint();
  });
  const k_copy = document.createElement('button');
  k_copy.type = 'button'; k_copy.textContent = 'Copiază valorile';
  k_copy.addEventListener('click', () => { copyLines(); k_copy.textContent = 'Copiat'; setTimeout(() => { k_copy.textContent = 'Copiază valorile'; }, 1200); });
  k_actions.append(k_reset, k_copy);

  k_panel.append(k_head, k_onRow,
    field('max', 'Maxim (fereastră lată)', 12, 22, 0.5),
    field('min', 'Minim', 8, 16, 0.5),
    field('start', 'Începe să scadă sub lățimea', 900, 2800, 20),
    field('floor', 'Ajunge la minim la lățimea', 400, 2400, 20),
    k_readout, k_actions, k_probe);
  document.body.append(k_panel);
}

// A reset takes the numbers again from typography.css (max / min / start; the rate is 0 there = off)
function readCss() {
  const k_style = getComputedStyle(k_root);
  const k_num = (k_name, k_fallback) => { const k_value = parseFloat(k_style.getPropertyValue(k_name)); return Number.isFinite(k_value) ? k_value : k_fallback; };
  const k_cssRate = k_num('--fs-fit-rate', 0);
  k_state.max = k_num('--fs-max', k_defaults.max);
  k_state.min = k_num('--fs-fit-min', k_defaults.min);
  k_state.start = k_num('--fs-fit-start', k_defaults.start);
  k_state.on = k_cssRate > 0;
  k_state.floor = k_cssRate > 0 ? Math.round(k_state.start - (k_state.max - k_state.min) / k_cssRate) : k_defaults.floor;
}

export function toggle(k_show) {
  const k_open = typeof k_show === 'boolean' ? k_show : k_panel.hidden;
  k_panel.hidden = !k_open;
  if (k_button) k_button.hidden = k_open;
  if (k_open) paint();
  return k_open ? 'panel open' : 'panel closed';
}

export function mount({ button = false } = {}) {
  if (k_panel) return;
  const k_link = document.createElement('link');
  k_link.rel = 'stylesheet';
  k_link.href = new URL('../../css/font-fit-debug.css', import.meta.url).href;
  document.head.append(k_link);

  readCss();
  const k_hadStored = load();
  build();
  if (button) {
    k_button = document.createElement('button');
    k_button.type = 'button'; k_button.className = 'fsfit__open'; k_button.textContent = 'Aa';
    k_button.title = 'Test: textul după mărimea ferestrei'; k_button.setAttribute('aria-label', 'Test: textul după mărimea ferestrei');
    k_button.addEventListener('click', () => toggle(true));
    document.body.append(k_button);
  }

  window.addEventListener('resize', paint);
  if (k_hadStored) apply(); else paint();
}
