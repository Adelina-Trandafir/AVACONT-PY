// Shared ordering for data controls: labels are text, dates are chronological,
// decimal values retain all digits (including DECIMAL(20,4) received as strings).
const alphabet = new Intl.Collator('ro', { sensitivity: 'base', numeric: false });
const blank = value => value == null || String(value).trim() === '';

function decimal(value) {
  let text = String(value).trim().replace(/[\s\u00a0\u202f]/g, '');
  if (text.includes(',')) text = text.replace(/\./g, '').replace(',', '.');
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(text);
  if (!match || !(match[2] || match[3])) return null;
  const exponent = Number(match[4] || 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null;
  let digits = (match[2] || '') + (match[3] || '');
  let point = (match[2] || '').length + exponent;
  if (point < 0) { digits = '0'.repeat(-point) + digits; point = 0; }
  if (point > digits.length) digits += '0'.repeat(point - digits.length);
  const whole = digits.slice(0, point).replace(/^0+/, '') || '0';
  const fraction = digits.slice(point).replace(/0+$/, '');
  return { sign: whole === '0' && !fraction ? 0 : match[1] === '-' ? -1 : 1, whole, fraction };
}

function dateValue(value) {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.getTime();
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const text = String(value).trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})(?=$|[ T])/.exec(text);
  const ro = /^(\d{1,2})\.(\d{1,2})\.(\d{4})(?=$|[ T])/.exec(text);
  if (!iso && !ro) return null;
  const [year, month, day] = iso ? [+iso[1], +iso[2], +iso[3]] : [+ro[3], +ro[2], +ro[1]];
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  const tail = text.slice((iso || ro)[0].length).replace(/^ /, 'T');
  if (!tail) return date.getTime();
  const stamp = Date.parse(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}${tail}`);
  return Number.isNaN(stamp) ? null : stamp;
}

export function compareTyped(a, b, type = 'text') {
  if (blank(a) || blank(b)) return blank(a) === blank(b) ? 0 : blank(a) ? -1 : 1;
  if (type === 'number') {
    const x = decimal(a), y = decimal(b);
    if (x && y) {
      if (x.sign !== y.sign) return x.sign - y.sign;
      const length = Math.max(x.fraction.length, y.fraction.length);
      const left = x.whole + x.fraction.padEnd(length, '0');
      const right = y.whole + y.fraction.padEnd(length, '0');
      return x.sign * (x.whole.length - y.whole.length || (left < right ? -1 : left > right ? 1 : 0));
    }
    if (x || y) return x ? 1 : -1;
  } else if (type === 'date' || type === 'datetime') {
    const x = dateValue(a), y = dateValue(b);
    if (x != null && y != null) return x - y;
    if (x != null || y != null) return x != null ? 1 : -1;
  } else if (type === 'boolean') return Number(Boolean(a)) - Number(Boolean(b));
  return alphabet.compare(String(a), String(b));
}

function itemValue(item, options) {
  if (options.sortValue) return options.sortValue(item);
  return Object.hasOwn(item, 'sortValue') ? item.sortValue : item.label ?? item.name ?? item.text ?? item.value ?? '';
}

export function compareItems(a, b, options = {}) {
  const left = itemValue(a, options), right = itemValue(b, options);
  let type = options.sortType || a.sortType || b.sortType;
  if (!type) type = dateValue(left) != null && dateValue(right) != null ? 'date'
    : decimal(left) && decimal(right) ? 'number' : 'text';
  const direction = (options.sortDirection || a.sortDirection || b.sortDirection) === 'desc' ? -1 : 1;
  const result = direction * compareTyped(left, right, type);
  if (result) return result;
  const aKeys = a.sortThen || [], bKeys = b.sortThen || [];
  for (let i = 0; i < Math.max(aKeys.length, bKeys.length); i++) {
    const x = aKeys[i], y = bKeys[i];
    const next = compareTyped(x?.value, y?.value, x?.type || y?.type || 'text');
    if (next) return direction * next;
  }
  return 0;
}

export function orderItems(items, options = {}) {
  const values = items.map(item => itemValue(item, options)).filter(value => !blank(value));
  const explicit = items.find(item => item.sortType)?.sortType;
  const sortType = options.sortType || explicit || (values.length && values.every(value => dateValue(value) != null) ? 'date'
    : values.length && values.every(value => decimal(value)) ? 'number' : 'text');
  return [...items].sort((a, b) => compareItems(a, b, { ...options, sortType }));
}
