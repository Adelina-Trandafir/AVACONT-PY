// File: static/js/mixins/listener-tracker-dom.js
/**
 * 🎯 DOM LISTENER TRACKER MODULE
 */

import { log } from './listener-tracker-mixin.js';

const SKIP_EVENTS = ['mousemove', 'mouseenter', 'mouseleave'];

/**
 * 🎯 ADAUGĂ DOM LISTENER CU TRACKING ȘI LOGGING STRUCTURAT - VERSIUNE ACTUALIZATĂ
 */
export function interceptDOMMethods(target) {
  target.addDOMListener = function (element, event, handler, options = {}) {
    try {
      // 🔍 VALIDARE PARAMETRI DE INTRARE
      if (!element || typeof handler !== 'function') {
        throw new Error('Element sau handler invalid');
      }

      // 🚫 DEFINIRE LISTĂ EVENIMENTE DE SĂRIT
      const skipEvents = ['mousemove', 'mouseenter', 'mouseleave'];
      const isSkipped = skipEvents.includes(event);

      // 🌍 INIȚIALIZEAZĂ COUNTER-ELE GLOBALE PE WINDOW (O SINGURĂ DATĂ)
      if (!window._globalListenerCounters) {
        window._globalListenerCounters = {
          totalDOMListeners: 0, // toate
          totalDOMLogged: 0, // doar cele care loghează (non-skip)
          totalBusListeners: 0,
          totalTimers: 0,
          totalIntervals: 0,
          startTime: Date.now(),
          instancesCount: 0,
        };
        console.log('🌍 Inițializat global listener counters pe window');
      }

      // 🔍 VERIFICĂ DACĂ ELEMENTUL EXISTĂ ÎN DOM
      const isInDOM = element.parentNode !== null || element === document || element === window;

      // 🏷️ DETECTAREA ÎMBUNĂTĂȚITĂ PENTRU ELEMENT TAG ȘI ID
      let elementTag, elementId;
      if (element === document) {
        elementTag = 'Document';
        elementId = 'Document';
      } else if (element === window) {
        elementTag = 'Window';
        elementId = 'Window';
      } else if (element === document.body) {
        elementTag = 'Body';
        elementId = 'Body';
      } else if (element === document.documentElement) {
        elementTag = 'HTML';
        elementId = 'HTML';
      } else {
        elementTag = element.tagName || 'Unknown';
        elementId =
          element.id ||
          element.dataset?.rowId ||
          element.dataset?.id ||
          element.getAttribute('data-row-id') ||
          element.getAttribute('data-id') ||
          (element.name ? `name:${element.name}` : null) ||
          (element.className && element.className.trim()
            ? `class:${element.className.split(' ')[0]}`
            : null) ||
          'No ID';
      }

      // 🕵️ GĂSEȘTE ADEVĂRATUL APELANT (NU MIXIN-UL) – doar dacă nu e skip
      let callerInfo = 'necunoscut';

      if (!isSkipped) {
        const stack = new Error().stack;
        const stackLines = stack.split('\n');
        const mixinFiles = ['listener-tracker-mixin.js', 'listener-tracker-dom.js', 'event-bus.js'];
        for (let i = 2; i < Math.min(stackLines.length, 8); i++) {
          const line = stackLines[i].trim();
          if (line && !line.includes('<anonymous>')) {
            const isMixinFile = mixinFiles.some((mixinFile) => line.includes(mixinFile));
            if (!isMixinFile) {
              const match = line.match(/at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/);
              if (match) {
                const [, functionName, fileName, lineNumber] = match;
                const shortFileName = fileName.split('/').pop();
                callerInfo = `${functionName} (${shortFileName}:${lineNumber})`;
                break;
              }
              const simpleMatch = line.match(/at\s+(.+?):(\d+):(\d+)/);
              if (simpleMatch) {
                const [, fileName, lineNumber] = simpleMatch;
                const shortFileName = fileName.split('/').pop();
                callerInfo = `${shortFileName}:${lineNumber}`;
                break;
              }
            }
          }
        }
      } else {
        window._globalListenerCounters.totalDOMListeners++;
      }

      // 📊 STATISTICI DE EXECUȚIE – doar dacă nu e skip
      const executionStats = {
        executionCount: 0,
        totalExecutionTime: 0,
        lastExecution: null,
        errors: 0,
      };

      // 🔧 CAPTUREAZĂ REFERINȚA LA INSTANȚA CURENTĂ
      const instanceContext = this;

      // 🎭 WRAPPER PENTRU HANDLER
      const originalHandler = handler;
      const wrappedHandler = function (domEvent) {
        if (isSkipped) {
          // 🚫 Fără loguri și fără stats
          return originalHandler.call(this, domEvent);
        }

        const startTime = performance.now();
        executionStats.executionCount++;
        executionStats.lastExecution = Date.now();

        const elementInfo = `${elementTag}#${elementId}`;
        const sameEventListeners = Array.from(
          instanceContext._listenerRegistry.domListeners
        ).filter((info) => info.element === element && info.event === event).length;

        log(instanceContext, `📢 DOM EXEC: ${event} pe ${elementInfo}`);
        log(instanceContext, `└─ Apelat din: ${callerInfo}`);
        log(
          instanceContext,
          `└─ Listeners pe event: ${sameEventListeners} | Execuție #${executionStats.executionCount}`
        );

        try {
          const result = originalHandler.call(this, domEvent);
          const endTime = performance.now();
          const duration = (endTime - startTime).toFixed(2);
          executionStats.totalExecutionTime += endTime - startTime;

          log(
            instanceContext,
            `└─ ✅ SUCCESS în ${duration}ms [avg: ${(executionStats.totalExecutionTime / executionStats.executionCount).toFixed(2)}ms]`
          );

          return result;
        } catch (error) {
          const endTime = performance.now();
          const duration = (endTime - startTime).toFixed(2);
          executionStats.errors++;

          log(
            instanceContext,
            `└─ ❌ ERROR în ${duration}ms [total erori: ${executionStats.errors}]`
          );
          log(instanceContext, `└─ Error details: ${error.message}`, error);

          throw error;
        }
      };

      // 📋 INFO PENTRU REGISTRY
      const listenerInfo = {
        element,
        event,
        handler: wrappedHandler,
        originalHandler,
        options,
        addedAt: Date.now(),
        elementTag,
        elementId,
        elementClass: element.className || 'No Class',
        callerInfo,
        isInDOM,
        executionStats,
      };

      // 🔢 STATISTICI ÎNAINTE DE ADD
      const existingListenersOnElement = Array.from(this._listenerRegistry.domListeners).filter(
        (info) => info.element === element
      );
      const eventsOnElement = existingListenersOnElement.map((info) => info.event);
      const newListenerCount = existingListenersOnElement.length + 1;
      const listenersOnSameEvent =
        existingListenersOnElement.filter((info) => info.event === event).length + 1;

      // 🌍 INCREMENTEAZĂ COUNTER GLOBAL
      window._globalListenerCounters.totalDOMListeners++;
      const totalDOMListenersGlobal = window._globalListenerCounters.totalDOMListeners;

      // 🎯 ADAUGĂ ÎN DOM
      element.addEventListener(event, wrappedHandler, options);

      // 📝 ADAUGĂ ÎN REGISTRY
      this._listenerRegistry.domListeners.add(listenerInfo);
      this._listenerRegistry.stats.domListenersAdded++;

      // 📢 LOG ADD (numai dacă nu e skip)
      if (!isSkipped) {
        const eventsList =
          eventsOnElement.length > 0 ? `${eventsOnElement.join(', ')}, ${event}` : event;

        log(this, `📢 DOM ADD: ${event} pe ${elementTag}#${elementId}`);
        log(this, `└─ Apelat din: ${callerInfo}`);
        log(
          this,
          `└─ Listeners: pe event: ${listenersOnSameEvent}, pe element: ${newListenerCount} [${eventsList}]`
        );
        log(
          this,
          `└─ Total global: ${totalDOMListenersGlobal} DOM listeners${isInDOM ? '' : ' ⚠️ Element NU e în DOM'}`
        );
      }

      // 🔄 CLEANUP
      return () => {
        element.removeEventListener(event, wrappedHandler, options);
        this._listenerRegistry.domListeners.delete(listenerInfo);

        // 🌍 DECREMENTEAZĂ COUNTER GLOBAL
        window._globalListenerCounters.totalDOMListeners--;

        if (!isSkipped) {
          const remainingOnElement = Array.from(this._listenerRegistry.domListeners).filter(
            (info) => info.element === element
          ).length;
          const remainingOnSameEvent = Array.from(this._listenerRegistry.domListeners).filter(
            (info) => info.element === element && info.event === event
          ).length;

          log(this, `📢 DOM CLEANUP: ${event} pe ${elementTag}#${elementId}`);
          log(
            this,
            `└─ Rămân: pe event: ${remainingOnSameEvent}, pe element: ${remainingOnElement}`
          );
          log(
            this,
            `└─ Total global: ${window._globalListenerCounters.totalDOMListeners} DOM listeners`
          );
          log(
            this,
            `└─ Exec stats: ${executionStats.executionCount} calls, ${executionStats.errors} errors`
          );
        }
      };
    } catch (error) {
      log(this, `❌ Eroare la adăugarea DOM listener ${event}:`, error);
      return () => {};
    }
  };

  // 🖱️ SHORTCUT PENTRU CLICK LISTENERS - ACTUALIZAT
  target.addClickListener = function (element, handler) {
    // 🏷️ DETECTAREA ÎMBUNĂTĂȚITĂ PENTRU LOG
    let elementInfo;
    if (element === document) {
      elementInfo = 'Document#Document';
    } else if (element === window) {
      elementInfo = 'Window#Window';
    } else if (element === document.body) {
      elementInfo = 'Body#Body';
    } else if (element.tagName) {
      const displayId = element.id || element.dataset?.rowId || 'NoID';
      elementInfo = `${element.tagName}#${displayId}`;
    } else {
      elementInfo = 'UnknownElement';
    }

    return this.addDOMListener(element, 'click', handler);
  };

  // 🗑️ ELIMINĂ DOM LISTENERS - CU SUPORT PENTRU SKIP-EVENTS
  target.removeDOMListener = function (element, eventType = null, handler = null, options = false) {
    try {
      if (!element) {
        log(this, '❌ Element null/undefined la removeDOMListener');
        return { success: false, removedCount: 0, error: 'Element invalid' };
      }

      if (eventType && SKIP_EVENTS.includes(eventType)) {
        try {
          element.removeEventListener(eventType, handler, options);
          window._globalListenerCounters.totalDOMListeners--;
          return {
            success: true,
            removedCount: 1,
            totalFound: 1,
            removedEventTypes: [eventType],
            operationType: 'skipEvent-direct',
          };
        } catch (error) {
          return {
            success: false,
            removedCount: 0,
            error: error.message,
            operationType: 'skipEvent-direct',
          };
        }
      }

      // 🏷️ DETECTAREA ELEMENTULUI PENTRU LOG
      let elementInfo;
      if (element === document) {
        elementInfo = 'Document#Document';
      } else if (element === window) {
        elementInfo = 'Window#Window';
      } else if (element === document.body) {
        elementInfo = 'Body#Body';
      } else if (element.tagName) {
        const displayId = element.id || element.dataset?.rowId || 'NoID';
        elementInfo = `${element.tagName}#${displayId}`;
      } else {
        elementInfo = 'UnknownElement';
      }

      // 🚫 SKIP EVENTS (nu sunt în registry, dar trebuie curățate dacă se cere explicit)
      const skipEvents = ['mousemove', 'mouseenter', 'mouseleave'];
      if (eventType && skipEvents.includes(eventType)) {
        try {
          element.removeEventListener(eventType, handler, options);
          return {
            success: true,
            removedCount: 1,
            totalFound: 1,
            remainingOnElement: 'N/A',
            removedEventTypes: [eventType],
            operationType: 'skipEvent-direct',
          };
        } catch (error) {
          return {
            success: false,
            removedCount: 0,
            error: error.message,
            operationType: 'skipEvent-direct',
          };
        }
      }

      // 🔍 LISTENERS DIN REGISTRY
      let listenersToRemove;
      let operationType;

      if (eventType === null || eventType === undefined) {
        listenersToRemove = Array.from(this._listenerRegistry.domListeners).filter(
          (listenerInfo) => listenerInfo.element === element
        );
        operationType = 'TOȚI listenerii';
      } else {
        listenersToRemove = Array.from(this._listenerRegistry.domListeners).filter(
          (listenerInfo) =>
            listenerInfo.element === element &&
            listenerInfo.event === eventType &&
            (handler ? listenerInfo.handler === handler : true)
        );
        operationType = `listenerii de tip '${eventType}'`;
      }

      if (listenersToRemove.length === 0) {
        log(this, `⚠️ Nu există ${operationType} pe ${elementInfo}`);
        return { success: true, removedCount: 0, message: 'Niciun listener găsit' };
      }

      let removedCount = 0;
      let errors = [];
      let removedEventTypes = new Set();

      listenersToRemove.forEach((listenerInfo, index) => {
        try {
          const { element, event, handler, options } = listenerInfo;

          element.removeEventListener(event, handler, options);
          this._listenerRegistry.domListeners.delete(listenerInfo);
          this._listenerRegistry.stats.domListenersAdded--;

          // 🌍 DECREMENTEAZĂ COUNTER-UL GLOBAL
          if (window._globalListenerCounters) {
            window._globalListenerCounters.totalDOMListeners--;
          }

          removedCount++;
          removedEventTypes.add(event);

          log(
            this,
            `🗑️ Eliminat ${event} listener [${index + 1}/${listenersToRemove.length}] de pe ${elementInfo}, total global: ${window._globalListenerCounters?.totalDOMListeners || 'N/A'}`
          );
        } catch (error) {
          const errorMsg = `Eroare la eliminarea listener-ului ${index + 1}: ${error.message}`;
          errors.push(errorMsg);
          log(this, `❌ ${errorMsg}`);
        }
      });

      const remainingOnElement = Array.from(this._listenerRegistry.domListeners).filter(
        (info) => info.element === element
      ).length;

      const remainingEvents = Array.from(this._listenerRegistry.domListeners)
        .filter((info) => info.element === element)
        .map((info) => info.event);

      const eventsText = remainingEvents.length > 0 ? ` [${remainingEvents.join(', ')}]` : '';
      const removedEventsText = Array.from(removedEventTypes).join(', ');

      if (eventType === null || eventType === undefined) {
        log(
          this,
          `✅ Eliminați ${removedCount}/${listenersToRemove.length} TOȚI listenerii [${removedEventsText}] de pe ${elementInfo} (rămân: ${remainingOnElement}${eventsText})`
        );
      } else {
        log(
          this,
          `✅ Eliminați ${removedCount}/${listenersToRemove.length} listeners '${eventType}' de pe ${elementInfo} (rămân: ${remainingOnElement}${eventsText})`
        );
      }

      return {
        success: errors.length === 0,
        removedCount,
        totalFound: listenersToRemove.length,
        remainingOnElement,
        removedEventTypes: Array.from(removedEventTypes),
        operationType: eventType === null || eventType === undefined ? 'all' : 'specific',
        errors: errors.length > 0 ? errors : undefined,
      };
    } catch (error) {
      log(this, `❌ Eroare critică în removeDOMListener:`, error);
      return { success: false, removedCount: 0, error: error.message };
    }
  };
}
