// Exact decimal text -> Romanian display, half up at ZEC (without floating point).
export function decimalText(raw, zec) {
  if (raw == null) return '';
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(String(raw));
  if (!match) return String(raw);
  const [, sign, whole, decimals = ''] = match;
  const tail = decimals.padEnd(zec + 1, '0');
  let coefficient = BigInt(whole + tail.slice(0, zec));
  if (tail[zec] >= '5') coefficient += 1n;
  const digits = coefficient.toString().padStart(zec + 1, '0');
  const integer = (zec ? digits.slice(0, -zec) : digits).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return (coefficient === 0n ? '' : sign) + integer + (zec ? ',' + digits.slice(-zec) : '');
}
