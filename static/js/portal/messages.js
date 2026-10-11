// Shared UI message delivery and in-memory journal; no business data in bus payloads.
import eventBus from '../event-bus/event-bus.js';
const history = [];
export function showMessage(element, message, level = 'info') {
  element.textContent = message || '';
  element.hidden = !message;
  element.setAttribute('role', level === 'error' ? 'alert' : 'status');
  if (message) {
    history.push({ at: new Date().toISOString(), message, level });
    if (history.length > 100) history.shift();
    // The message is already displayed above; bus subscribers are optional.
    if (eventBus.listenerCount('ui:message') > 0) eventBus.emit('ui:message', { level });
  }
}
export function messageHistory() { return history.map((item) => ({ ...item })); }
