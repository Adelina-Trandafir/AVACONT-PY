// Slice 0110-05 -- the read-only data grid of the web area: the PURE half (no DOM).
//
// Mirrors what KBotDataView (src/KBot.Controls/DataView) does with the data, minus editing:
// value types, display formats, per-column filters (checklist + condition), sorting,
// multi-level grouping and aggregates. Everything here takes plain values and returns plain
// values, so it can run (and be checked) outside a browser page.
//
// Vocabulary kept from the desktop control: value types text | number | datetime | boolean;
// operators equals notEquals contains notContains beginsWith notBeginsWith endsWith
// notEndsWith lessThan greaterThan between isEmpty isNotEmpty; aggregates sum count average
// min max countDistinct countEmpty countTrue countFalse first last.

export const ValueType = Object.freeze({ Text: 'text', Number: 'number', DateTime: 'datetime', Boolean: 'boolean' });

export const Operator = Object.freeze({
  Equals: 'equals',
  NotEquals: 'notEquals',
  Contains: 'contains',
  NotContains: 'notContains',
  BeginsWith: 'beginsWith',
  NotBeginsWith: 'notBeginsWith',
  EndsWith: 'endsWith',
  NotEndsWith: 'notEndsWith',
  LessThan: 'lessThan',
  GreaterThan: 'greaterThan',
  Between: 'between',
  IsEmpty: 'isEmpty',
  IsNotEmpty: 'isNotEmpty',
});

// What the operator is called in the filter popup (operator-visible, Romanian).
const OPERATOR_CAPTION = {
  equals: 'este egal cu',
  notEquals: 'este diferit de',
  contains: 'conține',
  notContains: 'nu conține',
  beginsWith: 'începe cu',
  notBeginsWith: 'nu începe cu',
  endsWith: 'se termină cu',
  notEndsWith: 'nu se termină cu',
  lessThan: 'este mai mic decât',
  greaterThan: 'este mai mare decât',
  between: 'este între',
  isEmpty: 'este gol',
  isNotEmpty: 'nu este gol',
};

const TEXT_OPS = ['equals', 'notEquals', 'contains', 'notContains', 'beginsWith', 'notBeginsWith', 'endsWith', 'notEndsWith', 'isEmpty', 'isNotEmpty'];
const ORDERED_OPS = ['equals', 'notEquals', 'lessThan', 'greaterThan', 'between', 'isEmpty', 'isNotEmpty'];
const BOOL_OPS = ['equals', 'isEmpty', 'isNotEmpty'];

export function allowedOperators(valueType) {
  if (valueType === ValueType.Number || valueType === ValueType.DateTime) return ORDERED_OPS;
  if (valueType === ValueType.Boolean) return BOOL_OPS;
  return TEXT_OPS;
}

export function operatorCaption(op) {
  if (!(op in OPERATOR_CAPTION)) throw new Error(`Unknown filter operator: ${op}`);
  return OPERATOR_CAPTION[op];
}

export function operandCount(op) {
  if (op === Operator.IsEmpty || op === Operator.IsNotEmpty) return 0;
  return op === Operator.Between ? 2 : 1;
}

// ---------------------------------------------------------------------------
// Values
// ---------------------------------------------------------------------------
export function isBlank(value) {
  return value === null || value === undefined || value === '' || (typeof value === 'number' && Number.isNaN(value));
}

/** The raw cell value read in the column's type: number | ms since epoch | boolean | string | null. */
export function toComparable(value, valueType) {
  if (isBlank(value)) return null;
  switch (valueType) {
    case ValueType.Number: {
      const n = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
      return Number.isNaN(n) ? null : n;
    }
    case ValueType.DateTime: {
      if (value instanceof Date) return value.getTime();
      if (typeof value === 'number') return value;
      const ms = parseDateText(String(value));
      return ms === null ? null : ms;
    }
    case ValueType.Boolean:
      if (typeof value === 'boolean') return value;
      if (typeof value === 'number') return value !== 0;
      return ['true', '1', 'da', 'yes', 'on'].includes(String(value).trim().toLowerCase());
    default:
      return String(value);
  }
}

