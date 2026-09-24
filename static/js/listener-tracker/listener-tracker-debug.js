// File: static/js/mixins/listener-tracker-debug.js
/**
 * 🌐 DEBUG TOOLS - GLOBAL WINDOW FUNCTIONS
 */

import { getInstance, getAvailableInstances } from '../instances-registry.js';

/**
 * 🎨 LOGGER PENTRU DEBUGGING GLOBAL
 */
const createDebugLogger = () => {
  const CPN = 'GlobalDebug'.padEnd(15);

  const log = (message, data = null) => {
    // Verifică MIXIN_DEBUG_OVERRIDE mai întâi, apoi default true
    const globalOverride = window.MIXIN_DEBUG_OVERRIDE;
    const shouldLog = globalOverride !== undefined ? globalOverride : true;

    if (!shouldLog) return;

    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;

    // 🎨 Sistem de culori pe tip de mesaj
    let color = '#00d4ff'; // Cyan default pentru info
    let fontWeight = 'normal';

    if (message.includes('📊') || message.includes('STATISTICI') || message.includes('SUMAR')) {
      color = '#9b59b6'; // Mov pentru statistici
      fontWeight = 'bold';
    } else if (
      message.includes('✅') ||
      message.includes('SUCCESS') ||
      message.includes('Rezultate')
    ) {
      color = '#00ff88'; // Verde pentru succes
      fontWeight = 'bold';
    } else if (
      message.includes('⚠️') ||
      message.includes('WARNING') ||
      message.includes('Niciun')
    ) {
      color = '#ffa500'; // Portocaliu pentru warnings
      fontWeight = 'bold';
    } else if (message.includes('🧹') || message.includes('CLEANUP')) {
      color = '#ff77ff'; // Magenta pentru cleanup
      fontWeight = 'bold';
    } else if (
      message.includes('🔍') ||
      message.includes('HEALTH') ||
      message.includes('VERIFICARE')
    ) {
      color = '#4ecdc4'; // Teal pentru health checks
      fontWeight = 'bold';
    } else if (message.includes('🔧') || message.includes('DEBUG')) {
      color = '#ffaa00'; // Galben pentru control
      fontWeight = 'bold';
    } else if (message.includes('===')) {
      color = '#00ffdd'; // Cyan intens pentru titluri
      fontWeight = 'bold';
    }

    console.log(
      `%c[${ts}] [${CPN}] ${message}`,
      `color: ${color}; font-weight: ${fontWeight};`,
      data ?? ''
    );
  };

  // ❌ METODA ERROR - LOGHEAZĂ ÎNTOTDEAUNA!
  log.error = (message, data = null) => {
    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;

    console.error(
      `%c[${ts}] [${CPN}] ❌ ${message}`,
      'color: #e74c3c; font-weight: bold;',
      data ?? ''
    );
  };

  // ⚠️ METODA WARNING - LOGHEAZĂ ÎNTOTDEAUNA!
  log.warn = (message, data = null) => {
    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;

    console.warn(
      `%c[${ts}] [${CPN}] ⚠️ ${message}`,
      'color: #ffa500; font-weight: bold;',
      data ?? ''
    );
  };

  // ✅ METODA SUCCESS - PENTRU OPERAȚII REUȘITE
  log.success = (message, data = null) => {
    const globalOverride = window.MIXIN_DEBUG_OVERRIDE;
    const shouldLog = globalOverride !== undefined ? globalOverride : true;

    if (!shouldLog) return;

    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;

    console.log(
      `%c[${ts}] [${CPN}] ✅ ${message}`,
      'color: #00ff88; font-weight: bold;',
      data ?? ''
    );
  };

  return log;
};

const log = createDebugLogger();

/**
 * 🔍 GĂSEȘTE TOATE INSTANȚELE CU LISTENER TRACKER DIN REGISTRY
 */
