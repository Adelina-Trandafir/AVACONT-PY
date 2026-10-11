// SLICE-AD10 (UI): the portal session lasts ONE hour from the mailed code, whatever the operator is doing
// (unsaved work included). The server holds the real limit; this only shows the last minutes and leaves on time.
const LAST_MINUTES = 5 * 60;   // the countdown is shown for the last five minutes
const RESYNC_MS = 60 * 1000;   // the server is asked again every minute (also when the tab wakes up)

export function startSessionTimer({ token, onExpired, host }) {
  let k_deadline = 0;
  let k_left = false;
  const k_show = (k_seconds) => {
    host.hidden = k_seconds > LAST_MINUTES;
    if (host.hidden) return;
    const k_text = `${String(Math.floor(k_seconds / 60)).padStart(2, '0')}:${String(k_seconds % 60).padStart(2, '0')}`;
    host.querySelector('b').textContent = k_text;
    host.classList.toggle('is-urgent', k_seconds <= 60);
    host.title = 'Sesiunea durează o oră de la introducerea codului și se închide automat.';
  };
  const k_leave = () => { if (k_left) return; k_left = true; onExpired(); };
  const k_sync = async () => {
    try {
      const k_response = await fetch('/api/portal/session/check', { headers: { 'X-Portal-Token': token() }, cache: 'no-store' });
      if (k_response.status === 401) { k_leave(); return; }
      const k_data = await k_response.json().catch(() => ({}));
      if (Number.isFinite(k_data.expires_in)) k_deadline = Date.now() + k_data.expires_in * 1000;
    } catch (k_error) { console.error('[ade] the session time could not be read', k_error); }
  };
  const k_tick = () => {
    if (!k_deadline) return;
    const k_seconds = Math.max(0, Math.ceil((k_deadline - Date.now()) / 1000));
    k_show(k_seconds);
    if (k_seconds <= 0) k_leave();
  };
  k_sync().then(k_tick);
  setInterval(k_tick, 1000);
  setInterval(k_sync, RESYNC_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) k_sync().then(k_tick); });
}
