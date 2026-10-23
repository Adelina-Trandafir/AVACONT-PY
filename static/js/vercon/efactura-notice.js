import { notify } from './messages.js';

// Check on entering a warehouse, even when no NIR screen has been opened yet.
export function efacturaNotice({ api, context, onError }) {
  let warehouse = null, checked = false, controller = null, sequence = 0, notice = null;
  const clearOwnMessage = () => {
    notice?.close(); notice = null;
  };
  return {
    async enter(ctx) {
      const sel = ctx.selection;
      const key = sel.subunit != null && sel.gestiune != null && ctx.menu?.some(item => item.key === 'nir')
        ? `${sel.subunit}/${sel.gestiune}` : null;
      if (key !== warehouse) { checked = false; warehouse = key; clearOwnMessage(); }
      if (key && checked) return;
      controller?.abort(); controller = null;
      const own = ++sequence;
      if (!key) return;
      controller = new AbortController();
      try {
        const result = await api('/api/vercon/doc/nir/efactura/notice?' + new URLSearchParams(Object.entries(sel).filter(([, value]) => value != null && value !== '')), { signal: controller.signal });
        if (own !== sequence || context()?.version !== ctx.version) return;
        checked = true;
        if (result.pending_count > 0) {
          const text = `Există ${result.pending_count} ${result.pending_count === 1 ? 'factură activă de preluat sau de asociat' : 'facturi active de preluat sau de asociat'}.\n\nDeschideți Intrări → NIR din e-Factura… pentru listă sau Asociază eFactura… dintr-un NIR salvat.`;
          notice = notify(text, 'info', { title: 'Facturi eFactura de preluat' });
        }
      } catch (error) {
        if (own === sequence && context()?.version === ctx.version && error.name !== 'AbortError') onError(error);
      }
    },
    destroy() { ++sequence; controller?.abort(); clearOwnMessage(); },
  };
}
