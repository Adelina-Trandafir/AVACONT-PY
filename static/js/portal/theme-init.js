// The saved look goes on before the first paint, so a dark page never flashes white.
// A plain (non-module, non-deferred) script in <head>, because the portal's CSP is script-src 'self'
// and forbids the inline version this replaces. The choice is written by portal.js (btn-theme).
try {
  if (localStorage.getItem('kbot-portal-theme') === 'dark') document.documentElement.dataset.theme = 'dark';
} catch (err) {
  // storage blocked: the page just starts in the light look
}
