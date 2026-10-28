// The server owns the deadline. Page load and the operator's activity (typing, clicks, scrolling, touch --
// never mouse movement) renew it; polling only reads it.
import { messageBox } from '../utils/confirm-box.js';
import { beginSessionExit } from '../utils/session-exit.js';

const RESYNC_MS = 60 * 1000;
// Scrolling (wheel, scroll, touch-drag) is deliberately not activity: only someone who scrolls and does nothing else is logged out.
const ACTIVITY_EVENTS = ['keydown', 'input', 'pointerdown', 'click'];
const ACTIVITY_SEND_MS = 20 * 1000;   // at most one renewal call per 20 s of continuous activity
const clock = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;

async function readSession(token, signal, renew = false) {
  const response = await fetch(`/api/portal/session/${renew ? 'renew' : 'check'}`, {
    method: renew ? 'POST' : 'GET', headers: { 'X-Portal-Token': token() }, cache: 'no-store', signal,
  });
  if (response.status === 401) return { expires_in: 0 };
  if (!response.ok) throw new Error(`Eroare HTTP ${response.status}`);
  return response.json();
}

// Injectable thresholds/checkSession are used only by the isolated local demo.
export function startSessionTimer({ token, onExpired, host, warningSeconds = 15 * 60, criticalSeconds = 5 * 60,
  showFromSeconds = warningSeconds, checkSession, renewSession }) {
  const check = checkSession || (signal => readSession(token, signal));
  // A demo supplying its own clock stays isolated from authenticated session routes.
  const renew = renewSession || checkSession || (signal => readSession(token, signal, true));
  let deadline = 0, stopped = false, left = false, syncing = false, warned = 0, notice = null, noticeLevel = 0;
  let total = 0, lastSent = 0, sending = false;
  const abort = new AbortController(), header = host?.closest('header');
  let tick, sync;
  const closeNotice = () => { notice?.close(); notice = null; };
  const stop = () => {
    if (stopped) return;
    stopped = true; abort.abort(); clearInterval(tick); clearInterval(sync);
    document.removeEventListener('visibilitychange', wake);
    ACTIVITY_EVENTS.forEach(name => document.removeEventListener(name, active, true));
    window.removeEventListener('pagehide', stop);
    window.removeEventListener('app:session-expired', stop);
    closeNotice(); header?.classList.remove('is-session-ending');
  };
  const leave = () => {
    if (left || stopped) return;
    left = true; stop(); beginSessionExit(); onExpired();
  };
  const noticeText = seconds => noticeLevel === 2
    ? `Deconectare automată în ${clock(seconds)}.\nSalvați acum și încheiați lucrul. La expirare toate ferestrele și formularele se închid fără confirmare. Modificările nesalvate se pierd.`
    : `Mai sunt ${Math.ceil(seconds / 60)} minute până la deconectare (${clock(seconds)}).\nApropiați-vă de încheierea lucrării și salvați modificările. Sesiunea se va închide automat la expirarea timpului.`;
  const show = seconds => {
    if (host) {
      host.hidden = seconds > showFromSeconds;
      const value = host.querySelector('b'); if (value) value.textContent = clock(seconds);
      host.classList.toggle('is-urgent', seconds <= criticalSeconds);
      host.title = 'Timp rămas până la deconectarea automată. Orice activitate (tastare, click, salvare, ștergere) îl reia de la capăt.';
    }
    header?.classList.toggle('is-session-ending', seconds <= criticalSeconds);
    const level = seconds <= criticalSeconds ? 2 : seconds <= warningSeconds ? 1 : 0;
    if (level < warned) { warned = level; closeNotice(); }
    if (level > warned) {
      warned = level; closeNotice(); noticeLevel = level;
      const box = messageBox(noticeText(seconds), {
        title: level === 2 ? 'Deconectare iminentă — salvați acum' : 'Sesiunea se apropie de încheiere', warning: level === 2,
      });
      notice = box;
      box.closed.then(() => { if (notice === box) notice = null; });
    }
    notice?.setText(noticeText(seconds));
  };
  const runTick = () => {
    if (!deadline || stopped) return;
    const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    // Another page sharing the token may have renewed it since the last poll.
    if (seconds === 0) { void resync(); return; }
    show(seconds);
  };
  const resync = async (initial = false) => {
    if (stopped || syncing) return;
    syncing = true;
    const started = Date.now();
    try {
      const data = await (initial ? renew : check)(abort.signal);
      if (stopped) return;
      if (data.authenticated === false || Number.isFinite(data.expires_in) && data.expires_in <= 0) { leave(); return; }
      if (Number.isFinite(data.expires_in)) {
        deadline = started + Math.max(0, data.expires_in) * 1000;
        if (initial || !total) total = data.expires_in;
      }
      runTick();
    } catch (error) {
      if (error.name !== 'AbortError') {
        console.error('[session] Timpul sesiunii nu a putut fi citit', error);
        if (deadline && deadline <= Date.now()) leave();
      }
    }
    finally { syncing = false; }
  };
  // Activity: the count starts over at once on the page; the server is told at most every ACTIVITY_SEND_MS.
  function active(event) {
    if (event.type === 'pointerdown' && event.pointerType === 'touch') return;   // a finger down may be the start of a scroll; the tap's own click counts
    if (stopped || !deadline || !total) return;
    const now = Date.now();
    if (deadline <= now) return;   // already over: only the server may say whether it really is
    deadline = now + total * 1000;
    runTick();
    if (sending || now - lastSent < ACTIVITY_SEND_MS) return;
    sending = true; lastSent = now;
    renew(abort.signal)
      .then(data => {
        if (stopped) return;
        if (data.authenticated === false || Number.isFinite(data.expires_in) && data.expires_in <= 0) leave();
      })
      .catch(error => { if (error.name !== 'AbortError') console.error('[session] Activitatea nu a putut fi trimisă', error); })
      .finally(() => { sending = false; });
  }
  function wake() { if (!document.hidden) { runTick(); if (!stopped) void resync(); } }
  tick = setInterval(runTick, 1000); sync = setInterval(resync, RESYNC_MS);
  document.addEventListener('visibilitychange', wake);
  ACTIVITY_EVENTS.forEach(name => document.addEventListener(name, active, { capture: true, passive: true }));
  window.addEventListener('pagehide', stop, { once: true });
  window.addEventListener('app:session-expired', stop, { once: true });
  void resync(true);
  return stop;
}
