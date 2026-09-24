// File: static/js/mixins/listener-tracker-eventbus.js
/**
 * 🚀 EVENTBUS & TIMER TRACKER MODULE
 */

import { log } from './listener-tracker-mixin.js';

/**
 * 🚀 INTERCEPTEAZĂ METODELE EVENTBUS CU LOGURI DETALIATE PE EVENIMENT
 */
export function interceptEventBusMethods(target, eventBus) {
  target._originalEventBusOn = eventBus.on.bind(eventBus);
  target._originalEventBusOnce = eventBus.once.bind(eventBus);

  target.addBusListener = function (eventName, handler, context = null) {
    try {
      const unsubscribe = this._originalEventBusOn(eventName, handler, context);

      if (!this._listenerRegistry.busListeners.has(eventName)) {
        this._listenerRegistry.busListeners.set(eventName, []);
      }
      this._listenerRegistry.busListeners.get(eventName).push(unsubscribe);
      this._listenerRegistry.stats.busListenersAdded++;

      // Calculează câți ascultă pe acest eveniment specific
      const listenersOnThisEvent = this._listenerRegistry.busListeners.get(eventName).length;

      // Calculează câți ascultă pe toate evenimentele din acest modul
      const totalListenersInThisModule = Array.from(
        this._listenerRegistry.busListeners.values()
      ).reduce((sum, funcs) => sum + funcs.length, 0);

      log(
        this,
        `📡 Bus listener tracked: ${eventName} (pe eveniment: ${listenersOnThisEvent}, în modul: ${totalListenersInThisModule}, total app: ${this._listenerRegistry.stats.busListenersAdded})`
      );

      return () => {
        const funcs = this._listenerRegistry.busListeners.get(eventName);
        if (funcs) {
          const index = funcs.indexOf(unsubscribe);
          if (index !== -1) {
            funcs.splice(index, 1);
            if (funcs.length === 0) {
              this._listenerRegistry.busListeners.delete(eventName);
            }

            // Calculează câți mai rămân pe acest eveniment
            const remainingOnEvent = funcs ? funcs.length : 0;
            const totalRemainingInModule = Array.from(
              this._listenerRegistry.busListeners.values()
            ).reduce((sum, funcs) => sum + funcs.length, 0);

            log(
              this,
              `📡 Bus listener individual cleanup: ${eventName} (rămân pe eveniment: ${remainingOnEvent}, în modul: ${totalRemainingInModule})`
            );
          }
        }
        unsubscribe();
      };
    } catch (error) {
      log(this, `❌ Eroare la adăugarea bus listener ${eventName}:`, error);
      return () => {};
    }
  };

  target.addBusListenerOnce = function (eventName, handler, context = null) {
    try {
      const unsubscribe = this._originalEventBusOnce(eventName, handler, context);

      // Nu adaugăm în registry pentru once listeners
      // Pentru că se auto-curăță la primul eveniment
      this._listenerRegistry.stats.busListenersAdded++;

      log(
        this,
        `📡 Bus listener ONCE tracked: ${eventName} (auto-cleanup la primul event, total app: ${this._listenerRegistry.stats.busListenersAdded})`
      );

      return unsubscribe; // Returnează direct unsubscribe-ul de la eventBus.once
    } catch (error) {
      log(this, `❌ Eroare la adăugarea bus listener once ${eventName}:`, error);
      return () => {};
    }
  };
}

/**
 * ⏰ INTERCEPTEAZĂ TIMER METHODS CU LOGURI
 */
export function interceptTimerMethods(target) {
  target.addTimeout = function (callback, delay, ...args) {
    const timerId = setTimeout(() => {
      this._listenerRegistry.timers.delete(timerId);
      log(this, `⏰ Timer executat și auto-curățat: ${timerId}`);
      callback(...args);
    }, delay);

    this._listenerRegistry.timers.add(timerId);
    this._listenerRegistry.stats.timersAdded++;

    log(this, `⏰ Timer tracked: ${delay}ms, ID: ${timerId}`);
    return timerId;
  };

  target.addInterval = function (callback, interval, ...args) {
    const intervalId = setInterval(() => {
      callback(...args);
    }, interval);

    this._listenerRegistry.intervals.add(intervalId);
    this._listenerRegistry.stats.intervalsAdded++;

    log(this, `🔄 Interval tracked: ${interval}ms, ID: ${intervalId}`);
    return intervalId;
  };
}
