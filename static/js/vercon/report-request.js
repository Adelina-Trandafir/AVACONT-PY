// Keep the context in the URL and potentially large checkbox selections in the body.
export function reportContext(params) {
  const source = new URLSearchParams(params), context = new URLSearchParams();
  for (const key of ['subunit', 'gestiune', 'year']) {
    if (source.has(key)) context.set(key, source.get(key));
  }
  return context;
}

export function generateReport(api, name, params) {
  return api(`/api/vercon/reports/${encodeURIComponent(name)}/generate?${reportContext(params)}`, {
    method: 'POST', body: new URLSearchParams(params),
  });
}
