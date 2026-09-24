// File: static/js/event-bus-logger.js
/**
 * 📝 EVENT BUS - LOGGER MODULE
 * Gestionează toate operațiunile de logging
 *
 * @version 2.0.0
 * @author Adelina Trandafir - Avatar Soft SRL
 */

export class EventBusLogger {
  constructor(debugMode = false) {
    this.debugMode = debugMode;
    this.componentName = 'EventBus'.padEnd(15);
  }

  /**
   * Generează timestamp formatat
   */
  getTimestamp() {
    const now = new Date();
    return `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;
  }

  /**
   * Log standard (respectă debugMode)
   */
  log(message, data = null) {
    if (this.debugMode) {
      const ts = this.getTimestamp();
      console.log(
        `%c[${ts}] [${this.componentName}] ${message}`,
        'color: #89b910ff; font-weight: bold;',
        data ?? ''
      );
    }
  }

  /**
   * Log error (întotdeauna activ, indiferent de debugMode)
   */
  error(message, data = null) {
    const ts = this.getTimestamp();
    console.error(
      `%c[${ts}] [${this.componentName}] ${message}`,
      'color: #ff3333; font-weight: bold;',
      data ?? ''
    );
  }

  /**
   * Log ignore (respectă debugMode, pentru evenimente detaliate)
   */
  ignore(message, data = null) {
    if (this.debugMode) {
      const ts = this.getTimestamp();
      console.log(
        `%c[${ts}] [${this.componentName}] ${message}`,
        'color: #89b910ff; font-weight: bold;',
        data ?? ''
      );
    }
  }

  /**
   * Log fără header (clean output)
   */
  noHeader(message, data = null) {
    if (data !== null && data !== undefined) {
      console.log(message);
      console.log(data);
    } else {
      console.log(message);
    }
  }

  /**
   * Setează debug mode
   */
  setDebugMode(enabled) {
    this.debugMode = enabled;
    this.log(`🔧 Debug mode ${enabled ? 'ACTIVAT' : 'DEZACTIVAT'}`);
  }
}
