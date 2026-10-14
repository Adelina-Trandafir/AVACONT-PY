// The contract of one editable field -- what the operator types (display text), what the draft
// holds (canonical value) and what is sent. Pure functions, no DOM. Shared (utils/): VERCON, ADECHIT, the TextBox control. Slice VC02-03.
//
// canonical values: text -> string (kept exactly: apostrophes, diacritics, inner spaces), int -> integer number,
// decimal -> string "1234.50" (a string so no float noise; the server applies the INT scale), date -> "YYYY-MM-DD",
// empty input -> null. 0 is a value; an empty box is NULL -- never confused.
// result: { ok: true, value } or { ok: false, message }.

const OK = (k_value) => ({ ok: true, value: k_value });
const BAD = (k_message) => ({ ok: false, message: k_message });

export function parse(k_text, k_column) {
  const k_raw = k_text == null ? '' : String(k_text);
  if (k_column.type === 'text') {
    const k_value = k_raw.trim() === '' ? null : k_raw.replace(/^\s+|\s+$/g, '');
    return validate(k_value, k_column);
  }
  const k_trim = k_raw.trim();
  if (k_trim === '') return validate(null, k_column);
  if (k_column.type === 'int') {
    if (!/^[+-]?\d+$/.test(k_trim)) return BAD('Introduceti un numar intreg.');
    return validate(Number(k_trim), k_column);
  }
  if (k_column.type === 'decimal') {
    const k_canon = canonicalDecimal(k_trim);
    return k_canon === null ? BAD('Introduceti un numar (zecimale cu virgula).') : validate(k_canon, k_column);
  }
  if (k_column.type === 'date') {
    const k_iso = parseDate(k_trim);
    return k_iso === null ? BAD('Introduceti data ca zz.ll.aaaa.') : validate(k_iso, k_column);
  }
  throw new Error(`Tip de coloana necunoscut: ${k_column.type}`);
}

// "1.234,56" -> "1234.56"; "12,5" -> "12.5"; "12.5" -> "12.5". A dot is a thousands mark only when it is followed by
// exactly three digits AND a comma decimal exists or the groups are regular; otherwise it is a decimal point.
function canonicalDecimal(k_text) {
  let k_value = k_text.replace(/\s/g, '');
  if (k_value.includes(',')) {
    if ((k_value.match(/,/g) || []).length > 1) return null;
    k_value = k_value.replace(/\./g, '').replace(',', '.');
  } else if (/^[+-]?\d{1,3}(\.\d{3}){2,}$/.test(k_value)) {
    k_value = k_value.replace(/\./g, '');     // 1.234.567 -> thousands marks
  }
  if (!/^[+-]?\d+(\.\d+)?$/.test(k_value)) return null;
  const [k_int, k_frac = ''] = k_value.replace(/^\+/, '').split('.');
  const k_sign = k_int.startsWith('-') ? '-' : '';
  const k_digits = k_int.replace('-', '').replace(/^0+(?=\d)/, '');
  const k_result = k_frac ? `${k_digits}.${k_frac}` : k_digits;
  return (k_sign && Number(k_result) !== 0 ? k_sign : '') + k_result;
}

function parseDate(k_text) {
  const k_local = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/.exec(k_text);
  const k_iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(k_text);
  const [k_y, k_m, k_d] = k_local ? [k_local[3], k_local[2], k_local[1]] : k_iso ? [k_iso[1], k_iso[2], k_iso[3]] : [];
  if (!k_y) return null;
  const k_date = new Date(Date.UTC(Number(k_y), Number(k_m) - 1, Number(k_d)));
  if (k_date.getUTCFullYear() !== Number(k_y) || k_date.getUTCMonth() !== Number(k_m) - 1 || k_date.getUTCDate() !== Number(k_d)) return null;
  return `${k_y}-${String(k_m).padStart(2, '0')}-${String(k_d).padStart(2, '0')}`;
}

export function validate(k_value, k_column) {
  if (k_value === null) return k_column.required ? BAD('Campul este obligatoriu.') : OK(null);
  if (k_column.type === 'text' && k_column.max && k_value.length > k_column.max) return BAD(`Cel mult ${k_column.max} caractere.`);
  if ((k_column.type === 'int' || k_column.type === 'decimal') && k_column.min != null && Number(k_value) < k_column.min) {
    return BAD(`Valoarea trebuie sa fie cel putin ${k_column.min}.`);
  }
  return OK(k_value);
}

// An amount for reading: always 2 decimals, '.' between thousands, ',' before the decimals (1234.5 -> 1.234,50). The decimal
// string is rounded half up as text, so no binary floating point error reaches the figure; anything that is not a plain number is returned as is.
export function formatMoney(k_value) {
  if (k_value === null || k_value === undefined || k_value === '') return '';
  const k_match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(k_value).trim().replace(',', '.'));
  if (!k_match) return String(k_value);
  const k_decimals = k_match[3] || '';
  let k_cents = BigInt(k_match[2] + k_decimals.padEnd(2, '0').slice(0, 2));
  if (k_decimals.length > 2 && k_decimals[2] >= '5') k_cents += 1n;
  const k_whole = String(k_cents / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${k_match[1] && k_cents !== 0n ? '-' : ''}${k_whole},${String(k_cents % 100n).padStart(2, '0')}`;
}

export function format(k_value, k_column) {
  if (k_value === null || k_value === undefined || k_value === '') return '';
  if (k_column.type === 'decimal') return String(k_value).replace('.', ',');
  if (k_column.type === 'date') { const [k_y, k_m, k_d] = String(k_value).split('-'); return `${k_d}.${k_m}.${k_y}`; }
  return String(k_value);
}
