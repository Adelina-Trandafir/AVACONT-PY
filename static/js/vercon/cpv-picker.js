import { el, scope, field, paintTree, refreshTree, showMessage } from './ui.js';
import { ModalFrame } from '../components/modal-frame/modal-frame.js';

// Same lazy branch pattern as the NIR tree; external search is performed on the server.
export function mountCpvPicker(host, opts) {
  const returnFocus = document.activeElement;
  const controls = scope(), treeUi = scope();
  const status = el('p', { className: 'vc-save-status', role: 'status' });
  const description = el('p', { className: 'vc-cpv-page__description', role: 'status', hidden: !opts.current,
    textContent: opts.current ? 'Se încarcă descrierea CPV…' : '' });
  const searches = el('div', { className: 'vc-cpv-page__search' });
  const treeHost = el('div', { className: 'vc-cpv-page__tree' });
  const root = el('section', { className: 'vc-cpv-page', 'aria-label': 'Selectare cod CPV' },
    el('div', { className: 'vc-cpv-page__summary' },
      el('p', { className: 'vc-cpv-page__product', textContent: opts.name + (opts.current ? ' · CPV: ' + opts.current : '') }), description),
    searches, treeHost, status);
  host.append(root);
  let disposed = false, busy = false, sequence = 0, timer = 0, roots = null, tree = null, searchMode = false, results = null;
  const frame = new ModalFrame(root, { width: 1000, height: 600, title: 'CPV', onRequestClose: () => { if (!busy) close(); } });
  let selected = opts.current || null;
  const branches = new Map(), pending = new Map(), entries = new Map();
  const name = field({ key: 'cpvName', label: 'Căutare după denumire', type: 'text', max: 160 }, '', true, schedule, controls);
  const code = field({ key: 'cpvCode', label: 'Căutare după cod', type: 'text', max: 160 }, '', true, schedule, controls);
  searches.append(name.node, code.node);
  const say = (message = '', bad = false) => { if (!disposed) showMessage(status, message, bad ? 'error' : 'info'); };
  const read = (params, path = '/api/vercon/cpv') => {
    const query = new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
    for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
    return opts.api(path + '?' + query);
  };
  async function loadDescription() {
    if (!opts.current) return;
    try {
      const answer = await read({}, '/api/vercon/cpv/' + encodeURIComponent(opts.current));
      if (!disposed) description.textContent = answer.name || 'Codul CPV nu are descriere.';
    } catch (error) {
      if (!disposed && error.name !== 'AbortError') description.textContent = error.message || 'Descrierea CPV nu a putut fi încărcată.';
    }
  }
  const passive = (id, label) => ({ id: 'passive:' + id, label, selectable: false });
  function node(item, flat = false) {
    entries.set(item.code, item);
    return { id: 'doc:' + item.code, label: item.code + ' — ' + item.name,
      rightIcon: { icon: '➕', action: 'choose-cpv', title: 'Folosește acest cod CPV' },
      children: !flat && item.has_children ? branches.has(item.code)
        ? branches.get(item.code).map((child) => node(child)) : [passive('wait:' + item.code, 'Se încarcă…')] : undefined };
  }
  function nodes() {
    const items = (searchMode ? results?.items : roots) || [];
    const values = items.map((item) => node(item, searchMode));
    if (searchMode && results.remaining > 0) values.push(passive('more',
      `Plus încă ${results.remaining} alte înregistrări. Ajustați căutarea pentru un rezultat mai restrâns.`));
    return values;
  }
  async function expand(id) {
    if (searchMode || !id.startsWith('doc:')) return;
    const key = id.slice(4);
    if (branches.has(key)) return;
    if (!pending.has(key)) pending.set(key, read({ parent: key }).then((answer) => branches.set(key, answer.items)).finally(() => pending.delete(key)));
    try {
      await pending.get(key);
      if (!disposed && !searchMode && tree) refreshTree(tree, nodes(), selected);
    } catch (error) { if (!disposed && error.name !== 'AbortError') say(error.message || 'Ramura CPV nu a putut fi încărcată.', true); }
  }
  async function choose(key) {
    if (busy || !entries.has(key)) return;
    busy = true; root.inert = true; say('Se salvează CPV…');
    try { await opts.onPick(key); close(); }
    catch (error) { if (!disposed && error.name !== 'AbortError') say(error.message || 'CPV nu a putut fi salvat.', true); }
    finally { busy = false; if (!disposed) root.inert = false; }
  }
  function draw() {
    treeUi.clear();
    tree = paintTree(treeHost, nodes(), { title: 'Coduri CPV', showSearchBox: false, collapsed: true,
      onSelect: (key) => { selected = key; }, onToggle: (id, open) => { if (open) void expand(id); },
      onAction: (key) => { void choose(key); },
    }, treeUi);
  }
  async function update(token) {
    treeHost.inert = true; say('Se încarcă…');
    const query = { name: name.input.value.trim(), code: code.input.value.trim() };
    const searching = !!(query.name || query.code);
    try {
      const answer = !searching && roots ? { items: roots, mode: 'tree' } : await read(query);
      if (disposed || token !== sequence) return;
      searchMode = searching; results = answer;
      if (!searching) roots = answer.items;
      draw(); say();
    } catch (error) {
      if (!disposed && token === sequence && error.name !== 'AbortError') say(error.message || 'Nomenclatorul CPV nu a putut fi încărcat.', true);
    } finally { if (!disposed && token === sequence) treeHost.inert = false; }
  }
  function schedule() {
    const token = ++sequence; clearTimeout(timer);
    treeHost.inert = true;
    timer = setTimeout(() => { void update(token); }, 300);
  }
  function close() {
    if (disposed) return;
    disposed = true; ++sequence; clearTimeout(timer); controls.clear(); treeUi.clear(); frame.destroy(); root.remove(); opts.onClose?.();
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
  }
  frame.open();
  void loadDescription();
  void update(++sequence);
  name.input.focus();
  return { destroy: close };
}
