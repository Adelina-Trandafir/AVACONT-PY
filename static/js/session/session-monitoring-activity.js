// ADAPTED (slice 0110-03) from JS_COMPONENTS/session for the portal: the server is the portal
// (/api/portal/session/*, header X-Portal-Token, no cookie); the page hands in the token and the
// sign-in address through init(options). Behaviour of the timers / warning / auto-extension is unchanged.
// js/session/session-monitoring-activity.js
/**
 * 👆 SESSION MONITORING ACTIVITY MIXIN
 * Gestionează activitatea utilizatorului și event listeners
 *
 * @version 3.0.0
 */

export const sessionMonitoringActivityMixin = {
  /**
   * 👆 Gestionează activitatea utilizatorului
   */
  handleUserActivity() {
    const now = Date.now();

    // Emit event pentru activitatea utilizatorului
    this.eventBus.emit(this.EVENTS.USER_ACTIVITY, {
      timestamp: now,
      inGracePeriod: this.inGracePeriod,
      extensionCount: this.extensionCount,
    });

    // DOAR în perioada de grație: încearcă prelungirea automată
    if (this.inGracePeriod && this.extensionCount < this.SESSION_CONFIG.MAX_EXTENSIONS) {
      // Ascunde warning-ul dacă e vizibil
      if (this.warningShown) {
        const modal = document.getElementById('session-warning-modal');
        if (modal) {
          const intervalId = modal.dataset.countdownInterval;
          if (intervalId) clearInterval(intervalId);
          modal.remove();
          this.warningShown = false;
        }
      }

      // Încearcă prelungirea automată
      this.attemptAutoExtension();
      this.lastActivitySent = now;
      return;
    }

    // ÎN AFARA perioadei de grație: doar înregistrează activitatea, NU reseta timerul
    if (!this.inGracePeriod) {
      // Ascunde warning-ul dacă e vizibil (pentru cazuri speciale)
      if (this.warningShown) {
        const modal = document.getElementById('session-warning-modal');
        if (modal) {
          const intervalId = modal.dataset.countdownInterval;
          if (intervalId) clearInterval(intervalId);
          modal.remove();
          this.warningShown = false;
        }
      }

      // DOAR actualizează timpul ultimei activități - NU reseta sessionStartTime
      this.lastActivitySent = now;

      this.log(
        `🔍 Activitate înregistrată la: ${new Date(now).toLocaleTimeString()} (timer continuă să curgă)`
      );

      // NU reprogramează warning-ul și expirarea - lasă timerul să curgă normal
    }
  },

  /**
   * 🎯 Setup activity listeners
   */
  setupActivityListeners() {
    this.activityHandler = this.handleUserActivity.bind(this);
    this.SESSION_CONFIG.ACTIVITY_EVENTS.forEach((event) => {
      document.addEventListener(event, this.activityHandler, { passive: true });
    });

    this.log('✅ Activity listeners configurați');
  },

  /**
   * 🧹 Remove activity listeners
   */
  removeActivityListeners() {
    if (!this.activityHandler) return;
    this.SESSION_CONFIG.ACTIVITY_EVENTS.forEach((event) => {
      document.removeEventListener(event, this.activityHandler);
    });
    this.activityHandler = null;

    this.log('🧹 Activity listeners eliminați');
  },
};
