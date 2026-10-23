// First association of a saved NIR with an e-invoice, saved WITHOUT opening the window when the proposals are certain:
// exactly one candidate invoice, nothing associated yet, no other NIR on the invoice and the proposals use up EVERY line of both sides
// (positive quantities, nothing left over). Anything else -> null, and the window opens as usual.
import { proposals, units } from './efactura-match.js';

const left = line => units(line.left) ?? 0n;
const used = (links, side, id) => links.filter(link => link[side] === id)
  .reduce((sum, link) => sum + (units(link[side === 'line' ? 'qty' : 'nir_qty']) ?? 0n), 0n);

export async function tryAutoAssociate({ api, sel, doc }) {
  const query = extra => new URLSearchParams({ ...sel(), ...extra });
  const read = (path, extra = {}) => api(`/api/vercon/doc/nir/efactura${path}?${query(extra)}`);
  try {
    const list = await read('', { include_completed: 1, visibility: 'active', nir: doc.id });
    const candidates = (list.items || []).filter(item => item.state !== 'PRELUATA' || item.manual_links);
    if (candidates.length !== 1) return null;
    const data = await read(`/${candidates[0].id}/association`, { nir: doc.id });
    if (data.allocations.length || data.other_receipts.length || !data.lines.length || !data.nir_lines.length) return null;
    const links = proposals(data.lines, data.nir_lines, []);
    if (!links.length
        || data.lines.some(line => left(line) <= 0n || used(links, 'line', line.id) !== left(line))
        || data.nir_lines.some(line => left(line) <= 0n || used(links, 'nir_line', line.id) !== left(line))) return null;
    return await api(`/api/vercon/doc/nir/efactura/${data.id}/association`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...sel(), nir: doc.id, version: data.version, request_key: crypto.randomUUID(),
        allocations: links.map(({ line, nir_line, qty, nir_qty }) => ({ line, nir_line, qty, nir_qty, learn: false })) }) });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    console.error('[vercon] automatic eFactura association failed, the window opens instead', error);
    return null;
  }
}
