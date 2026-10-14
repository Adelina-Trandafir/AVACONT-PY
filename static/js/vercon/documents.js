// slice VC01-05: documents of VERCON (print page, PDF, xlsx) go through the same window/download helper as ADECHIT
// (js/adechit/reports.js), so the browser behaves the same. VERCON only adds the context: every report path is
// built with the chosen DC / subunit / gestiune / year, and the page's own preferences use the `vercon.` prefix
// so the ADECHIT preferences are never read or changed.
import { outputReport } from '../adechit/reports.js';
import { portalToken } from './api.js';

const PREF_PREFIX = 'vercon.';

export function readPreference(k_key, k_fallback = null) {
  try { return window.localStorage.getItem(PREF_PREFIX + k_key) ?? k_fallback; } catch (k_error) { console.error('[vercon] localStorage is blocked', k_error); return k_fallback; }
}

export function writePreference(k_key, k_value) {
  try { window.localStorage.setItem(PREF_PREFIX + k_key, String(k_value)); } catch (k_error) { console.error('[vercon] localStorage is blocked', k_error); }
}

// context() -> { unit, subunit, gestiune, year }; read on every call, never copied.
export function createDocuments({ context, signal }) {
  async function fetchOutput(k_path, k_accept) {
    const k_ctx = context();
    const k_response = await fetch(k_path, { signal, headers: {
      Accept: k_accept, 'X-Portal-Token': portalToken(), 'X-Vercon-Unit': k_ctx.unit,
      'X-Vercon-Subunit': String(k_ctx.subunit ?? ''), 'X-Vercon-Gestiune': String(k_ctx.gestiune ?? ''),
    } });
    if (!k_response.ok) {
      const k_body = await k_response.json().catch(() => ({}));
      throw new Error(k_body.error || `Eroare HTTP ${k_response.status}`);
    }
    return k_response;
  }
  return {
    // kind: 'print' | 'pdf'. xlsx is a plain download of the same data (no templates, decision 11.10.2026).
    open: (k_report, k_kind, k_query, k_title) => outputReport({
      fetchOutput, path: `/api/vercon/reports/${k_report}/${k_kind}?${new URLSearchParams(k_query)}`, pdf: k_kind === 'pdf', title: k_title }),
    download: async (k_report, k_query, k_name) => {
      const k_response = await fetchOutput(`/api/vercon/reports/${k_report}/xlsx?${new URLSearchParams(k_query)}`, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      const k_url = URL.createObjectURL(await k_response.blob());
      const k_link = document.createElement('a'); k_link.href = k_url; k_link.download = k_name;
      document.body.append(k_link); k_link.click(); k_link.remove();
      setTimeout(() => URL.revokeObjectURL(k_url), 60000);
    },
  };
}
