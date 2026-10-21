import { TOOLTIP_DEFAULTS, TOOLTIP_CHOICES, getTooltipSettings, saveTooltipSettings } from './tooltip-config.js';
import { ensureTooltipStyle, showTooltip } from './tooltip.js';

let opened;
export function openTooltipSettings() {
  if (opened?.isConnected) { opened.focus(); return; }
  ensureTooltipStyle();
  const origin = document.activeElement;
  const dialog = document.createElement('dialog');
  opened = dialog; dialog.className = 'kbot-tooltip-settings';
  dialog.setAttribute('aria-label', 'Setări tooltip');
  const form = document.createElement('form');
  const heading = document.createElement('h2'); heading.className = 'kbot-tooltip-settings__header';
  heading.textContent = 'ℹ️ Setări tooltip';
  const body = document.createElement('div'); body.className = 'kbot-tooltip-settings__body';
  const fields = new Map();
  function field(key, caption, type = 'text', min, max) {
    const label = document.createElement('label'); label.className = 'kbot-tooltip-settings__field';
    const name = document.createElement('span'); name.textContent = caption;
    const control = document.createElement(TOOLTIP_CHOICES[key] ? 'select' : 'input');
    if (TOOLTIP_CHOICES[key]) {
      for (const [value, text] of Object.entries(TOOLTIP_CHOICES[key])) {
        const option = document.createElement('option'); option.value = value; option.textContent = text; control.append(option);
      }
    } else {
      control.type = type;
      if (type === 'number') { control.min = String(min); control.max = String(max); control.step = '1'; control.required = true; }
      else if (type !== 'checkbox') control.maxLength = key === 'title' ? 80 : 16;
    }
    fields.set(key, control); label.append(name, control); body.append(label);
  }
  field('appearance', 'Aspect'); field('accent', 'Paletă informări'); field('position', 'Poziție');
  field('width', 'Lățime maximă (px)', 'number', 120, 640);
  field('delay', 'Întârziere înainte de afișare (ms)', 'number', 0, 10000);
  field('animation', 'Durată animație (ms)', 'number', 0, 2000);
  field('duration', 'Durată pe ecran (ms; 0 = manual)', 'number', 0, 120000);
  field('showHeader', 'Afișează antetul', 'checkbox');
  field('icon', 'Emoticon antet'); field('title', 'Antet (gol = după tipul mesajului)');
  const note = document.createElement('p'); note.className = 'kbot-tooltip-settings__note';
  note.textContent = 'Setări comune pentru ADECHIT, K-BOT și VERCON, în acest browser. 1000 ms = 1 secundă. Durata se suspendă cât timp cursorul sau focusul este pe tooltip și cât timp fila este ascunsă. Erorile și avertizările păstrează culori distincte. Tooltipurile sunt dezactivate în modul mobil și pe dispozitive cu interacțiune tactilă principală.';
  body.append(note);
  const feedback = document.createElement('p'); feedback.className = 'kbot-tooltip-settings__note';
  feedback.setAttribute('role', 'status'); body.append(feedback);
  const fill = value => {
    for (const [key, control] of fields) {
      if (control.type === 'checkbox') control.checked = Boolean(value[key]);
      else control.value = String(value[key]);
    }
  };
  const read = () => Object.fromEntries([...fields].map(([key, control]) => [key,
    control.type === 'checkbox' ? control.checked : control.type === 'number' ? Number(control.value) : control.value]));
  const footer = document.createElement('footer'); footer.className = 'kbot-tooltip-settings__footer';
  let preview;
  function button(text, run, type = 'button') {
    const item = document.createElement('button'); item.type = type; item.textContent = text;
    if (run) item.addEventListener('click', run);
    footer.append(item);
  }
  button('Implicit', () => { preview?.close(); fill(TOOLTIP_DEFAULTS); feedback.textContent = ''; });
  button('Previzualizare', () => {
    if (!form.reportValidity()) return;
    preview?.close();
    preview = showTooltip('Așa vor arăta notificările aplicațiilor. Puteți închide acest tooltip cu ×.', { ...read(), source: dialog });
  });
  button('Renunță', () => dialog.close()); button('Salvează', null, 'submit');
  form.addEventListener('submit', event => {
    event.preventDefault();
    const { persisted } = saveTooltipSettings(read());
    preview?.close();
    if (!persisted) {
      feedback.textContent = 'Setările sunt aplicate pentru această pagină. Browserul nu permite memorarea lor; nu vor fi păstrate după reîncărcare.';
      return;
    }
    dialog.close();
  });
  dialog.addEventListener('close', () => {
    preview?.close(); dialog.remove(); opened = null;
    if (origin?.isConnected) origin.focus({ preventScroll: true });
  }, { once: true });
  fill(getTooltipSettings()); form.append(heading, body, footer); dialog.append(form);
  document.body.append(dialog); dialog.showModal(); fields.get('appearance').focus();
}

export function tooltipSettingsMenuEntry() {
  return { icon: 'ℹ️', label: 'Setări tooltip', rule: true, run: openTooltipSettings };
}
