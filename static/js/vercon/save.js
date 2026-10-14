// slice VC02-04: the save protocol. One logical save = one RequestKey. The outcome is one of
//   saved     the server applied it (also when the answer was a replay of an earlier try)
//   error     the server refused it for a reason the operator can fix; nothing was applied, so the next try starts with a new key
//   conflict  somebody else changed the document (carries the current revision); the edited data stays in the draft
//   unknown   the answer was lost (timeout / network / 5xx); the server may or may not have applied it
// While the outcome is unknown the key is NEVER replaced: the next action is "reconcile" (ask the server about
// that key), so a retry cannot create the document twice. A second call during a save returns the same promise
// (double Enter does not save twice). A response is used only if the context version is still the one it was
// started in.
import { portalToken } from './api.js';

const newKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;

// endpoint: where a save is POSTed (reconcile asks GET endpoint/<key>). Production routes arrive with VC06.
export function createSaver({ endpoint, getUnit, getVersion, getLock = () => null, signal }) {
  let k_inFlight = null;
  let k_pending = null;    // { key, version } of a save whose outcome is unknown

  const k_headers = (k_extra) => ({ 'Content-Type': 'application/json', Accept: 'application/json', 'X-Portal-Token': portalToken(), 'X-Vercon-Unit': getUnit(), ...(getLock() ? { 'X-Vercon-Lock': getLock() } : {}), ...(k_extra || {}) });

  async function run(k_payload, k_extraHeaders) {
    const k_key = k_pending?.key || newKey();       // an unknown earlier outcome keeps its key
    const k_version = k_pending?.version || getVersion();
    let k_response;
    try {
      k_response = await fetch(endpoint, {
        method: 'POST', signal, headers: k_headers(k_extraHeaders),
        body: JSON.stringify({ ...k_payload, request_key: k_key, version: k_version }),
      });
    } catch (k_error) {
      if (k_error.name === 'AbortError') throw k_error;
      k_pending = { key: k_key, version: k_version };
      return { outcome: 'unknown', requestKey: k_key, message: 'Nu s-a primit raspunsul serverului. Verificati inainte de a salva din nou.' };
    }
    if (k_version !== getVersion()) return { outcome: 'stale', requestKey: k_key };   // the context changed meanwhile
    const k_body = await k_response.json().catch(() => ({}));
    if (k_response.ok) { k_pending = null; return { outcome: 'saved', requestKey: k_key, revision: k_body.revision, replayed: Boolean(k_body.replayed) }; }
    if (k_response.status === 409 && k_body.reason === 'CONFLICT') { k_pending = null; return { outcome: 'conflict', requestKey: k_key, revision: k_body.revision, message: k_body.error }; }
    if (k_response.status === 401) { return { outcome: 'expired', requestKey: k_key, message: 'Sesiunea a expirat. Datele editate raman in pagina.' }; }
    if (k_response.status >= 500 || k_response.status === 504) {
      k_pending = { key: k_key, version: k_version };
      return { outcome: 'unknown', requestKey: k_key, message: k_body.error || 'Raspuns nesigur de la server.' };
    }
    k_pending = null;
    return { outcome: 'error', requestKey: k_key, reason: k_body.reason, message: k_body.error || `Eroare HTTP ${k_response.status}` };
  }

  return {
    get hasUnknown() { return k_pending !== null; },
    save(k_payload, k_extraHeaders) {
      if (k_inFlight) return k_inFlight;
      k_inFlight = run(k_payload, k_extraHeaders).finally(() => { k_inFlight = null; });
      return k_inFlight;
    },
    // Ask the server whether the key of the unknown save was applied. Clears the unknown state only on a clear answer.
    async reconcile() {
      if (!k_pending) return { outcome: 'none' };
      const k_response = await fetch(`${endpoint}/${encodeURIComponent(k_pending.key)}`, { signal, headers: k_headers() });
      const k_body = await k_response.json().catch(() => ({}));
      if (!k_response.ok) return { outcome: 'unknown', message: k_body.error || 'Nu s-a putut verifica.' };
      const k_key = k_pending.key;
      k_pending = null;
      return k_body.known ? { outcome: 'saved', requestKey: k_key, revision: k_body.result?.revision } : { outcome: 'not-applied', requestKey: k_key };
    },
  };
}
