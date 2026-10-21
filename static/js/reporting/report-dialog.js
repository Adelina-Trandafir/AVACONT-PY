// The report viewer owns its resources and releases them on close or navigation.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

export function reportDialog(title, owner, details = {}) {
  const heading = details.heading || title;
  const dialog = el('dialog', { className: 'confirm-box report-view', 'aria-label': heading });
  const body = el('div', { className: 'report-view-body' }), footer = el('footer');
  const resources = new Set();
  let closed = false;
  const scope = { own(item) { resources.add(item); return item; } };
  const close = () => {
    if (closed) return;
    closed = true;
    for (const item of resources) { if (typeof item === 'function') item(); else item.destroy?.(); }
    resources.clear();
    dialog.close(); dialog.remove();
    window.removeEventListener('pagehide', close);
    window.removeEventListener('app:session-expired', close);
    details.onClose?.(close);
  };
  const header = details.createHeader?.(heading) || el('header', {}, el('h2', { textContent: heading }));
  dialog.append(header, body, footer);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  owner?.own(close); window.addEventListener('pagehide', close, { once: true });
  window.addEventListener('app:session-expired', close, { once: true });
  document.body.append(dialog); dialog.showModal();
  return { dialog, header, body, footer, scope, close,
    setTitle(value, metadata = {}) {
      const text = details.headingFor?.(value, metadata) || value;
      header.querySelector('h2').textContent = text;
      dialog.setAttribute('aria-label', text);
    },
    get closed() { return closed; } };
}
