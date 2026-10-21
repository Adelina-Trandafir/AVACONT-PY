import { openMenu } from '../components/menu/menu.js';
import { tooltipSettingsMenuEntry } from '../components/tooltip/tooltip-settings.js';

let applicationMenu;

function openApplications(anchor) {
  if (!applicationMenu || applicationMenu.signal?.aborted) return;
  const { context, navigate } = applicationMenu;
  openMenu(anchor, [
    { icon: '🏢', label: context() || 'Contul curent', disabled: true },
    { icon: '🤖', label: 'K-BOT', rule: true, run: () => navigate('/portal') },
    { icon: '📋', label: 'ADECHIT', run: () => navigate('/adechit') },
    { icon: '📒', label: 'Avacont · în curând', disabled: true },
    tooltipSettingsMenuEntry(),
  ], { label: 'Aplicații', below: anchor.closest('header') });
}

export function bindApplicationMenu(anchor, { context, navigate, signal }) {
  applicationMenu = { context, navigate, signal };
  anchor.addEventListener('click', () => openApplications(anchor), { signal });
}

export function reportApplicationButton() {
  const source = document.getElementById('vc-apps');
  if (!source) return null;
  const button = source.cloneNode(true);
  button.removeAttribute('id');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', () => openApplications(button), { signal: applicationMenu?.signal });
  return button;
}
