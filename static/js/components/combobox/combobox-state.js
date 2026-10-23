/**
 * COMBOBOX STATE MIXIN
 * Gestionează starea internă a combobox-ului
 */
import { orderItems } from '../../utils/typed-order.js';

export const comboboxStateMixin = {
  /**
   * Inițializează starea
   */
  initializeState() {
    this.searchTimeout = null;
    this.currentQuery = '';
    this.selectedText = '';
    this.selectedValue = '';
    this.selectedIndex = -1;
    this.results = [];
    this.isVisible = false;
    this.disabled = false;
    this.lastShownAt = 0;
    this.instanceId = `combobox_${this.container.id || this.container.className}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  },

  /**
   * Actualizează rezultatele
   */
  updateResults(results, query) {
    const ordering = this.options.allowHtml && !this.options.sortValue
      ? { ...this.options, sortValue: item => Object.hasOwn(item, 'sortValue') ? item.sortValue : this.stripHtml(item.label || item.text || '') }
      : this.options;
    this.results = orderItems(results || [], ordering);
    this.selectedIndex = -1;
  },

  /**
   * Verifică dacă are rezultate
   */
  hasResults() {
    return this.results && this.results.length > 0;
  },

  /**
   * Obține rezultatul selectat
   */
  getSelectedResult() {
    if (this.selectedIndex >= 0 && this.selectedIndex < this.results.length) {
      return this.results[this.selectedIndex];
    }
    return null;
  },
};
