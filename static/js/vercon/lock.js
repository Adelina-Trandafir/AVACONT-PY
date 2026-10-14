// slice VC04-04: the page side of the exclusive access. One token per browser tab. It is taken when a form that works
// on data is opened, renewed by ONE interval (a third of the server's ttl), released when the form closes or the page
// is left, and every request carries it (X-Vercon-Lock, added in api.js). If the server says the token is gone
// (expired or taken over) the page is told once and the edited data stays on screen.
import { portalToken } from './api.js';

export function createLock({ api, signal, getUnit, onLost }) {
  let k_token = null;
  let k_timer = null;
  let k_scope = null;   // { subunit, gestiune } the token belongs to

  const k_stop = () => { if (k_timer) { clearInterval(k_timer); k_timer = null; } };

  async function k_beat() {
    try { await api('/api/vercon/lock/heartbeat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(k_scope) }); }
    catch (k_error) {
      if (k_error.name === 'AbortError') return;
      if (k_error.reason === 'LOCK_LOST') { k_stop(); k_token = null; onLost(k_error.message); }
      else console.error('[vercon] heartbeat failed', k_error);   // a network blip: the next beat decides
    }
  }

  return {
    get token() { return k_token; },
    get held() { return k_token !== null; },
    holds: (k_selection) => k_token !== null && k_scope.subunit === k_selection.subunit && k_scope.gestiune === k_selection.gestiune,
    // takeover: true only after the operator confirmed taking the gestiune over from another tab of theirs
    async acquire(k_selection, k_takeover = false) {
      await this.release();
      const k_body = { subunit: k_selection.subunit, gestiune: k_selection.gestiune, takeover: k_takeover };
      const k_result = await api('/api/vercon/lock/acquire', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(k_body) });
      k_token = k_result.token; k_scope = { subunit: k_selection.subunit, gestiune: k_selection.gestiune };
      k_timer = setInterval(k_beat, Math.max(5000, (k_result.ttl * 1000) / 3));
    },
    async release(k_keepalive = false) {
      if (!k_token) return;
      const k_old = k_token; const k_oldScope = k_scope;
      k_stop(); k_token = null; k_scope = null;
      try {
        await fetch('/api/vercon/lock/release', { method: 'POST', keepalive: k_keepalive, signal: k_keepalive ? undefined : signal,
          headers: { 'Content-Type': 'application/json', 'X-Portal-Token': portalToken(), 'X-Vercon-Unit': getUnit(), 'X-Vercon-Lock': k_old },
          body: JSON.stringify(k_oldScope) });
      } catch (k_error) { if (k_error.name !== 'AbortError') console.error('[vercon] release failed', k_error); }
    },
  };
}
