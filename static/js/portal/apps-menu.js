// The K-BOT logo opens the same application menu as the «A» of ADECHIT (shared popup: js/adechit/menu.js).
// Signed out, the logo keeps its old job: a link to the presentation page.
import { openMenu } from '../adechit/menu.js';
import { tooltipSettingsMenuEntry } from '../components/tooltip/tooltip-settings.js';

const brand = document.querySelector('#portal-header .brand');
if (brand) {
  brand.setAttribute('aria-haspopup', 'menu'); brand.setAttribute('aria-expanded', 'false');
  brand.addEventListener('click', (k_event) => {
    const k_user = document.getElementById('user-info');
    if (!k_user || k_user.hidden) return;
    k_event.preventDefault();
    const k_email = document.getElementById('user-email')?.textContent || 'Contul curent';
    openMenu(brand, [
      { icon: '🏢', label: k_email, disabled: true },
      { icon: '🤖', label: 'K-BOT', rule: true, disabled: true },
      { icon: '📋', label: 'ADECHIT', run: () => { window.location.href = '/adechit'; } },
      { icon: '🏗️', label: 'VERCON', run: () => { window.location.href = '/vercon'; } },
      { icon: '📒', label: 'Avacont · în curând', disabled: true },
      tooltipSettingsMenuEntry(),
    ], { label: 'Aplicații', below: brand.closest('header') });
  });
}
