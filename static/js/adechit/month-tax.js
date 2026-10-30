import { Combobox } from '../components/combobox/combobox.js';
import { confirmBox } from '../utils/confirm-box.js';

const amount = rate => `${Number(rate).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} lei`;

// SLICE-AD14-02: the selected fee belongs to the whole open month, not the catalog's active default.
export function bindMonthTax({ api, host, state, month, loadSituation, selectChild, unsavedWork, report }) {
  let rates = null; let loading = null; let generation = 0; let busy = false; let attempt = null;
  const context = () => state.context;
  const combo = new Combobox(host.querySelector('.ade-picker'), {
    dropDownStyle: 'simple', allowEmpty: false, placeholder: 'Alegeți taxa', dropDownMinWidth: 360,
    onSelect: value => change(Number(value)),
  });
  combo.input.id = 'ade-month-tax-input';
  combo.input.setAttribute('aria-label', 'Taxa zilnică a lunii alese');

  function paint() {
    const selected = month();
    const current = rates?.find(row => row.IDV === selected?.IDV);
    host.hidden = !selected;
    combo.options.staticData = (rates || []).filter(row => Number.isFinite(Number(row.TaxaZilnica)) && Number(row.TaxaZilnica) > 0)
      .map(row => ({ value: String(row.IDV), label: `${amount(row.TaxaZilnica)} · ${row.Expl || ''}`,
        sortValue: row.TaxaZilnica, sortType: 'number',
        sortThen: [{ value: row.Expl || '', type: 'text' }, { value: row.DeLa ? `${row.DeLa}-01` : null, type: 'date' }] }));
    combo.options.maxResults = Math.max(50, combo.options.staticData.length);
    if (current) combo.setValue(String(current.IDV), amount(current.TaxaZilnica)); else combo.clear();
    combo.setEnabled(Boolean(selected && !selected.Inchisa && rates?.length && !busy && context()?.permissions.includes('catalog')));
    host.title = selected?.Inchisa ? 'Taxa nu se poate schimba într-o lună închisă.'
      : current?.Expl || 'Alegeți taxa zilnică pentru întreaga lună selectată.';
    host.setAttribute('aria-busy', String(busy));
  }

  async function load() {
    if (loading) return loading;
    const own = generation;
    loading = api('/api/adechit/rows/ValoriTaxe').then(({ rows }) => { if (own === generation) rates = rows; })
      .finally(() => { if (own === generation) loading = null; });
    return loading;
  }

  async function sync() {
    try { if (month() && !rates) await load(); paint(); }
    catch (error) { paint(); report(error); }
  }

  async function beforeChange() {
    if (state.grid?.hasEdit && !await state.grid.commitEdit()) return false;
    if (state.pending > 0 || unsavedWork()) throw new Error('Salvați sau renunțați la modificările în curs înainte de a schimba taxa lunii.');
    return true;
  }

  function setSaving(saving) {
    state.pending += saving ? 1 : -1;
    document.querySelector('.ade-main').inert = saving;
    document.querySelector('.ade-header').inert = saving;
  }

  async function refresh(updated) {
    const current = state.context.months.find(row => row.IDL === updated.IDL);
    if (current) Object.assign(current, updated);
    if (state.monthId !== updated.IDL) return;
    const order = state.grid?.sort; const selectedId = state.selected?.IDZ;
    await loadSituation();
    if (state.monthId !== updated.IDL) return;
    state.grid?.sortBy(order?.key ?? null, order?.dir ?? null);
    const row = state.rows.find(item => item.IDZ === selectedId);
    if (row) { state.grid.selectRow(row.IDZ); selectChild(row); }
  }

  async function change(taxId) {
    const selected = month();
    const tax = rates?.find(row => row.IDV === taxId);
    if (busy || !selected || !tax || selected.Inchisa || selected.IDV === taxId || !context()?.permissions.includes('catalog')) {
      paint(); return;
    }
    const reviewed = { ...selected };
    let saving = false;
    busy = true; paint();
    try {
      if (!await beforeChange()) return;
      if (month()?.IDL !== reviewed.IDL) return;
      if (!await confirmBox(`Aplicați taxa de ${amount(tax.TaxaZilnica)} pentru întreaga lună ${reviewed.LunaT} ${reviewed.Anul}?\n`
        + 'Se recalculează valorile pentru copiii din toate grupele subunității curente. Zilele de prezență se păstrează.', {
        title: 'Schimbă taxa lunii', yes: 'Aplică taxa', no: 'Renunță' })) return;
      if (month()?.IDL !== reviewed.IDL) return;
      const body = JSON.stringify({ month_id: reviewed.IDL, version: reviewed.Version, tax_id: taxId, tax_version: tax.Version });
      if (!attempt || attempt.body !== body) attempt = { body, key: crypto.randomUUID() };
      saving = true; setSaving(true);
      const result = await api('/api/adechit/month-tax', { method: 'POST', ...attempt });
      attempt = null;
      try { await refresh(result.month); }
      catch (error) { throw new Error(`Taxa a fost salvată. Reîncărcați situația: ${error.message}`); }
    } catch (error) { report(error); }
    finally { if (saving) setSaving(false); busy = false; paint(); }
  }

  function reset() { generation += 1; rates = null; loading = null; attempt = null; paint(); }
  return { sync, reset };
}
