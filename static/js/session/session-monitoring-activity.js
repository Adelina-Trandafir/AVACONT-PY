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
  handleUserActivity(event) {
    // Un deget pe ecran poate fi începutul unui scroll; atingerea propriu-zisă ajunge ca `click`.
    if (event && event.type === 'pointerdown' && event.pointerType === 'touch') return;
    const now = Date.now();

    // Emit event pentru activitatea utilizatorului
    this.eventBus.emit(this.EVENTS.USER_ACTIVITY, {
      timestamp: now,
      inGracePeriod: this.inGracePeriod,
      extensionCount: this.extensionCount,
    });

    // Orice activitate (tastare, click, scroll, atingere - nu mișcarea mouse-ului) reia timpul de la capăt.
    if (!this.sessionDuration) return;
    if (this.sessionStartTime + this.sessionDuration <= now) return; // expirat: doar serverul decide

    // Local, imediat (cel mult o dată pe secundă, în afară de cazul în care un avertisment e deschis).
    if (this.warningShown || this.finalWarningShown || this.inGracePeriod || now - this.lastLocalReset >= 1000) {
      this.lastLocalReset = now;
      this.adoptSessionDeadline(this.sessionDuration / 1000);
    }

    // Serverul află cel mult o dată la ACTIVITY_DEBOUNCE.
    if (this.renewing || now - this.lastActivitySent < this.SESSION_CONFIG.ACTIVITY_DEBOUNCE) return;
    this.lastActivitySent = now;
    void this.renewOnServer();
  },

  /**
   * 🔄 Spune serverului că utilizatorul lucrează (restartează durata sesiunii)
   */
  async renewOnServer() {
    this.renewing = true;
    try {
      const response = await this.api('POST', '/api/portal/session/renew');
      const data = await response.json().catch(() => ({}));
      if (response.status === 401 || (response.ok && data.authenticated === false)) {
        this.handleSessionExpired('Sesiunea a expirat');
      }
    } catch (error) {
      this.log.error('❌ Reînnoirea sesiunii a eșuat:', error);
    } finally {
      this.renewing = false;
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
