// Shared identity provider for local document printouts and snapshot previews.
// Resolve at generation time so a subunit switch cannot reuse an old heading.
let provider;

export function bindReportIdentity(api, selection) {
  provider = { api, selection };
}

export async function readReportIdentity() {
  const selection = provider?.selection();
  if (!selection?.subunit || !selection?.gestiune) throw new Error('Selectați subunitatea și gestiunea pentru raport.');
  const before = JSON.stringify(selection);
  const identity = await provider.api(`/api/vercon/reports/identity?${new URLSearchParams(selection)}`);
  if (JSON.stringify(provider.selection()) !== before) throw new Error('Contextul s-a schimbat. Generați din nou raportul.');
  return identity;
}
