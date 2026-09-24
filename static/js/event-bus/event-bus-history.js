// File: static/js/event-bus-history.js
/**
 * 📚 EVENT BUS - HISTORY MODULE
 * Gestionează istoricul evenimentelor pentru debugging
 *
 * @version 2.0.0
 * @author Adelina Trandafir - Avatar Soft SRL
 */

export class EventBusHistory {
  constructor(maxSize = 100) {
    this.entries = [];
    this.maxSize = maxSize;
  }

  /**
   * Adaugă un eveniment în istoric
   */
  add(eventName, action, data = null) {
    const entry = {
      eventName,
      action,
      data,
      timestamp: Date.now(),
    };

    this.entries.push(entry);

    // Păstrează doar ultimele N evenimente
    if (this.entries.length > this.maxSize) {
      this.entries = this.entries.slice(-this.maxSize);
    }
  }

  /**
   * Returnează întregul istoric
   */
  getAll() {
    return [...this.entries];
  }

  /**
   * Returnează numărul de intrări
   */
  getSize() {
    return this.entries.length;
  }

  /**
   * Curăță istoricul
   */
  clear() {
    this.entries = [];
  }

  /**
   * Setează dimensiunea maximă
   */
  setMaxSize(maxSize) {
    this.maxSize = maxSize;
    if (this.entries.length > this.maxSize) {
      this.entries = this.entries.slice(-this.maxSize);
    }
  }

  /**
   * Filtrează istoric după nume eveniment
   */
  filterByEvent(eventName) {
    return this.entries.filter((entry) => entry.eventName === eventName);
  }

  /**
   * Filtrează istoric după acțiune (emit, on, once, off)
   */
  filterByAction(action) {
    return this.entries.filter((entry) => entry.action === action);
  }

  /**
   * Returnează ultimele N evenimente
   */
  getRecent(count = 10) {
    return this.entries.slice(-count);
  }
}