export function compareValues(a, b, valueType) {
  const x = toComparable(a, valueType);
  const y = toComparable(b, valueType);
  if (x === null && y === null) return 0;
  if (x === null) return -1; // blanks first, as in the desktop grid
  if (y === null) return 1;
  if (valueType === ValueType.Text) return String(x).localeCompare(String(y), 'ro', { sensitivity: 'base' });
  if (valueType === ValueType.Boolean) return Number(x) - Number(y);
  return x - y;
}

// dd.MM.yyyy[ HH:mm[:ss[.fff]]]  or  yyyy-MM-dd[ThH:mm[:ss]]  ->  ms since epoch (local time), null when it is not a date
export function parseDateText(text) {
  const t = String(text).trim();
  let m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?)?$/.exec(t);
  if (m) return build(+m[3], +m[2], +m[1], m[4], m[5], m[6], m[7]);
  m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?)?/.exec(t);
  if (m) return build(+m[1], +m[2], +m[3], m[4], m[5], m[6], m[7]);
  return null;

  function build(y, mo, d, h, mi, s, ms) {
    const date = new Date(y, mo - 1, d, +(h || 0), +(mi || 0), +(s || 0), +String((ms || '0').padEnd(3, '0')));
    // refuse 31.02 and friends: the date must read back the same
    if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
    return date.getTime();
  }
}

/** An operand typed by a person -> comparable value, or null (an empty or unreadable operand makes the condition inert). */
export function coerceOperand(text, valueType) {
  if (isBlank(text)) return null;
  if (valueType === ValueType.Number) {
    const n = Number(String(text).trim().replace(/\s/g, '').replace(',', '.'));
    return Number.isNaN(n) ? null : n;
  }
  if (valueType === ValueType.DateTime) return parseDateText(String(text));
  if (valueType === ValueType.Boolean) return toComparable(text, valueType);
  return String(text);
}

// ---------------------------------------------------------------------------
// Display formats
// ---------------------------------------------------------------------------
const pad = (n, w = 2) => String(n).padStart(w, '0');

export function formatDate(ms, pattern) {
  const d = new Date(ms);
  return pattern.replace(/yyyy|MM|dd|HH|mm|ss|fff/g, (token) => {
    switch (token) {
      case 'yyyy': return String(d.getFullYear());
      case 'MM': return pad(d.getMonth() + 1);
      case 'dd': return pad(d.getDate());
      case 'HH': return pad(d.getHours());
      case 'mm': return pad(d.getMinutes());
      case 'ss': return pad(d.getSeconds());
      default: return pad(d.getMilliseconds(), 3);
    }
  });
}

const NAMED_DATE = {
  shortDate: 'dd.MM.yyyy',
  generalDate: 'dd.MM.yyyy HH:mm',
  generalDateSec: 'dd.MM.yyyy HH:mm:ss',
  generalDateMs: 'dd.MM.yyyy HH:mm:ss.fff',
  shortTime: 'HH:mm',
  longTime: 'HH:mm:ss',
  longTimeMs: 'HH:mm:ss.fff',
};

const numberFormatters = new Map();
function numberFormatter(min, max, grouping) {
  const key = `${min}|${max}|${grouping}`;
  if (!numberFormatters.has(key)) {
    numberFormatters.set(key, new Intl.NumberFormat('ro-RO', { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: grouping }));
  }
  return numberFormatters.get(key);
}

/**
 * The text a cell shows. `col` carries format (named), formatString (date pattern), decimals, valueType.
 * Named formats: general | fixed | standard | currency | euro | percent | shortDate | generalDate |
 * generalDateSec | generalDateMs | shortTime | longTime | longTimeMs | yesNo | trueFalse | onOff.
 */
