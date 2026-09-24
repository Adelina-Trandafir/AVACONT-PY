// File: static/js/mixins/listener-tracker-cleanup.js
/**
 * 🔧 CLEANUP MANAGER & STATISTICS MODULE
 */

import { log } from './listener-tracker-mixin.js';

/**
 * 🔧 ADAUGĂ METODELE DE TRACKING CU LOGURI EXTINSE
 */
export function addTrackingMethods(target) {
  target.cleanupAllListeners = function (which = 'all') {
    const startTime = performance.now();
    let cleanupStats = {
      busListeners: 0,
      domListeners: 0,
      timers: 0,
      intervals: 0,
      cleanupCallbacks: 0,
      errors: [],
    };

    // 🔍 LOG DETAILAT ÎNAINTE DE CLEANUP
    log(this, '🧹 === ÎNCEPE CLEANUP COMPLET LISTENERS ===');
    log(this, `📊 REGISTRY ÎNAINTE DE CLEANUP:`, {
      busListeners: this._listenerRegistry.busListeners.size,
      domListeners: this._listenerRegistry.domListeners.size,
      timers: this._listenerRegistry.timers.size,
      intervals: this._listenerRegistry.intervals.size,
      cleanupCallbacks: this._listenerRegistry.cleanupCallbacks.size,
    });

    if (which === 'all' || which === 'bus') {
      // 📋 DETALII DESPRE FIECARE BUS LISTENER
      if (this._listenerRegistry.busListeners.size > 0) {
        log(this, '📡 BUS LISTENERS ÎNAINTE DE CLEANUP (PE EVENIMENT):');
        this._listenerRegistry.busListeners.forEach((unsubscribeFunctions, eventName) => {
          log(this, `  - ${eventName}: ${unsubscribeFunctions.length} listeneri`);
        });
      }
    }

    if (which === 'all' || which === 'dom') {
      // 📋 DETALII DESPRE FIECARE LISTENER PE ELEMENT
      if (this._listenerRegistry.domListeners.size > 0) {
        log(this, '🎧 DOM LISTENERS ÎNAINTE DE CLEANUP (PE ELEMENT):');

        // Grupează listeners pe element
        const listenersByElement = new Map();
        this._listenerRegistry.domListeners.forEach((listenerInfo) => {
          const elementKey = `${listenerInfo.elementTag}#${listenerInfo.elementId}`;
          if (!listenersByElement.has(elementKey)) {
            listenersByElement.set(elementKey, []);
          }
          listenersByElement.get(elementKey).push(listenerInfo);
        });

        listenersByElement.forEach((listeners, elementKey) => {
          const events = listeners.map((l) => l.event);
          log(this, `  - ${elementKey}: ${listeners.length} listeners [${events.join(', ')}]`);
        });
      }
    }

    try {
      if (which === 'all' || which === 'bus') {
        // 1. CLEANUP EVENTBUS LISTENERS CU LOGURI DETALIATE PE EVENIMENT
        log(this, '🧹 Curăț EventBus listeners pe eveniment...');
        this._listenerRegistry.busListeners.forEach((unsubscribeFunctions, eventName) => {
          log(this, `  Curăț eveniment ${eventName}: ${unsubscribeFunctions.length} listeneri`);
          unsubscribeFunctions.forEach((unsubscribe, index) => {
            try {
              unsubscribe();
              cleanupStats.busListeners++;
              window._globalListenerCounters.totalBusListeners--;
              log(
                this,
                `    ✅ Curățat listener ${index + 1}/${unsubscribeFunctions.length} pentru ${eventName}`
              );
            } catch (error) {
              log(
                this,
                `    ❌ Eroare la cleanup listener ${index + 1}/${unsubscribeFunctions.length} pentru ${eventName}:`,
                error
              );
              cleanupStats.errors.push(`EventBus ${eventName}[${index + 1}]: ${error.message}`);
            }
          });
        });
        this._listenerRegistry.busListeners.clear();
        log(this, `✅ EventBus listeners curățați: ${cleanupStats.busListeners}`);
      }

      if (which === 'all' || which === 'dom') {
        // 2. CLEANUP DOM LISTENERS CU LOGURI PE ELEMENT
        log(this, '🧹 Curăț DOM listeners pe element...');

        // Grupează cleanup pe element
        const cleanupByElement = new Map();
        this._listenerRegistry.domListeners.forEach((listenerInfo) => {
          const elementKey = `${listenerInfo.elementTag}#${listenerInfo.elementId}`;
          if (!cleanupByElement.has(elementKey)) {
            cleanupByElement.set(elementKey, []);
          }
          cleanupByElement.get(elementKey).push(listenerInfo);
        });

        cleanupByElement.forEach((listeners, elementKey) => {
          log(this, `  Curăț ${elementKey}: ${listeners.length} listeners`);
          listeners.forEach((listenerInfo, index) => {
            try {
              const { element, event, handler, options } = listenerInfo;
              element.removeEventListener(event, handler, options);
              cleanupStats.domListeners++;
              window._globalListenerCounters.totalDOMListeners--;
              log(this, `    ✅ Curățat ${event} [${index + 1}/${listeners.length}]`);
            } catch (error) {
              log(this, `    ❌ Eroare la cleanup ${listenerInfo.event}:`, error);
              cleanupStats.errors.push(
                `DOM ${elementKey}[${listenerInfo.event}]: ${error.message}`
              );
            }
          });
        });

        this._listenerRegistry.domListeners.clear();
        log(this, `✅ DOM listeners curățați: ${cleanupStats.domListeners}`);

        // 3. CLEANUP TIMERS CU LOGURI
        if (this._listenerRegistry.timers.size > 0) {
          log(this, '🧹 Curăț timers...');
          this._listenerRegistry.timers.forEach((timerId) => {
            try {
              clearTimeout(timerId);
              cleanupStats.timers++;
              log(this, `    ✅ Curățat timer: ${timerId}`);
            } catch (error) {
              log(this, `    ❌ Eroare la cleanup timer ${timerId}:`, error);
              cleanupStats.errors.push(`Timer ${timerId}: ${error.message}`);
            }
          });
          this._listenerRegistry.timers.clear();
          log(this, `✅ Timers curățați: ${cleanupStats.timers}`);
        }

        // 4. CLEANUP INTERVALS CU LOGURI
        if (this._listenerRegistry.intervals.size > 0) {
          log(this, '🧹 Curăț intervals...');
          this._listenerRegistry.intervals.forEach((intervalId) => {
            try {
              clearInterval(intervalId);
              cleanupStats.intervals++;
              log(this, `    ✅ Curățat interval: ${intervalId}`);
            } catch (error) {
              log(this, `    ❌ Eroare la cleanup interval ${intervalId}:`, error);
              cleanupStats.errors.push(`Interval ${intervalId}: ${error.message}`);
            }
          });
          this._listenerRegistry.intervals.clear();
          log(this, `✅ Intervals curățați: ${cleanupStats.intervals}`);
        }

        // 5. CLEANUP CALLBACKS CUSTOM CU LOGURI
        if (this._listenerRegistry.cleanupCallbacks.size > 0) {
          log(this, '🧹 Curăț cleanup callbacks...');
          let callbackIndex = 0;
          this._listenerRegistry.cleanupCallbacks.forEach((callback) => {
            try {
              callback();
              cleanupStats.cleanupCallbacks++;
              log(this, `    ✅ Executat cleanup callback ${callbackIndex + 1}`);
              callbackIndex++;
            } catch (error) {
              log(this, `    ❌ Eroare la cleanup callback ${callbackIndex + 1}:`, error);
              cleanupStats.errors.push(`Cleanup callback[${callbackIndex + 1}]: ${error.message}`);
              callbackIndex++;
            }
          });
          this._listenerRegistry.cleanupCallbacks.clear();
          log(this, `✅ Cleanup callbacks executați: ${cleanupStats.cleanupCallbacks}`);
        }
      }

      const cleanupTime = performance.now() - startTime;

      log(
        this,
        `🎉 === CLEANUP COMPLET FINALIZAT ===\n` +
          `📊 SUMAR: ${cleanupStats.busListeners} bus + ${cleanupStats.domListeners} DOM + ` +
          `${cleanupStats.timers} timers + ${cleanupStats.intervals} intervals + ` +
          `${cleanupStats.cleanupCallbacks} callbacks în ${cleanupTime.toFixed(2)}ms`
      );

      if (cleanupStats.errors.length > 0) {
        log(this, `⚠️ ERORI LA CLEANUP (${cleanupStats.errors.length}):`, cleanupStats.errors);
      }

      return cleanupStats;
    } catch (error) {
      log(this, '❌ EROARE CRITICĂ LA CLEANUP:', error);
      return { error: error.message, ...cleanupStats };
    }
  };

  target.waitForEventResult = function (eventName, callback, timeout = 10000) {
    // 🎯 FOLOSEȘTE ONCE în loc de listener normal
    const cleanup = this.addBusListenerOnce(eventName, (result) => {
      clearTimeout(timeoutId);
      callback(null, result); // Success callback
    });

    // Timeout simplu
    const timeoutId = setTimeout(() => {
      cleanup(); // Curăță listener-ul once
      callback(new Error('Timeout'), null); // Error callback
    }, timeout);
  };

  target.getListenerStats = function () {
    // Grupează pe element pentru statistici DOM
    const listenersByElement = new Map();
    this._listenerRegistry.domListeners.forEach((listenerInfo) => {
      const elementKey = `${listenerInfo.elementTag}#${listenerInfo.elementId}`;
      if (!listenersByElement.has(elementKey)) {
        listenersByElement.set(elementKey, []);
      }
      listenersByElement.get(elementKey).push(listenerInfo);
    });

    const elementStats = Array.from(listenersByElement.entries()).map(
      ([elementKey, listeners]) => ({
        element: elementKey,
        count: listeners.length,
        events: listeners.map((l) => l.event),
      })
    );

    // Statistici bus pe eveniment
    const busStats = Array.from(this._listenerRegistry.busListeners.entries()).map(
      ([eventName, funcs]) => ({ eventName, count: funcs.length })
    );

    const stats = {
      busListeners: this._listenerRegistry.busListeners.size,
      domListeners: this._listenerRegistry.domListeners.size,
      timers: this._listenerRegistry.timers.size,
      intervals: this._listenerRegistry.intervals.size,
      cleanupCallbacks: this._listenerRegistry.cleanupCallbacks.size,
      totalAdded: this._listenerRegistry.stats,
      busListenersByEvent: busStats,
      domListenersByElement: elementStats,
    };

    // LOG DETALIAT STATS PE ELEMENT ȘI BUS
    log(this, '📊 LISTENER STATS PE ELEMENT ȘI BUS:', stats);
    return stats;
  };

  target.checkListenersHealth = function () {
    log(this, '🔍 === VERIFICARE HEALTH LISTENERS PE ELEMENT ȘI BUS ===');

    const health = {
      busListenersActive: this._listenerRegistry.busListeners.size,
      domListenersActive: this._listenerRegistry.domListeners.size,
      timersActive: this._listenerRegistry.timers.size,
      intervalsActive: this._listenerRegistry.intervals.size,
      isHealthy: true,
      issues: [],
      elementDetails: [],
      busDetails: [],
    };

    // Verifică bus listeners pe eveniment
    this._listenerRegistry.busListeners.forEach((funcs, eventName) => {
      health.busDetails.push({
        event: eventName,
        listenerCount: funcs.length,
      });
    });

    // Verifică DOM listeners pe element
    const listenersByElement = new Map();
    let invalidDomListeners = 0;

    this._listenerRegistry.domListeners.forEach((listenerInfo) => {
      const elementKey = `${listenerInfo.elementTag}#${listenerInfo.elementId}`;
      if (!listenersByElement.has(elementKey)) {
        listenersByElement.set(elementKey, { listeners: [], isValid: true });
      }

      const elementData = listenersByElement.get(elementKey);
      elementData.listeners.push(listenerInfo);

      if (!listenerInfo.element || !listenerInfo.element.parentNode) {
        invalidDomListeners++;
        elementData.isValid = false;
        health.issues.push(
          `DOM listener pe element detașat: ${elementKey} - ${listenerInfo.event}`
        );
      }
    });

    // Creează detalii pe element
    listenersByElement.forEach((data, elementKey) => {
      health.elementDetails.push({
        element: elementKey,
        listenerCount: data.listeners.length,
        events: data.listeners.map((l) => l.event),
        isValid: data.isValid,
      });
    });

    if (invalidDomListeners > 0) {
      health.isHealthy = false;
      log(this, `⚠️ ${invalidDomListeners} DOM listeners pe elemente detașate`);
    }

    // Verifică dacă sunt listeneri adăugați dar registry-ul este gol
    if (
      this._listenerRegistry.stats.domListenersAdded > 0 &&
      this._listenerRegistry.domListeners.size === 0
    ) {
      health.isHealthy = false;
      health.issues.push(`Toți DOM listeners au fost curățați neașteptat!`);
      log(
        this,
        `🚨 PROBLEMĂ: Adăugați ${this._listenerRegistry.stats.domListenersAdded} DOM listeners dar registry-ul este gol!`
      );
    }

    log(this, '📊 HEALTH CHECK RESULT PE ELEMENT ȘI BUS:', health);
    return health;
  };

  target.addCleanupCallback = function (callback) {
    if (typeof callback === 'function') {
      this._listenerRegistry.cleanupCallbacks.add(callback);
      this._listenerRegistry.stats.cleanupCallbacksAdded++;
      log(this, '🧹 Cleanup callback adăugat');
    }
  };
}
