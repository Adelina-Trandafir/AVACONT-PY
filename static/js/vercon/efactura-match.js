// Proposals only: exact remembered product or exact CPV, with quantity as a tie breaker. The unit of measure is NOT a criterion.
import { compareTyped } from '../utils/typed-order.js';

export function units(value) {
  const match = /^(\d+)(?:[.,](\d{1,4}))?$/.exec(String(value ?? '').trim());
  return match ? BigInt(match[1]) * 10000n + BigInt((match[2] || '').padEnd(4, '0')) : null;
}
export function quantity(value) {
  const sign = value < 0n ? '-' : ''; value = value < 0n ? -value : value;
  return `${sign}${value / 10000n}.${String(value % 10000n).padStart(4, '0')}`.replace(/\.?0+$/, '') || '0';
}

export function proposals(invoice, nir, links) {
  const free = (line, side) => (units(line.left) ?? 0n) - links.filter(l => l[side] === line.id)
    .reduce((sum, l) => sum + (units(l[side === 'line' ? 'qty' : 'nir_qty']) ?? 0n), 0n);
  const ef = invoice.filter(l => free(l, 'line') > 0n), ns = nir.filter(l => free(l, 'nir_line') > 0n);
  const answer = [];
  const usedE = new Set(), usedN = new Set();
  const eligible = (e, n, stage) => !links.some(l => l.line === e.id && l.nir_line === n.id)
    && (stage === 'memory' ? e.learned.length === 1 && e.learned.includes(n.product) && (!e.cpv || !n.cpv || e.cpv === n.cpv)
      : !!e.cpv && e.cpv === n.cpv);
  for (const stage of ['memory', 'cpv']) {
    const choices = new Map();
    for (const e of ef.filter(l => !usedE.has(l.id))) {
      let candidates = ns.filter(n => !usedN.has(n.id) && eligible(e, n, stage));
      const competing = ef.filter(other => !usedE.has(other.id) && other.id !== e.id
        && candidates.some(n => eligible(other, n, stage)));
      if (candidates.length > 1 || competing.length) candidates = candidates.filter(n => free(n, 'nir_line') === free(e, 'line'));
      if (candidates.length === 1) choices.set(e.id, candidates[0]);
    }
    for (const e of ef) {
      const n = choices.get(e.id);
      if (!n || [...choices.values()].filter(c => c.id === n.id).length !== 1) continue;
      const eq = free(e, 'line'), nq = free(n, 'nir_line');
      const qty = quantity(eq < nq ? eq : nq);
      answer.push({ line: e.id, nir_line: n.id, qty, nir_qty: qty,
        reason: stage === 'memory' ? 'Denumire memorată' : compareTyped(eq, nq, 'number') === 0 ? 'CPV + cantitate' : 'CPV' });
      usedE.add(e.id); usedN.add(n.id);
    }
  }
  return answer;
}
