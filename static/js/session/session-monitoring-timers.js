// ADAPTED (slice 0110-03) from JS_COMPONENTS/session for the portal: the server is the portal
// (/api/portal/session/*, header X-Portal-Token, no cookie); the page hands in the token and the
// sign-in address through init(options). Behaviour of the timers / warning / auto-extension is unchanged.
// js/session/session-monitoring-timers.js
/**
 * ⏰ SESSION MONITORING TIMERS MIXIN
 * Gestionează programarea timer-elor pentru warning și expirare
 *
 * @version 3.0.0
 */

import { SESSION_CONFIG } from './session-monitoring.js';

export const sessionMonitoringTimersMixin = {
  /**
   * ⏰ Programează warning și expirare - FĂRĂ REQUEST-URI
   */
  scheduleWarningAndExpiry() {
    if (this.warningTimeout) clearTimeout(this.warningTimeout);
    if (this.expiryTimeout) clearTimeout(this.expiryTimeout);

    const now = Date.now();
    const timeElapsed = now - this.sessionStartTime;
    const timeRemaining = this.sessionDuration - timeElapsed;

    if (timeRemaining <= 0) {
      this.handleSessionExpired('Sesiunea a expirat');
      return;
    }

    // Programează warning
    const warningTime = timeRemaining - SESSION_CONFIG.WARNING_TIME;
    if (warningTime > 0) {
      this.warningTimeout = setTimeout(() => {
        this.inGracePeriod = true; // Activează perioada de grație
        this.verifySessionBeforeWarning();
      }, warningTime);
    } else if (timeRemaining > 0) {
      this.inGracePeriod = true;
      this.showSessionWarning(timeRemaining);
    }

    // Programează expirarea
    this.expiryTimeout = setTimeout(() => {
      this.inGracePeriod = false;
      this.handleSessionExpired('Timpul a expirat');
    }, timeRemaining);

    this.log(
      `⏰ Timere programate: warning în ${Math.round(warningTime / 1000)}s, expirare în ${Math.round(timeRemaining / 1000)}s`
    );
  },
};
