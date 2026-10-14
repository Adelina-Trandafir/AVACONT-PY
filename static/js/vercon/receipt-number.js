// VERCON v2: the NIR number with an optional sub-number (707,1 = a receipt inserted at its chronological place between two numbers).
// The server stores the whole part in Numar and the fraction in HUNDREDTHS in NumarSub (707,1 = 707 + 10; 707,01 = 707 + 1). At most 2 digits.

/** The text of the sub-number box for a stored value: 10 -> '1', 12 -> '12', 1 -> '01', 0 -> ''. */
export const subText = (k_hundredths) => {
  const k_value = Number(k_hundredths) || 0;
  return k_value ? (k_value % 10 === 0 ? String(k_value / 10) : String(k_value).padStart(2, '0')) : '';
};

/** The stored value for what was typed in the box: '1' -> 10, '12' -> 12, '01' -> 1, '' / '0' / '00' -> 0. */
export const subHundredths = (k_text) => {
  const k_digits = String(k_text ?? '').replace(/\D/g, '').slice(0, 2);
  return k_digits ? Number(k_digits.padEnd(2, '0')) : 0;
};

/** The number as the operator reads it: 707 or 707,1. */
export const numberText = (k_doc) => {
  const k_sub = subText(k_doc?.sub_number);
  return k_sub ? `${k_doc.number},${k_sub}` : k_doc?.number;
};
