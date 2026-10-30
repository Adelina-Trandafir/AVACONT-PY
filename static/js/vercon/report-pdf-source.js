// App-only annotations in the preview; downloads and printing use a clean PDF.
export async function reportPdfSource(api, model, selection, isCurrent = () => true) {
  const params = new URLSearchParams(selection);
  params.delete('preview');
  const endpoint = `/api/vercon/reports/export/${model.snapshot}/pdf`;
  const cleanUrl = `${endpoint}?${params}`;
  if (model.stock_card) params.set('preview', '1');
  const data = await api(`${endpoint}?${params}`, { responseType: 'arrayBuffer' });
  let clean = model.stock_card ? null : Promise.resolve(data);
  return { data, exportPdf: async view => {
    if (!isCurrent() || view.closed) return null;
    if (!clean) {
      clean = api(cleanUrl, { responseType: 'arrayBuffer' }).catch(error => { clean = null; throw error; });
    }
    const bytes = await clean;
    return isCurrent() && !view.closed ? bytes : null;
  } };
}