function getTrackedInstances() {
  const availableInstances = getAvailableInstances();
  const trackedInstances = [];

  availableInstances.forEach((name) => {
    try {
      const instance = getInstance(name);
      if (instance && instance._listenerRegistry && instance._trackerOptions) {
        trackedInstances.push({ name, obj: instance });
      }
    } catch (error) {
      // Skip instances that can't be retrieved
      log.warn(`Nu se poate obține instanța ${name}:`, error.message);
    }
  });

  return trackedInstances;
}

/**
 * 🌐 EXPUNE FUNCȚII GLOBALE PENTRU DEBUGGING LISTENERS
 */
export function exposeGlobalDebugFunctions() {
  // Verifică dacă funcțiile au fost deja expuse
  if (window._listenerDebugFunctionsAdded) return;

  /**
   * 📊 OBȚINE TOȚI LISTENERII ACTIVI DIN TOATE INSTANȚELE
   */
  window.debugAllListeners = function (suppressInternalLogs = true) {
    log('=== 📊 DEBUG: TOȚI LISTENERII ACTIVI ===');

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return {
        totalInstances: 0,
        availableInRegistry: getAvailableInstances(),
        message: 'Nicio instanță cu ListenerTracker găsită',
      };
    }

    // Salvează debugMode original pentru fiecare instanță + global override
    const originalDebugModes = new Map();
    const originalGlobalOverride = window.MIXIN_DEBUG_OVERRIDE;

    if (suppressInternalLogs) {
      // Setează override global la false (prioritar față de debugMode)
      window.MIXIN_DEBUG_OVERRIDE = false;

      trackedInstances.forEach(({ name, obj }) => {
        if (obj._trackerOptions) {
          originalDebugModes.set(name, obj._trackerOptions.debugMode);
          obj._trackerOptions.debugMode = false;
        }
      });
    }

    const allInstances = [];
    const summary = {
      totalInstances: 0,
      totalBusListeners: 0,
      totalDomListeners: 0,
      totalTimers: 0,
      totalIntervals: 0,
      globalCounters: window._globalListenerCounters || null,
      instancesDetails: [],
      registryInfo: {
        totalInRegistry: getAvailableInstances().length,
        trackedInstances: trackedInstances.length,
      },
    };

    trackedInstances.forEach(({ name, obj }) => {
      const instanceInfo = {
        name,
        path: `registry.${name}`,
        className: obj._trackerOptions.logPrefix || obj.constructor.name,
        busListeners: obj._listenerRegistry.busListeners.size,
        domListeners: obj._listenerRegistry.domListeners.size,
        timers: obj._listenerRegistry.timers.size,
        intervals: obj._listenerRegistry.intervals.size,
        stats: obj._listenerRegistry.stats,
        busListenersByEvent: {},
        domListenersByElement: {},
      };

      // Detalii bus listeners
      obj._listenerRegistry.busListeners.forEach((funcs, eventName) => {
        instanceInfo.busListenersByEvent[eventName] = funcs.length;
      });

      // Detalii DOM listeners
      const listenersByElement = new Map();
      obj._listenerRegistry.domListeners.forEach((listenerInfo) => {
        const elementKey = `${listenerInfo.elementTag}#${listenerInfo.elementId}`;
        if (!listenersByElement.has(elementKey)) {
          listenersByElement.set(elementKey, []);
        }
        listenersByElement.get(elementKey).push(listenerInfo.event);
      });
      listenersByElement.forEach((events, elementKey) => {
        instanceInfo.domListenersByElement[elementKey] = events;
      });

      allInstances.push({ obj, info: instanceInfo });
      summary.totalInstances++;
      summary.totalBusListeners += instanceInfo.busListeners;
      summary.totalDomListeners += instanceInfo.domListeners;
      summary.totalTimers += instanceInfo.timers;
      summary.totalIntervals += instanceInfo.intervals;
      summary.instancesDetails.push(instanceInfo);
    });

    // Restaurează debugMode-urile originale + global override
    if (suppressInternalLogs) {
      window.MIXIN_DEBUG_OVERRIDE = originalGlobalOverride;

      originalDebugModes.forEach((debugMode, name) => {
        const tracked = trackedInstances.find((t) => t.name === name);
        if (tracked?.obj?._trackerOptions) {
          tracked.obj._trackerOptions.debugMode = debugMode;
        }
      });
    }

    log('📊 SUMAR GENERAL:', summary);

    allInstances.forEach(({ obj, info }) => {
      log(`🎯 ${info.className} [${info.name}]`);
      log('  └─ Listeners activi:', {
        bus: info.busListeners,
        dom: info.domListeners,
        timers: info.timers,
        intervals: info.intervals,
      });
      log('  └─ Statistici totale adăugate:', info.stats);

      if (info.busListeners > 0) {
        log('  └─ 📡 Bus Listeners pe eveniment:', info.busListenersByEvent);
      }

      if (info.domListeners > 0) {
        log('  └─ 🎧 DOM Listeners pe element:', info.domListenersByElement);
      }
    });

    log.success(
      `Găsite ${summary.totalInstances} instanțe cu ${summary.totalBusListeners} bus + ${summary.totalDomListeners} DOM listeners`
    );
    return summary;
  };

  /**
   * 🧹 CURĂȚĂ TOȚI LISTENERII DIN TOATE INSTANȚELE
   */
  window.cleanupAllListeners = function (suppressInternalLogs = true) {
    log('=== 🧹 CLEANUP GLOBAL: TOȚI LISTENERII ===');

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return [];
    }

    // Salvează debugMode original pentru fiecare instanță + global override
    const originalDebugModes = new Map();
    const originalGlobalOverride = window.MIXIN_DEBUG_OVERRIDE;

    if (suppressInternalLogs) {
      // Setează override global la false (prioritar față de debugMode)
      window.MIXIN_DEBUG_OVERRIDE = false;

      trackedInstances.forEach(({ name, obj }) => {
        if (obj._trackerOptions) {
          originalDebugModes.set(name, obj._trackerOptions.debugMode);
          obj._trackerOptions.debugMode = false;
        }
      });
    }

    const results = [];

    trackedInstances.forEach(({ name, obj }) => {
      if (obj.cleanupAllListeners) {
        log(`🧹 Curăț listeneri pentru ${name}...`);
        try {
          const result = obj.cleanupAllListeners();
          results.push({ instance: name, result, success: true });
          log.success(
            `${name}: ${result.busListeners} bus + ${result.domListeners} DOM + ${result.timers} timers + ${result.intervals} intervals`
          );
        } catch (error) {
          results.push({ instance: name, error: error.message, success: false });
          log.error(`Eroare la cleanup ${name}:`, error);
        }
      } else {
        log.warn(`${name} nu are metoda cleanupAllListeners`);
      }
    });

    // Restaurează debugMode-urile originale + global override
    if (suppressInternalLogs) {
      window.MIXIN_DEBUG_OVERRIDE = originalGlobalOverride;

      originalDebugModes.forEach((debugMode, name) => {
        const tracked = trackedInstances.find((t) => t.name === name);
        if (tracked?.obj?._trackerOptions) {
          tracked.obj._trackerOptions.debugMode = debugMode;
        }
      });
    }

    const totalCleaned = results.reduce((sum, r) => {
      if (r.success && r.result) {
        return (
          sum +
          (r.result.busListeners || 0) +
          (r.result.domListeners || 0) +
          (r.result.timers || 0) +
          (r.result.intervals || 0)
        );
      }
      return sum;
    }, 0);

    log.success(`✅ Cleanup global finalizat: ${totalCleaned} listeners curățați total`, results);
    return results;
  };

  /**
   * 🔍 VERIFICĂ HEALTH PENTRU TOATE INSTANȚELE
   */
  window.checkAllListenersHealth = function (suppressInternalLogs = true) {
    log('=== 🔍 HEALTH CHECK: TOATE INSTANȚELE ===');

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return {
        isHealthy: true,
        instances: [],
        message: 'Nicio instanță cu ListenerTracker găsită',
      };
    }

    // Salvează debugMode original pentru fiecare instanță + global override
    const originalDebugModes = new Map();
    const originalGlobalOverride = window.MIXIN_DEBUG_OVERRIDE;

    if (suppressInternalLogs) {
      // Setează override global la false (prioritar față de debugMode)
      window.MIXIN_DEBUG_OVERRIDE = false;

      trackedInstances.forEach(({ name, obj }) => {
        if (obj._trackerOptions) {
          originalDebugModes.set(name, obj._trackerOptions.debugMode);
          obj._trackerOptions.debugMode = false;
        }
      });
    }

    const healthResults = [];

    trackedInstances.forEach(({ name, obj }) => {
      if (obj.checkListenersHealth) {
        log(`🔍 Health check pentru ${name}:`);
        try {
          const health = obj.checkListenersHealth();

          if (health.isHealthy) {
            log.success(
              `${name}: HEALTHY - ${health.busListenersActive} bus + ${health.domListenersActive} DOM`
            );
          } else {
            log.warn(`${name}: ISSUES FOUND - ${health.issues.length} probleme`, health.issues);
          }

          healthResults.push({ instance: name, health, success: true });
        } catch (error) {
          log.error(`Eroare la health check ${name}:`, error);
          healthResults.push({ instance: name, error: error.message, success: false });
        }
      } else {
        log.warn(`${name} nu are metoda checkListenersHealth`);
      }
    });

    // Restaurează debugMode-urile originale
    if (suppressInternalLogs) {
      originalDebugModes.forEach((debugMode, name) => {
        const tracked = trackedInstances.find((t) => t.name === name);
        if (tracked?.obj?._trackerOptions) {
          tracked.obj._trackerOptions.debugMode = debugMode;
        }
      });
    }

    const overallHealth = {
      isHealthy: healthResults.every((r) => r.success && r.health?.isHealthy),
      instances: healthResults,
      summary: {
        totalBusListeners: healthResults.reduce(
          (sum, r) => sum + (r.health?.busListenersActive || 0),
          0
        ),
        totalDomListeners: healthResults.reduce(
          (sum, r) => sum + (r.health?.domListenersActive || 0),
          0
        ),
        totalIssues: healthResults.reduce((sum, r) => sum + (r.health?.issues?.length || 0), 0),
      },
      globalCounters: window._globalListenerCounters || null,
    };

    if (overallHealth.isHealthy) {
      log.success('🎯 HEALTH GENERAL: TOATE INSTANȚELE SUNT HEALTHY', overallHealth.summary);
    } else {
      log.warn('⚠️ HEALTH GENERAL: PROBLEME DETECTATE', overallHealth);
    }

    return overallHealth;
  };

  /**
   * 📈 STATISTICI DETALIATE PENTRU TOATE INSTANȚELE
   */
  window.getAllListenerStats = function (suppressInternalLogs = true) {
    log('=== 📊 STATISTICI: TOATE INSTANȚELE ===');

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return [];
    }

    // Salvează debugMode original pentru fiecare instanță + global override
    const originalDebugModes = new Map();
    const originalGlobalOverride = window.MIXIN_DEBUG_OVERRIDE;

    if (suppressInternalLogs) {
      // Setează override global la false (prioritar față de debugMode)
      window.MIXIN_DEBUG_OVERRIDE = false;

      trackedInstances.forEach(({ name, obj }) => {
        if (obj._trackerOptions) {
          originalDebugModes.set(name, obj._trackerOptions.debugMode);
          obj._trackerOptions.debugMode = false;
        }
      });
    }

    const allStats = [];

    trackedInstances.forEach(({ name, obj }) => {
      if (obj.getListenerStats) {
        log(`📊 Statistici pentru ${name}:`);
        try {
          const stats = obj.getListenerStats();
          log(`  └─ Bus: ${stats.busListeners} evenimente`, stats.busListenersByEvent);
          log(`  └─ DOM: ${stats.domListeners} listeners`, stats.domListenersByElement);
          log(`  └─ Timers: ${stats.timers}, Intervals: ${stats.intervals}`);
          log(`  └─ Total adăugate:`, stats.totalAdded);

          allStats.push({ instance: name, stats, success: true });
        } catch (error) {
          log.error(`Eroare la statistici ${name}:`, error);
          allStats.push({ instance: name, error: error.message, success: false });
        }
      } else {
        log.warn(`${name} nu are metoda getListenerStats`);
      }
    });

    // Restaurează debugMode-urile originale + global override
    if (suppressInternalLogs) {
      window.MIXIN_DEBUG_OVERRIDE = originalGlobalOverride;

      originalDebugModes.forEach((debugMode, name) => {
        const tracked = trackedInstances.find((t) => t.name === name);
        if (tracked?.obj?._trackerOptions) {
          tracked.obj._trackerOptions.debugMode = debugMode;
        }
      });
    }

    log.success(`Statistici generate pentru ${allStats.filter((s) => s.success).length} instanțe`);
    return allStats;
  };

  /**
   * 🔎 GĂSEȘTE LISTENERI PE ELEMENT SPECIFIC
   */
  window.findListenersByElement = function (element) {
    if (!element) {
      log.error('Trebuie să specifici un element valid');
      return [];
    }

    const elementInfo = element.tagName
      ? `${element.tagName}#${element.id || 'NoID'}`
      : 'UnknownElement';

    log(`🔎 Căutare listeneri pe ${elementInfo}`);

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return [];
    }

    const results = [];

    trackedInstances.forEach(({ name, obj }) => {
      if (obj._listenerRegistry) {
        const foundListeners = Array.from(obj._listenerRegistry.domListeners).filter(
          (info) => info.element === element
        );

        if (foundListeners.length > 0) {
          const events = foundListeners.map((l) => l.event);
          log(`  └─ 📡 ${name}: ${foundListeners.length} listeneri [${events.join(', ')}]`);
          results.push({
            instance: name,
            listeners: foundListeners,
            events: events,
          });
        }
      }
    });

    if (results.length === 0) {
      log.warn(`Niciun listener găsit pe ${elementInfo}`);
    } else {
      log.success(
        `Găsiți ${results.reduce((sum, r) => sum + r.listeners.length, 0)} listeneri pe ${elementInfo}`
      );
    }

    return results;
  };

  /**
   * 🗑️ ELIMINĂ LISTENERI DE PE ELEMENT SPECIFIC
   */
  window.removeListenersFromElement = function (element, eventType = null) {
    if (!element) {
      log.error('Trebuie să specifici un element valid');
      return [];
    }

    const elementInfo = element.tagName
      ? `${element.tagName}#${element.id || 'NoID'}`
      : 'UnknownElement';

    log(`🗑️ Eliminare listeneri de pe ${elementInfo}${eventType ? ` (event: ${eventType})` : ''}`);

    const trackedInstances = getTrackedInstances();

    if (trackedInstances.length === 0) {
      log.warn('Nu există instanțe cu ListenerTracker înregistrate în registry');
      return [];
    }

    const results = [];

    trackedInstances.forEach(({ name, obj }) => {
      if (obj.removeDOMListener) {
        log(`  └─ 🧹 Elimin din ${name}...`);
        try {
          const result = obj.removeDOMListener(element, eventType);
          if (result.removedCount > 0) {
            log.success(`    └─ ${name}: ${result.removedCount} listeners eliminați`);
            results.push({
              instance: name,
              ...result,
              success: true,
            });
          } else {
            log.warn(`    └─ ${name}: niciun listener găsit`);
          }
        } catch (error) {
          log.error(`    └─ ${name}: eroare la eliminare`, error);
          results.push({
            instance: name,
            error: error.message,
            success: false,
          });
        }
      }
    });

    const totalRemoved = results.reduce((sum, r) => sum + (r.removedCount || 0), 0);

    if (totalRemoved > 0) {
      log.success(`✅ ${totalRemoved} listeneri eliminați de pe ${elementInfo}`);
    } else {
      log.warn(`Niciun listener eliminat de pe ${elementInfo}`);
    }

    return results;
  };

  /**
   * 📋 LISTEAZĂ TOATE INSTANȚELE DIN REGISTRY
   */
  window.listTrackedInstances = function () {
    log('=== 📋 INSTANȚE CU LISTENER TRACKER ===');

    const availableInstances = getAvailableInstances();
    const trackedInstances = getTrackedInstances();

    log(`📦 Total instanțe în registry: ${availableInstances.length}`);
    log(`🎯 Instanțe cu ListenerTracker: ${trackedInstances.length}`);

    if (trackedInstances.length > 0) {
      log('✅ Instanțe tracked:');
      trackedInstances.forEach(({ name, obj }) => {
        const stats = obj._listenerRegistry?.stats || {};
        log(`  └─ ${name} (${obj.constructor.name})`, {
          busListenersAdded: stats.busListenersAdded || 0,
          domListenersAdded: stats.domListenersAdded || 0,
          timersAdded: stats.timersAdded || 0,
        });
      });
    }

    const untrackedInstances = availableInstances.filter(
      (name) => !trackedInstances.find((t) => t.name === name)
    );

    if (untrackedInstances.length > 0) {
      log('⚠️ Instanțe fără ListenerTracker:');
      untrackedInstances.forEach((name) => {
        try {
          const instance = getInstance(name);
          log(`  └─ ${name} (${instance.constructor.name})`);
        } catch (error) {
          log(`  └─ ${name} (eroare la obținere)`);
        }
      });
    }

    return {
      totalInstances: availableInstances.length,
      trackedCount: trackedInstances.length,
      untrackedCount: untrackedInstances.length,
      tracked: trackedInstances.map((t) => t.name),
      untracked: untrackedInstances,
    };
  };

  /**
   * 🔧 CONTROL DEBUG MODE PENTRU MIXIN
   */
  window.enableMixinDebug = function () {
    window.MIXIN_DEBUG_OVERRIDE = true;
    log.success('🔧 MIXIN DEBUG FORȚAT ACTIVAT - toate log-urile vor apărea');
  };

  window.disableMixinDebug = function () {
    window.MIXIN_DEBUG_OVERRIDE = false;
    log.warn('🔧 MIXIN DEBUG FORȚAT DEZACTIVAT - nu vor apărea log-uri');
  };

  window.resetMixinDebug = function () {
    window.MIXIN_DEBUG_OVERRIDE = undefined;
    log('🔧 MIXIN DEBUG RESET - va folosi debugMode din fiecare modul individual');
  };

  window.getMixinDebugStatus = function () {
    const override = window.MIXIN_DEBUG_OVERRIDE;
    const status = {
      hasOverride: override !== undefined,
      overrideValue: override,
      behavior:
        override === true
          ? 'FORȚAT ACTIVAT'
          : override === false
            ? 'FORȚAT DEZACTIVAT'
            : 'FOLOSEȘTE SETĂRILE DIN MODULE',
      moduleSettings: {},
    };

    log('=== 🔧 STATUS DEBUG MIXIN ===');
    log(`  └─ Override activ: ${status.hasOverride}`);
    log(`  └─ Valoare override: ${status.overrideValue}`);
    log(`  └─ Comportament: ${status.behavior}`);

    if (!status.hasOverride) {
      log('📋 Setări individuale din module:');
      const trackedInstances = getTrackedInstances();
      trackedInstances.forEach(({ name, obj }) => {
        if (obj._trackerOptions) {
          const debugMode = obj._trackerOptions.debugMode;
          status.moduleSettings[name] = debugMode;
          log(`  └─ ${name.padEnd(20)}: ${debugMode}`);
        }
      });
    }

    log.success('Status debug obținut', status);
    return status;
  };

  // Adaugă flag pentru a nu expune din nou
  window._listenerDebugFunctionsAdded = true;

  log.success('🌐 Funcții debug listeners expuse pe window:', [
    'debugAllListeners(suppressInternalLogs=true)',
    'cleanupAllListeners(suppressInternalLogs=true)',
    'checkAllListenersHealth(suppressInternalLogs=true)',
    'getAllListenerStats(suppressInternalLogs=true)',
    'findListenersByElement(element)',
    'removeListenersFromElement(element, eventType?)',
    'listTrackedInstances()',
    'enableMixinDebug()',
    'disableMixinDebug()',
    'resetMixinDebug()',
    'getMixinDebugStatus()',
  ]);
}
