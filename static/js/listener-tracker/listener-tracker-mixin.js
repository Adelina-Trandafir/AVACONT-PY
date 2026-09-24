// File: static/js/mixins/listener-tracker-mixin.js
/**
 * 🎧 LISTENER TRACKER MIXIN - CORE ORCHESTRATOR
 */

import eventBus from '../event-bus/event-bus.js';
import { interceptDOMMethods } from './listener-tracker-dom.js';
import { interceptEventBusMethods, interceptTimerMethods } from './listener-tracker-eventbus.js';
import { addTrackingMethods } from './listener-tracker-cleanup.js';
import { exposeGlobalDebugFunctions } from './listener-tracker-debug.js';

class ListenerTracker {
  constructor() {
    this.debugMode = true;
  }

  static applyTo(target, options = {}) {
    if (target._listenerTrackerApplied) {
      console.warn('⚠️ ListenerTracker deja aplicat pe această instanță');
      return;
    }

    // 📊 REGISTRY LISTENERS PER-INSTANȚĂ
    target._listenerRegistry = {
      busListeners: new Map(),
      domListeners: new Set(),
      timers: new Set(),
      intervals: new Set(),
      cleanupCallbacks: new Set(),
      stats: {
        busListenersAdded: 0,
        domListenersAdded: 0,
        timersAdded: 0,
        intervalsAdded: 0,
        cleanupCallbacksAdded: 0,
      },
    };

    target._trackerOptions = {
      debugMode: true, // FORȚEAZĂ DEBUG PENTRU TESTARE
      trackPerformance: true,
      autoCleanupOnError: true,
      logPrefix: target.constructor.name || 'Unknown',
      ...options,
    };

    // 🎯 APLICĂ METODELE MIXIN DIN MODULE EXTERNE
    addTrackingMethods(target);
    interceptEventBusMethods(target, eventBus);
    interceptDOMMethods(target);
    interceptTimerMethods(target);

    target._listenerTrackerApplied = true;

    ListenerTracker._log(
      target,
      `🎧 ListenerTracker aplicat pe ${target._trackerOptions.logPrefix}`
    );

    // 🌍 EXPUNE FUNCȚII GLOBALE PENTRU DEBUGGING LISTENERS
    exposeGlobalDebugFunctions();
  }

  /**
   * 🔍 LOGGING HELPER ÎMBUNĂTĂȚIT CU OVERRIDE DEBUG ȘI STRUCTURĂ CA EVENTBUS
   */
  static _log(target, message, data = null) {
    // 🆕 VERIFICARE DEBUG CU OVERRIDE GLOBAL
    let targetDebugMode = true; // Default true
    const globalMixinDebugOverride = window.MIXIN_DEBUG_OVERRIDE;

    if (target._trackerOptions) {
      targetDebugMode = target._trackerOptions.debugMode;
    }

    // Logica de debug în ordinea priorității:
    // 1. Dacă MIXIN_DEBUG_OVERRIDE este setat explicit (true/false), folosește-l
    // 2. Altfel, folosește debugMode din target._trackerOptions
    let shouldLog = false;

    if (globalMixinDebugOverride !== undefined) {
      shouldLog = globalMixinDebugOverride; // Override global prioritar
    } else {
      shouldLog = targetDebugMode !== false; // Default: true, doar dacă e explicit false nu loga
    }

    if (!shouldLog) return;

    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now.getMilliseconds().toString().padStart(3, '0')}`;
    const prefix = 'MIXIN';

    // 🎨 SISTEM DE CULORI ÎMBUNĂTĂȚIT SIMILAR EVENTBUS
    let prefixColor = '#ffffff';
    let fontWeight = 'normal';

    // Detectează tipul de mesaj pentru culori specifice
    if (message.includes('📢 DOM ADD:')) {
      prefixColor = '#00a8ffff';
      fontWeight = 'bold'; // Albastru pentru ADD
    } else if (message.includes('📢 DOM EXEC:')) {
      prefixColor = '#ff9500ff';
      fontWeight = 'bold'; // Portocaliu pentru EXEC
    } else if (message.includes('📢 DOM CLEANUP:')) {
      prefixColor = '#ff5555ff';
      fontWeight = 'bold'; // Roșu pentru CLEANUP
    } else if (message.includes('└─ ✅ SUCCESS')) {
      prefixColor = '#00ff88ff';
      fontWeight = 'bold'; // Verde pentru SUCCESS
    } else if (message.includes('└─ ❌ ERROR')) {
      prefixColor = '#ff3366ff';
      fontWeight = 'bold'; // Roșu intens pentru ERROR
    } else if (message.includes('└─')) {
      prefixColor = '#888888ff';
      fontWeight = 'normal'; // Gri pentru detalii
    } else if (message.includes('🖱️')) {
      prefixColor = '#ffaa00ff';
      fontWeight = 'bold'; // Galben pentru click actions
    } else if (message.includes('🗑️') || message.includes('🧹')) {
      prefixColor = '#aa00ffff';
      fontWeight = 'bold'; // Mov pentru cleanup actions
    } else if (message.includes('🎯') || message.includes('ListenerTracker aplicat')) {
      prefixColor = '#00ffddff';
      fontWeight = 'bold'; // Cyan pentru init/setup
    } else if (message.includes('📡') || message.includes('Bus listener')) {
      prefixColor = '#89b910ff';
      fontWeight = 'bold'; // Verde EventBus pentru BUS events
    } else if (message.includes('⏰') || message.includes('Timer')) {
      prefixColor = '#ff6b35ff';
      fontWeight = 'bold'; // Portocaliu pentru timers
    } else if (message.includes('🔄') || message.includes('Interval')) {
      prefixColor = '#4ecdc4ff';
      fontWeight = 'bold'; // Teal pentru intervals
    } else if (message.includes('⚠️') || message.includes('WARNING')) {
      prefixColor = '#ffa500ff';
      fontWeight = 'bold'; // Portocaliu pentru warnings
    } else if (message.includes('❌') || message.includes('ERROR') || message.includes('Eroare')) {
      prefixColor = '#ff4444ff';
      fontWeight = 'bold'; // Roșu pentru erori generale
    } else if (message.includes('✅') || message.includes('SUCCESS')) {
      prefixColor = '#44ff44ff';
      fontWeight = 'bold'; // Verde pentru success general
    } else if (message.includes('🔧') || message.includes('CLEANUP') || message.includes('===')) {
      prefixColor = '#ff77ffff';
      fontWeight = 'bold'; // Magenta pentru operații speciale
    }

    // 📢 LOG FINAL CU FORMATARE COMPLETĂ
    console.log(
      `%c[${ts}] [${prefix}] > [${target._trackerOptions.logPrefix.padEnd(15)}] ${message}`,
      `color: ${prefixColor}; font-weight: ${fontWeight};`,
      data ?? ''
    );
  }
}

// Export funcție standalone pentru logging (folosită în alte module)
export const log = (target, message, data = null) => ListenerTracker._log(target, message, data);

export default ListenerTracker;
