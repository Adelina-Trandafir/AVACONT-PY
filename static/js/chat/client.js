// SLICE-CHAT-04. Shared controller; component rendering remains under components/chat.
import { ChatPanel } from '../components/chat/chat-panel.js';
import { APPLICATIONS } from '../components/chat/applications.js';

const greater = (left, right) => BigInt(left || '0') > BigInt(right || '0');
const filterKey = state => state.preferences.applications.join('');
const OPEN_REFRESH_MS = 5000;
const CLOSED_REFRESH_MS = 60000;
const CATCHUP_MS = 1000;

export function mountChat({ application, token, onUnauthorized = () => {}, signal }) {
  const abort = new AbortController();
  let stopped = false, timer = 0, readTimer = 0, queued = 0;
  let chain = Promise.resolve(), loaded = false, tailComplete = true;
  let currentFilter = '', retrySend = null, readFrontiers = {};
  let stateUpdatedAt = 0, messagesUpdatedAt = 0;
  const panel = new ChatPanel({
    application,
    onOpen: () => { planPoll(); return enqueue(() => refresh()); },
    onClose: () => { clearTimeout(readTimer); planPoll(); },
    onRefresh: () => enqueue(() => refresh(true)),
    onSend: data => enqueue(() => send(data), true),
    onFilter: apps => enqueue(() => changeFilter(apps), true),
    onPreference: data => enqueue(() => savePreference(data), true),
    onOlder: () => enqueue(older, true),
    onRead: scheduleRead,
  });

  async function call(path, body) {
    const headers = { Accept: 'application/json', 'X-Portal-Token': token() };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/chat/' + path, {
      method: body === undefined ? 'GET' : 'POST', headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store', signal: abort.signal,
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      destroy(); onUnauthorized();
      throw new DOMException('Session expired', 'AbortError');
    }
    if (!response.ok) throw new Error(result.error || 'Chatul nu este momentan disponibil.');
    return result;
  }

  function enqueue(task, busy = false) {
    if (stopped) return Promise.resolve();
    queued += 1;
    if (busy) panel.setBusy(true);
    const operation = chain.then(async () => {
      if (stopped) return;
      if (busy) panel.setBusy(true);
      try { await task(); }
      catch (error) {
        if (error.name !== 'AbortError' && !stopped) {
          stateUpdatedAt = 0; messagesUpdatedAt = 0;
          panel.setStatus(error.message || 'Conexiunea cu chatul s-a întrerupt. Reîncercați.', true);
        }
      } finally {
        queued -= 1;
        if (!stopped && busy) panel.setBusy(false);
      }
    });
    chain = operation;
    return operation;
  }

  function applyState(state, settings = false) {
    stateUpdatedAt = Date.now();
    readFrontiers = state.read_by_application;
    panel.setState(state, { settings });
  }

  function extremes() {
    let first = null, last = '0';
    for (const id of panel.messages.keys()) {
      if (first === null || greater(first, id)) first = id;
      if (greater(id, last)) last = id;
    }
    return { first, last };
  }

  async function latest() {
    const result = await call('messages');
    if (stopped) return;
    currentFilter = filterKey(result.state);
    applyState(result.state, !panel.ready);
    panel.resetMessages();
    loaded = true;
    tailComplete = true;
    messagesUpdatedAt = Date.now();
    panel.setHasOlder(result.has_more);
    panel.addMessages(result.messages, { initial: true });
    panel.setStatus('');
    scheduleRead();
  }

  async function refresh(force = false) {
    if (stopped || document.hidden) return;
    if (!loaded && panel.isOpen) { await latest(); return; }
    if (!panel.isOpen) {
      if (!force && stateUpdatedAt && Date.now() - stateUpdatedAt < CLOSED_REFRESH_MS) return;
      const state = await call('state');
      if (stopped) return;
      const changed = currentFilter && currentFilter !== filterKey(state);
      currentFilter = filterKey(state);
      applyState(state, !panel.ready);
      if (changed) { loaded = false; messagesUpdatedAt = 0; }
      panel.setStatus('');
      return;
    }
    // Metadata polling while closed does not make the message list fresh.
    // Reopening within the open polling interval can reuse the loaded history.
    if (!force && tailComplete && messagesUpdatedAt && Date.now() - messagesUpdatedAt < OPEN_REFRESH_MS) {
      scheduleRead();
      return;
    }
    // Bound each poll. A large backlog continues in the following poll, without
    // marking unseen batches as read or blocking other actions indefinitely.
    for (let batch = 0; batch < 5; batch += 1) {
      const result = await call(`messages?after=${extremes().last}`);
      if (stopped) return;
      if (currentFilter !== filterKey(result.state)) { await latest(); return; }
      applyState(result.state);
      tailComplete = !result.has_more;
      panel.addMessages(result.messages);
      if (tailComplete) break;
    }
    messagesUpdatedAt = tailComplete ? Date.now() : 0;
    panel.setStatus('');
    scheduleRead();
  }

  async function changeFilter(apps) {
    try {
      const state = await call('preferences', { applications: apps });
      if (stopped) return;
      currentFilter = filterKey(state);
      applyState(state);
      // The filter is saved even if the subsequent history request is interrupted.
      loaded = false;
      messagesUpdatedAt = 0;
      panel.resetMessages();
      await latest();
    } catch (error) {
      // Restore the server's accepted checkboxes after an unsuccessful change.
      if (!stopped) {
        for (const [app, control] of panel.filters) control.checked = currentFilter.includes(app);
      }
      throw error;
    }
  }

  async function savePreference(data) {
    try {
      const state = await call('preferences', data);
      if (!stopped) { applyState(state); panel.setStatus(''); }
    } catch (error) {
      // A send always includes the explicit currently shown mode, even if saving
      // that default failed. No implicit change from pseudonym to name is possible.
      throw error;
    }
  }

  async function older() {
    const before = extremes().first;
    if (!before) return;
    const result = await call(`messages?before=${before}`);
    if (stopped) return;
    if (currentFilter !== filterKey(result.state)) { await latest(); return; }
    applyState(result.state);
    panel.addMessages(result.messages, { prepend: true });
    panel.setHasOlder(result.has_more);
    panel.setStatus('');
  }

  async function send(data) {
    const signature = JSON.stringify(data);
    if (!retrySend || retrySend.signature !== signature) retrySend = { signature, key: crypto.randomUUID() };
    const result = await call('messages', { ...data, client_key: retrySend.key });
    if (stopped) return;
    retrySend = null;
    panel.clearDraft();
    applyState(result.state);
    if (currentFilter !== filterKey(result.state)) {
      loaded = false;
      await latest();
    }
    if (!result.state.preferences.applications.includes(application)) {
      panel.setStatus(`Mesaj trimis în ${APPLICATIONS[application].label}. Filtrul curent îl ascunde.`);
    } else {
      if (!loaded) await latest(); else await refresh(true);
      panel.scrollToBottom();
    }
  }

  function scheduleRead() {
    clearTimeout(readTimer);
    if (stopped || !loaded || !tailComplete || !panel.isOpen || document.hidden || !document.hasFocus() || !panel.atBottom()) return;
    readTimer = setTimeout(() => {
      void enqueue(async () => {
        if (!loaded || !tailComplete || !panel.isOpen || document.hidden || !document.hasFocus() || !panel.atBottom()) return;
        const frontiers = {};
        for (const message of panel.messages.values()) {
          if (!currentFilter.includes(message.application)) continue;
          const previous = frontiers[message.application] || readFrontiers[message.application] || '0';
          if (greater(message.id, previous)) frontiers[message.application] = message.id;
        }
        if (!Object.keys(frontiers).length) return;
        const state = await call('read', { frontiers });
        if (!stopped) applyState(state);
      });
    }, 350);
  }

  function planPoll() {
    clearTimeout(timer);
    if (stopped || document.hidden) return;
    const interval = panel.isOpen ? (tailComplete ? OPEN_REFRESH_MS : CATCHUP_MS) : CLOSED_REFRESH_MS;
    const updatedAt = panel.isOpen ? messagesUpdatedAt : stateUpdatedAt;
    const delay = updatedAt ? Math.max(CATCHUP_MS, interval - (Date.now() - updatedAt)) : interval;
    timer = setTimeout(async () => {
      if (!queued) await enqueue(() => refresh());
      planPoll();
    }, delay);
  }

  function wake() {
    if (document.hidden) { clearTimeout(timer); clearTimeout(readTimer); return; }
    if (!queued) void enqueue(() => refresh()).then(planPoll);
    else planPoll();
    scheduleRead();
  }

  function destroy() {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer); clearTimeout(readTimer); abort.abort(); panel.destroy();
    signal?.removeEventListener('abort', destroy);
  }

  document.addEventListener('visibilitychange', wake, { signal: abort.signal });
  window.addEventListener('focus', wake, { signal: abort.signal });
  window.addEventListener('pagehide', destroy, { signal: abort.signal });
  window.addEventListener('app:session-expired', destroy, { signal: abort.signal });
  signal?.addEventListener('abort', destroy, { once: true });
  if (signal?.aborted) destroy();
  else { void enqueue(() => refresh(true)); planPoll(); }
  return { destroy };
}
