// ADAPTED (slice 0110-03) from JS_COMPONENTS/session for the portal: the server is the portal
// (/api/portal/session/*, header X-Portal-Token, no cookie); the page hands in the token and the
// sign-in address through init(options). Behaviour of the timers / warning / auto-extension is unchanged.
// js/components/session-monitoring/session-monitoring-manager.js
/**
 * 🚀 SESSION MONITORING MANAGER
 * Gestionează monitorizarea sesiunilor cu prelungire automată limitată
 *
 * @version 3.0.0
 * @author Avatar Soft SRL
 */

import eventBus, { EVENTS } from '../event-bus/event-bus.js';
import { registerInstance } from '../instances-registry.js';
import ListenerTracker from '../listener-tracker/listener-tracker-mixin.js';

import { sessionMonitoringTimersMixin } from './session-monitoring-timers.js';
import { sessionMonitoringExtensionMixin } from './session-monitoring-extension.js';
import { sessionMonitoringUIMixin } from './session-monitoring-ui.js';
import { sessionMonitoringActivityMixin } from './session-monitoring-activity.js';

// 📋 CONFIGURAȚIE SESSION
const SESSION_CONFIG = {
  SESSION_DURATION: null, // Se va seta din server (minute)
  WARNING_TIME: 60000, // Avertizează cu 1 minut înainte
  ACTIVITY_EVENTS: ['click', 'keypress', 'scroll', 'touchstart'],
  DEBUG_MODE: false,
  ACTIVITY_DEBOUNCE: 5000, // Nu trimite activitate mai des de 5 secunde
  CHECK_BEFORE_WARNING: 10000, // Verifică cu server cu 10s înainte de warning
  MAX_EXTENSIONS: 2, // replaced by the server's value in init()
};

/**
 * 🔐 SESSION MONITORING CLASS
 */
class SessionMonitoring {
  constructor() {
    // Singleton check
    if (SessionMonitoring.instance) {
      console.warn('⚠️ SessionMonitoring is singleton, returning existing instance');
      return SessionMonitoring.instance;
    }

    this.debugMode = SESSION_CONFIG.DEBUG_MODE;

    // 🎯 APLICĂ MIXIN-UL LISTENER TRACKER
    ListenerTracker.applyTo(this, {
      debugMode: this.debugMode,
      logPrefix: 'Monitoring',
      trackPerformance: true,
    });

    this.eventBus = eventBus;
    this.EVENTS = EVENTS;
    this.SESSION_CONFIG = SESSION_CONFIG;

    // Apply mixins
    Object.assign(this, sessionMonitoringTimersMixin);
    Object.assign(this, sessionMonitoringExtensionMixin);
    Object.assign(this, sessionMonitoringUIMixin);
    Object.assign(this, sessionMonitoringActivityMixin);

    // Variabile de stare
    this.sessionStartTime = null;
    this.sessionDuration = null;
    this.warningTimeout = null;
    this.expiryTimeout = null;
    this.lastActivitySent = 0;
    this.warningShown = false;
    this.finalWarningShown = false;
    this.sessionExpiryTime = null;
    this.countdownInterval = null;
    this.headerCountdownInterval = null; // Pentru header warning
    this.extensionCount = 0;
    this.inGracePeriod = false;
    this.getToken = () => '';
    this.loginUrl = '/portal';
    this.onExpired = null;

    // Store singleton instance
    SessionMonitoring.instance = this;

    // 🎯 AUTO-REGISTER în registry
    registerInstance('sessionMonitoring', this, {
      version: '3.0.0',
      description: 'Session monitoring with automatic extension and ES6 modular architecture',
      features: ['auto-extension', 'event-driven', 'modular-mixins'],
      dependencies: ['eventBus', 'ListenerTracker'],
    });

    this.log('🎯 SessionMonitoring instance created and registered');
  }

  /**
   * 🚀 Inițializează monitorizarea - O SINGURĂ DATĂ LA LOGIN
   */
  async init(options = {}) {
    this.getToken = options.getToken || this.getToken;
    this.loginUrl = options.loginUrl || this.loginUrl;
    this.onExpired = options.onExpired || null;
    this.resetSessionState();
    this.log('🔍 Inițializez monitorizarea automată...');

    try {
      // Încarcă CSS-ul
      await this.loadMonitoringCSS();

      const response = await this.api('GET', '/api/portal/session/info');
      const data = await response.json().catch(() => ({}));

      if (!response.ok || !data.authenticated) {
        this.leave();
        return false;
      }

      // Setează parametrii sesiunii
      this.sessionStartTime = Date.now();
      this.sessionDuration = data.expires_in * 1000;
      SESSION_CONFIG.SESSION_DURATION = this.sessionDuration;
      SESSION_CONFIG.MAX_EXTENSIONS = data.max_extensions;
      this.extensionCount = data.extensions || 0;

      this.log(`⏱️ Durată sesiune: ${data.expires_in} secunde`);

      // Pornește timer-ele CLIENT-SIDE
      this.scheduleWarningAndExpiry();

      // Înregistrează evenimente de activitate
      this.setupActivityListeners();

      // Salvează funcția de cleanup
      window.cleanupSession = this.cleanup.bind(this);

      return true;
    } catch (error) {
      this.log.error('❌ Eroare la inițializare:', error);

      eventBus.emit(EVENTS.SESSION_ERROR, {
        error: error.message,
        timestamp: Date.now(),
      });
      return false;
    }
  }

