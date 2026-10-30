// SLICE-WB01-01: read the current unit role when opening an application menu.
import { canEnterKbot } from './access.js';
import { isPhoneOrTablet } from './certificat.js';

export async function applicationAccountEntries(navigate) {
  const empty = { applications: [], certificates: [] };
  try {
    const token = window.sessionStorage.getItem('kbot-portal-token') || '';
    if (!token) return empty;
    const response = await fetch('/api/portal/me', {
      headers: { Accept: 'application/json', 'X-Portal-Token': token }, cache: 'no-store',
    });
    if (!response.ok) return empty;
    const me = await response.json();
    if (!me.db_name) return empty;
    const administrator = String(me.role || '').trim().toLocaleLowerCase('ro-RO') === 'administrator';
    const back = ['/adechit', '/vercon', '/portal'].includes(window.location.pathname) ? window.location.pathname : '/portal';
    return {
      applications: canEnterKbot(me.role)
        ? [{ icon: '🤖', label: 'K-BOT', rule: true, run: () => navigate('/portal') }] : [],
      certificates: administrator && !isPhoneOrTablet()
        ? [{ icon: '🔐', label: 'Înrolează Certificat Digital', rule: true,
          run: () => navigate('/certificat-administrator?next=' + encodeURIComponent(back)) }] : [],
    };
  } catch (error) {
    console.error('[applications] Cannot read account access', error);
    return empty;
  }
}
