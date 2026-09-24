// File: static/js/event-bus.js
/**
 * 🚀 EVENT BUS - SISTEM CENTRAL DE EVENIMENTE
 * Elimină toate dependențele circulare și permite comunicare unidirecțională
 *
 * PATTERN: Publisher-Subscriber
 * - Decuplare totală între module
 * - Comunicare asincronă
 * - Event history pentru debugging
 *
 * @version 2.0.0 - Arhitectură modulară
 * @author Adelina Trandafir - Avatar Soft SRL
 */

import { EVENTS } from './event-bus-events.js';
import { EventBusLogger } from './event-bus-logger.js';
import { EventBusStats } from './event-bus-stats.js';
import { EventBusHistory } from './event-bus-history.js';
import { EventBusHelpers } from './event-bus-helpers.js';

class EventBus {
  constructor(options = {}) {
    // Inițializare module
    this.logger = new EventBusLogger(options.debugMode || false);
    this.stats = new EventBusStats();
    this.history = new EventBusHistory(options.maxHistorySize || 100);

    // Storage pentru evenimente și listeners
    this.events = new Map();

    // Configurație
    this.returnBehavior = options.returnBehavior || 'always-true';

    // Method binding pentru API public
    this.emit = this.emit.bind(this);
    this.on = this.on.bind(this);
    this.off = this.off.bind(this);
    this.once = this.once.bind(this);

    this.logger.log('🚀 EventBus inițializat', {
      maxHistorySize: this.history.maxSize,
    });
  }

  /**
   * Emit un eveniment cu date opționale
   */
  emit(eventName, data = {}) {
    // Validare eventName
    if (!eventName || typeof eventName !== 'string') {
      this.logger.error(`❌ emit: Eveniment inexistent sau invalid din ${new Error().stack}`);
      this.stats.incrementFailedEmits();
      return this.returnBehavior === 'always-true' ? true : false;
    }

    // Adaugă în istoric
    this.history.add(eventName, 'emit', data);

    // Incrementează stats
    this.stats.incrementEventsEmitted();

    const listeners = this.events.get(eventName);
    if (!listeners || listeners.length === 0) {
      this.logger.error(`⚠️ Niciun listener pentru: ${eventName}`);
      return this.returnBehavior === 'always-true' ? true : false;
    }

    let successCount = 0;
    let errorCount = 0;

    // Găsește numele constantei și caller info
    const eventDisplayName = EventBusHelpers.getEventDisplayName(eventName, EVENTS);
    const callerInfo = EventBusHelpers.getCallerInfo();
    const listenerDetails = EventBusHelpers.getListenerDetails(listeners);
    const dataPreview = EventBusHelpers.getDataPreview(data);

    // Log complet
    this.logger.ignore(`📢 EMIT: ${eventDisplayName}`);
    this.logger.ignore(`   └─ Apelat din: ${callerInfo}`);
    this.logger.ignore(`   └─ Listeners (${listeners.length}): [${listenerDetails.join(', ')}]`);
    this.logger.ignore(`   └─ Data: ${dataPreview.text}${dataPreview.truncated ? '...' : ''}`);

    // Colectează listenerii "once" pentru ștergere DUPĂ iterație
    const listenersToRemove = [];

    // Execută toți listenerii
    listeners.forEach((listenerObj, index) => {
      try {
        const { callback, context, once } = listenerObj;

        // Log pentru fiecare listener executat
        const listenerName = listenerDetails[index].split('.')[1];
        this.logger.ignore(`   └─ Se execută: ${listenerName}`);

        // Apelează callback-ul cu contextul corect
        if (context) {
          callback.call(context, {
            eventName,
            data,
            timestamp: Date.now(),
          });
        } else {
          callback({
            eventName,
            data,
            timestamp: Date.now(),
          });
        }

        successCount++;

        // Marchează listener-ul pentru ștergere dacă e once
        if (once) {
          listenersToRemove.push(listenerObj);
        }
      } catch (error) {
        errorCount++;
        this.logger.error(`❌ Eroare în listener ${listenerDetails[index]}:`, error);
      }
    });

    // Șterge listenerii "once" DUPĂ ce s-au executat toți
    listenersToRemove.forEach((listenerObj) => {
      const index = listeners.indexOf(listenerObj);
      if (index !== -1) {
        listeners.splice(index, 1);
        this.stats.removeListener();

        const listenerName = listenerObj.callback.name || 'anonymous';
        this.logger.ignore(`  ↳ ${listenerName} eliminat (once)`);
      }
    });

    this.logger.log(
      `✅ EMIT complet: ${eventDisplayName} (${successCount} success, ${errorCount} erori)`
    );

    return this.returnBehavior === 'always-true' ? true : successCount > 0;
  }

