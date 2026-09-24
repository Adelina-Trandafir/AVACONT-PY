// File: static/js/event-bus-helpers.js
/**
 * 🔧 EVENT BUS - HELPER FUNCTIONS
 * Funcții utilitare pentru procesarea stack traces și formatare
 *
 * @version 2.0.0
 * @author Adelina Trandafir - Avatar Soft SRL
 */

export const EventBusHelpers = {
  /**
   * Obține informații despre apelant din stack trace
   * Ignoră fișierele interne (event-bus.js, listener-tracker-mixin.js)
   */
  getCallerInfo() {
    const stack = new Error().stack;
    const stackLines = stack.split('\n');

    let callerInfo = 'necunoscut';
    const mixinFiles = ['listener-tracker-mixin.js', 'event-bus.js'];

    for (let i = 2; i < Math.min(stackLines.length, 8); i++) {
      const line = stackLines[i].trim();
      if (line && !line.includes('<anonymous>')) {
        const isMixinFile = mixinFiles.some((mixinFile) => line.includes(mixinFile));

        if (!isMixinFile) {
          // Pattern: at functionName (file.js:line:col)
          const match = line.match(/at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/);
          if (match) {
            const [, functionName, fileName, lineNumber] = match;
            const shortFileName = fileName.split('/').pop();
            callerInfo = `${functionName} (${shortFileName}:${lineNumber})`;
            break;
          }

          // Pattern: at file.js:line:col
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

    return callerInfo;
  },

  /**
   * Obține detalii formatate despre listeners pentru logging
   * Returnează array cu stringuri formatate: "1.listenerName@ClassName🔥"
   */
  getListenerDetails(listeners) {
    return listeners.map((listenerObj, index) => {
      const { callback, context, once, callingMethod } = listenerObj;

      let listenerName = 'anonymous';

      // Încearcă să obții numele din callingMethod (cel mai precis)
      if (callingMethod && !callingMethod.includes('listener-tracker-mixin.js')) {
        listenerName = callingMethod;
      }
      // Apoi din numele funcției
      else if (callback.name && callback.name !== 'bound ') {
        listenerName = callback.name;
      }
      // Sau din string-ul funcției
      else {
        const funcString = callback.toString();
        const nameMatch = funcString.match(/function\s+([^(]+)/);
        if (nameMatch) {
          listenerName = nameMatch[1];
        } else {
          const firstLine = funcString.split('\n')[0];
          if (firstLine.includes('=>')) {
            listenerName = 'arrowFunction';
          }
        }
      }

      // Extrage context info
      let contextInfo = '';

      if (context && context.constructor && context.constructor.name) {
        const className = context.constructor.name;
        if (className !== 'Object' && !className.includes('Tracker')) {
          contextInfo = `@${className}`;
        }
      }

      if (!contextInfo && callingMethod) {
        const methodMatch = callingMethod.match(/(\w+)\.|\@(\w+)/);
        if (methodMatch) {
          const className = methodMatch[1] || methodMatch[2];
          if (className && className !== 'anonymous') {
            contextInfo = `@${className}`;
          }
        }
      }

      const onceMarker = once ? '🔥' : '';

      return `${index + 1}.${listenerName}${contextInfo}${onceMarker}`;
    });
  },

  /**
   * Obține preview formatat al datelor pentru logging
   * Returnează obiect cu { text, truncated }
   */
  getDataPreview(data, maxLength = 80) {
    const preview =
      typeof data === 'object'
        ? JSON.stringify(data).substring(0, maxLength)
        : String(data).substring(0, maxLength);

    return {
      text: preview,
      truncated: preview.length >= maxLength,
    };
  },

  /**
   * Capturează informații complete despre înregistrarea listener-ului
   * Include: info string, context, și full stack pentru debugging
   */
  captureRegistrationInfo() {
    let registrationInfo = 'necunoscut';
    let contextFromStack = '';
    let fullRegistrationStack = '';

    try {
      const registrationStack = new Error().stack;

      if (registrationStack && typeof registrationStack === 'string') {
        fullRegistrationStack = registrationStack;
        const stackLines = registrationStack.split('\n');

        if (Array.isArray(stackLines) && stackLines.length > 0) {
          for (let i = 2; i < Math.min(stackLines.length, 6); i++) {
            const line = stackLines[i];

            if (line && typeof line === 'string') {
              const trimmedLine = line.trim();

              // Skip internal files
              if (
                trimmedLine &&
                !trimmedLine.includes('event-bus.js') &&
                !trimmedLine.includes('event-bus-helpers.js') &&
                !trimmedLine.includes('listener-tracker-mixin.js')
              ) {
                // Pattern: at functionName (file.js:line:col)
                const match = trimmedLine.match(/at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/);
                if (match && match.length >= 3) {
                  const functionName = match[1];
                  const fileName = match[2];
                  const lineNumber = match[3];
                  const columnNumber = match[4];

                  if (functionName && fileName) {
                    const shortFileName = fileName.split('/').pop();

                    if (lineNumber && columnNumber) {
                      registrationInfo = `${functionName}@${shortFileName}:${lineNumber}:${columnNumber}`;
                    } else {
                      registrationInfo = `${functionName}@${shortFileName}`;
                    }

                    // Extrage numele clasei din funcție
                    const classMatch = functionName.match(/(\w+)\.(\w+)/);
                    if (classMatch && classMatch[1]) {
                      contextFromStack = classMatch[1];
                    }
                    break;
                  }
                }

                // Fallback pattern: at file.js:line:col
                const simpleMatch = trimmedLine.match(/at\s+(.+?):(\d+):(\d+)/);
                if (simpleMatch && simpleMatch.length >= 3) {
                  const fileName = simpleMatch[1];
                  const lineNumber = simpleMatch[2];

                  if (fileName && lineNumber) {
                    const shortFileName = fileName.split('/').pop();
                    registrationInfo = `${shortFileName}:${lineNumber}`;
                    break;
                  }
                }
              }
            }
          }
        }
      }
    } catch (stackError) {
      console.error('⚠️ Eroare la capturarea stack trace pentru listener:', stackError);
      registrationInfo = 'stack-error';
    }

    return {
      info: registrationInfo,
      context: contextFromStack,
      fullStack: fullRegistrationStack,
    };
  },

  /**
   * Găsește numele constantei pentru un eveniment
   * Ex: 'table-ready' -> 'EVENTS.TABLE_READY'
   */
  getEventDisplayName(eventName, EVENTS) {
    if (typeof EVENTS === 'object') {
      const constantEntry = Object.entries(EVENTS).find(([key, value]) => value === eventName);
      if (constantEntry) {
        return `EVENTS.${constantEntry[0]}`;
      }
    }
    return eventName;
  },
};
