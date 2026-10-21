// Shared UI message delivery and in-memory journal; no business data in bus payloads.
import eventBus from '../event-bus/event-bus.js';
import { showTooltip } from '../components/tooltip/tooltip.js';
const history = [];
const tooltips = new WeakMap();
export function showMessage(element, message, level = 'info', options = {}) {
  tooltips.get(element)?.close('replaced');
  tooltips.delete(element);
  element.textContent = options.inline === false ? '' : (message || '');
  element.hidden = options.inline === false || !message;
  element.setAttribute('role', level === 'error' ? 'alert' : 'status');
  if (message) {
    history.push({ at: new Date().toISOString(), message, level });
    if (history.length > 100) history.shift();
    if (options.tooltip !== false) {
      const tooltip = showTooltip(message, { ...options, source: element, level });
      tooltips.set(element, tooltip);
      tooltip.closed.then(() => { if (tooltips.get(element) === tooltip) tooltips.delete(element); });
    }
    if (eventBus.listenerCount('ui:message') > 0) eventBus.emit('ui:message', { level });
  }
}
export function messageHistory() { return history.map((item) => ({ ...item })); }

// Opt-in lifecycle for transient page errors; clear before the next action runs.
export function clearMessageOnAction(element) {
  const listeners = new AbortController();
  const target = element.ownerDocument.defaultView;
  const clear = (event) => {
    if (event.target.closest?.('.kbot-tooltip-host')) return;
    if (element.getAttribute('role') === 'alert' && element.textContent) showMessage(element, '');
  };
  for (const type of ['pointerdown', 'click', 'keydown', 'input', 'change', 'submit']) {
    target.addEventListener(type, clear, { capture: true, signal: listeners.signal });
  }
  return () => listeners.abort();
}
