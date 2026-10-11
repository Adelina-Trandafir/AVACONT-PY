// ADAPTED (slice 0110-03) from JS_COMPONENTS/session for the portal: the server is the portal
// (/api/portal/session/*, header X-Portal-Token, no cookie); the page hands in the token and the
// sign-in address through init(options). Behaviour of the timers / warning / auto-extension is unchanged.
// js/session/session-monitoring-extension.js
/**
 * 🔄 SESSION MONITORING EXTENSION MIXIN
 * Gestionează verificarea și prelungirea automată a sesiunii
 *
 * @version 3.0.0
 */

import { SESSION_CONFIG } from './session-monitoring.js';

export const sessionMonitoringExtensionMixin = {
  /**
   * 🔍 Verifică cu serverul DOAR înainte de warning
   */
  async verifySessionBeforeWarning() {
    try {
      const response = await this.api('GET', '/api/portal/session/check');

      const data = await response.json();

      if (!response.ok || !data.authenticated) {
        this.handleSessionExpired('Sesiunea a fost invalidată');
        return;
      }

      // Verifică dacă utilizatorul a fost activ recent ȘI suntem în perioada de grație
      if (
        this.inGracePeriod &&
        Date.now() - this.lastActivitySent < SESSION_CONFIG.ACTIVITY_DEBOUNCE
      ) {
        await this.attemptAutoExtension();
      } else {
        this.showSessionWarning(SESSION_CONFIG.WARNING_TIME);
      }
    } catch (error) {
      this.log.error('❌ Eroare verificare:', error);
      this.showSessionWarning(SESSION_CONFIG.WARNING_TIME);
    }
  },

  /**
   * 🔄 Încearcă prelungirea automată (DOAR în perioada de grație)
   */
  async attemptAutoExtension() {
    if (!this.inGracePeriod) {
      this.log('⚠️ Prelungire refuzată - nu suntem în perioada de grație');
      this.showSessionWarning(SESSION_CONFIG.WARNING_TIME);
      return;
    }

    if (this.extensionCount >= SESSION_CONFIG.MAX_EXTENSIONS) {
      this.log.error('❌ Numărul maxim de prelungiri a fost atins');
      // ⚠️ NU mai arată modal, arată header warning NON-BLOCANT
      this.showFinalWarning();
      return;
    }

    try {
      const response = await this.api('POST', '/api/portal/session/extend');

      if (response.ok) {
        const data = await response.json();
        this.extensionCount++;

        // Ascunde warning-ul dacă e afișat (modal SAU header)
        const modal = document.getElementById('session-warning-modal');
        if (modal) {
          const intervalId = modal.dataset.countdownInterval;
          if (intervalId) clearInterval(intervalId);
          modal.remove();
        }

        // Ascunde header warning dacă e afișat
        this.hideHeaderWarning();

        this.warningShown = false;
        this.inGracePeriod = false; // Ieșim din perioada de grație

        this.sessionStartTime = Date.now();
        this.sessionDuration = data.new_expires_in * 1000;

        this.log(`✅ Prelungire automată ${this.extensionCount}/${SESSION_CONFIG.MAX_EXTENSIONS}`);
        this.log(`⏱️ Timp nou: ${data.new_expires_in} secunde`);

        // Emit event pentru prelungirea sesiunii
        this.eventBus.emit(this.EVENTS.SESSION_EXTENDED, {
          extensionCount: this.extensionCount,
          maxExtensions: SESSION_CONFIG.MAX_EXTENSIONS,
          newDuration: this.sessionDuration,
          newExpiresAt: new Date(this.sessionStartTime + this.sessionDuration),
          timestamp: Date.now(),
        });

        this.showNotification(
          `Sesiunea prelungită automat (${this.extensionCount}/${SESSION_CONFIG.MAX_EXTENSIONS})`,
          'success'
        );

        this.scheduleWarningAndExpiry();
      } else {
        throw new Error('Failed to extend session');
      }
    } catch (error) {
      this.log.error('❌ Eroare la prelungirea automată:', error);
      this.showSessionWarning(SESSION_CONFIG.WARNING_TIME);
    }
  },
};
