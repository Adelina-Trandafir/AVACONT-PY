// Canonical decimal amounts, rounded half up at the receipt's precision.
function units(raw, precision) {
  if (raw == null || raw === '') return null;
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(String(raw));
  if (!match) return null;
  const fraction = (match[3] || '').padEnd(precision + 1, '0');
  let value = BigInt(match[2]) * 10n ** BigInt(precision) + BigInt(fraction.slice(0, precision));
  if (fraction[precision] >= '5') value += 1n;
  return match[1] ? -value : value;
}

const sumUnits = (lines, name, precision) => lines.reduce((sum, row) => sum + (units(row[name], precision) ?? 0n), 0n);

export function receiptTotal(lines, name, decimals) {
  const precision = Math.max(2, decimals), total = sumUnits(lines, name, precision);
  const digits = (total < 0n ? -total : total).toString().padStart(precision + 1, '0');
  return `${total < 0n ? '-' : ''}${digits.slice(0, -precision)}.${digits.slice(-precision)}`;
}

// No binary floating point tolerance at zero or at the one-leu boundary.
export function receiptDifference(declared, lines, decimals) {
  const precision = Math.max(2, decimals);
  const scale = 10n ** BigInt(precision);
  const target = units(declared, precision);
  if (target === null) return { different: false, canCorrect: false };
  const total = sumUnits(lines, 'ValoareCUTVA', precision);
  const delta = target >= total ? target - total : total - target;
  return { different: delta !== 0n, canCorrect: delta > 0n && delta <= scale };
}