  /**
   * Așteaptă un eveniment cu timeout
   */
  waitFor(eventName, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const handler = (data) => {
        clearTimeout(timer);
        this.off(eventName, handler);
        resolve(data);
      };

      const timer = setTimeout(() => {
        this.off(eventName, handler);
        this.logger.error(`Timeout așteptând evenimentul: ${eventName}`);
        resolve(null);
      }, timeout);

      this.on(eventName, handler);
    });
  }

  /**
   * Înregistrează un listener pentru un eveniment
   */
  on(eventName, callback, context = null) {
    // Validare parametri
    if (!eventName || typeof eventName !== 'string') {
      this.logger.error(`❌ on: Eveniment inexistent sau invalid`);
      return () => {};
    }

    if (typeof callback !== 'function') {
      this.logger.error('❌ on: callback trebuie să fie funcție');
      return () => {};
    }

    // Capturează informații despre înregistrare
    const registrationInfo = EventBusHelpers.captureRegistrationInfo();

    // Creează array de listeners dacă nu există
    if (!this.events.has(eventName)) {
      this.events.set(eventName, []);
    }

    // Stochează informații extinse despre listener
    const listenerObj = {
      callback,
      context: context || null,
      once: false,
      callingMethod: registrationInfo.info || 'necunoscut',
      registeredAt: Date.now(),
      contextFromStack: registrationInfo.context || '',
      registrationStack: registrationInfo.fullStack || '',
    };

    // Adaugă listener
    const listeners = this.events.get(eventName);
    if (Array.isArray(listeners)) {
      listeners.push(listenerObj);
    } else {
      this.logger.error('⚠️ Listeners array corupt, recreez...');
      this.events.set(eventName, [listenerObj]);
    }

    // Adaugă în istoric
    try {
      this.history.add(eventName, 'on');
    } catch (historyError) {
      this.logger.error('⚠️ Eroare la adăugarea în istoric:', historyError);
    }

    // Incrementează stats
    this.stats.addListener();

    this.logger.log(`👂 ON: ${eventName} ← ${registrationInfo.info}`);

    // Returnează funcția de unsubscribe
    return () => {
      try {
        const currentListeners = this.events.get(eventName);
        if (Array.isArray(currentListeners)) {
          const index = currentListeners.indexOf(listenerObj);
          if (index !== -1) {
            currentListeners.splice(index, 1);
            this.stats.removeListener();
            this.logger.log(`🚫 OFF: ${eventName} ← ${registrationInfo.info}`);

            // Dacă nu mai sunt listeneri, șterge complet evenimentul
            if (currentListeners.length === 0) {
              this.events.delete(eventName);
            }
          } else {
            this.logger.error('⚠️ Listener nu a fost găsit la unsubscribe');
          }
        } else {
          this.logger.error('⚠️ Listeners array invalid la unsubscribe');
        }
      } catch (unsubscribeError) {
        this.logger.error('⚠️ Eroare la unsubscribe:', unsubscribeError);
      }
    };
  }

  /**
   * Înregistrează un listener care se execută o singură dată
   */
  once(eventName, callback, context = null) {
    if (!eventName || typeof eventName !== 'string') {
      this.logger.error('❌ once: eventName trebuie să fie string non-gol');
      return () => {};
    }

    if (typeof callback !== 'function') {
      this.logger.error('❌ once: callback trebuie să fie funcție');
      return () => {};
    }

    this.logger.log(`👂 ONCE: ${eventName}`);

    // Adaugă în istoric
    this.history.add(eventName, 'once');

    // Creează array de listeners dacă nu există
    if (!this.events.has(eventName)) {
      this.events.set(eventName, []);
    }

    // Adaugă listener-ul cu flag once
    const listeners = this.events.get(eventName);
    const listenerObj = { callback, context, once: true, id: Date.now() };
    listeners.push(listenerObj);

    // Update stats
    this.stats.addListener();

    // Returnează funcție de unsubscribe
    return () => {
      this.off(eventName, callback, context);
    };
  }

  /**
   * Elimină un listener specific sau toți listenerii pentru un eveniment
   */
  off(eventName, callback = null, context = null) {
    if (!eventName || typeof eventName !== 'string') {
      this.logger.error('❌ off: eventName trebuie să fie string non-gol');
      return false;
    }

    this.logger.log(`🔇 OFF: ${eventName}`);

    const listeners = this.events.get(eventName);
    if (!listeners || listeners.length === 0) {
      return false;
    }

    // Dacă nu e specificat callback, elimină toți listenerii
    if (!callback) {
      const count = listeners.length;
      this.stats.removeListeners(count);
      this.events.delete(eventName);
      return true;
    }

    // Elimină listener specific
    const initialLength = listeners.length;
    const filteredListeners = listeners.filter((listenerObj) => {
      return !(
        listenerObj.callback === callback &&
        (context === null || listenerObj.context === context)
      );
    });

    if (filteredListeners.length === 0) {
      this.events.delete(eventName);
    } else {
      this.events.set(eventName, filteredListeners);
    }

    const removedCount = initialLength - filteredListeners.length;
    this.stats.removeListeners(removedCount);

    return removedCount > 0;
  }

  /**
   * Elimină toți listenerii pentru toate evenimentele
   */
  clear() {
    this.logger.log('🧹 CLEAR: Eliminare toți listenerii');
    this.events.clear();
    this.stats.clearActiveListeners();
  }

  /**
   * Returnează numărul de listeners pentru un eveniment
   */
  listenerCount(eventName) {
    const listeners = this.events.get(eventName);
    return listeners ? listeners.length : 0;
  }

  /**
   * Returnează toate evenimentele înregistrate
   */
  getEvents() {
    return Array.from(this.events.keys());
  }

  /**
   * Returnează istoricul evenimentelor
   */
  getHistory() {
    return this.history.getAll();
  }

  /**
   * Returnează statistici despre EventBus
   */
  getStats() {
    return this.stats.getFullStats(this.events, this.history.getSize());
  }

  /**
   * Curăță istoricul
   */
  clearHistory() {
    this.history.clear();
    this.logger.log('🧹 Istoric curățat');
  }

  /**
   * Cleanup pentru listeners care nu mai sunt valizi
   */
  cleanup() {
    let removedCount = 0;

    this.events.forEach((listeners, eventName) => {
      const activeListeners = listeners.filter((listenerObj) => {
        return typeof listenerObj.callback === 'function';
      });

      removedCount += listeners.length - activeListeners.length;

      if (activeListeners.length === 0) {
        this.events.delete(eventName);
      } else {
        this.events.set(eventName, activeListeners);
      }
    });

    this.stats.removeListeners(removedCount);
    this.logger.log(`🧹 CLEANUP: Eliminați ${removedCount} listeneri expirați`);

    return removedCount;
  }

  /**
   * Destroy complet al EventBus-ului
   */
  destroy() {
    this.logger.log('🗑️ Distrugere EventBus...');
    this.clear();
    this.history.clear();
    this.logger.log('✅ EventBus distrus');
  }
}

// ==================== VARIANTE DE EXPORT ====================

/**
 * Variantă Heavy cu debugging activat
 */
export class EventBusHeavy extends EventBus {
  constructor(options = {}) {
    super({
      debugMode: true,
      maxHistorySize: 100,
      returnBehavior: 'always-true',
      ...options,
    });
  }
}

// ==================== SINGLETON INSTANCE ====================

window.MIXIN_DEBUG_OVERRIDE = true;

const eventBusInstance = new EventBusHeavy();

// Expune pe window pentru debugging și backward compatibility
window.eventBus = eventBusInstance;
window.EVENTS = EVENTS;

// Export default și named exports
export default eventBusInstance;
export { EVENTS };
