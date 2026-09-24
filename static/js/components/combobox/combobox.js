/**
 * COMBOBOX - CLASA PRINCIPALĂ
 * Versiune modulară cu ListenerTracker pentru tracking automat
 * ✨ UPDATE: Suport pentru prefix icons (steaguri, avatare, etc.)
 */

import { comboboxStateMixin } from './combobox-state.js';
import { comboboxUIMixin } from './combobox-ui.js';
import { comboboxEventsMixin } from './combobox-events.js';
import { comboboxSearchMixin } from './combobox-search.js';
import ListenerTracker from '../../listener-tracker/listener-tracker-mixin.js';

export class Combobox {
  constructor(container, options = {}) {
    // Validare container
    if (!container) {
      throw new Error('Combobox: container is required');
    }

    // Proprietăți de bază
    this.overlay = null;
    this.container = container;
    this.input = null;
    this.loader = null;
    this.dropdown = null;
    this.arrow = null;
    this.prefixIconContainer = null; // ✨ NOU

    this.options = {
      placeholder: options.placeholder || 'Selectați...',
      searchDelay: options.searchDelay || 300,
      onSearch: options.onSearch || null,
      onSelect: options.onSelect || null,
      showLoader: options.showLoader !== false,
      minSearchLength: options.minSearchLength || 0,
      maxResults: options.maxResults || 50,
      highlightMatches: options.highlightMatches !== false,
      allowEmpty: options.allowEmpty !== false,
      readonly: options.readonly || false,
      staticData: options.staticData || [],
      allowHtml: options.allowHtml || false,
      prefixIcon: options.prefixIcon || false, // ✨ activează prefix icon
      showOnlyIcon: options.showOnlyIcon || false, // ✨ afișează doar icon-ul în input
      ...options,
    };

    // Aplică ListenerTracker pentru tracking automat
    ListenerTracker.applyTo(this, {
      debugMode: false,
      logPrefix: `Combobox[${options.placeholder || 'unnamed'}]`,
    });

    // Inițializare
    this.init();
  }

  /**
   * Inițializează combobox-ul
   */
  init() {
    this.initializeState();
    this.createOverlayElement();
    this.createElements();
    this.bindEvents();
    this.updateState();
  }

  /**
   * Setează starea enabled/disabled
   */
  setEnabled(enabled) {
    this.disabled = !enabled;
    this.input.disabled = !enabled;
    if (!enabled) {
      this.hide();
    }
    this.updateState();
  }

  /**
   * Setează valoarea
   */
  setValue(value, text) {
    this.input.value = text || value || '';
    this.currentQuery = this.input.value;
    this.selectedValue = value;
    this.selectedText = text;
  }

  /**
   * ✨ NOU - Setează prefix icon (steag, avatar, etc.)
   * @param {string} html - HTML pentru icon (ex: '<span class="fi fi-ro"></span>')
   */
  setPrefixIcon(html) {
    if (!this.options.prefixIcon || !this.prefixIconContainer) {
      return;
    }

    if (html) {
      this.prefixIconContainer.innerHTML = html;
      this.prefixIconContainer.style.display = 'flex';
    } else {
      this.prefixIconContainer.innerHTML = '';
      this.prefixIconContainer.style.display = 'none';
    }
  }

  /**
   * Obține valoarea input-ului
   */
  getInputValue() {
    return this.input.value;
  }

  /**
   * Obține valoarea selectată (ID din BD)
   */
  getSelectedValue() {
    return this.selectedValue;
  }

  /**
   * Obține textul selectat
   */
  getSelectedText() {
    return this.selectedText;
  }

  /**
   * Obține index-ul selectat
   */
  getIndex() {
    return this.selectedIndex;
  }

  /**
   * Curăță valoarea
   */
  clear() {
    this.setValue('', '');
    this.setPrefixIcon(''); // ✨ Curăță și icon-ul
    this.hide();
  }

  /**
   * Distruge combobox-ul
   */
  destroy() {
    // Clear timeout
    if (this.searchTimeout) {
      clearTimeout(this.searchTimeout);
    }

    // Cleanup TOȚI listenerii folosind ListenerTracker
    this.cleanupAllListeners();

    // Clear container
    this.container.innerHTML = '';
    this.overlay.remove();
    this.container.classList.remove('combobox-container');
  }
}

// Aplicăm toate mixins pe prototype
Object.assign(
  Combobox.prototype,
  comboboxStateMixin,
  comboboxUIMixin,
  comboboxEventsMixin,
  comboboxSearchMixin
);
