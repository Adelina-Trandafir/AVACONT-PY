// Matches routes/portal/access.py; the server enforces these rules independently.
const documentRoles = new Set(['administrator', 'director', 'secretar']);
const documentTabs = new Set(['fundamentari', 'ordonantari']);
const normalizeRole = (role) => String(role || '').trim().toLowerCase();

export const isAccountant = (role) => normalizeRole(role) === 'contabil';
export const canOpenTab = (role, tab) => isAccountant(role)
  || (documentRoles.has(normalizeRole(role)) && documentTabs.has(tab));

export function applyPortalAccess(role, currentTab) {
  const tab = canOpenTab(role, currentTab) ? currentTab : 'fundamentari';
  document.querySelectorAll('.pa__tabs button[data-tab]').forEach((button) => {
    const allowed = canOpenTab(role, button.dataset.tab);
    button.hidden = !allowed;
    button.disabled = !allowed;
    button.classList.toggle('is-active', allowed && button.dataset.tab === tab);
    button.setAttribute('aria-selected', String(allowed && button.dataset.tab === tab));
  });
  return tab;
}