  /**
   * 💥 Gestionează expirarea sesiunii
   */
  handleSessionExpired(reason) {
    this.cleanup();

    this.log(`🚪 ${reason}`);

    eventBus.emit(EVENTS.SESSION_EXPIRED, {
      reason,
      extensionCount: this.extensionCount,
      maxExtensions: SESSION_CONFIG.MAX_EXTENSIONS,
      timestamp: Date.now(),
    });

    this.showNotification(`${reason}. Veți fi redirecționat la autentificare.`, 'error');

    setTimeout(() => this.leave(), 3000);
  }

  /**
   * 🚪 Forget the session on the page and go to the sign-in page
   */
  leave() {
    if (this.onExpired) {
      try {
        this.onExpired();
      } catch (error) {
        this.log.error('❌ onExpired a eșuat:', error);
      }
    }
    window.location.href = this.loginUrl;
  }

  /**
   * 🌐 One call to the portal's session routes, with the portal token header
   */
  api(method, path) {
    return fetch(path, {
      method,
      headers: { 'X-Portal-Token': this.getToken() || '' },
      cache: 'no-store',
    });
  }

  /**
   * 🧹 Resetare stare sesiune
   */
  resetSessionState() {
    this.sessionStartTime = null;
    this.sessionDuration = null;
    this.warningTimeout = null;
    this.expiryTimeout = null;
    this.lastActivitySent = 0;
    this.warningShown = false;
    this.sessionExpiryTime = null;
    this.extensionCount = 0;
    this.inGracePeriod = false;
    if (this.countdownInterval) clearInterval(this.countdownInterval);
    if (this.headerCountdownInterval) clearInterval(this.headerCountdownInterval);

    // Curăță header warning dacă există
    this.hideHeaderWarning();
  }

  /**
   * 🧹 Curățare completă
   */
  cleanup() {
    if (this.warningTimeout) clearTimeout(this.warningTimeout);
    if (this.expiryTimeout) clearTimeout(this.expiryTimeout);
    if (this.countdownInterval) clearInterval(this.countdownInterval);
    if (this.headerCountdownInterval) clearInterval(this.headerCountdownInterval);

    this.removeActivityListeners();
    this.hideHeaderWarning();

    eventBus.emit(EVENTS.SESSION_CLEANUP, {
      timestamp: Date.now(),
    });
  }

  /**
   * 📊 GET STATUS - Helper pentru debugging
   */
  getStatus() {
    return {
      sessionStartTime: this.sessionStartTime,
      sessionDuration: this.sessionDuration,
      extensionCount: this.extensionCount,
      maxExtensions: SESSION_CONFIG.MAX_EXTENSIONS,
      inGracePeriod: this.inGracePeriod,
      warningShown: this.warningShown,
      timeRemaining: this.sessionStartTime
        ? this.sessionStartTime + this.sessionDuration - Date.now()
        : null,
    };
  }

  /**
   * 🔍 LOG pentru debugging
   */
  log = (() => {
    const fn = (message, data = null) => {
      if (this.debugMode) {
        const now = new Date();
        const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
          .getMilliseconds()
          .toString()
          .padStart(3, '0')}`;
        const CPN = 'Monitoring'.padEnd(15);
        console.log(
          `%c[${ts}] [${CPN}] ${message}`,
          'color: #aea200ff; font-weight: bold;',
          data ?? ''
        );
      }
    };

    fn.error = (message, data = null) => {
      const now = new Date();
      const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
        .getMilliseconds()
        .toString()
        .padStart(3, '0')}`;
      const CPN = 'Monitoring'.padEnd(15);
      console.error(
        `%c[${ts}] [${CPN}] ${message}`,
        'color: #ef4444; font-weight: bold;',
        data ?? ''
      );
    };

    return fn;
  })();
}

// 🎯 CREAZĂ INSTANȚA SINGLETON
const sessionMonitoring = new SessionMonitoring();

// 🎯 ES6 EXPORTS
export default sessionMonitoring;
export { SESSION_CONFIG };
