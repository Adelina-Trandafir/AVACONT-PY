// File: static/js/event-bus-stats.js
/**
 * 📊 EVENT BUS - STATISTICS MODULE
 * Gestionează statisticile despre evenimente și listeners
 *
 * @version 2.0.0
 * @author Adelina Trandafir - Avatar Soft SRL
 */

export class EventBusStats {
  constructor() {
    this.data = {
      eventsEmitted: 0,
      totalListeners: 0,
      activeListeners: 0,
      failedEmits: 0,
    };
  }

  /**
   * Incrementează contorul de evenimente emise
   */
  incrementEventsEmitted() {
    this.data.eventsEmitted++;
  }

  /**
   * Incrementează contorul de emitări eșuate
   */
  incrementFailedEmits() {
    this.data.failedEmits++;
  }

  /**
   * Adaugă un listener (total și activ)
   */
  addListener() {
    this.data.totalListeners++;
    this.data.activeListeners++;
  }

  /**
   * Elimină un listener activ
   */
  removeListener() {
    this.data.activeListeners = Math.max(0, this.data.activeListeners - 1);
  }

  /**
   * Elimină mai mulți listeners activi
   */
  removeListeners(count) {
    this.data.activeListeners = Math.max(0, this.data.activeListeners - count);
  }

  /**
   * Resetează contorul de listeners activi
   */
  clearActiveListeners() {
    this.data.activeListeners = 0;
  }

  /**
   * Returnează datele statistice
   */
  getData() {
    return { ...this.data };
  }

  /**
   * Returnează statistici complete cu evenimente
   */
  getFullStats(events, historySize) {
    return {
      ...this.data,
      eventTypes: events.size,
      historySize: historySize,
      events: Array.from(events.entries()).map(([name, listeners]) => ({
        name,
        listenerCount: listeners.length,
      })),
    };
  }

  /**
   * Reset complet al statisticilor
   */
  reset() {
    this.data = {
      eventsEmitted: 0,
      totalListeners: 0,
      activeListeners: 0,
      failedEmits: 0,
    };
  }
}
