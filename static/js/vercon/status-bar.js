// A message occupies only the space left between the two groups of actions.
// Keep that space reserved when the message is cleared or hidden.
export function statusBar(host, { start = [], status, end = [], note = null }) {
  const group = (name, children) => {
    const node = document.createElement('div');
    node.className = `vc-status-bar__${name}`;
    node.append(...children.filter(Boolean));
    return node;
  };
  host.classList.add('vc-status-bar');
  host.replaceChildren(group('start', start), group('messages', [status, note]), group('end', end));
  return host;
}
