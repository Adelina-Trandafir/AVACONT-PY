// VERCON v2: the saved-receipts tree (supplier > month > receipt), read one level at a time.
// Only the suppliers are read when the page opens; the months of a supplier and the receipts of a month are read when the
// operator opens that branch. Until then a branch holds one placeholder child, so that it can be opened.
import { format, formatMoney } from './fields.js';
import { docNode } from './ui.js';

const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const partyId = (party) => `party:${encodeURIComponent(party)}`;
const monthId = (party, month) => `month:${encodeURIComponent(party)}|${month}`;
const waiting = (parent) => ({ id: `wait:${parent}`, label: 'Se încarcă…' });
const monthLabel = (month) => `${MONTHS[Number(month.slice(5)) - 1]}/${month.slice(0, 4)}`;
// A receipt row: NIR date, invoice number (left, the label) and the value (right-aligned by the tree, no unit, 2 decimals).
// The NIR number is left out on purpose.
const leafLabel = (doc) => [format(doc.date, { type: 'date' }), doc.invoice, doc.annulled ? 'anulat' : ''].filter(Boolean).join(' • ');
const sameParty = (a, b) => a.toLocaleLowerCase('ro') === b.toLocaleLowerCase('ro');

function parse(id) {
  if (id.startsWith('party:')) return { party: decodeURIComponent(id.slice(6)) };
  if (id.startsWith('month:')) { const [party, month] = id.slice(6).split('|'); return { party: decodeURIComponent(party), month }; }
  return null;
}

// fetchLevel({ level, party?, month? }) -> the server answer { items, ... }
export function receiptTree(fetchLevel) {
  let parties = [], months = new Map(), docs = new Map();   // months: party -> [{ month, count }]; docs: month node id -> [summary]
  const pending = new Map();
  const needs = (id) => { const target = parse(id); return !!target && (target.month ? !docs.has(id) : !months.has(target.party)); };

  async function load() { const answer = await fetchLevel({ level: 'parties' }); parties = answer.items; return answer; }

  // Reads the branch `id` (a party or a month node) unless it is already here. Resolves true when something was read.
  function expand(id) {
    const target = parse(id); if (!target || !needs(id)) return Promise.resolve(false);
    if (!pending.has(id)) {
      pending.set(id, (async () => {
        if (target.month) docs.set(id, (await fetchLevel({ level: 'documents', party: target.party, month: target.month })).items);
        else months.set(target.party, (await fetchLevel({ level: 'months', party: target.party })).items);
        return true;
      })().finally(() => pending.delete(id)));
    }
    return pending.get(id);
  }

  function nodes() {
    return parties.map(({ party }) => {
      const id = partyId(party);
      return { id, label: party || '—', children: months.has(party) ? months.get(party).map(({ month }) => {
        const key = monthId(party, month);
        return { id: key, label: monthLabel(month), children: docs.has(key) ? docs.get(key).map((doc) => docNode(doc, leafLabel(doc), formatMoney(doc.total))) : [waiting(key)] };
      }) : [waiting(id)] };
    });
  }

  function find(id) {
    for (const list of docs.values()) { const hit = list.find((doc) => String(doc.id) === String(id)); if (hit) return hit; }
    return null;
  }

  // After a save or a cancel: reads again what the operator had opened. `target` ({ party, month: 'YYYY-MM' }) is the receipt
  // to show; its branches are read if needed. Resolves the node ids to keep open for it.
  async function refresh(target) {
    const fresh = (await fetchLevel({ level: 'parties' })).items, nextMonths = new Map(), nextDocs = new Map();
    await Promise.all([...months.keys()].filter((party) => fresh.some((item) => item.party === party)).map(async (party) => {
      nextMonths.set(party, (await fetchLevel({ level: 'months', party })).items);
    }));
    await Promise.all([...docs.keys()].map(async (id) => {
      const { party, month } = parse(id);
      if (nextMonths.has(party)) nextDocs.set(id, (await fetchLevel({ level: 'documents', party, month })).items);
    }));
    parties = fresh; months = nextMonths; docs = nextDocs;
    const shown = [], hit = target && (parties.find((item) => item.party === target.party) || parties.find((item) => sameParty(item.party, target.party)));
    if (hit && /^\d{4}-\d{2}$/.test(target.month)) {
      const party = partyId(hit.party), month = monthId(hit.party, target.month);
      await expand(party); await expand(month); shown.push(party, month);
    }
    return shown;
  }

  return { load, expand, needs, nodes, find, refresh };
}