export function formatValue(value, col) {
  if (typeof col.formatter === 'function') return col.formatter(value);
  const valueType = col.valueType || ValueType.Text;
  const comparable = toComparable(value, valueType);
  if (comparable === null) return '';
  switch (valueType) {
    case ValueType.Number: {
      const decimals = Number.isInteger(col.decimals) && col.decimals >= 0 ? col.decimals : null;
      switch (col.format) {
        case 'fixed': return numberFormatter(decimals ?? 2, decimals ?? 2, false).format(comparable);
        case 'standard': return numberFormatter(decimals ?? 2, decimals ?? 2, true).format(comparable);
        case 'currency': return `${numberFormatter(decimals ?? 2, decimals ?? 2, true).format(comparable)} lei`;
        case 'euro': return `${numberFormatter(decimals ?? 2, decimals ?? 2, true).format(comparable)} €`;
        case 'percent': return `${numberFormatter(decimals ?? 0, decimals ?? 2, false).format(comparable * 100)}%`;
        default:
          return decimals === null ? numberFormatter(0, 10, false).format(comparable) : numberFormatter(decimals, decimals, false).format(comparable);
      }
    }
    case ValueType.DateTime:
      return formatDate(comparable, col.formatString || NAMED_DATE[col.format] || NAMED_DATE.shortDate);
    case ValueType.Boolean: {
      const words = { yesNo: ['Da', 'Nu'], trueFalse: ['Adevărat', 'Fals'], onOff: ['Activ', 'Inactiv'] }[col.format] || ['Da', 'Nu'];
      return comparable ? words[0] : words[1];
    }
    default:
      return String(comparable);
  }
}

// ---------------------------------------------------------------------------
// Column filter: checklist of display values + one condition
// ---------------------------------------------------------------------------
/** `selected`: Set of display texts that pass (null = no checklist); `condition`: {op, v1, v2} or null. */
export function createFilter() {
  return { selected: null, condition: null };
}

export function filterIsActive(filter) {
  if (!filter) return false;
  if (filter.selected) return true;
  const c = filter.condition;
  return !!c && (operandCount(c.op) === 0 || !isBlank(c.v1));
}

export function matchesCondition(raw, condition, valueType) {
  const { op } = condition;
  if (!allowedOperators(valueType).includes(op)) throw new Error(`Operator ${op} is not offered for ${valueType} columns`);
  const blank = toComparable(raw, valueType) === null;
  if (op === Operator.IsEmpty) return blank;
  if (op === Operator.IsNotEmpty) return !blank;

  const a = coerceOperand(condition.v1, valueType);
  if (a === null) return true; // an unreadable operand is inert, as in the desktop grid
  if (blank) return op === Operator.NotEquals || op === Operator.NotContains || op === Operator.NotBeginsWith || op === Operator.NotEndsWith;
  const value = toComparable(raw, valueType);

  if (valueType === ValueType.Text) {
    const v = String(value).toLowerCase();
    const x = String(a).toLowerCase();
    switch (op) {
      case Operator.Equals: return v === x;
      case Operator.NotEquals: return v !== x;
      case Operator.Contains: return v.includes(x);
      case Operator.NotContains: return !v.includes(x);
      case Operator.BeginsWith: return v.startsWith(x);
      case Operator.NotBeginsWith: return !v.startsWith(x);
      case Operator.EndsWith: return v.endsWith(x);
      default: return !v.endsWith(x);
    }
  }
  const cmp = valueType === ValueType.Boolean ? Number(value) - Number(a) : value - a;
  switch (op) {
    case Operator.Equals: return cmp === 0;
    case Operator.NotEquals: return cmp !== 0;
    case Operator.LessThan: return cmp < 0;
    case Operator.GreaterThan: return cmp > 0;
    case Operator.Between: {
      const b = coerceOperand(condition.v2, valueType);
      if (b === null) return true;
      const lo = Math.min(a, b);
      const hi = Math.max(a, b);
      return value >= lo && value <= hi;
    }
    default: throw new Error(`Unhandled operator ${op}`);
  }
}

