import { warehouseStock, warehouseChoices } from './oi-product-choices.js';
import { receiptTotal } from './receipt-reconciliation.js';
import { format, parse } from './fields.js';

// Availability belongs to a document/date snapshot, never to the last selected line.
export function consumptionEditor({ state, inputs, api, selection, changed }) {
  let loaded = null, loading = null, sequence = 0, failure = '';
  const document = () => state.mode === 'view' ? state.doc : state.draft;
  const excluded = () => state.mode === 'add' ? null : state.doc?.id;
  const same = snapshot => snapshot?.document === document() && snapshot?.date === document()?.header.Data
    && snapshot?.exclude === excluded();
  function remaining(row) {
    const doc = document();
    return receiptTotal([{ quantity: state.available[String(row.variant)] ?? '0' },
      ...(state.mode === 'view' ? [] : doc.lines.filter(other => other.key !== row.key
        && String(other.variant) === String(row.variant) && Number(other.Cant) > 0)
        .map(other => ({ quantity: '-' + String(other.Cant).replace(',', '.') })))], 'quantity', 4);
  }
  function syncDate() {
    const input = inputs.get('Data'), picker = input?._picker;
    if (!picker) return;
    const locked = state.mode === 'view' || !!state.draft?.lines.some(row => row.variant != null);
    if (locked) picker.close();
    input.disabled = picker.field.disabled = picker.button.disabled = locked;
    picker.field.title = locked && state.mode !== 'view' ? 'Data este blocată după alegerea primului produs.' : '';
  }
  async function load() {
    if (!document()) return;
    if (same(loaded)) return;
    if (same(loading)) return loading.promise;
    const snapshot = { document: document(), date: document().header.Data, exclude: excluded() };
    const token = ++sequence;
    failure = ''; loaded = null; state.available = {};
    const operation = (async () => {
      const date = parse(snapshot.date, { type: 'date', required: true });
      if (!date.ok) throw new Error('Completați o dată validă pentru calculul stocului.');
      const available = await warehouseStock(api, selection(), date.value, snapshot.exclude);
      if (token !== sequence || !same(snapshot)) return;
      state.available = available; loaded = snapshot;
      snapshot.document.lines.forEach(row => { if (row.variant) row.avail = remaining(row); });
      changed();
    })();
    loading = { ...snapshot, promise: operation };
    try { await operation; }
    catch (error) { if (token === sequence && same(snapshot)) failure = error.message; throw error; }
    finally { if (token === sequence) loading = null; }
  }
  return {
    load, syncDate, remaining,
    lockMessage: () => same(loaded) ? '' : failure || 'Se încarcă stocul la data bonului. Așteptați înainte de alegerea produsului.',
    nodes(items, current) {
      if (!same(loaded)) return [];
      const eligible = warehouseChoices(items, state.available, state.draft?.lines || [], current);
      const groups = new Map();
      for (const item of eligible) {
        if (!groups.has(item.product)) groups.set(item.product, {
          id: 'bc-product:' + item.product, label: item.name, children: [],
        });
        const available = remaining({ ...current, variant: item.id });
        groups.get(item.product).children.push({ id: String(item.id),
          label: `${format(item.putva, { type: 'decimal' })} lei / stoc ${format(available, { type: 'decimal' })} ${item.um || ''}${item.code ? ' / ' + item.code : ''}`,
          tooltip: item.name });
      }
      return groups.size ? [...groups.values()] : [{ id: 'bc-empty', label: 'Nu există produse cu stoc la data bonului.', selectable: false }];
    },
    validate(row, key) {
      if (!row.variant || !['variant', 'Cant'].includes(key)) return;
      if (!same(loaded)) throw new Error(failure || 'Stocul la data bonului nu este încă disponibil.');
      const available = remaining(row);
      if (!(Number(available) > 0)) throw new Error('Produsul nu mai are stoc disponibil la data bonului.');
      if (key === 'Cant' && (!(Number(row.Cant) > 0) || Number(row.Cant) > Number(available))) {
        throw new Error('Introduceți o cantitate pozitivă în limita stocului disponibil.');
      }
      row.avail = available;
    },
  };
}
