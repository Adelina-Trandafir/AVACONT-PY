// The daily fee of the chosen month, shown read-only in the blue bar. It is a plain label, not an editor:
// it changes only when the month does (or when the fees were reloaded with the context).
export function bindMonthTax({ api, host, month, report }) {
  const value = host.querySelector('b');
  let rates = null; let loading = null;

  function paint() {
    const k_month = month();
    const k_rate = k_month && rates ? rates.get(k_month.IDV) : null;
    host.hidden = k_rate == null;
    value.textContent = k_rate == null ? '' : `${Number(k_rate).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} lei`;
  }

  async function load() {
    if (loading) return loading;
    loading = api('/api/adechit/rows/ValoriTaxe').then(({ rows }) => { rates = new Map(rows.map((row) => [row.IDV, row.TaxaZilnica])); })
      .catch(report).finally(() => { loading = null; });
    return loading;
  }

  async function sync() {
    if (month() && !rates) await load();
    paint();
  }

  // the fees are read again with the next context (a changed fee must show on its month)
  function reset() { rates = null; paint(); }

  return { sync, reset };
}