export function matchesFilter(filter, raw, display, valueType) {
  if (!filterIsActive(filter)) return true;
  if (filter.selected && !filter.selected.has(display)) return false;
  if (filter.condition && (operandCount(filter.condition.op) === 0 || !isBlank(filter.condition.v1))) {
    return matchesCondition(raw, filter.condition, valueType);
  }
  return true;
}

// ---------------------------------------------------------------------------
// Aggregates
// ---------------------------------------------------------------------------
const NUMERIC_AGGREGATES = ['sum', 'count', 'average', 'min', 'max', 'countDistinct', 'countEmpty', 'first', 'last'];
const TEXT_AGGREGATES = ['count', 'countDistinct', 'countEmpty', 'min', 'max', 'first', 'last'];
const BOOL_AGGREGATES = ['count', 'countEmpty', 'countTrue', 'countFalse', 'first', 'last'];

export function aggregatesFor(valueType) {
  if (valueType === ValueType.Number) return NUMERIC_AGGREGATES;
  if (valueType === ValueType.Boolean) return BOOL_AGGREGATES;
  if (valueType === ValueType.DateTime) return TEXT_AGGREGATES;
  return TEXT_AGGREGATES;
}

/** Aggregate of `values` (raw cell values). Returns {value, valueType} so the caller can format it, or null for an empty result. */
export function aggregate(kind, values, valueType) {
  if (!aggregatesFor(valueType).includes(kind)) throw new Error(`Aggregate ${kind} is not offered for ${valueType} columns`);
  const comparable = values.map((v) => toComparable(v, valueType));
  const present = comparable.filter((v) => v !== null);
  const number = (value) => ({ value, valueType: ValueType.Number });
  switch (kind) {
    case 'count': return number(present.length);
    case 'countEmpty': return number(comparable.length - present.length);
    case 'countDistinct': return number(new Set(present).size);
    case 'countTrue': return number(present.filter(Boolean).length);
    case 'countFalse': return number(present.filter((v) => v === false).length);
    case 'sum': return number(present.reduce((s, v) => s + v, 0));
    case 'average': return present.length ? number(present.reduce((s, v) => s + v, 0) / present.length) : null;
    case 'min':
    case 'max': {
      if (!present.length) return null;
      const sorted = [...present].sort((a, b) => compareValues(a, b, valueType));
      return { value: kind === 'min' ? sorted[0] : sorted[sorted.length - 1], valueType };
    }
    case 'first': return present.length ? { value: present[0], valueType } : null;
    case 'last': return present.length ? { value: present[present.length - 1], valueType } : null;
    default: throw new Error(`Unhandled aggregate ${kind}`);
  }
}

// ---------------------------------------------------------------------------
// Grouping + flattening
// ---------------------------------------------------------------------------
/**
 * The key a row falls under at one group level. `pattern` is a regex over the DISPLAYED text:
 * key = the capture groups joined, or the whole match; no match = the whole text.
 */
export function groupKey(display, pattern) {
  if (!pattern) return display;
  const m = pattern.exec(display);
  if (!m) return display;
  return m.length > 1 ? m.slice(1).filter((p) => p !== undefined).join('') : m[0];
}

/**
 * Turns rows into the flat list the grid paints: rows, group headers and group footers.
 *
 *   rows       array of plain objects
 *   columns    [{key, valueType, format, formatString, decimals, aggregate}]
 *   filters    Map columnKey -> filter
 *   sort       {key, dir: 'asc'|'desc'} or null
 *   groups     [{key, dir, keyPattern (RegExp|null), showHeader, showFooter, collapsedByDefault}]
 *   collapsed  Set of group ids toggled AWAY from their default (an id is the path of keys)
 *
 * Returns {items, shownRows}. item = {kind:'row', index, row} | {kind:'gh'|'gf', level, id, key, count, rows}
 */
