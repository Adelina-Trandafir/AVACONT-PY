// slice VC01-02/03: the one fetch wrapper of the VERCON page. Adds the portal token, the unit and a request id
// (echoed by the server in logs and shown in the error box) and aborts on cleanup.
import { beginSessionExit } from '../utils/session-exit.js';
const PORTAL_TOKEN_KEY = 'kbot-portal-token';

export function portalToken() {
  try { return window.sessionStorage.getItem(PORTAL_TOKEN_KEY) || ''; } catch (k_error) { console.error('[vercon] sessionStorage is blocked', k_error); return ''; }
}

export function toPortal(k_dropToken) {
  if (k_dropToken) {
    beginSessionExit();
    try { window.sessionStorage.removeItem(PORTAL_TOKEN_KEY); } catch (k_error) { console.error('[vercon] sessionStorage is blocked', k_error); }
  }
  window.location.replace('/portal?next=' + encodeURIComponent('/vercon'));
}

function requestId() {
  return globalThis.crypto?.randomUUID?.().replaceAll('-', '') || `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

export function createApi({ getUnit, signal, extraHeaders = () => ({}) }) {
  return async function api(k_path, k_options = {}) {
    const k_id = requestId();
    const k_headers = { Accept: 'application/json', 'X-Portal-Token': portalToken(), 'X-Request-Id': k_id, ...(getUnit() ? { 'X-Vercon-Unit': getUnit() } : {}), ...extraHeaders(), ...(k_options.headers || {}) };
    const k_signal = signal && k_options.signal ? AbortSignal.any([signal, k_options.signal]) : k_options.signal || signal;
    const k_response = await fetch(k_path, { ...k_options, headers: k_headers, signal: k_signal });
    const k_binary = k_options.responseType === 'arrayBuffer';
    const k_result = k_response.ok && k_binary ? null : await k_response.json().catch(() => ({}));
    if (k_response.status === 401 || k_result?.reason === 'UNITATE_NEDESCHISA') {
      toPortal(k_response.status === 401);
      // Navigation has begun; callers must stop without painting another operation error.
      throw Object.assign(new DOMException(k_result?.error || 'Autentificați-vă.', 'AbortError'), {
        status: k_response.status, reason: k_result?.reason, requestId: k_response.headers.get('X-Request-Id') || k_id,
      });
    }
    if (!k_response.ok) throw Object.assign(new Error(k_result.error || `Eroare HTTP ${k_response.status}`), { reason: k_result.reason, status: k_response.status, fields: k_result.fields || null, requestId: k_response.headers.get('X-Request-Id') || k_id });
    return k_binary ? k_response.arrayBuffer() : k_result;
  };
}
