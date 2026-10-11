// ADAPTED (slice 0110-03) from JS_COMPONENTS/session for the portal: the server is the portal
// (/api/portal/session/*, header X-Portal-Token, no cookie); the page hands in the token and the
// sign-in address through init(options). Behaviour of the timers / warning / auto-extension is unchanged.
// js/session/session-monitoring-ui.js
/**
 * 🎨 SESSION MONITORING UI MIXIN
 * Gestionează interface-ul: warnings, notificări, loading CSS
 *
 * @version 3.0.0
 */

import { SESSION_CONFIG } from './session-monitoring.js';

export const sessionMonitoringUIMixin = {
  /**
   * 🎨 Încarcă CSS-ul dinamic (DOAR o dată)
   */
  async loadMonitoringCSS() {
    if (document.getElementById('sessionMonitoringCSS')) {
      this.log('✅ CSS deja încărcat');
      return;
    }

    this.log('🎨 Încarc CSS-ul monitoring...');

    return new Promise((resolve, reject) => {
      const link = document.createElement('link');
      link.id = 'sessionMonitoringCSS';
      link.rel = 'stylesheet';
      link.href = '/static/css/monitoring.css';

      link.onload = () => {
        this.log('✅ CSS încărcat cu succes');
        resolve();
      };

      link.onerror = () => {
        this.log.error('❌ Eroare la încărcarea CSS-ului');
        reject(new Error('Failed to load CSS'));
      };

      document.head.appendChild(link);
    });
  },

  /**
   * ⚠️ Arată warning standard
   */
  showSessionWarning(timeRemaining) {
    if (this.warningShown) return;

    this.warningShown = true;
    const seconds = Math.round(timeRemaining / 1000);
    const remainingExtensions = SESSION_CONFIG.MAX_EXTENSIONS - this.extensionCount;

    this.eventBus.emit(this.EVENTS.SESSION_WARNING, {
      timeRemaining: seconds,
      remainingExtensions,
      maxExtensions: SESSION_CONFIG.MAX_EXTENSIONS,
      timestamp: Date.now(),
    });

    const warningModal = document.createElement('div');
    warningModal.id = 'session-warning-modal';

    let message;
    if (remainingExtensions > 0) {
      message = `
        <p>
          Sesiunea dvs. va expira în <strong id="countdown">${seconds}</strong> secunde.
          <br><br>
          <strong>Prelungiri disponibile:</strong> ${remainingExtensions}/${SESSION_CONFIG.MAX_EXTENSIONS}
          <br>
          <em>Sesiunea se va prelungi automat dacă continuați să lucrați.</em>
        </p>
      `;

      warningModal.innerHTML = `
      <div class="session-modal-content">
        <h3>⚠️ Sesiunea va expira în curând!</h3>
        ${message}
      </div>
      <div class="session-modal-overlay"></div>
    `;

      document.body.appendChild(warningModal);
    } else {
      this.showFinalWarning();
    }

    let remainingSeconds = seconds;
    const countdownInterval = setInterval(() => {
      remainingSeconds--;
      const countdownEl = document.getElementById('countdown');
      if (countdownEl) {
        countdownEl.textContent = remainingSeconds;
      }

      if (remainingSeconds <= 0) {
        clearInterval(countdownInterval);
      }
    }, 1000);

    warningModal.dataset.countdownInterval = countdownInterval;
  },

  /**
   * ⚠️ Arată warning final în HEADER (non-blocant)
   */
  showFinalWarning() {
    if (this.finalWarningShown) return;

    this.finalWarningShown = true;

    this.eventBus.emit(this.EVENTS.SESSION_FINAL_WARNING, {
      extensionCount: this.extensionCount,
      maxExtensions: SESSION_CONFIG.MAX_EXTENSIONS,
      timestamp: Date.now(),
    });

    // Arată warning în header în loc de modal
    this.showHeaderWarning(SESSION_CONFIG.WARNING_TIME);
  },

  /**
   * 🔴 Arată warning NON-BLOCANT în header cu countdown
   */
  showHeaderWarning(timeRemaining) {
    const header = document.querySelector('.header');
    if (!header) {
      this.log.error('❌ Header nu a fost găsit!');
      return;
    }

    const seconds = Math.round(timeRemaining / 1000);

    // Creează element warning în mijloc
    const warningElement = document.createElement('div');
    warningElement.id = 'session-header-warning';
    warningElement.className = 'session-header-warning';
    warningElement.innerHTML = `
      ⚠️ Sesiunea va expira în <strong class="countdown-pulse" id="header-countdown">${seconds}</strong> secunde! Salvați-vă lucrul!
    `;

    // Inserează între logo și user-info
    const userInfo = header.querySelector('.user-info');
    header.insertBefore(warningElement, userInfo);

    // Adaugă clasa pentru header roșu
    header.classList.add('header-warning');

    // Start countdown
    let remainingSeconds = seconds;
    const countdownInterval = setInterval(() => {
      remainingSeconds--;
      const countdownEl = document.getElementById('header-countdown');
      if (countdownEl) {
        countdownEl.textContent = remainingSeconds;
      }

      if (remainingSeconds <= 0) {
        clearInterval(countdownInterval);
      }
    }, 1000);

    // Salvează intervalul pentru cleanup
    this.headerCountdownInterval = countdownInterval;

    this.log('🔴 Header warning afișat (non-blocant)');
  },

  /**
   * 🧹 Ascunde header warning
   */
  hideHeaderWarning() {
    const header = document.querySelector('.header');
    const warningElement = document.getElementById('session-header-warning');

    if (warningElement) {
      warningElement.remove();
    }

    if (header) {
      header.classList.remove('header-warning');
    }

    if (this.headerCountdownInterval) {
      clearInterval(this.headerCountdownInterval);
      this.headerCountdownInterval = null;
    }

    this.log('🧹 Header warning ascuns');
  },

  /**
   * 💬 Notificări
   */
  showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;

    document.body.appendChild(notification);

    setTimeout(() => {
      notification.classList.add('fade-out');
      setTimeout(() => notification.remove(), 300);
    }, 3000);
  },
};
