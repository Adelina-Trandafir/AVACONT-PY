// NIR stays lazy in either mode: month > documents or supplier > documents.
import { documentLeaf, documentMonthId, documentMonthLabel, documentPartyId } from './document-tree-order.js';

const waiting = parent => ({ id: `wait:${parent}`, label: 'Se încarcă…', selectable: false });
function targetOf(id) {
  if (id.startsWith('party:')) return { party: decodeURIComponent(id.slice(6)) };
  if (id.startsWith('document-month:')) return { month: id.slice('document-month:'.length) };
  return null;
}

export function receiptTree(fetchLevel, byDate = () => true) {
  const roots = new Map(), docs = new Map(), pending = new Map();
  let generation = 0;
  const mode = () => byDate() ? 'months' : 'parties';
  const needs = id => !!targetOf(id) && !docs.has(id);

  async function load() {
    const level = mode(), version = generation;
    const answer = await fetchLevel({ level });
    if (version === generation) roots.set(level, answer.items);
    return answer;
  }

  function expand(id) {
    const target = targetOf(id);
    if (!target || !needs(id)) return Promise.resolve(false);
    if (!pending.has(id)) {
      const version = generation;
      const request = (async () => {
        const answer = await fetchLevel({ level: 'documents', ...target });
        if (version !== generation) return false;
        docs.set(id, answer.items);
        return true;
      })().finally(() => { if (pending.get(id) === request) pending.delete(id); });
      pending.set(id, request);
    }
    return pending.get(id);
  }

  function nodes() {
    return (roots.get(mode()) || []).map(item => {
      const id = byDate() ? documentMonthId(item.month) : documentPartyId(item.party);
      return { id, label: byDate() ? documentMonthLabel(item.month) : item.party || '—',
        children: docs.has(id) ? docs.get(id).map(documentLeaf) : [waiting(id)] };
    }).sort((a, b) => byDate() ? b.id.localeCompare(a.id) : a.label.localeCompare(b.label, 'ro', { sensitivity: 'base' }));
  }

  function find(id) {
    for (const list of docs.values()) {
      const found = list.find(doc => String(doc.id) === String(id));
      if (found) return found;
    }
    return null;
  }

  async function reveal(target) {
    if (!target) return [];
    const item = byDate() ? (roots.get('months') || []).find(item => item.month === target.month)
      : (roots.get('parties') || []).find(item => item.party.localeCompare(target.party, 'ro', { sensitivity: 'base' }) === 0);
    if (!item) return [];
    const id = byDate() ? documentMonthId(item.month) : documentPartyId(item.party);
    await expand(id);
    return [id];
  }

  async function refresh(target) {
    const loaded = [...docs.keys()], version = ++generation;
    pending.clear(); roots.clear();
    await load();
    await Promise.all(loaded.map(async id => {
      const answer = await fetchLevel({ level: 'documents', ...targetOf(id) });
      if (version === generation) docs.set(id, answer.items);
    }));
    return version === generation ? reveal(target) : [];
  }

  return { load, expand, needs, nodes, find, refresh, reveal };
}