export function buildItems({ rows, columns, filters, sort, groups, collapsed }) {
  const colByKey = new Map(columns.map((c) => [c.key, c]));
  const displayOf = (row, key) => formatValue(row[key], colByKey.get(key));

  // 1) filter
  const passing = [];
  rows.forEach((row, index) => {
    for (const [key, filter] of filters) {
      const col = colByKey.get(key);
      if (!col) throw new Error(`Filter on unknown column ${key}`);
      if (!matchesFilter(filter, row[key], displayOf(row, key), col.valueType || ValueType.Text)) return;
    }
    passing.push({ index, row });
  });

  // 2) sort: group levels first (rows of one key must sit together), then the user's column
  const levels = groups.filter((g) => g.key);
  const keyed = passing.map((entry) => ({
    ...entry,
    keys: levels.map((g) => groupKey(displayOf(entry.row, g.key), g.keyPattern)),
  }));
  const compareKeys = (a, b, level) => {
    const g = levels[level];
    const col = colByKey.get(g.key);
    const type = col.valueType || ValueType.Text;
    // read the key in the column's type when it can be read that way (a date key sorts as a date)
    const x = coerceOperand(a, type);
    const y = coerceOperand(b, type);
    const r = x !== null && y !== null && type !== ValueType.Text ? compareValues(x, y, type) : String(a).localeCompare(String(b), 'ro', { sensitivity: 'base', numeric: true });
    return g.dir === 'desc' ? -r : r;
  };
  keyed.sort((a, b) => {
    for (let level = 0; level < levels.length; level += 1) {
      if (a.keys[level] !== b.keys[level]) {
        const r = compareKeys(a.keys[level], b.keys[level], level);
        if (r !== 0) return r;
      }
    }
    if (sort && colByKey.has(sort.key)) {
      const col = colByKey.get(sort.key);
      const r = compareValues(a.row[sort.key], b.row[sort.key], col.valueType || ValueType.Text);
      if (r !== 0) return sort.dir === 'desc' ? -r : r;
    }
    return a.index - b.index; // stable
  });

  // 3) flatten
  if (!levels.length) {
    return { items: keyed.map((e) => ({ kind: 'row', index: e.index, row: e.row })), shownRows: keyed.length };
  }
  const items = [];
  let shownRows = 0;
  const walk = (entries, level, path, hidden) => {
    const g = levels[level];
    const buckets = new Map();
    for (const e of entries) {
      const k = e.keys[level];
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k).push(e);
    }
    for (const [key, bucket] of buckets) {
      const id = `${path}\u0001${key}`;
      const isCollapsed = g.showHeader !== false && (collapsed.has(id) ? !g.collapsedByDefault : !!g.collapsedByDefault);
      const group = { level, id, key, count: bucket.length, rows: bucket.map((e) => e.row) };
      if (!hidden && g.showHeader !== false) items.push({ kind: 'gh', collapsed: isCollapsed, ...group });
      const inner = hidden || isCollapsed;
      if (level + 1 < levels.length) {
        walk(bucket, level + 1, id, inner);
      } else if (!inner) {
        for (const e of bucket) {
          items.push({ kind: 'row', index: e.index, row: e.row });
          shownRows += 1;
        }
      }
      if (!hidden && !isCollapsed && g.showFooter) items.push({ kind: 'gf', ...group });
    }
  };
  walk(keyed, 0, '', false);
  return { items, shownRows };
}

/** `{0}` = column title, `{1}` = group value, `{2}` = row count. An empty value reads as `emptyCaption`. */
export function groupCaption(pattern, title, value, count, emptyCaption = '(goale)') {
  return pattern
    .replace('{0}', title)
    .replace('{1}', value === '' ? emptyCaption : value)
    .replace('{2}', String(count));
}
