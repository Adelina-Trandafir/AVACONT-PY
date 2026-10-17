// OI drafts use the same four decimal storage precision as the server.
function coefficient(raw) {
  const match = /^(\d+)(?:\.(\d{0,4}))?$/.exec(String(raw ?? '').replace(',', '.'));
  return match ? BigInt(match[1]) * 10000n + BigInt((match[2] || '').padEnd(4, '0')) : null;
}

export function oiLineValue(quantity, price, decimals) {
  const q = coefficient(quantity), p = coefficient(price);
  if (q == null || p == null) return '';
  const divisor = 10n ** BigInt(8 - decimals);
  const value = (q * p + divisor / 2n) / divisor;
  if (!decimals) return String(value);
  const digits = value.toString().padStart(decimals + 1, '0');
  return `${digits.slice(0, -decimals)}.${digits.slice(-decimals)}`;
}
